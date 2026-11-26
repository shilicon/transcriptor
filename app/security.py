from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path
from urllib.parse import urlparse

from . import config

SESSION_COOKIE = "transcriptor_session"
SESSION_HEADER = "X-Transcriptor-Token"
TOKEN_FILENAME = ".session-token"
TOKEN = os.environ.get("TRANSCRIPTOR_SESSION_TOKEN") or secrets.token_urlsafe(32)
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


def token_file() -> Path:
    return config.DATA_DIR / TOKEN_FILENAME


def write_session_token() -> Path:
    path = token_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(TOKEN)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def token_valid(value: str | None) -> bool:
    if not value:
        return False
    return hmac.compare_digest(str(value), TOKEN)


def _host_parts(host_header: str | None) -> tuple[str, str] | None:
    if not host_header:
        return None
    host = host_header.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        if end < 0:
            return None
        name = host[1:end]
        rest = host[end + 1:]
        if not rest:
            return name, "80"
        if not rest.startswith(":"):
            return None
        return name, rest[1:]
    if ":" in host:
        name, port = host.rsplit(":", 1)
        return name, port
    return host, "80"


def host_allowed(host_header: str | None) -> bool:
    parts = _host_parts(host_header)
    return bool(parts and parts[0] in ALLOWED_HOSTS)


def origin_allowed(origin: str | None, host_header: str | None = None) -> bool:
    if not origin:
        return True
    parsed = urlparse(origin)
    if parsed.scheme != "http":
        return False
    if (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        return False
    if not host_header:
        return False
    host_parts = _host_parts(host_header)
    if not host_parts or host_parts[0] != (parsed.hostname or "").lower():
        return False
    try:
        origin_port = str(parsed.port or 80)
    except ValueError:
        return False
    return origin_port == host_parts[1]
