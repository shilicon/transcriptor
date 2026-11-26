from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException
from fastapi.testclient import TestClient

import httpx

from app import correction, main, pipeline, security, transcribe
from app.config import MAX_TIME_SECONDS
from app.correction import CorrectionCancelled
from app.transcribe import TranscriptionError
from app.utils import (
    JobDataError,
    clear_job_deleted,
    is_job_deleted,
    job_dir,
    load_job,
    save_job,
    sentence_count,
)
from tests.support import isolated_jobs_dir, make_job


class JobLifecycleTests(unittest.TestCase):
    def test_running_job_delete_does_not_resurrect(self):
        with isolated_jobs_dir() as (_, jobs):
            job_id = "delete-running"
            save_job(make_job(job_id, "queued"))
            job_dir(job_id).mkdir(parents=True, exist_ok=True)
            (job_dir(job_id) / "input.wav").write_bytes(b"synthetic")

            entered, release = threading.Event(), threading.Event()

            def fake_transcribe(**kwargs):
                entered.set()
                self.assertTrue(release.wait(10), "test barrier timed out")
                return [{"start": 0, "end": 10, "text": "Audit transcript."}]

            with (
                patch.object(pipeline, "probe_duration", return_value=10),
                patch.object(pipeline, "extract_audio"),
                patch.object(pipeline, "transcribe", side_effect=fake_transcribe),
                patch.object(pipeline, "diarize_segments", return_value=([0], 1)),
            ):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(pipeline.run_pipeline, job_id)
                    self.assertTrue(entered.wait(10), "worker did not start")
                    deleted = main.delete_job(job_id)
                    self.assertEqual(deleted, {"deleted": True})
                    self.assertFalse(job_dir(job_id).exists())
                    release.set()
                    future.result(10)

            self.assertFalse(job_dir(job_id).exists())
            self.assertIsNone(load_job(job_id))
            clear_job_deleted(job_id)

    def test_extract_audio_does_not_recreate_deleted_parent(self):
        with tempfile.TemporaryDirectory(prefix="transcriptor-extract-test-") as tmp:
            root = Path(tmp)
            input_path = root / "input.wav"
            input_path.write_bytes(b"synthetic")
            output_path = root / "missing-task" / "audio.wav"
            with self.assertRaises(TranscriptionError):
                transcribe.extract_audio(input_path, output_path)
            self.assertFalse(output_path.parent.exists())

    def test_extraction_delete_window_leaves_no_directory(self):
        with isolated_jobs_dir() as (_, jobs):
            job_id = "extract-delete-race"
            save_job(make_job(job_id, "queued"))
            job_dir(job_id).mkdir(parents=True, exist_ok=True)
            (job_dir(job_id) / "input.wav").write_bytes(b"synthetic input")

            entered, release = threading.Event(), threading.Event()
            real_binary = transcribe._require_binary

            def gated_binary(name):
                binary = real_binary(name)
                if name == "ffmpeg":
                    entered.set()
                    self.assertTrue(release.wait(10), "extraction barrier timed out")
                return binary

            with (
                patch.object(pipeline, "probe_duration", return_value=10),
                patch.object(transcribe, "_require_binary", side_effect=gated_binary),
            ):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(pipeline.run_pipeline, job_id)
                    self.assertTrue(entered.wait(10), "extraction did not start")
                    main.delete_job(job_id)
                    release.set()
                    future.result(10)

            self.assertFalse(job_dir(job_id).exists())
            self.assertFalse((jobs / job_id / "job.json").exists())
            clear_job_deleted(job_id)

    def test_ai_retry_stops_after_real_delete(self):
        with isolated_jobs_dir():
            job_id = "retry-delete-real"
            save_job(make_job(job_id))
            calls = []

            def handle(request):
                calls.append(is_job_deleted(job_id))
                if len(calls) == 1:
                    main.delete_job(job_id)
                    return httpx.Response(503, request=request, json={"error": "mock"})
                return httpx.Response(
                    200,
                    request=request,
                    json={"choices": [{"message": {"content": '{"edits":[]}'}}]},
                )

            transport = httpx.MockTransport(handle)
            real_client = httpx.Client

            def mock_client(*args, **kwargs):
                return real_client(*args, transport=transport, **kwargs)

            with (
                patch.object(correction.httpx, "Client", side_effect=mock_client),
                patch.object(correction.time, "sleep"),
            ):
                with self.assertRaises(CorrectionCancelled):
                    correction.correct_paragraphs(
                        [{"id": "p1", "start": 0, "text": "Synthetic private transcript"}],
                        {},
                        "dummy-key",
                        "http://audit.invalid",
                        "mock",
                        should_cancel=lambda: is_job_deleted(job_id),
                    )
            self.assertEqual(len(calls), 1)
            self.assertFalse(job_dir(job_id).exists())
            clear_job_deleted(job_id)

    def test_queued_job_delete_never_runs(self):
        with isolated_jobs_dir() as (_, jobs):
            job_id = "delete-queued"
            save_job(make_job(job_id, "queued"))
            main.delete_job(job_id)
            pipeline.run_pipeline(job_id)
            self.assertFalse(job_dir(job_id).exists())
            clear_job_deleted(job_id)

    def test_delete_corrupt_job_removes_files(self):
        with isolated_jobs_dir() as (_, jobs):
            job_id = "delete-corrupt"
            folder = jobs / job_id
            folder.mkdir(parents=True)
            (folder / "job.json").write_text("{broken", encoding="utf-8")
            self.assertEqual(main.delete_job(job_id), {"deleted": True})
            self.assertFalse(folder.exists())
            clear_job_deleted(job_id)


