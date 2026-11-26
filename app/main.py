from __future__ import annotations

import copy
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

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import security
from .chapters import ChapterError, auto_chapters_llm, make_basic_chapters
from .config import (
    ALLOWED_EXTENSIONS,
    DEFAULT_AI_CORRECTION,
    DEFAULT_CHAPTERS,
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_GLOSSARY,
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    DEFAULT_WHISPER_MODEL,
    JOBS_DIR,
    MAX_UPLOAD_BYTES,
    WHISPER_MODELS,
)
from .download import DownloadError, normalize_youtube_url
from .exporters import write_exports
from .job_setup import build_job as build_job_data
from .job_setup import validate_job_options
from .pipeline import run_pipeline
from .secrets import pop_job_secrets, set_job_secret
from .subtitles import sync_subtitle_segments
from .utils import (
    JobDataError,
    JobDeletedError,
    JobNotFoundError,
    delete_job_storage,
    job_dir,
    job_lock,
    load_job,
    load_job_safe,
    is_job_deleted,
    mutate_job,
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
    security.write_session_token()
    for folder in JOBS_DIR.iterdir():
        if not folder.is_dir():
            continue
        job, error = load_job_safe(folder.name)
        if error:
            print(f"[transcriptor] skipping corrupt job {folder.name}: {error}")
            continue
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


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    if not security.host_allowed(request.headers.get("host")):
        return JSONResponse({"detail": "不信任的 Host"}, status_code=400)

    origin = request.headers.get("origin")
    if origin is not None and not security.origin_allowed(
        origin, request.headers.get("host")
    ):
        return JSONResponse({"detail": "不信任的 Origin"}, status_code=403)

    path = request.url.path
    if path.startswith("/api/") and path not in {"/api/health", "/api/session"}:
        supplied = request.headers.get(security.SESSION_HEADER) or request.cookies.get(
            security.SESSION_COOKIE
        )
        if not security.token_valid(supplied):
            return JSONResponse({"detail": "未认证的本地请求"}, status_code=401)

    if (
        request.method == "POST"
        and path == "/api/jobs"
        and request.headers.get("content-length")
    ):
        try:
            length = int(request.headers["content-length"])
        except ValueError:
            return JSONResponse({"detail": "无效的 Content-Length"}, status_code=400)
        if length > MAX_UPLOAD_BYTES + 1024 * 1024:
            return JSONResponse({"detail": "文件超过 4 GB 限制。"}, status_code=413)

    response = await call_next(request)
    return response


@app.post("/api/session")
async def create_session(request: Request) -> JSONResponse:
    supplied = request.headers.get(security.SESSION_HEADER) or ""
    if not supplied:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            supplied = str(payload.get("token") or "")
    if not security.token_valid(supplied):
        raise HTTPException(status_code=401, detail="会话凭据无效")

    response = JSONResponse({"ok": True})
    response.set_cookie(
        security.SESSION_COOKIE,
        security.TOKEN,
        httponly=True,
        samesite="strict",
        secure=False,
        path="/",
    )
    return response


@app.delete("/api/session")
def delete_session() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(security.SESSION_COOKIE, path="/")
    return response


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
    api_key: str = ""


class URLJobRequest(BaseModel):
    url: str
    title: str = ""
    speakers: str = "说话人 1, 说话人 2"
    num_speakers: int = 2
    language: str = "auto"
    model: str = DEFAULT_WHISPER_MODEL
    initial_prompt: str = ""
    ai_correction: bool = DEFAULT_AI_CORRECTION
    chapters: bool = DEFAULT_CHAPTERS
    glossary: bool = DEFAULT_GLOSSARY
    deepseek_base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    deepseek_model: str = DEFAULT_DEEPSEEK_MODEL
    deepseek_api_key: str = ""


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "id", "title", "created_at", "started_at", "finished_at",
        "status", "stage", "progress", "error", "audio", "settings",
        "speakers", "paragraphs", "chapters", "stats", "exports",
        "export_error", "speaker_names", "correction", "correction_warning",
    ]
    return {key: job.get(key) for key in keys}


