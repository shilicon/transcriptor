from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

from .chapters import make_basic_chapters
from .config import JOBS_DIR
from .diarize import diarize_segments
from .exporters import render_docx, render_markdown
from .formatting import build_paragraphs, build_speakers
from .transcribe import extract_audio, probe_duration, transcribe
from .utils import job_dir, load_job, now_iso, save_job


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

    md_path = job_dir(job["id"]) / "transcript.md"
    docx_path = job_dir(job["id"]) / "transcript.docx"
    try:
        md_path.write_text(render_markdown(job), encoding="utf-8")
        job["exports"] = {"md": md_path.name, "docx": docx_path.name}
        render_docx(job, docx_path)
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
        wav_path = folder / "audio.wav"

        _update(job, "正在读取音频", 4)
        duration = probe_duration(source)
        job["audio"]["duration"] = duration
        save_job(job)

        _update(job, "正在提取音轨", 8)
        extract_audio(source, wav_path)

        _update(job, "正在转写", 14)
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

        _update(job, "正在整理逐字稿", 80)
        paragraphs = build_paragraphs(
            segments,
            speaker_indices,
            language=job["settings"].get("language"),
        )
        for paragraph in paragraphs:
            paragraph["speaker_id"] = f"S{int(paragraph['speaker']) + 1}"
            paragraph.pop("speaker", None)
        job["paragraphs"] = paragraphs

        _update(job, "正在生成章节", 92)
        job["chapters"] = make_basic_chapters(paragraphs, duration)
        job["stats"] = {
            "segments": len(segments),
            "paragraphs": len(paragraphs),
            "speakers": speaker_count,
        }
        _update(job, "正在导出 Markdown 和 Word", 97)
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