class CorruptJobTests(unittest.TestCase):
    def test_list_jobs_isolates_corrupt_entries(self):
        with isolated_jobs_dir() as (_, jobs):
            save_job(make_job("healthy"))
            broken = jobs / "broken-json"
            broken.mkdir()
            broken_file = broken / "job.json"
            broken_file.write_text("{broken", encoding="utf-8")
            invalid = jobs / "invalid-structure"
            invalid.mkdir()
            invalid_file = invalid / "job.json"
            invalid_file.write_text(
                json.dumps({"id": "invalid-structure", "paragraphs": "not-a-list"}),
                encoding="utf-8",
            )

            items = main.list_jobs()
            self.assertEqual(len(items), 3)
            by_id = {item["id"]: item for item in items}
            self.assertEqual(by_id["healthy"]["status"], "done")
            self.assertTrue(by_id["broken-json"]["corrupt"])
            self.assertTrue(by_id["invalid-structure"]["corrupt"])
            self.assertEqual(broken_file.read_text(encoding="utf-8"), "{broken")
            self.assertIn("paragraphs", invalid_file.read_text(encoding="utf-8"))

            with self.assertRaises(HTTPException) as caught:
                main._require_job("broken-json")
            self.assertEqual(caught.exception.status_code, 422)

    def test_metadata_type_errors_are_isolated(self):
        with isolated_jobs_dir() as (_, jobs):
            save_job(make_job("healthy-meta"))
            for job_id, field, value in [
                ("bad-created", "created_at", 123),
                ("bad-title", "title", ["invalid-title"]),
                ("bad-progress", "progress", 10 ** 400),
                ("bad-stats", "stats", {"paragraphs": 2 ** 63}),
                ("bad-correction", "correction", {"enabled": "yes", "errors": "bad"}),
            ]:
                job = make_job(job_id)
                job[field] = value
                save_job(job)

            with self.assertRaises(JobDataError):
                load_job("bad-created")
            with self.assertRaises(JobDataError):
                load_job("bad-title")
            with self.assertRaises(JobDataError):
                load_job("bad-progress")
            with self.assertRaises(JobDataError):
                load_job("bad-stats")
            with self.assertRaises(JobDataError):
                load_job("bad-correction")

            items = main.list_jobs()
            self.assertEqual(len(items), 6)
            by_id = {item["id"]: item for item in items}
            self.assertEqual(by_id["healthy-meta"]["status"], "done")
            for job_id in ("bad-created", "bad-title", "bad-progress", "bad-stats", "bad-correction"):
                self.assertTrue(by_id[job_id]["corrupt"])
            self.assertIsInstance(by_id["bad-created"]["created_at"], type(None))
            self.assertIsInstance(by_id["bad-title"]["title"], str)
            self.assertIn("123", (jobs / "bad-created" / "job.json").read_text(encoding="utf-8"))

            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                response = client.get(
                    "/api/jobs",
                    headers={security.SESSION_HEADER: security.TOKEN},
                )
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(len(payload), 6)
                self.assertTrue(next(item for item in payload if item["id"] == "bad-title")["corrupt"])
                for job_id in ("bad-created", "bad-progress", "bad-stats", "bad-correction"):
                    detail = client.get(
                        f"/api/jobs/{job_id}",
                        headers={security.SESSION_HEADER: security.TOKEN},
                    )
                    self.assertEqual(detail.status_code, 422)

            with self.assertRaises(HTTPException) as caught:
                main._require_job("bad-created")
            self.assertEqual(caught.exception.status_code, 422)

    def test_time_range_errors_are_isolated(self):
        with isolated_jobs_dir() as (_, jobs):
            save_job(make_job("healthy-time"))
            cases = [
                ("bad-duration", lambda job: job["audio"].__setitem__("duration", 1e20)),
                (
                    "bad-paragraph-time",
                    lambda job: (
                        job["paragraphs"][0].__setitem__("start", 1e20),
                        job["paragraphs"][0].__setitem__("end", 1e20),
                    ),
                ),
                (
                    "bad-chapter-time",
                    lambda job: job.__setitem__(
                        "chapters", [{"start": 1e20, "title": "Synthetic"}]
                    ),
                ),
                (
                    "bad-segment-time",
                    lambda job: (
                        job["segments"][0].__setitem__("start", 1e20),
                        job["segments"][0].__setitem__("end", 1e20),
                    ),
                ),
            ]
            for job_id, mutate in cases:
                job = make_job(job_id)
                mutate(job)
                save_job(job)

            for job_id, _ in cases:
                with self.assertRaises(JobDataError):
                    load_job(job_id)

            items = main.list_jobs()
            by_id = {item["id"]: item for item in items}
            self.assertEqual(by_id["healthy-time"]["status"], "done")
            for job_id, _ in cases:
                self.assertTrue(by_id[job_id]["corrupt"])

            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                response = client.get(
                    "/api/jobs",
                    headers={security.SESSION_HEADER: security.TOKEN},
                )
                self.assertEqual(response.status_code, 200)
                for job_id, _ in cases:
                    detail = client.get(
                        f"/api/jobs/{job_id}",
                        headers={security.SESSION_HEADER: security.TOKEN},
                    )
                    self.assertEqual(detail.status_code, 422)

            for job_id, _ in cases:
                self.assertIn("1e+20", (jobs / job_id / "job.json").read_text(encoding="utf-8"))

    def test_time_boundaries_and_legacy_strings(self):
        with isolated_jobs_dir():
            boundary = make_job("boundary-time")
            boundary["audio"]["duration"] = float(MAX_TIME_SECONDS)
            boundary["paragraphs"][0]["start"] = 1.25
            boundary["paragraphs"][0]["end"] = float(MAX_TIME_SECONDS)
            boundary["chapters"] = [{"start": "00:00", "title": "Legacy"}]
            save_job(boundary)
            loaded = load_job("boundary-time")
            self.assertEqual(loaded["audio"]["duration"], float(MAX_TIME_SECONDS))
            self.assertEqual(loaded["paragraphs"][0]["start"], 1.25)
            self.assertEqual(loaded["chapters"][0]["start"], 0.0)

            too_large = make_job("too-large-time")
            too_large["audio"]["duration"] = float(MAX_TIME_SECONDS) + 0.5
            save_job(too_large)
            with self.assertRaises(JobDataError):
                load_job("too-large-time")

    def test_corrupt_job_does_not_block_startup(self):
        with isolated_jobs_dir() as (_, jobs):
            broken = jobs / "broken-startup"
            broken.mkdir()
            (broken / "job.json").write_text("{broken", encoding="utf-8")
            save_job(make_job("healthy-startup"))
            with TestClient(main.app, base_url="http://127.0.0.1:8765") as client:
                response = client.get("/api/health")
                self.assertEqual(response.status_code, 200)

    def test_legacy_job_does_not_claim_raw_transcript(self):
        with isolated_jobs_dir() as (_, jobs):
            job = make_job("legacy")
            job["paragraphs"][0].pop("raw_text", None)
            job["paragraphs"][0].pop("formatted_text", None)
            save_job(job)
            loaded = load_job("legacy")
            paragraph = loaded["paragraphs"][0]
            self.assertNotIn("raw_text", paragraph)
            self.assertEqual(paragraph["formatted_text"], paragraph["original_text"])
            self.assertFalse(paragraph["manual_edited"])


