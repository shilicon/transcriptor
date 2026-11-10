from __future__ import annotations

import os
import traceback
from pathlib import Path
from typing import Any

from .chapters import make_basic_chapters
from .config import (
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    JOBS_DIR,
)
from .correction import CorrectionError, correct_paragraphs
from .diarize import diarize_segments
from .download import download_audio
from .exporters import write_exports
from .formatting import build_paragraphs, build_speakers
from .secrets import pop_job_secrets
from .subtitles import sync_subtitle_segments
from .transcribe import extract_audio, probe_duration, transcribe
from .utils import clean_filename, job_dir, load_job, now_iso, save_job


def _update(job: dict[str, Any], stage: str, progress: float) -> None:
    job["stage"] = stage
    job["progress"] = round(max(0.0, min(100.0, progress)), 1)
    save_job(job)


def _finish(job: dict[str, Any]) -> None:
    job["status"] = "done"
    job["stage"] = "完成"
    job["progress"] = 100.0
    job["finished_at"] = now_iso()
    job["error"] = None
    save_job(job)

    try:
        job["exports"] = write_exports(job, job_dir(job["id"]))
        save_job(job)
    except Exception as exc:  # exports should not fail the whole job
        job["export_error"] = str(exc)
        save_job(job)


def run_pipeline(job_id: str) -> None:
    job = load_job(job_id)
    if not job:
        return
    job["status"] = "running"
    job["started_at"] = now_iso()
    job["error"] = None
    save_job(job)

    try:
        folder = JOBS_DIR / job_id
        source = folder / job["audio"]["source_filename"]
        source_url = (job.get("audio") or {}).get("source_url")

        if source_url:
            _update(job, "正在下载 YouTube 音频", 2)
            info = download_audio(
                source_url,
                folder,
                progress=lambda stage, pct: _update(job, stage, pct),
            )
            source = info["path"]
            job["audio"]["source_filename"] = source.name
            job["audio"]["filename"] = f"{info['title']}.{info['ext']}".strip(".")
            job["audio"]["duration"] = info.get("duration")
            job["audio"]["size"] = info.get("size")
            if job.pop("title_from_url", False):
                job["title"] = clean_filename(info.get("title") or "YouTube 转写")
            save_job(job)

        wav_path = folder / "audio.wav"

        _update(job, "正在读取音频", 12)
        duration = probe_duration(source)
        job["audio"]["duration"] = duration
        save_job(job)

        _update(job, "正在提取音轨", 14)
        extract_audio(source, wav_path)

        _update(job, "正在转写", 16)
        segments = transcribe(
            wav_path=wav_path,
            model_key=job["settings"].get("model", "large-v3"),
            language=None
            if job["settings"].get("language") in (None, "", "auto")
            else job["settings"].get("language"),
            initial_prompt=job["settings"].get("initial_prompt") or None,
            progress=lambda stage, pct: _update(job, stage, pct),
        )
        job["segments_count"] = len(segments)
        _update(job, "转写完成，准备说话人分离", 58)

        speaker_indices, speaker_count = diarize_segments(
            segments,
            wav_path,
            int(job["settings"].get("num_speakers", 2)),
            progress=lambda stage, pct: _update(job, stage, pct),
        )

        names = list(job.get("speaker_names") or [])
        while len(names) < speaker_count:
            names.append(f"说话人 {len(names) + 1}")
        job["speakers"] = build_speakers(names[:speaker_count])

        job["segments"] = [
            {
                "start": float(segment["start"]),
                "end": float(segment["end"]),
                "text": segment["text"],
                "speaker_id": f"S{int(speaker) + 1}",
            }
            for segment, speaker in zip(segments, speaker_indices)
        ]

        _update(job, "正在整理逐字稿", 80)
        paragraphs = build_paragraphs(
            segments,
            speaker_indices,
            language=job["settings"].get("language"),
        )
        for index, paragraph in enumerate(paragraphs, start=1):
            paragraph["id"] = f"p{index:04d}"
            paragraph["speaker_id"] = f"S{int(paragraph['speaker']) + 1}"
            paragraph["original_text"] = paragraph["text"]
            paragraph.pop("speaker", None)
        job["paragraphs"] = paragraphs
        job["stats"] = {
            "segments": len(segments),
            "paragraphs": len(paragraphs),
            "speakers": speaker_count,
        }

        ai_correction = bool(job["settings"].get("ai_correction", True))
        chapters_enabled = bool(job["settings"].get("chapters", True))
        job["correction"] = {
            "enabled": ai_correction,
            "status": "pending" if ai_correction else "disabled",
            "model": job["settings"].get("deepseek_model") or DEFAULT_DEEPSEEK_MODEL,
            "applied": 0,
            "rejected": 0,
            "batches": 0,
            "errors": [],
            "error": None,
        }

        if ai_correction:
            _update(job, "正在 AI 校正", 88)
            secrets = pop_job_secrets(job_id)
            api_key = (
                secrets.get("deepseek_api_key")
                or os.environ.get("TRANSCRIPTOR_DEEPSEEK_API_KEY")
                or os.environ.get("DEEPSEEK_API_KEY")
                or ""
            )
            speakers_map = {speaker["id"]: speaker["name"] for speaker in job["speakers"]}
            try:
                result = correct_paragraphs(
                    paragraphs=paragraphs,
                    speakers=speakers_map,
                    api_key=api_key,
                    base_url=job["settings"].get("deepseek_base_url") or DEFAULT_DEEPSEEK_BASE_URL,
                    model=job["settings"].get("deepseek_model") or DEFAULT_DEEPSEEK_MODEL,
                    progress=lambda stage, pct: _update(job, stage, pct),
                )
                job["correction"].update(
                    status="applied" if result["applied"] else "unchanged",
                    applied=result["applied"],
                    rejected=result["rejected"],
                    batches=result["batches"],
                    errors=result.get("errors") or [],
                    model=result.get("model") or job["correction"]["model"],
                )
                if result.get("errors"):
                    job["correction"]["status"] = "partial"
                    job["correction"]["error"] = "；".join(result["errors"][:3])
                job["stats"]["corrected"] = result["applied"]
            except CorrectionError as exc:
                job["correction"].update(status="failed", error=str(exc))
                job["correction_warning"] = str(exc)
            except Exception as exc:
                job["correction"].update(status="failed", error=str(exc))
                job["correction_warning"] = str(exc)

            changed_paragraphs = [
                paragraph["id"]
                for paragraph in paragraphs
                if paragraph.get("text") != paragraph.get("original_text")
            ]
            if changed_paragraphs:
                sync_subtitle_segments(job, changed_paragraphs)

        if chapters_enabled:
            _update(job, "正在生成章节", 96)
            job["chapters"] = make_basic_chapters(paragraphs, duration)
        else:
            job["chapters"] = []

        _update(job, "正在导出 Markdown 和 Word", 98)
        _finish(job)
    except Exception as exc:
        job = load_job(job_id) or job
        job["status"] = "error"
        job["stage"] = "处理失败"
        job["error"] = str(exc)
        job["finished_at"] = now_iso()
        job["traceback"] = traceback.format_exc()
        save_job(job)
        print(f"[transcriptor] job {job_id} failed:\n{job['traceback']}")
    finally:
        pop_job_secrets(job_id)
