from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

ProgressFn = Callable[[str, float], None]

YOUTUBE_HOSTS = {
    "youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
}
BARE_URL_RE = re.compile(
    r"^(?:(?:[a-z0-9-]+\.)*youtube(?:-nocookie)?\.com|youtu\.be)/",
    re.IGNORECASE,
)


class DownloadError(RuntimeError):
    pass


def normalize_youtube_url(value: str) -> str:
    url = (value or "").strip()
    if not url:
        raise DownloadError("请输入 YouTube 视频链接。")
    if "://" not in url and BARE_URL_RE.match(url):
        url = "https://" + url

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise DownloadError("链接必须以 http:// 或 https:// 开头。")
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        host not in YOUTUBE_HOSTS
        and not host.endswith(".youtube.com")
        and not host.endswith(".youtube-nocookie.com")
    ):
        raise DownloadError("只支持 YouTube 视频链接。")

    query = parsed.query
    if "list=" in query and "v=" not in query:
        raise DownloadError("请提供单个视频链接，不支持整个播放列表。")
    if host == "youtu.be":
        if not parsed.path.strip("/"):
            raise DownloadError("YouTube 链接缺少视频 ID。")
    else:
        path = parsed.path.rstrip("/")
        is_watch = path == "/watch" and bool(parse_qs(query).get("v"))
        is_path_video = any(
            path.startswith(prefix) and len(path) > len(prefix)
            for prefix in ("/shorts/", "/live/", "/embed/", "/v/", "/clip/")
        )
        if not is_watch and not is_path_video:
            raise DownloadError("请提供单个 YouTube 视频链接，不支持频道或主页链接。")
    return url


def _js_runtimes() -> dict[str, dict[str, str]]:
    """Use any installed JS runtime for YouTube's player challenges."""
    runtimes: dict[str, dict[str, str]] = {}
    for name in ("deno", "node", "bun", "quickjs"):
        path = shutil.which(name)
        if path:
            runtimes[name] = {"path": path}
    return runtimes


def _download_options(folder: Path, progress: ProgressFn | None) -> dict[str, Any]:
    last_update = 0.0
    last_percent = -1.0

    def hook(data: dict[str, Any]) -> None:
        nonlocal last_update, last_percent
        if not progress:
            return
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes") or 0
            pct = 4.0 + 8.0 * min(1.0, downloaded / total) if total else 6.0
            now = time.monotonic()
            if pct - last_percent < 0.8 and now - last_update < 1.0:
                return
            last_percent = pct
            last_update = now
            progress("正在下载 YouTube 音频", pct)
        elif status == "finished":
            progress("音频下载完成，准备转写", 12)

    options: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": str(folder / "input.%(ext)s"),
        "noplaylist": True,
        "playlist_items": "1",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "overwrites": True,
        "writesubtitles": False,
        "writeautomaticsub": False,
        "writeinfojson": False,
        "writethumbnail": False,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 45,
        "progress_hooks": [hook],
    }
    runtimes = _js_runtimes()
    if runtimes:
        options["js_runtimes"] = runtimes

    proxy = (
        os.environ.get("TRANSCRIPTOR_YTDLP_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
    )
    if proxy:
        options["proxy"] = proxy

    cookies = os.environ.get("TRANSCRIPTOR_YTDLP_COOKIES")
    if cookies:
        cookie_path = Path(cookies).expanduser()
        if not cookie_path.exists():
            raise DownloadError(f"找不到 cookies 文件：{cookie_path}")
        options["cookiefile"] = str(cookie_path)
    return options


def _friendly_error(exc: Exception) -> str:
    detail = str(exc).strip()
    lowered = detail.lower()
    if "sign in to confirm" in lowered or "bot" in lowered:
        hint = "YouTube 要求登录验证。请设置 TRANSCRIPTOR_YTDLP_COOKIES 后重试。"
    elif "403" in detail:
        hint = "YouTube 拒绝下载音频（HTTP 403）。可以尝试更新 yt-dlp、配置代理或 cookies。"
    elif "private video" in lowered:
        hint = "这个视频是私享视频，无法下载。"
    elif "video unavailable" in lowered or "not available" in lowered:
        hint = "这个视频不可用，可能已被删除或有地区限制。"
    elif "unsupported url" in lowered:
        hint = "无法识别这个链接，请确认它是 YouTube 视频链接。"
    else:
        hint = "下载 YouTube 音频失败。"
    if len(detail) > 700:
        detail = detail[:700] + "…"
    return f"{hint}\n{detail}" if detail else hint


def download_audio(
    url: str,
    folder: Path,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Download the best audio stream with yt-dlp.

    YouTube subtitles are never requested or written; the returned file is
    only the audio/video container that ffmpeg will later convert to WAV.
    """
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise DownloadError("未安装 yt-dlp，请先运行 ./scripts/setup.sh。") from exc

    folder.mkdir(parents=True, exist_ok=True)
    options = _download_options(folder, progress)
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadError:
        raise
    except Exception as exc:
        raise DownloadError(_friendly_error(exc)) from exc

    if not info:
        raise DownloadError("yt-dlp 没有返回视频信息。")
    if info.get("entries"):
        entries = [item for item in info["entries"] if item]
        if not entries:
            raise DownloadError("播放列表中没有可下载的视频。")
        info = entries[0]

    files = [
        path
        for path in folder.glob("input.*")
        if path.is_file() and path.suffix.lower() not in {".part", ".ytdl", ".json", ".temp"}
    ]
    if not files:
        raise DownloadError("音频下载完成，但没有找到下载文件。")
    path = max(files, key=lambda item: item.stat().st_size)

    title = str(info.get("title") or path.stem).strip()
    duration = info.get("duration")
    try:
        duration_value = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_value = None

    return {
        "path": path,
        "title": title,
        "duration": duration_value,
        "ext": path.suffix.lstrip(".").lower(),
        "size": path.stat().st_size,
    }