class ParagraphFormattingPipelineTests(unittest.TestCase):
    def test_pipeline_caps_long_speaker_turns_near_ten_sentences(self):
        with isolated_jobs_dir():
            job_id = "format-pipeline"
            save_job(make_job(job_id, "queued"))
            job_dir(job_id).mkdir(parents=True, exist_ok=True)
            (job_dir(job_id) / "input.wav").write_bytes(b"synthetic")

            segments = [
                {
                    "start": index * 15.0,
                    "end": index * 15.0 + 14.0,
                    "text": (
                        "Alpha one. Beta two. Gamma three. "
                        "This fragment continues"
                    ),
                }
                for index in range(12)
            ]
            with (
                patch.object(pipeline, "probe_duration", return_value=180),
                patch.object(pipeline, "extract_audio"),
                patch.object(pipeline, "transcribe", return_value=segments),
                patch.object(
                    pipeline,
                    "diarize_segments",
                    return_value=([0] * len(segments), 1),
                ),
            ):
                pipeline.run_pipeline(job_id)

            job = load_job(job_id)
            self.assertEqual(job["status"], "done")
            self.assertGreater(len(job["segments"]), len(segments))
            self.assertGreater(len(job["paragraphs"]), 1)
            self.assertTrue(
                all(
                    sentence_count(paragraph["text"]) <= 10
                    for paragraph in job["paragraphs"]
                )
            )

    def test_pipeline_uses_ai_semantic_breaks(self):
        with isolated_jobs_dir():
            job_id = "format-ai"
            job = make_job(job_id, "queued")
            job["settings"]["ai_correction"] = True
            save_job(job)
            job_dir(job_id).mkdir(parents=True, exist_ok=True)
            (job_dir(job_id) / "input.wav").write_bytes(b"synthetic")

            segments = [
                {
                    "start": index * 15.0,
                    "end": index * 15.0 + 14.0,
                    "text": (
                        "Alpha one. Beta two. Gamma three. "
                        "This fragment continues"
                    ),
                }
                for index in range(12)
            ]
            captured = {}

            def fake_plan(sentence_segments, **kwargs):
                captured["count"] = len(sentence_segments)
                captured["api_key"] = kwargs.get("api_key")
                return {
                    index
                    for index in range(5, len(sentence_segments), 5)
                }

            with (
                patch.object(pipeline, "probe_duration", return_value=180),
                patch.object(pipeline, "extract_audio"),
                patch.object(pipeline, "transcribe", return_value=segments),
                patch.object(
                    pipeline,
                    "diarize_segments",
                    return_value=([0] * len(segments), 1),
                ),
                patch.object(
                    pipeline,
                    "pop_job_secrets",
                    return_value={"deepseek_api_key": "sk-synthetic"},
                ),
                patch.object(
                    pipeline,
                    "semantic_paragraph_breaks",
                    side_effect=fake_plan,
                ),
                patch.object(
                    pipeline,
                    "correct_paragraphs",
                    return_value={
                        "applied": 0,
                        "rejected": 0,
                        "batches": 0,
                        "errors": [],
                        "model": "test",
                    },
                ),
            ):
                pipeline.run_pipeline(job_id)

            job = load_job(job_id)
            self.assertEqual(job["status"], "done")
            self.assertEqual(captured["api_key"], "sk-synthetic")
            self.assertGreater(captured["count"], len(segments))
            self.assertGreater(len(job["paragraphs"]), 1)
            self.assertTrue(
                all(
                    sentence_count(paragraph["text"]) <= 6
                    for paragraph in job["paragraphs"]
                )
            )


