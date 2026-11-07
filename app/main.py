from __future__ import annotations

import os
import shutil
import threading
import time
import uuid

import httpx
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .chapters import ChapterError, auto_chapters_llm, make_basic_chapters
from .config import (
    ALLOWED_EXTENSIONS,
    DEFAULT_AI_CORRECTION,
    DEFAULT_CHAPTERS,
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    DEFAULT_WHISPER_MODEL,
    JOBS_DIR,
    MAX_UPLOAD_BYTES,
    WHISPER_MODELS,
)
from .exporters import render_docx, render_markdown
from .formatting import build_speakers, parse_speaker_names
from .pipeline import run_pipeline
from .secrets import set_job_secret
from .utils import (
    clean_filename,
    format_duration,
    job_dir,
    load_job,
    now_iso,
    parse_ts,
    save_job,
    slugify,
)

executor = ThreadPoolExecutor(max_workers=1)


def _watch_parent(pid: int) -> None:
    """Exit when the native macOS app that launched us disappears."""
    while True:
        time.sleep(2)
        try:
            os.kill(pid, 0)
        except OSError:
            os._exit(0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    parent_pid = os.environ.get("TRANSCRIPTOR_PARENT_PID")
    if parent_pid:
        try:
            threading.Thread(
                target=_watch_parent,
                args=(int(parent_pid),),
                daemon=True,
            ).start()
        except ValueError:
            pass

    # Mark interrupted jobs instead of leaving them "running" forever.
    for folder in JOBS_DIR.iterdir():
        if not folder.is_dir():
            continue
        job = load_job(folder.name)
        if job and job.get("status") in {"queued", "running"}:
            job["status"] = "error"
            job["stage"] = "任务中断"
            job["error"] = "应用重启，任务已中断。请重新上传。"
            job["finished_at"] = now_iso()
            save_job(job)
    yield
    executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Transcriptor", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static")


class SpeakerItem(BaseModel):
    id: str
    name: str


class SpeakersUpdate(BaseModel):
    speakers: list[SpeakerItem]


class ParagraphsUpdate(BaseModel):
    texts: list[str]
    speaker_ids: list[str] | None = None


class ChapterItem(BaseModel):
    start: str | float
    title: str


class ChaptersUpdate(BaseModel):
    chapters: list[ChapterItem]


class AutoChaptersRequest(BaseModel):
    endpoint: str | None = None
    model: str | None = None
    title_language: str = Field(default="zh", pattern="^(zh|en)$")


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "id", "title", "created_at", "started_at", "finished_at",
        "status", "stage", "progress", "error", "audio", "settings",
        "speakers", "paragraphs", "chapters", "stats", "exports",
        "export_error", "speaker_names", "correction", "correction_warning",
    ]
    return {key: job.get(key) for key in keys}


def _refresh_exports(job: dict[str, Any]) -> None:
    folder = job_dir(job["id"])
    md_path = folder / "transcript.md"
    docx_path = folder / "transcript.docx"
    try:
        md_path.write_text(render_markdown(job), encoding="utf-8")
        render_docx(job, docx_path)
        job["exports"] = {"md": md_path.name, "docx": docx_path.name}
        job.pop("export_error", None)
    except Exception as exc:
        job["export_error"] = str(exc)
    save_job(job)


def _require_job(job_id: str) -> dict[str, Any]:
    job = load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


