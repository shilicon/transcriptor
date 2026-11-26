from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import patch


@contextmanager
def isolated_jobs_dir() -> Iterator[tuple[Path, Path]]:
    """Patch every module-level JOBS_DIR used by the backend."""
    with tempfile.TemporaryDirectory(prefix="transcriptor-test-") as tmp:
        root = Path(tmp)
        jobs = root / "jobs"
        jobs.mkdir()
        with (
            patch("app.config.JOBS_DIR", jobs),
            patch("app.utils.JOBS_DIR", jobs),
            patch("app.main.JOBS_DIR", jobs),
            patch("app.pipeline.JOBS_DIR", jobs),
            patch("app.security.config.DATA_DIR", root),
        ):
            yield root, jobs


def make_job(
    job_id: str,
    status: str = "done",
    text: str = "Original approved text.",
    *,
    paragraphs: list[dict] | None = None,
) -> dict:
    if paragraphs is None:
        paragraphs = [
            {
                "id": "p0001",
                "start": 0,
                "end": 10,
                "speaker_id": "S1",
                "text": text,
                "formatted_text": text,
                "original_text": text,
                "raw_text": text,
                "correction_status": "original",
                "manual_edited": False,
                "segment_indices": [0],
            }
        ]
    return {
        "id": job_id,
        "title": "Synthetic audit fixture",
        "created_at": "2026-11-11T00:00:00+08:00",
        "status": status,
        "progress": 100 if status == "done" else 0,
        "settings": {
            "ai_correction": False,
            "chapters": False,
            "num_speakers": 1,
            "glossary": False,
        },
        "speaker_names": ["Audit speaker"],
        "speakers": [{"id": "S1", "name": "Audit speaker"}],
        "audio": {"filename": "audit.wav", "source_filename": "input.wav", "duration": 10},
        "raw_segments": [
            {"start": 0, "end": 10, "text": text, "speaker_id": "S1"}
        ],
        "segments": [
            {"start": 0, "end": 10, "text": text, "speaker_id": "S1"}
        ],
        "paragraphs": paragraphs,
        "chapters": [],
        "translations": {},
        "stats": {"segments": 1, "paragraphs": len(paragraphs), "speakers": 1},
    }
