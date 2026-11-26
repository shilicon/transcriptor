from __future__ import annotations

import stat
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app import main, security
from app.utils import save_job
from tests.support import isolated_jobs_dir, make_job


class LocalAPISecurityTests(unittest.TestCase):
    def test_health_is_public(self):
        with isolated_jobs_dir():
            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                response = client.get("/api/health")
                self.assertEqual(response.status_code, 200)

    def test_api_requires_token(self):
        with isolated_jobs_dir():
            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                response = client.get("/api/jobs")
                self.assertEqual(response.status_code, 401)

    def test_native_header_authenticates_without_origin(self):
        with isolated_jobs_dir():
            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                response = client.get(
                    "/api/jobs",
                    headers={security.SESSION_HEADER: security.TOKEN},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), [])

    def test_anonymous_root_does_not_issue_session(self):
        with isolated_jobs_dir():
            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                index = client.get("/")
                self.assertEqual(index.status_code, 200)
                self.assertNotIn(security.SESSION_COOKIE, client.cookies)
                response = client.get("/api/jobs")
                self.assertEqual(response.status_code, 401)

    def test_anonymous_root_cannot_delete_via_cookie(self):
        with isolated_jobs_dir() as (_, jobs):
            save_job(make_job("cookie-victim"))
            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                self.assertEqual(client.get("/").status_code, 200)
                response = client.delete("/api/jobs/cookie-victim")
                self.assertEqual(response.status_code, 401)
                self.assertTrue((jobs / "cookie-victim" / "job.json").exists())

    def test_session_login_with_token_sets_cookie(self):
        with isolated_jobs_dir():
            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                login = client.post("/api/session", json={"token": security.TOKEN})
                self.assertEqual(login.status_code, 200)
                self.assertIn(security.SESSION_COOKIE, client.cookies)
                set_cookie = login.headers.get("set-cookie", "").lower()
                self.assertIn("httponly", set_cookie)
                self.assertIn("samesite=strict", set_cookie)
                response = client.get("/api/jobs")
                self.assertEqual(response.status_code, 200)

    def test_session_login_rejects_wrong_token(self):
        with isolated_jobs_dir():
            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                login = client.post("/api/session", json={"token": "wrong-token"})
                self.assertEqual(login.status_code, 401)
                self.assertNotIn(security.SESSION_COOKIE, client.cookies)
                self.assertEqual(client.get("/api/jobs").status_code, 401)

    def test_session_login_with_header_also_works(self):
        with isolated_jobs_dir():
            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                login = client.post(
                    "/api/session",
                    headers={security.SESSION_HEADER: security.TOKEN},
                )
                self.assertEqual(login.status_code, 200)
                self.assertEqual(client.get("/api/jobs").status_code, 200)

    def test_untrusted_host_is_rejected(self):
        with isolated_jobs_dir():
            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                response = client.get(
                    "/api/jobs",
                    headers={
                        "Host": "untrusted.invalid:8765",
                        security.SESSION_HEADER: security.TOKEN,
                    },
                )
                self.assertEqual(response.status_code, 400)

    def test_untrusted_origin_is_rejected(self):
        with isolated_jobs_dir():
            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                response = client.get(
                    "/api/jobs",
                    headers={
                        "Origin": "https://untrusted.invalid",
                        security.SESSION_HEADER: security.TOKEN,
                    },
                )
                self.assertEqual(response.status_code, 403)
                response = client.post(
                    "/api/jobs/does-not-exist/correction/undo",
                    headers={
                        "Origin": "https://untrusted.invalid",
                        security.SESSION_HEADER: security.TOKEN,
                    },
                )
                self.assertEqual(response.status_code, 403)

    def test_allowed_origin_is_accepted(self):
        with isolated_jobs_dir():
            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                response = client.get(
                    "/api/jobs",
                    headers={
                        "Origin": "http://127.0.0.1:8765",
                        security.SESSION_HEADER: security.TOKEN,
                    },
                )
                self.assertEqual(response.status_code, 200)

    def test_different_local_origin_port_is_rejected(self):
        with isolated_jobs_dir():
            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                response = client.get(
                    "/api/jobs",
                    headers={
                        "Origin": "http://127.0.0.1:9999",
                        security.SESSION_HEADER: security.TOKEN,
                    },
                )
                self.assertEqual(response.status_code, 403)

    def test_token_file_is_private_and_not_exposed(self):
        with isolated_jobs_dir() as (root, _):
            save_job(make_job("security-fixture"))
            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                response = client.get(
                    "/api/config",
                    headers={security.SESSION_HEADER: security.TOKEN},
                )
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(security.TOKEN, response.text)
            token_path = root / security.TOKEN_FILENAME
            self.assertTrue(token_path.exists())
            self.assertEqual(stat.S_IMODE(token_path.stat().st_mode) & 0o077, 0)
            job_text = (root / "jobs" / "security-fixture" / "job.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn(security.TOKEN, job_text)


class UploadBoundaryTests(unittest.TestCase):
    def test_upload_rejects_without_token(self):
        with isolated_jobs_dir():
            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                response = client.post(
                    "/api/jobs",
                    files={"file": ("synthetic.wav", b"RIFF" + b"0" * 1024, "audio/wav")},
                )
                self.assertEqual(response.status_code, 401)

    def test_upload_uses_header_key_and_never_writes_it(self):
        with isolated_jobs_dir() as (root, jobs):
            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                with patch.object(main.executor, "submit"):
                    response = client.post(
                        "/api/jobs",
                        headers={
                            security.SESSION_HEADER: security.TOKEN,
                            "X-Transcriptor-DeepSeek-Key": "dummy-secret",
                        },
                        data={
                            "title": "Synthetic upload",
                            "ai_correction": "true",
                            "chapters": "false",
                            "glossary": "false",
                        },
                        files={
                            "file": (
                                "synthetic.wav",
                                b"RIFF" + b"0" * (1024 * 1024),
                                "audio/wav",
                            )
                        },
                    )
                self.assertEqual(response.status_code, 200)
                job_id = response.json()["id"]
                job_text = (jobs / job_id / "job.json").read_text(encoding="utf-8")
                self.assertNotIn("dummy-secret", job_text)
                from app.secrets import pop_job_secrets

                self.assertEqual(
                    pop_job_secrets(job_id).get("deepseek_api_key"),
                    "dummy-secret",
                )

    def test_server_rejects_oversized_content_length_before_reading(self):
        with isolated_jobs_dir() as (root, jobs):
            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                with patch.object(main, "MAX_UPLOAD_BYTES", 1024 * 1024):
                    response = client.post(
                        "/api/jobs",
                        headers={security.SESSION_HEADER: security.TOKEN},
                        data={"ai_correction": "false", "chapters": "false"},
                        files={
                            "file": (
                                "synthetic.wav",
                                b"RIFF" + b"0" * (3 * 1024 * 1024),
                                "audio/wav",
                            )
                        },
                    )
                self.assertEqual(response.status_code, 413)
                job_dirs = [item for item in jobs.iterdir() if item.is_dir()]
                self.assertEqual(job_dirs, [])


if __name__ == "__main__":
    unittest.main()