def _safe_upload_name(filename: str) -> tuple[str, str]:
    original = Path(filename or "audio").name
    suffix = Path(original).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型：{suffix or '未知'}。请上传音频或视频文件。",
        )
    return original, f"input{suffix}"


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _detect_llm_base_url() -> str:
    candidates: list[str] = []
    if DEFAULT_LLM_BASE_URL:
        candidates.append(DEFAULT_LLM_BASE_URL.rstrip("/"))
    for port in (1234, 1235, 11434):
        url = f"http://127.0.0.1:{port}/v1"
        if url not in candidates:
            candidates.append(url)
    for url in candidates:
        try:
            response = httpx.get(f"{url}/models", timeout=0.45)
            if response.status_code == 200:
                payload = response.json()
                if payload.get("data"):
                    return url
        except Exception:
            continue
    return DEFAULT_LLM_BASE_URL


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {
        "llm_base_url": _detect_llm_base_url(),
        "llm_model": DEFAULT_LLM_MODEL,
        "whisper_models": [
            {"key": key, "available": path.exists()}
            for key, path in WHISPER_MODELS.items()
        ],
        "default_model": DEFAULT_WHISPER_MODEL,
        "ai_correction_default": DEFAULT_AI_CORRECTION,
        "chapters_default": DEFAULT_CHAPTERS,
        "deepseek_base_url": DEFAULT_DEEPSEEK_BASE_URL,
        "deepseek_model": DEFAULT_DEEPSEEK_MODEL,
        "deepseek_configured": bool(
            os.environ.get("TRANSCRIPTOR_DEEPSEEK_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
        ),
    }


@app.post("/api/jobs")
def create_job(
    file: UploadFile = File(...),
    title: str = Form(""),
    speakers: str = Form("说话人 1, 说话人 2"),
    num_speakers: int = Form(2),
    language: str = Form("auto"),
    model: str = Form(DEFAULT_WHISPER_MODEL),
    initial_prompt: str = Form(""),
    ai_correction: bool = Form(DEFAULT_AI_CORRECTION),
    chapters: bool = Form(DEFAULT_CHAPTERS),
    deepseek_base_url: str = Form(DEFAULT_DEEPSEEK_BASE_URL),
    deepseek_model: str = Form(DEFAULT_DEEPSEEK_MODEL),
    deepseek_api_key: str = Form(""),
) -> dict[str, str]:
    if num_speakers < 1 or num_speakers > 4:
        raise HTTPException(status_code=400, detail="说话人数量需在 1–4 之间。")
    if model not in WHISPER_MODELS:
        raise HTTPException(status_code=400, detail="未知的语音模型。")

    original, stored_name = _safe_upload_name(file.filename or "audio")
    job_id = uuid.uuid4().hex[:10]
    folder = job_dir(job_id)
    folder.mkdir(parents=True, exist_ok=False)

    destination = folder / stored_name
    size = 0
    try:
        with destination.open("wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="文件超过 4 GB 限制。")
                out.write(chunk)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        file.file.close()

    if size == 0:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(status_code=400, detail="上传的文件为空。")

    names = parse_speaker_names(speakers, num_speakers)
    job: dict[str, Any] = {
        "id": job_id,
        "title": clean_filename(title or Path(original).stem, "访谈逐字稿"),
        "created_at": now_iso(),
        "status": "queued",
        "stage": "排队中",
        "progress": 0,
        "error": None,
        "audio": {
            "filename": original,
            "source_filename": stored_name,
            "duration": None,
            "size": size,
        },
        "settings": {
            "language": language,
            "num_speakers": num_speakers,
            "model": model,
            "initial_prompt": initial_prompt.strip(),
            "ai_correction": bool(ai_correction),
            "chapters": bool(chapters),
            "deepseek_base_url": deepseek_base_url.strip() or DEFAULT_DEEPSEEK_BASE_URL,
            "deepseek_model": deepseek_model.strip() or DEFAULT_DEEPSEEK_MODEL,
        },
        "speaker_names": names,
        "speakers": build_speakers(names),
        "paragraphs": [],
        "chapters": [],
        "stats": {},
    }
    if deepseek_api_key.strip():
        set_job_secret(job_id, "deepseek_api_key", deepseek_api_key.strip())
    save_job(job)
    executor.submit(run_pipeline, job_id)
    return {"id": job_id}


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for folder in JOBS_DIR.iterdir():
        if not folder.is_dir():
            continue
        job = load_job(folder.name)
        if not job:
            continue
        items.append(
            {
                "id": job.get("id"),
                "title": job.get("title"),
                "status": job.get("status"),
                "progress": job.get("progress"),
                "stage": job.get("stage"),
                "created_at": job.get("created_at"),
                "duration": (job.get("audio") or {}).get("duration"),
                "stats": job.get("stats") or {},
            }
        )
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return items[:60]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    return _public_job(_require_job(job_id))


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict[str, bool]:
    job = _require_job(job_id)
    shutil.rmtree(job_dir(job["id"]), ignore_errors=True)
    return {"deleted": True}


@app.put("/api/jobs/{job_id}/speakers")
def update_speakers(job_id: str, payload: SpeakersUpdate) -> dict[str, Any]:
    job = _require_job(job_id)
    existing = {s["id"]: s for s in job.get("speakers", [])}
    if not existing:
        raise HTTPException(status_code=400, detail="该任务还没有说话人信息。")
    for item in payload.speakers:
        if item.id not in existing:
            raise HTTPException(status_code=400, detail=f"未知说话人：{item.id}")
        existing[item.id]["name"] = item.name.strip()[:40] or existing[item.id]["name"]
    job["speakers"] = list(existing.values())
    save_job(job)
    _refresh_exports(job)
    return _public_job(job)


@app.put("/api/jobs/{job_id}/paragraphs")
def update_paragraphs(job_id: str, payload: ParagraphsUpdate) -> dict[str, Any]:
    job = _require_job(job_id)
    paragraphs = job.get("paragraphs", [])
    if len(payload.texts) != len(paragraphs):
        raise HTTPException(status_code=400, detail="段落数量不一致，请刷新后重试。")
    speaker_ids = payload.speaker_ids
    valid_speakers = {s["id"] for s in job.get("speakers", [])}
    if speaker_ids is not None and len(speaker_ids) != len(paragraphs):
        raise HTTPException(status_code=400, detail="说话人数量不一致，请刷新后重试。")
    for index, (paragraph, text) in enumerate(zip(paragraphs, payload.texts)):
        paragraph["text"] = text.strip()
        if speaker_ids and speaker_ids[index] in valid_speakers:
            paragraph["speaker_id"] = speaker_ids[index]
    job["paragraphs"] = paragraphs
    save_job(job)
    _refresh_exports(job)
    return _public_job(job)


@app.put("/api/jobs/{job_id}/chapters")
def update_chapters(job_id: str, payload: ChaptersUpdate) -> dict[str, Any]:
    job = _require_job(job_id)
    chapters: list[dict[str, Any]] = []
    for item in payload.chapters[:30]:
        start = parse_ts(item.start)
        title = item.title.strip()[:60]
        if start is None or not title:
            continue
        chapters.append({"start": start, "title": title})
    chapters.sort(key=lambda chapter: chapter["start"])
    if chapters:
        chapters[0]["start"] = 0.0
    job["chapters"] = chapters
    save_job(job)
    _refresh_exports(job)
    return _public_job(job)


@app.post("/api/jobs/{job_id}/chapters/basic")
def basic_chapters(job_id: str) -> dict[str, Any]:
    job = _require_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="任务还未完成。")
    duration = float((job.get("audio") or {}).get("duration") or 0)
    job["chapters"] = make_basic_chapters(job.get("paragraphs", []), duration)
    save_job(job)
    _refresh_exports(job)
    return _public_job(job)


@app.post("/api/jobs/{job_id}/chapters/auto")
async def auto_chapters(job_id: str, payload: AutoChaptersRequest) -> dict[str, Any]:
    job = _require_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="任务还未完成。")
    speakers = {s["id"]: s.get("name", "") for s in job.get("speakers", [])}
    try:
        chapters = await run_in_threadpool(
            auto_chapters_llm,
            job.get("paragraphs", []),
            speakers,
            payload.endpoint or _detect_llm_base_url(),
            payload.model or DEFAULT_LLM_MODEL,
            payload.title_language,
        )
    except ChapterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job["chapters"] = chapters
    save_job(job)
    await run_in_threadpool(_refresh_exports, job)
    return _public_job(job)