class ChapterConcurrencyTests(unittest.TestCase):
    def test_auto_chapters_forwards_deepseek_key_and_instructions(self):
        with isolated_jobs_dir():
            job_id = "chapter-key"
            save_job(make_job(job_id, "done"))
            calls = {}

            def fake_llm(*args, **kwargs):
                calls["endpoint"] = args[2]
                calls["model"] = args[3]
                calls["api_key"] = kwargs.get("api_key")
                calls["instructions"] = kwargs.get("instructions")
                return [{"start": 0, "title": "章节"}]

            async def scenario():
                with patch.object(main, "auto_chapters_llm", side_effect=fake_llm):
                    return await main.auto_chapters(
                        job_id,
                        main.AutoChaptersRequest(
                            endpoint="https://api.deepseek.com",
                            model="deepseek-flash",
                            api_key="sk-synthetic",
                            instructions="每 3 分钟一章",
                        ),
                    )

            result = asyncio.run(scenario())
            self.assertEqual(calls["endpoint"], "https://api.deepseek.com")
            self.assertEqual(calls["model"], "deepseek-flash")
            self.assertEqual(calls["api_key"], "sk-synthetic")
            self.assertEqual(calls["instructions"], "每 3 分钟一章")
            self.assertEqual(result["chapters"], [{"start": 0.0, "title": "章节"}])

    def test_chapter_response_preserves_text_and_speaker_edits(self):
        with isolated_jobs_dir() as (_, jobs):
            job_id = "chapter-race"
            save_job(make_job(job_id, "done"))
            entered, release = threading.Event(), threading.Event()

            def fake_llm(*args, **kwargs):
                entered.set()
                self.assertTrue(release.wait(10), "test barrier timed out")
                return [{"start": 0, "title": "Generated chapter"}]

            async def scenario():
                with patch.object(main, "auto_chapters_llm", side_effect=fake_llm):
                    pending = asyncio.create_task(
                        main.auto_chapters(
                            job_id,
                            main.AutoChaptersRequest(
                                endpoint="http://audit.invalid/v1",
                                model="mock",
                                title_language="zh",
                            ),
                        )
                    )
                    self.assertTrue(
                        await asyncio.to_thread(entered.wait, 10),
                        "chapter worker did not start",
                    )
                    edited = await asyncio.to_thread(
                        main.update_paragraphs,
                        job_id,
                        main.ParagraphsUpdate(
                            texts=["Saved human correction."],
                            speaker_ids=["S1"],
                        ),
                    )
                    renamed = await asyncio.to_thread(
                        main.update_speakers,
                        job_id,
                        main.SpeakersUpdate(
                            speakers=[main.SpeakerItem(id="S1", name="Renamed speaker")]
                        ),
                    )
                    self.assertEqual(edited["paragraphs"][0]["text"], "Saved human correction.")
                    self.assertEqual(renamed["speakers"][0]["name"], "Renamed speaker")
                    release.set()
                    return await pending

            result = asyncio.run(scenario())
            loaded = load_job(job_id)
            self.assertEqual(loaded["paragraphs"][0]["text"], "Saved human correction.")
            self.assertEqual(loaded["speakers"][0]["name"], "Renamed speaker")
            self.assertEqual(loaded["chapters"], [{"start": 0.0, "title": "Generated chapter"}])
            self.assertEqual(result["chapters"], [{"start": 0.0, "title": "Generated chapter"}])

    def test_deleted_job_does_not_get_chapters_or_files(self):
        with isolated_jobs_dir() as (_, jobs):
            job_id = "chapter-delete"
            save_job(make_job(job_id, "done"))
            entered, release = threading.Event(), threading.Event()

            def fake_llm(*args, **kwargs):
                entered.set()
                self.assertTrue(release.wait(10), "test barrier timed out")
                return [{"start": 0, "title": "Should not be saved"}]

            async def scenario():
                with patch.object(main, "auto_chapters_llm", side_effect=fake_llm):
                    pending = asyncio.create_task(
                        main.auto_chapters(
                            job_id,
                            main.AutoChaptersRequest(
                                endpoint="http://audit.invalid/v1",
                                model="mock",
                                title_language="zh",
                            ),
                        )
                    )
                    self.assertTrue(await asyncio.to_thread(entered.wait, 10))
                    main.delete_job(job_id)
                    release.set()
                    with self.assertRaises(HTTPException) as caught:
                        await pending
                    self.assertEqual(caught.exception.status_code, 404)

            asyncio.run(scenario())
            self.assertFalse(job_dir(job_id).exists())
            clear_job_deleted(job_id)


