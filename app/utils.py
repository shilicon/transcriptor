from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .config import JOBS_DIR, MAX_TIME_SECONDS


class JobDataError(RuntimeError):
    """The on-disk job file exists but cannot be trusted."""

    def __init__(self, job_id: str, detail: str) -> None:
        self.job_id = job_id
        self.detail = detail
        super().__init__(f"任务 {job_id} 的数据文件损坏：{detail}")


class JobNotFoundError(RuntimeError):
    pass


class JobDeletedError(RuntimeError):
    pass


_locks_guard = threading.Lock()
_job_locks: dict[str, threading.RLock] = {}
_deleted_jobs: set[str] = set()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def job_dir(job_id: str) -> Path:
    if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
        raise ValueError("非法任务 ID")
    return JOBS_DIR / job_id


def job_file(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def _is_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (OverflowError, ValueError, TypeError):
        return False
    return math.isfinite(number)


def _is_time(value: Any) -> bool:
    return _is_number(value) and 0 <= float(value) <= MAX_TIME_SECONDS


def _optional_str(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _optional_number(value: Any) -> bool:
    return value is None or _is_number(value)


CLIENT_INT_MIN = -(2**63)
CLIENT_INT_MAX = 2**63 - 1


def _optional_int(value: Any) -> bool:
    return value is None or (
        isinstance(value, int)
        and not isinstance(value, bool)
        and CLIENT_INT_MIN <= value <= CLIENT_INT_MAX
    )


def _validate_job_data(job_id: str, data: Any) -> dict[str, Any]:
    def fail(detail: str) -> None:
        raise JobDataError(job_id, detail)

    if not isinstance(data, dict):
        fail("job.json 顶层不是对象")
    if data.get("id") != job_id:
        fail("任务 ID 与目录名不一致")

    title = data.get("title")
    if title is None:
        data["title"] = "未命名任务"
    elif not isinstance(title, str):
        fail("title 不是字符串")
    if not _optional_str(data.get("created_at")):
        fail("created_at 不是字符串")
    if not _optional_str(data.get("finished_at")):
        fail("finished_at 不是字符串")
    if not _optional_str(data.get("started_at")):
        fail("started_at 不是字符串")
    if data.get("status") is None or not isinstance(data.get("status"), str):
        fail("status 不是字符串")
    progress = data.get("progress")
    if progress is not None and (
        not _is_number(progress) or not 0 <= float(progress) <= 100
    ):
        fail("progress 不是 0–100 的有限数字")
    if not _optional_str(data.get("stage")):
        fail("stage 不是字符串")
    if not _optional_str(data.get("error")):
        fail("error 不是字符串")
    if not _optional_str(data.get("export_error")):
        fail("export_error 不是字符串")
    if not _optional_str(data.get("correction_warning")):
        fail("correction_warning 不是字符串")

    speaker_names = data.get("speaker_names")
    if speaker_names is not None and (
        not isinstance(speaker_names, list)
        or any(not isinstance(name, str) for name in speaker_names)
    ):
        fail("speaker_names 不是字符串数组")

    exports = data.get("exports")
    if exports is not None and (
        not isinstance(exports, dict)
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in exports.items())
    ):
        fail("exports 不是字符串映射")

    for key in ("paragraphs", "speakers", "chapters", "segments", "raw_segments"):
        value = data.get(key)
        if value is None:
            data[key] = []
            continue
        if not isinstance(value, list):
            fail(f"{key} 不是数组")
        if any(not isinstance(item, dict) for item in value):
            fail(f"{key} 中包含无效条目")
    for key in ("audio", "settings", "stats", "correction", "translations"):
        value = data.get(key)
        if value is not None and not isinstance(value, dict):
            fail(f"{key} 不是对象")

    translations = data.get("translations")
    if isinstance(translations, dict):
        for language, entry in translations.items():
            if not isinstance(language, str) or not language:
                fail("translations 的语言键不是字符串")
            if not isinstance(entry, dict):
                fail("translations 中包含无效条目")
            for key in ("model", "created_at"):
                if not _optional_str(entry.get(key)):
                    fail(f"translations.{key} 不是字符串")
            texts = entry.get("texts")
            if not isinstance(texts, dict) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in texts.items()
            ):
                fail("translations.texts 不是字符串映射")

    audio = data.get("audio") or {}
    for key in ("filename", "source_filename", "source_url"):
        if not _optional_str(audio.get(key)):
            fail(f"audio.{key} 不是字符串")
    duration = audio.get("duration")
    if duration is not None and not _is_time(duration):
        fail(f"audio.duration 超出 0–{MAX_TIME_SECONDS} 秒范围")
    size = audio.get("size")
    if size is not None and (not _optional_int(size) or size < 0):
        fail("audio.size 不是非负客户端整数")

    settings = data.get("settings") or {}
    for key in ("language", "model", "deepseek_base_url", "deepseek_model", "initial_prompt"):
        if not _optional_str(settings.get(key)):
            fail(f"settings.{key} 不是字符串")
    num_speakers = settings.get("num_speakers")
    if num_speakers is not None and (
        not _optional_int(num_speakers) or not 1 <= num_speakers <= 4
    ):
        fail("settings.num_speakers 不是 1–4 的整数")
    for key in ("ai_correction", "chapters", "glossary"):
        value = settings.get(key)
        if value is not None and not isinstance(value, bool):
            fail(f"settings.{key} 不是布尔值")

    stats = data.get("stats") or {}
    if any(
        not _optional_int(value) or (isinstance(value, int) and value < 0)
        for value in stats.values()
    ):
        fail("stats 包含负数或非客户端整数")

    for paragraph in data.get("paragraphs") or []:
        if not isinstance(paragraph.get("text"), str):
            fail("段落 text 不是字符串")
        if not isinstance(paragraph.get("speaker_id"), str):
            fail("段落 speaker_id 不是字符串")
        if not _is_time(paragraph.get("start")):
            fail(f"段落 start 超出 0–{MAX_TIME_SECONDS} 秒范围")
        if paragraph.get("end") is not None and not _is_time(paragraph.get("end")):
            fail(f"段落 end 超出 0–{MAX_TIME_SECONDS} 秒范围")
        if paragraph.get("end") is not None and float(paragraph.get("end")) < float(
            paragraph.get("start")
        ):
            fail("段落 end 早于 start")
        for key in ("original_text", "formatted_text", "raw_text"):
            if not _optional_str(paragraph.get(key)):
                fail(f"段落 {key} 不是字符串")
        indices = paragraph.get("segment_indices")
        if indices is not None and (
            not isinstance(indices, list)
            or any(not _optional_int(index) or index < 0 for index in indices)
        ):
            fail("段落 segment_indices 不是非负客户端整数数组")
        if "formatted_text" not in paragraph and isinstance(paragraph.get("original_text"), str):
            # Older jobs only have original_text, which is the post-format
            # baseline, not the raw ASR transcript.  Label it accurately.
            paragraph["formatted_text"] = paragraph["original_text"]
        paragraph.setdefault("manual_edited", False)

    for speaker in data.get("speakers") or []:
        if not isinstance(speaker.get("id"), str) or not isinstance(speaker.get("name"), str):
            fail("说话人条目缺少 id 或 name")
        if not _optional_str(speaker.get("color")):
            fail("说话人 color 不是字符串")
    for chapter in data.get("chapters") or []:
        if not isinstance(chapter.get("title"), str):
            fail("章节条目缺少 title")
        start = chapter.get("start")
        if _is_number(start):
            if not _is_time(start):
                fail(f"章节 start 超出 0–{MAX_TIME_SECONDS} 秒范围")
            chapter["start"] = float(start)
        elif isinstance(start, str):
            parsed = parse_ts(start)
            if parsed is None or not _is_time(parsed):
                fail(f"章节 start 超出 0–{MAX_TIME_SECONDS} 秒范围")
            chapter["start"] = parsed
        else:
            fail("章节 start 不是数字或时间字符串")

    for key in ("segments", "raw_segments"):
        for segment in data.get(key) or []:
            if not isinstance(segment.get("text"), str):
                fail(f"{key} 中的 text 不是字符串")
            if not _is_time(segment.get("start")) or not _is_time(segment.get("end")):
                fail(f"{key} 中的时间戳超出 0–{MAX_TIME_SECONDS} 秒范围")
            if float(segment.get("end")) < float(segment.get("start")):
                fail(f"{key} 中的 end 早于 start")
            if not _optional_str(segment.get("speaker_id")):
                fail(f"{key} 中的 speaker_id 不是字符串")

    correction = data.get("correction")
    if isinstance(correction, dict):
        enabled = correction.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            fail("correction.enabled 不是布尔值")
        for key in ("status", "model", "error"):
            if not _optional_str(correction.get(key)):
                fail(f"correction.{key} 不是字符串")
        for key in ("applied", "rejected", "batches", "skipped_manual"):
            value = correction.get(key)
            if value is not None and (not _optional_int(value) or value < 0):
                fail(f"correction.{key} 不是非负客户端整数")
        errors = correction.get("errors")
        if errors is not None and (
            not isinstance(errors, list)
            or any(not isinstance(item, str) for item in errors)
        ):
            fail("correction.errors 不是字符串数组")
    return data


