from __future__ import annotations

from typing import Any

# Secrets are kept in memory only.  They are intentionally not written to
# job.json, which may be backed up or shared.
JOB_SECRETS: dict[str, dict[str, Any]] = {}


def set_job_secret(job_id: str, key: str, value: Any) -> None:
    JOB_SECRETS.setdefault(job_id, {})[key] = value


def pop_job_secrets(job_id: str) -> dict[str, Any]:
    return JOB_SECRETS.pop(job_id, {})
