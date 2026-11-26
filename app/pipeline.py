from __future__ import annotations

import copy
import os
import traceback
from pathlib import Path
from typing import Any

from .chapters import make_basic_chapters
from .config import (
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    JOBS_DIR,
    MAX_TIME_SECONDS,
)
from .correction import (
    CorrectionCancelled,
    CorrectionError,
    correct_paragraphs,
)
from .diarize import diarize_segments
from .download import download_audio
from .exporters import write_exports
from .formatting import build_paragraphs, build_speakers
from .secrets import pop_job_secrets
from .subtitles import sync_subtitle_segments
from .transcribe import extract_audio, probe_duration, transcribe
from .utils import (
    JobDataError,
    JobDeletedError,
    JobNotFoundError,
    clean_filename,
    is_job_deleted,
    job_dir,
    load_job,
    mutate_job,
    now_iso,
)


def _update(job: dict[str, Any], stage: str, progress: float) -> None:
    pct = round(max(0.0, min(100.0, progress)), 1)

    def apply(latest: dict[str, Any]) -> None:
        latest["stage"] = stage
        latest["progress"] = pct

    mutate_job(job["id"], apply)
    job["stage"] = stage
    job["progress"] = pct


def _commit(job: dict[str, Any], keys: set[str]) -> None:
    values = {key: copy.deepcopy(job.get(key)) for key in keys if key in job}

    def apply(latest: dict[str, Any]) -> None:
        latest.update(values)

    mutate_job(job["id"], apply)


def _finish(job_id: str) -> None:
    def apply(latest: dict[str, Any]) -> None:
        latest["status"] = "done"
        latest["stage"] = "完成"
        latest["progress"] = 100.0
        latest["finished_at"] = now_iso()
        latest["error"] = None
        try:
            latest["exports"] = write_exports(latest, job_dir(job_id))
            latest.pop("export_error", None)
        except Exception as exc:  # exports should not fail the whole job
            latest["export_error"] = str(exc)

    mutate_job(job_id, apply)


