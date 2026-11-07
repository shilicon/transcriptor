from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from .config import WHISPER_MODELS

ProgressFn = Callable[[str, float], None]


class TranscriptionError(RuntimeError):
    pass


def _require_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise TranscriptionError(f"找不到 {name}，请先安装 ffmpeg。")
    return path


def probe_duration(path: Path) -> float:
    ffprobe = _require_binary("ffprobe")
    proc = subprocess.run(
        [
            ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise TranscriptionError(proc.stderr.strip() or "无法读取音频时长。")
    try:
        return float(proc.stdout.strip())
    except ValueError as exc:
        raise TranscriptionError("无法读取音频时长。") from exc


def extract_audio(input_path: Path, output_path: Path) -> None:
    ffmpeg = _require_binary("ffmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            ffmpeg, "-y", "-v", "error",
            "-i", str(input_path),
            "-vn", "-map", "0:a:0",
            "-ar", "16000", "-ac", "1",
            "-c:a", "pcm_s16le",
            str(output_path),
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[-8:]
        raise TranscriptionError(
            "无法从文件中提取音轨。" + ("\n" + "\n".join(detail) if detail else "")
        )


def model_path(model_key: str) -> Path:
    path = WHISPER_MODELS.get(model_key)
    if not path:
        raise TranscriptionError(f"未知模型：{model_key}")
    if not path.exists():
        raise TranscriptionError(
            f"模型文件不存在：{path}\n"
            "请把 whisper 模型放到 models/ 目录，或设置 TRANSCRIPTOR_MODELS_DIR。"
        )
    return path


def transcribe(
    wav_path: Path,
    model_key: str,
    language: str | None = None,
    initial_prompt: str | None = None,
    progress: ProgressFn | None = None,
) -> list[dict[str, Any]]:
    try:
        import mlx_whisper
    except Exception as exc:  # pragma: no cover - environment dependent
        raise TranscriptionError(f"无法加载 mlx-whisper：{exc}") from exc

    path = model_path(model_key)
    if progress:
        progress("正在加载语音模型并转写", 18)

    try:
        result = mlx_whisper.transcribe(
            str(wav_path),
            path_or_hf_repo=str(path),
            language=language or None,
            task="transcribe",
            temperature=0.0,
            word_timestamps=False,
            condition_on_previous_text=False,
            initial_prompt=initial_prompt or None,
            verbose=False,
        )
    except Exception as exc:  # pragma: no cover - model/runtime dependent
        raise TranscriptionError(f"转写失败：{exc}") from exc

    segments: list[dict[str, Any]] = []
    for segment in result.get("segments", []):
        text = (segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment.get("start") or 0)
        end = float(segment.get("end") or start)
        if end <= start:
            end = start + 0.2
        if segment.get("no_speech_prob", 0) > 0.85 and len(text) < 8:
            continue
        segments.append({"start": start, "end": end, "text": text})

    if not segments:
        raise TranscriptionError("没有识别到语音内容。")
    return segments
