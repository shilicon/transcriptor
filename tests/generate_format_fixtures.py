from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app import main, security
from app.utils import save_job
from tests.support import isolated_jobs_dir, make_job


def main_entry() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    with isolated_jobs_dir():
        healthy = make_job("healthy")
        healthy["audio"]["duration"] = 10.5
        healthy["paragraphs"][0]["start"] = 1.25
        healthy["paragraphs"][0]["end"] = 2.75
        healthy["chapters"] = [{"start": 3.5, "title": "Healthy"}]
        save_job(healthy)

        damaged = make_job("damaged")
        damaged["audio"]["duration"] = 1e20
        save_job(damaged)

        with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
            headers = {security.SESSION_HEADER: security.TOKEN}
            listing = client.get("/api/jobs", headers=headers)
            detail = client.get("/api/jobs/healthy", headers=headers)
            if listing.status_code != 200 or detail.status_code != 200:
                raise SystemExit(
                    f"fixture API failed: list={listing.status_code} detail={detail.status_code}"
                )
            (out / "list.json").write_text(json.dumps(listing.json(), ensure_ascii=False))
            (out / "healthy-detail.json").write_text(
                json.dumps(detail.json(), ensure_ascii=False)
            )


if __name__ == "__main__":
    main_entry()