@app.post("/api/jobs/{job_id}/correction/undo")
def undo_correction(job_id: str) -> dict[str, Any]:
    job = _require_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="任务还未完成。")
    changed = 0
    for paragraph in job.get("paragraphs", []):
        original = paragraph.get("original_text")
        if original is not None and paragraph.get("text") != original:
            paragraph["text"] = original
            changed += 1
        paragraph.pop("correction_edits", None)
        paragraph["correction_status"] = "original"
    correction = job.setdefault("correction", {})
    correction["status"] = "undone"
    correction["applied"] = 0
    correction["error"] = None
    job.pop("correction_warning", None)
    save_job(job)
    _refresh_exports(job)
    return _public_job(job)


@app.get("/api/jobs/{job_id}/export.md")
def export_markdown(job_id: str) -> FileResponse:
    job = _require_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="任务还未完成。")
    path = job_dir(job_id) / "transcript.md"
    if not path.exists():
        path.write_text(render_markdown(job), encoding="utf-8")
    filename = f"{slugify(job.get('title') or '访谈逐字稿')}.md"
    return FileResponse(str(path), media_type="text/markdown", filename=filename)


@app.get("/api/jobs/{job_id}/export.docx")
def export_docx(job_id: str) -> FileResponse:
    job = _require_job(job_id)
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="任务还未完成。")
    path = job_dir(job_id) / "transcript.docx"
    if not path.exists():
        render_docx(job, path)
    filename = f"{slugify(job.get('title') or '访谈逐字稿')}.docx"
    return FileResponse(
        str(path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )


@app.get("/")
def index():
    from fastapi.responses import FileResponse

    return FileResponse(
        Path(__file__).resolve().parent / "templates" / "index.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )
