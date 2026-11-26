from __future__ import annotations

from typing import Any

from .config import (
    DEFAULT_AI_CORRECTION,
    DEFAULT_CHAPTERS,
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_GLOSSARY,
    DEFAULT_WHISPER_MODEL,
    WHISPER_MODELS,
)
from .formatting import build_speakers, parse_speaker_names
from .utils import clean_filename, now_iso


def validate_job_options(num_speakers: int, model: str) -> None:
    if num_speakers < 1 or num_speakers > 4:
        raise ValueError("说话人数量需在 1–4 之间。")
    if model not in WHISPER_MODELS:
        raise ValueError("未知的语音模型。")


def build_job(
    job_id: str,
    title: str,
    speakers: str = "说话人 1, 说话人 2",
    num_speakers: int = 2,
    language: str = "auto",
    model: str = DEFAULT_WHISPER_MODEL,
    initial_prompt: str = "",
    ai_correction: bool = DEFAULT_AI_CORRECTION,
    chapters: bool = DEFAULT_CHAPTERS,
    glossary: bool = DEFAULT_GLOSSARY,
    deepseek_base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
    deepseek_model: str = DEFAULT_DEEPSEEK_MODEL,
    audio: dict[str, Any] | None = None,
    title_from_url: bool = False,
) -> dict[str, Any]:
    names = parse_speaker_names(speakers, num_speakers)
    job: dict[str, Any] = {
        "id": job_id,
        "title": clean_filename(title or "访谈逐字稿", "访谈逐字稿"),
        "created_at": now_iso(),
        "status": "queued",
        "stage": "排队中",
        "progress": 0,
        "error": None,
        "audio": audio or {},
        "settings": {
            "language": language,
            "num_speakers": num_speakers,
            "model": model,
            "initial_prompt": initial_prompt.strip(),
            "ai_correction": bool(ai_correction),
            "chapters": bool(chapters),
            "glossary": bool(glossary),
            "deepseek_base_url": deepseek_base_url.strip() or DEFAULT_DEEPSEEK_BASE_URL,
            "deepseek_model": deepseek_model.strip() or DEFAULT_DEEPSEEK_MODEL,
        },
        "speaker_names": names,
        "speakers": build_speakers(names),
        "raw_segments": [],
        "segments": [],
        "paragraphs": [],
        "chapters": [],
        "stats": {},
    }
    if title_from_url:
        job["title_from_url"] = True
    return job