def _validate_job_options(num_speakers: int, model: str) -> None:
    try:
        validate_job_options(num_speakers, model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _build_job(
    job_id: str,
    title: str,
    speakers: str,
    num_speakers: int,
    language: str,
    model: str,
    initial_prompt: str,
    ai_correction: bool,
    chapters: bool,
    glossary: bool,
    deepseek_base_url: str,
    deepseek_model: str,
    audio: dict[str, Any],
    title_from_url: bool = False,
) -> dict[str, Any]:
    return build_job_data(
        job_id=job_id,
        title=title,
        speakers=speakers,
        num_speakers=num_speakers,
        language=language,
        model=model,
        initial_prompt=initial_prompt,
        ai_correction=ai_correction,
        chapters=chapters,
        glossary=glossary,
        deepseek_base_url=deepseek_base_url,
        deepseek_model=deepseek_model,
        audio=audio,
        title_from_url=title_from_url,
    )


def _write_exports_in_place(job: dict[str, Any]) -> None:
    try:
        job["exports"] = write_exports(job, job_dir(job["id"]))
        job.pop("export_error", None)
    except Exception as exc:
        job["export_error"] = str(exc)


def _require_job(job_id: str) -> dict[str, Any]:
    try:
        job = load_job(job_id)
    except JobDataError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"任务文件损坏，未修改该文件：{exc.detail}",
        ) from exc
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


def _mutate_or_http(job_id: str, mutator) -> dict[str, Any]:
    try:
        return mutate_job(job_id, mutator)
    except JobDeletedError as exc:
        raise HTTPException(status_code=404, detail="任务已删除") from exc
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except JobDataError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"任务文件损坏，未修改该文件：{exc.detail}",
        ) from exc


def _require_done(job: dict[str, Any]) -> None:
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="任务还未完成。")


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
        "glossary_default": DEFAULT_GLOSSARY,
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
    glossary: bool = Form(DEFAULT_GLOSSARY),
    deepseek_base_url: str = Form(DEFAULT_DEEPSEEK_BASE_URL),
    deepseek_model: str = Form(DEFAULT_DEEPSEEK_MODEL),
    deepseek_api_key_header: str | None = Header(
        default=None, alias="X-Transcriptor-DeepSeek-Key"
    ),
) -> dict[str, str]:
    _validate_job_options(num_speakers, model)

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

    job = _build_job(
        job_id=job_id,
        title=title or Path(original).stem,
        speakers=speakers,
        num_speakers=num_speakers,
        language=language,
        model=model,
        initial_prompt=initial_prompt,
        ai_correction=ai_correction,
        chapters=chapters,
        glossary=glossary,
        deepseek_base_url=deepseek_base_url,
        deepseek_model=deepseek_model,
        audio={
            "filename": original,
            "source_filename": stored_name,
            "duration": None,
            "size": size,
        },
    )
    api_key = (deepseek_api_key_header or "").strip()
    if api_key:
        set_job_secret(job_id, "deepseek_api_key", api_key)
    save_job(job)
    executor.submit(run_pipeline, job_id)
    return {"id": job_id}


