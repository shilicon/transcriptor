from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default


DATA_DIR = _path_from_env("TRANSCRIPTOR_DATA_DIR", BASE_DIR / "data")
JOBS_DIR = DATA_DIR / "jobs"
MODELS_DIR = _path_from_env("TRANSCRIPTOR_MODELS_DIR", BASE_DIR / "models")

WHISPER_MODELS = {
    "large-v3": MODELS_DIR / "whisper-large-v3",
    "turbo": MODELS_DIR / "whisper-large-v3-turbo",
}
DEFAULT_WHISPER_MODEL = "large-v3"

DEFAULT_LLM_BASE_URL = os.environ.get(
    "TRANSCRIPTOR_LLM_BASE_URL", "http://127.0.0.1:1234/v1"
)
DEFAULT_LLM_MODEL = os.environ.get("TRANSCRIPTOR_LLM_MODEL", "")

DEFAULT_DEEPSEEK_BASE_URL = os.environ.get(
    "TRANSCRIPTOR_DEEPSEEK_BASE_URL", "https://api.deepseek.com"
)
DEFAULT_DEEPSEEK_MODEL = os.environ.get(
    "TRANSCRIPTOR_DEEPSEEK_MODEL", "deepseek-flash"
)
DEFAULT_AI_CORRECTION = os.environ.get("TRANSCRIPTOR_AI_CORRECTION", "1") == "1"
DEFAULT_CHAPTERS = os.environ.get("TRANSCRIPTOR_CHAPTERS", "1") == "1"

MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB
ALLOWED_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
    ".mp4", ".mov", ".mkv", ".webm", ".m4v",
}

JOBS_DIR.mkdir(parents=True, exist_ok=True)
