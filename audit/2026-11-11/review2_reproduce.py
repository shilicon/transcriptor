"""Second remediation review: synthetic isolated observations, no product edits."""
import contextlib
import io
import json
from pathlib import Path
import runpy
import sys

prior = runpy.run_path(str(Path(__file__).with_name("reaudit_reproduce.py")))
main, security = prior["main"], prior["security"]
TestClient = prior["TestClient"]
isolated_jobs_dir, make_job = prior["isolated_jobs_dir"], prior["make_job"]
save_job, load_job = prior["save_job"], prior["load_job"]


def metadata():
    results = {}
    cases = [
        ("created_at", 123),
        ("title", ["invalid"]),
        ("progress", 10 ** 400),
        ("stats", {"paragraphs": 2 ** 63}),
        ("correction", {"enabled": "yes", "errors": "bad"}),
    ]
    for field, value in cases:
        with isolated_jobs_dir():
            save_job(make_job("healthy"))
            job = make_job("damaged")
            job[field] = value
            save_job(job)
            row = {}
            try:
                load_job("damaged")
                row["loader"] = "accepted"
            except Exception as exc:
                row["loader"] = type(exc).__name__
            with contextlib.redirect_stdout(sys.stderr):
                try:
                    with TestClient(main.app, base_url="http://127.0.0.1:8765", raise_server_exceptions=False):
                        row["startup"] = "ok"
                except Exception as exc:
                    row["startup"] = type(exc).__name__
            # Deliberately not a context manager: exercise HTTP without startup.
            client = TestClient(main.app, base_url="http://127.0.0.1:8765", raise_server_exceptions=False)
            try:
                response = client.get("/api/jobs", headers={security.SESSION_HEADER: security.TOKEN})
                detail = client.get("/api/jobs/damaged", headers={security.SESSION_HEADER: security.TOKEN})
                row.update(list_status=response.status_code, detail_status=detail.status_code)
                if response.status_code == 200:
                    row["summary"] = next(x for x in response.json() if x["id"] == "damaged")
                if field == "stats" and len(sys.argv) > 1:
                    Path(sys.argv[1]).write_text(response.text)
                if field == "correction" and len(sys.argv) > 1:
                    Path(sys.argv[1]).with_name("correction-detail.json").write_text(detail.text)
            finally:
                client.close()
            results[field] = row
    return results


if __name__ == "__main__":
    print(json.dumps({
        "negation": prior["negation_probe"](),
        "anonymous_home": prior["unauthenticated_cookie_probe"](),
        "retry_after_delete": prior["retry_after_delete_probe"](),
        "extract_after_delete": prior["extraction_delete_probe"](),
        "metadata": metadata(),
    }, ensure_ascii=False, indent=2))