class PipelineRawTextTests(unittest.TestCase):
    def test_pipeline_keeps_raw_asr_text_separate_from_glossary(self):
        with isolated_jobs_dir() as (_, jobs):
            job_id = "raw-text"
            job = make_job(job_id, "queued")
            job["settings"]["glossary"] = True
            save_job(job)
            job_dir(job_id).mkdir(parents=True, exist_ok=True)
            (job_dir(job_id) / "input.wav").write_bytes(b"synthetic")
            raw = "We use chip stack every day."

            with (
                patch.object(pipeline, "probe_duration", return_value=10),
                patch.object(pipeline, "extract_audio"),
                patch.object(
                    pipeline,
                    "transcribe",
                    return_value=[{"start": 0, "end": 10, "text": raw}],
                ),
                patch.object(pipeline, "diarize_segments", return_value=([0], 1)),
            ):
                pipeline.run_pipeline(job_id)

            loaded = load_job(job_id)
            self.assertEqual(loaded["status"], "done")
            self.assertEqual(loaded["raw_segments"][0]["text"], raw)
            self.assertEqual(loaded["paragraphs"][0]["raw_text"], raw)
            self.assertEqual(loaded["paragraphs"][0]["text"], "We use ChipStack every day.")
            self.assertEqual(
                loaded["paragraphs"][0]["formatted_text"],
                "We use ChipStack every day.",
            )
            self.assertEqual(
                loaded["paragraphs"][0]["original_text"],
                "We use ChipStack every day.",
            )