@app.post("/api/jobs/url")
def create_job_from_url(payload: URLJobRequest) -> dict[str, str]:
    try:
        url = normalize_youtube_url(payload.url)
    except DownloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _validate_job_options(payload.num_speakers, payload.model)

    job_id = uuid.uuid4().hex[:10]
    folder = job_dir(job_id)
    folder.mkdir(parents=True, exist_ok=False)

    job = _build_job(
        job_id=job_id,
        title=payload.title or "YouTube 视频",
        speakers=payload.speakers,
        num_speakers=payload.num_speakers,
        language=payload.language,
        model=payload.model,
        initial_prompt=payload.initial_prompt,
        ai_correction=payload.ai_correction,
        chapters=payload.chapters,
        glossary=payload.glossary,
        deepseek_base_url=payload.deepseek_base_url,
        deepseek_model=payload.deepseek_model,
        audio={
            "filename": url,
            "source_filename": "",
            "source_url": url,
            "duration": None,
            "size": 0,
        },
        title_from_url=not payload.title.strip(),
    )
    if payload.deepseek_api_key.strip():
        set_job_secret(job_id, "deepseek_api_key", payload.deepseek_api_key.strip())
    save_job(job)
    executor.submit(run_pipeline, job_id)
    return {"id": job_id}


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    healthy: list[dict[str, Any]] = []
    corrupt: list[dict[str, Any]] = []
    for folder in JOBS_DIR.iterdir():
        if not folder.is_dir():
            continue
        job, error = load_job_safe(folder.name)
        if error:
            corrupt.append(
                {
                    "id": folder.name,
                    "title": "损坏的任务",
                    "status": "error",
                    "progress": 0,
                    "stage": "任务文件损坏",
                    "created_at": None,
                    "duration": None,
                    "stats": {},
                    "error": error.detail,
                    "corrupt": True,
                }
            )
            continue
        if not job:
            continue
        healthy.append(
            {
                "id": job.get("id"),
                "title": job.get("title"),
                "status": job.get("status"),
                "progress": job.get("progress"),
                "stage": job.get("stage"),
                "created_at": job.get("created_at"),
                "duration": (job.get("audio") or {}).get("duration"),
                "stats": job.get("stats") or {},
                "error": job.get("error"),
                "corrupt": False,
            }
        )
    healthy.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    corrupt.sort(key=lambda item: item.get("id") or "")
    return (healthy + corrupt)[:60]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    return _public_job(_require_job(job_id))


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict[str, bool]:
    try:
        folder = job_dir(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    if not folder.exists():
        raise HTTPException(status_code=404, detail="任务不存在")
    pop_job_secrets(job_id)
    delete_job_storage(job_id)
    return {"deleted": True}


@app.put("/api/jobs/{job_id}/speakers")
def update_speakers(job_id: str, payload: SpeakersUpdate) -> dict[str, Any]:
    def apply(job: dict[str, Any]) -> None:
        _require_done(job)
        existing = {s["id"]: s for s in job.get("speakers", [])}
        if not existing:
            raise HTTPException(status_code=400, detail="该任务还没有说话人信息。")
        for item in payload.speakers:
            if item.id not in existing:
                raise HTTPException(status_code=400, detail=f"未知说话人：{item.id}")
            existing[item.id]["name"] = item.name.strip()[:40] or existing[item.id]["name"]
        job["speakers"] = list(existing.values())
        _write_exports_in_place(job)

    return _public_job(_mutate_or_http(job_id, apply))


@app.put("/api/jobs/{job_id}/paragraphs")
def update_paragraphs(job_id: str, payload: ParagraphsUpdate) -> dict[str, Any]:
    def apply(job: dict[str, Any]) -> None:
        _require_done(job)
        paragraphs = job.get("paragraphs", [])
        if len(payload.texts) != len(paragraphs):
            raise HTTPException(status_code=400, detail="段落数量不一致，请刷新后重试。")
        speaker_ids = payload.speaker_ids
        valid_speakers = {s["id"] for s in job.get("speakers", [])}
        if speaker_ids is not None and len(speaker_ids) != len(paragraphs):
            raise HTTPException(status_code=400, detail="说话人数量不一致，请刷新后重试。")
        changed_ids: list[str] = []
        for index, (paragraph, text) in enumerate(zip(paragraphs, payload.texts)):
            new_text = text.strip()
            new_speaker = paragraph.get("speaker_id")
            if speaker_ids and speaker_ids[index] in valid_speakers:
                new_speaker = speaker_ids[index]
            text_changed = new_text != paragraph.get("text")
            speaker_changed = new_speaker != paragraph.get("speaker_id")
            if text_changed or speaker_changed:
                changed_ids.append(str(paragraph.get("id") or ""))
            if text_changed:
                paragraph["manual_edited"] = True
                paragraph["correction_status"] = "edited"
            paragraph["text"] = new_text
            paragraph["speaker_id"] = new_speaker
        if changed_ids:
            sync_subtitle_segments(job, [item for item in changed_ids if item])
        job["paragraphs"] = paragraphs
        _write_exports_in_place(job)

    return _public_job(_mutate_or_http(job_id, apply))


@app.put("/api/jobs/{job_id}/chapters")
def update_chapters(job_id: str, payload: ChaptersUpdate) -> dict[str, Any]:
    def apply(job: dict[str, Any]) -> None:
        _require_done(job)
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
        _write_exports_in_place(job)

    return _public_job(_mutate_or_http(job_id, apply))


@app.post("/api/jobs/{job_id}/chapters/basic")
def basic_chapters(job_id: str) -> dict[str, Any]:
    def apply(job: dict[str, Any]) -> None:
        _require_done(job)
        duration = float((job.get("audio") or {}).get("duration") or 0)
        job["chapters"] = make_basic_chapters(job.get("paragraphs", []), duration)
        _write_exports_in_place(job)

    return _public_job(_mutate_or_http(job_id, apply))


@app.post("/api/jobs/{job_id}/chapters/auto")
async def auto_chapters(job_id: str, payload: AutoChaptersRequest) -> dict[str, Any]:
    with job_lock(job_id):
        job = _require_job(job_id)
        _require_done(job)
        paragraphs = copy.deepcopy(job.get("paragraphs", []))
        speakers = {s["id"]: s.get("name", "") for s in job.get("speakers", [])}
    if is_job_deleted(job_id):
        raise HTTPException(status_code=404, detail="任务已删除")

    try:
        chapters = await run_in_threadpool(
            auto_chapters_llm,
            paragraphs,
            speakers,
            payload.endpoint or _detect_llm_base_url(),
            payload.model or DEFAULT_LLM_MODEL,
            payload.title_language,
            api_key=payload.api_key,
        )
    except ChapterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def apply(job: dict[str, Any]) -> None:
        _require_done(job)
        job["chapters"] = chapters
        _write_exports_in_place(job)

    return _public_job(_mutate_or_http(job_id, apply))


@app.post("/api/jobs/{job_id}/correction/undo")
def undo_correction(job_id: str) -> dict[str, Any]:
    def apply(job: dict[str, Any]) -> None:
        _require_done(job)
        changed_ids: list[str] = []
        skipped_manual = 0
        for paragraph in job.get("paragraphs", []):
            if paragraph.get("manual_edited"):
                skipped_manual += 1
            elif paragraph.get("correction_status") == "corrected":
                ai_text = paragraph.get("ai_text")
                if isinstance(ai_text, str) and paragraph.get("text") != ai_text:
                    skipped_manual += 1
                else:
                    original = paragraph.get("formatted_text") or paragraph.get("original_text")
                    if isinstance(original, str) and paragraph.get("text") != original:
                        paragraph["text"] = original
                        if paragraph.get("id"):
                            changed_ids.append(str(paragraph["id"]))
                    paragraph["correction_status"] = "original"
            paragraph.pop("correction_edits", None)
        if changed_ids:
            sync_subtitle_segments(job, changed_ids)
        correction = job.setdefault("correction", {})
        correction["status"] = "undone"
        correction["applied"] = 0
        correction["error"] = None
        correction["skipped_manual"] = skipped_manual
        job.pop("correction_warning", None)
        _write_exports_in_place(job)

    return _public_job(_mutate_or_http(job_id, apply))


def _prepare_export(job_id: str, extension: str) -> tuple[dict[str, Any], Path]:
    with job_lock(job_id):
        job = _require_job(job_id)
        _require_done(job)
        try:
            write_exports(job, job_dir(job_id))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"导出失败：{exc}") from exc
    path = job_dir(job_id) / f"transcript.{extension}"
    return job, path