def run_pipeline(job_id: str) -> None:
    if is_job_deleted(job_id):
        return
    try:
        job = load_job(job_id)
    except JobDataError as exc:
        print(f"[transcriptor] job {job_id} is corrupt, not running it: {exc}")
        return
    if not job:
        return

    try:
        def start(latest: dict[str, Any]) -> None:
            latest["status"] = "running"
            latest["started_at"] = now_iso()
            latest["error"] = None

        job = mutate_job(job_id, start)
    except (JobDeletedError, JobNotFoundError):
        return
    job["status"] = "running"

    try:
        folder = JOBS_DIR / job_id
        source = folder / job["audio"]["source_filename"]
        source_url = (job.get("audio") or {}).get("source_url")

        if source_url:
            _update(job, "正在下载 YouTube 音频", 2)
            if is_job_deleted(job_id):
                return
            info = download_audio(
                source_url,
                folder,
                progress=lambda stage, pct: _update(job, stage, pct),
            )
            if is_job_deleted(job_id):
                return
            source = info["path"]
            job["audio"]["source_filename"] = source.name
            job["audio"]["filename"] = f"{info['title']}.{info['ext']}".strip(".")
            job["audio"]["duration"] = info.get("duration")
            job["audio"]["size"] = info.get("size")
            if job.pop("title_from_url", False):
                job["title"] = clean_filename(info.get("title") or "YouTube 转写")
            _commit(job, {"audio", "title"})

        wav_path = folder / "audio.wav"

        _update(job, "正在读取音频", 12)
        duration = probe_duration(source)
        if not (0 <= duration <= MAX_TIME_SECONDS):
            raise RuntimeError(
                f"音频时长超出支持范围（0–{MAX_TIME_SECONDS} 秒）：{duration}"
            )
        job["audio"]["duration"] = duration
        _commit(job, {"audio"})

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
        if is_job_deleted(job_id):
            return
        job["segments_count"] = len(segments)
        raw_segments = [
            {
                "start": float(segment["start"]),
                "end": float(segment["end"]),
                "text": segment["text"],
                "speaker_id": "",
            }
            for segment in segments
        ]
        subtitle_segments = copy.deepcopy(raw_segments)
        job["raw_segments"] = raw_segments
        job["segments"] = subtitle_segments
        _commit(job, {"segments_count", "raw_segments", "segments"})

        _update(job, "转写完成，准备说话人分离", 58)
        speaker_indices, speaker_count = diarize_segments(
            segments,
            wav_path,
            int(job["settings"].get("num_speakers", 2)),
            progress=lambda stage, pct: _update(job, stage, pct),
        )
        if is_job_deleted(job_id):
            return

        names = list(job.get("speaker_names") or [])
        while len(names) < speaker_count:
            names.append(f"说话人 {len(names) + 1}")
        job["speakers"] = build_speakers(names[:speaker_count])

        for raw, subtitle, speaker in zip(raw_segments, subtitle_segments, speaker_indices):
            speaker_id = f"S{int(speaker) + 1}"
            raw["speaker_id"] = speaker_id
            subtitle["speaker_id"] = speaker_id
        job["raw_segments"] = raw_segments
        job["segments"] = subtitle_segments
        _commit(job, {"speakers", "raw_segments", "segments"})

        _update(job, "正在整理逐字稿", 80)
        paragraphs = build_paragraphs(
            segments,
            speaker_indices,
            language=job["settings"].get("language"),
            glossary=bool(job["settings"].get("glossary", False)),
        )
        for index, paragraph in enumerate(paragraphs, start=1):
            paragraph["id"] = f"p{index:04d}"
            paragraph["speaker_id"] = f"S{int(paragraph['speaker']) + 1}"
            paragraph["original_text"] = paragraph.get("formatted_text", paragraph["text"])
            paragraph["formatted_text"] = paragraph["original_text"]
            paragraph["correction_status"] = "original"
            paragraph["manual_edited"] = False
            paragraph.pop("speaker", None)
        job["paragraphs"] = paragraphs
        job["stats"] = {
            "segments": len(segments),
            "paragraphs": len(paragraphs),
            "speakers": speaker_count,
        }
        _commit(job, {"paragraphs", "stats"})

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
                    should_cancel=lambda: is_job_deleted(job_id),
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
            except CorrectionCancelled:
                return
            except CorrectionError as exc:
                job["correction"].update(status="failed", error=str(exc))
                job["correction_warning"] = str(exc)
            except Exception as exc:
                job["correction"].update(status="failed", error=str(exc))
                job["correction_warning"] = str(exc)

            if is_job_deleted(job_id):
                return
            changed_paragraphs = [
                paragraph["id"]
                for paragraph in paragraphs
                if paragraph.get("text") != paragraph.get("formatted_text")
            ]
            if changed_paragraphs:
                sync_subtitle_segments(job, changed_paragraphs)
            _commit(
                job,
                {
                    "paragraphs",
                    "segments",
                    "correction",
                    "correction_warning",
                    "stats",
                },
            )

        if chapters_enabled:
            _update(job, "正在生成章节", 96)
            job["chapters"] = make_basic_chapters(paragraphs, duration)
        else:
            job["chapters"] = []
        _commit(job, {"chapters"})

        _update(job, "正在导出 Markdown 和 Word", 98)
        _finish(job_id)
    except (CorrectionCancelled, JobDeletedError, JobNotFoundError):
        return
    except JobDataError as exc:
        print(f"[transcriptor] job {job_id} became corrupt: {exc}")
    except Exception as exc:
        if is_job_deleted(job_id):
            return
        try:
            def fail(latest: dict[str, Any]) -> None:
                latest["status"] = "error"
                latest["stage"] = "处理失败"
                latest["error"] = str(exc)
                latest["finished_at"] = now_iso()
                latest["traceback"] = traceback.format_exc()

            mutate_job(job_id, fail)
        except (JobDeletedError, JobNotFoundError, JobDataError):
            pass
        print(f"[transcriptor] job {job_id} failed:\n{traceback.format_exc()}")
    finally:
        pop_job_secrets(job_id)