class UndoCorrectionTests(unittest.TestCase):
    def test_undo_restores_only_ai_corrected_paragraphs(self):
        with isolated_jobs_dir() as (_, jobs):
            job_id = "undo-manual"
            paragraphs = [
                {
                    "id": "p0001",
                    "start": 0,
                    "end": 10,
                    "speaker_id": "S1",
                    "text": "AI corrected text.",
                    "formatted_text": "Original formatted text.",
                    "original_text": "Original formatted text.",
                    "correction_status": "corrected",
                    "manual_edited": False,
                    "segment_indices": [0],
                },
                {
                    "id": "p0002",
                    "start": 10,
                    "end": 20,
                    "speaker_id": "S1",
                    "text": "Manual correction only.",
                    "formatted_text": "Original second paragraph.",
                    "original_text": "Original second paragraph.",
                    "correction_status": "edited",
                    "manual_edited": True,
                    "segment_indices": [1],
                },
            ]
            job = make_job(job_id, "done", paragraphs=paragraphs)
            job["segments"] = [
                {"start": 0, "end": 10, "text": "AI corrected text.", "speaker_id": "S1"},
                {"start": 10, "end": 20, "text": "Manual correction only.", "speaker_id": "S1"},
            ]
            job["correction"] = {"enabled": True, "status": "applied", "applied": 1}
            save_job(job)

            response = main.undo_correction(job_id)
            loaded = load_job(job_id)
            self.assertEqual(loaded["paragraphs"][0]["text"], "Original formatted text.")
            self.assertEqual(loaded["paragraphs"][1]["text"], "Manual correction only.")
            self.assertEqual(response["correction"]["skipped_manual"], 1)
            self.assertEqual(loaded["correction"]["status"], "undone")


if __name__ == "__main__":
    unittest.main()
