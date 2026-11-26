"""Independent remediation probes; records observations, not regression expectations.

Uses only synthetic jobs, temporary storage and mock HTTP. No model calls.
Run from the repository: .venv/bin/python audit/2026-11-11/reaudit_reproduce.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
BOOT = tempfile.TemporaryDirectory(prefix="transcriptor-reaudit-boot-")
os.environ["TRANSCRIPTOR_DATA_DIR"] = str(Path(BOOT.name) / "data")
os.environ["TRANSCRIPTOR_MODELS_DIR"] = str(Path(BOOT.name) / "models")
os.environ["TRANSCRIPTOR_SESSION_TOKEN"] = "synthetic-reaudit-session-token"
os.environ.pop("TRANSCRIPTOR_PARENT_PID", None)

import httpx
from fastapi.testclient import TestClient
from app import correction, main, pipeline, security, transcribe
from app.utils import clear_job_deleted, is_job_deleted, job_dir, load_job, save_job
from tests.support import isolated_jobs_dir, make_job


def negation_probe():
    observations = []
    for text, original, replacement in [
        ("我不赞成这个方案。", "不赞成", "赞成"),
        ("这个操作不安全。", "不安全", "安全"),
        ("This change is not safe for customers.", "not safe", "safe"),
    ]:
        paragraph = {"id": "p1", "text": text, "original_text": text}
        count, _ = correction._apply_edits(paragraph, [{
            "original": original, "replacement": replacement,
            "type": "homophone", "confidence": 0.99,
        }])
        observations.append({"before": text, "after": paragraph["text"], "accepted": count})
    return observations


def unauthenticated_cookie_probe():
    with isolated_jobs_dir():
        save_job(make_job("cookie-victim"))
        # No token header, no token-file read, no browser Origin.
        with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
            before = client.get("/api/jobs")
            root = client.get("/")
            after = client.get("/api/jobs")
            deleted = client.delete("/api/jobs/cookie-victim")
            return {
                "before_root": before.status_code,
                "root": root.status_code,
                "root_issues_session_cookie": security.SESSION_COOKIE in client.cookies,
                "after_root": after.status_code,
                "delete_status": deleted.status_code,
                "victim_exists_after_delete": job_dir("cookie-victim").exists(),
            }


def corrupt_metadata_probe():
    result = {}
    for field, value in [("created_at", 123), ("title", ["invalid-title"])]:
        with isolated_jobs_dir():
            save_job(make_job("healthy-metadata"))
            broken = make_job("bad-metadata")
            broken[field] = value
            save_job(broken)
            loaded = load_job("bad-metadata")
            with TestClient(main.app, base_url="http://127.0.0.1:8765", raise_server_exceptions=False) as client:
                response = client.get("/api/jobs", headers={security.SESSION_HEADER: security.TOKEN})
                detail = client.get("/api/jobs/bad-metadata", headers={security.SESSION_HEADER: security.TOKEN})
            item = {"loader_accepts": loaded is not None, "list_status": response.status_code,
                    "detail_status": detail.status_code}
            if response.status_code == 200:
                record = next(x for x in response.json() if x["id"] == "bad-metadata")
                item["listed_as_corrupt"] = record["corrupt"]
                item["returned_title_type"] = type(record["title"]).__name__
            result[field] = item
    return result


def retry_after_delete_probe():
    observations = []
    for first_status in (503, 400):
        with isolated_jobs_dir():
            job_id = f"retry-delete-{first_status}"
            save_job(make_job(job_id))
            calls = []
            def handle(request):
                calls.append({"after_delete": is_job_deleted(job_id),
                              "contains_transcript": "Synthetic private transcript" in request.content.decode()})
                if len(calls) == 1:
                    main.delete_job(job_id)
                    return httpx.Response(first_status, request=request, json={"error": "mock error"})
                return httpx.Response(200, request=request, json={
                    "choices": [{"message": {"content": '{"edits":[]}'}}]})
            real_client = httpx.Client
            transport = httpx.MockTransport(handle)
            def mock_client(*args, **kwargs):
                return real_client(*args, transport=transport, **kwargs)
            outcome = "returned"
            with patch.object(correction.httpx, "Client", side_effect=mock_client), patch.object(correction.time, "sleep"):
                try:
                    correction.correct_paragraphs(
                        [{"id": "p1", "start": 0, "text": "Synthetic private transcript"}],
                        {}, "dummy-key", "http://audit.invalid", "mock",
                        should_cancel=lambda: is_job_deleted(job_id))
                except correction.CorrectionCancelled:
                    outcome = "cancelled-after-http"
            observations.append({"first_status": first_status, "requests": calls, "outcome": outcome})
            clear_job_deleted(job_id)
    return observations


def extraction_delete_probe():
    with isolated_jobs_dir():
        job_id = "extract-delete"
        save_job(make_job(job_id, "queued"))
        (job_dir(job_id) / "input.wav").write_bytes(b"synthetic input")
        entered, release = threading.Event(), threading.Event()
        real_binary = transcribe._require_binary
        def gated_binary(name):
            binary = real_binary(name)
            if name == "ffmpeg":
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("barrier timed out")
            return binary
        with redirect_stdout(sys.stderr), patch.object(pipeline, "probe_duration", return_value=10), patch.object(transcribe, "_require_binary", side_effect=gated_binary):
            with ThreadPoolExecutor(max_workers=1) as pool:
                worker = pool.submit(pipeline.run_pipeline, job_id)
                if not entered.wait(10):
                    release.set()
                    raise RuntimeError("extraction did not enter")
                main.delete_job(job_id)
                immediately_exists = job_dir(job_id).exists()
                release.set()
                worker.result(10)
        result = {"exists_immediately_after_delete": immediately_exists,
                  "exists_after_worker_finishes": job_dir(job_id).exists(),
                  "job_json_recreated": (job_dir(job_id) / "job.json").exists(),
                  "remaining_files": sorted(p.name for p in job_dir(job_id).iterdir()) if job_dir(job_id).exists() else []}
        clear_job_deleted(job_id)
        return result


if __name__ == "__main__":
    print(json.dumps({
        "A02_negation": negation_probe(),
        "A06_cookie_bootstrap": unauthenticated_cookie_probe(),
        "A08_metadata": corrupt_metadata_probe(),
        "A04_retry": retry_after_delete_probe(),
        "A04_extraction": extraction_delete_probe(),
    }, ensure_ascii=False, indent=2))