def load_job(job_id: str) -> dict[str, Any] | None:
    try:
        path = job_file(job_id)
    except ValueError as exc:
        raise JobDataError(job_id, str(exc)) from exc
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise JobDataError(job_id, str(exc)) from exc
    try:
        return _validate_job_data(job_id, data)
    except JobDataError:
        raise
    except (OverflowError, TypeError, ValueError) as exc:
        raise JobDataError(job_id, f"字段校验失败：{exc}") from exc


def load_job_safe(job_id: str) -> tuple[dict[str, Any] | None, JobDataError | None]:
    try:
        return load_job(job_id), None
    except JobDataError as exc:
        return None, exc


def save_job(job: dict[str, Any]) -> None:
    path = job_file(job["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".job-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(job, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


@contextmanager
def job_lock(job_id: str) -> Iterator[None]:
    with _locks_guard:
        lock = _job_locks.setdefault(job_id, threading.RLock())
    with lock:
        yield


def mark_job_deleted(job_id: str) -> None:
    with _locks_guard:
        _deleted_jobs.add(job_id)


def clear_job_deleted(job_id: str) -> None:
    """Test helper; production deletions intentionally stay tombstoned."""
    with _locks_guard:
        _deleted_jobs.discard(job_id)


def is_job_deleted(job_id: str) -> bool:
    with _locks_guard:
        return job_id in _deleted_jobs


def delete_job_storage(job_id: str) -> bool:
    """Tombstone a job and remove its files under the per-job lock.

    The tombstone prevents an in-flight worker from recreating the directory
    with save_job after the delete response has returned.
    """
    import shutil

    with job_lock(job_id):
        mark_job_deleted(job_id)
        folder = job_dir(job_id)
        existed = folder.exists()
        shutil.rmtree(folder, ignore_errors=True)
        return existed


def mutate_job(job_id: str, mutator: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    """Serialize a read-modify-write cycle for one job.

    The mutator receives the latest on-disk job.  A JSON atomic replace is
    deliberately performed only after the mutator finishes, while the job
    lock is still held.
    """
    with job_lock(job_id):
        if is_job_deleted(job_id):
            raise JobDeletedError(f"任务 {job_id} 已删除")
        job = load_job(job_id)
        if job is None:
            raise JobNotFoundError(f"任务 {job_id} 不存在")
        mutator(job)
        save_job(job)
        return job


def format_ts(seconds: float | int | None) -> str:
    if seconds is None:
        return "00:00"
    total = max(0, int(float(seconds)))
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


def parse_ts(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return max(0.0, float(text))
    parts = text.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        return max(0.0, nums[0] * 60 + nums[1])
    if len(nums) == 3:
        return max(0.0, nums[0] * 3600 + nums[1] * 60 + nums[2])
    return None


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "未知时长"
    total = max(0, int(round(float(seconds))))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分 {secs} 秒"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def sentence_count(text: str) -> int:
    return max(1, len(re.findall(r"[.!?。！？]+", text)))


def clean_filename(name: str, fallback: str = "transcript") -> str:
    name = (name or "").strip()
    name = re.sub(r"[\\/:*?\"<>|\n\r\t]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120] or fallback


def slugify(name: str, fallback: str = "job") -> str:
    name = clean_filename(name, fallback)
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", name).strip("._") or fallback