@app.get("/api/jobs/{job_id}/export.md")
def export_markdown(job_id: str) -> FileResponse:
    job, path = _prepare_export(job_id, "md")
    filename = f"{slugify(job.get('title') or '访谈逐字稿')}.md"
    return FileResponse(str(path), media_type="text/markdown", filename=filename)


@app.get("/api/jobs/{job_id}/export.docx")
def export_docx(job_id: str) -> FileResponse:
    job, path = _prepare_export(job_id, "docx")
    filename = f"{slugify(job.get('title') or '访谈逐字稿')}.docx"
    return FileResponse(
        str(path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )


@app.get("/api/jobs/{job_id}/export.srt")
def export_srt(job_id: str) -> FileResponse:
    job, path = _prepare_export(job_id, "srt")
    filename = f"{slugify(job.get('title') or '访谈逐字稿')}.srt"
    return FileResponse(str(path), media_type="application/x-subrip", filename=filename)


@app.get("/api/jobs/{job_id}/export.vtt")
def export_vtt(job_id: str) -> FileResponse:
    job, path = _prepare_export(job_id, "vtt")
    filename = f"{slugify(job.get('title') or '访谈逐字稿')}.vtt"
    return FileResponse(str(path), media_type="text/vtt", filename=filename)


@app.get("/")
def index():
    from fastapi.responses import FileResponse

    return FileResponse(
        Path(__file__).resolve().parent / "templates" / "index.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )
