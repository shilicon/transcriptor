from __future__ import annotations

import asyncio
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main, translation
from app.utils import load_job, save_job
from tests.support import isolated_jobs_dir, make_job


PARAGRAPHS = [
    {
        "id": "p0001",
        "start": 0.0,
        "end": 10.0,
        "speaker_id": "S1",
        "text": "Hello and welcome.",
    },
    {
        "id": "p0002",
        "start": 10.0,
        "end": 20.0,
        "speaker_id": "S1",
        "text": "Today we discuss chips.",
    },
]


def _response(content: str, finish_reason: str = "stop") -> dict:
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"content": content},
            }
        ]
    }


class TranslationLLMTests(unittest.TestCase):
    def _run(
        self,
        paragraphs: list[dict] | None = None,
        api_key: str = "sk-synthetic",
        handler=None,
    ):
        calls = []
        items = paragraphs or PARAGRAPHS

        def default_handler(request):
            payload = json.loads(request.content)
            calls.append(payload)
            segments = json.loads(payload["messages"][1]["content"].split("\n\n", 1)[1])
            translated = [
                {"id": segment["id"], "text": f"EN: {segment['text']}"}
                for segment in segments
            ]
            return httpx.Response(
                200,
                request=request,
                json=_response(
                    json.dumps({"translations": translated}, ensure_ascii=False)
                ),
            )

        transport = httpx.MockTransport(handler or default_handler)
        real_client = httpx.Client

        def mock_client(*args, **kwargs):
            return real_client(*args, transport=transport, **kwargs)

        with patch.object(translation.httpx, "Client", side_effect=mock_client):
            result = translation.translate_paragraphs(
                items,
                "English",
                api_key=api_key,
                base_url="https://api.deepseek.com",
                model="deepseek-flash",
            )
        return calls, result

    def test_translates_all_segments_with_bearer_and_json_mode(self):
        calls, result = self._run()
        self.assertEqual(len(calls), 1)
        payload = calls[0]
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertIn("English", payload["messages"][0]["content"])
        self.assertEqual(result["p0001"], "EN: Hello and welcome.")
        self.assertEqual(result["p0002"], "EN: Today we discuss chips.")

    def test_long_transcript_is_batched(self):
        paragraphs = [
            {
                "id": f"p{index:04d}",
                "start": index * 10.0,
                "end": index * 10.0 + 9.0,
                "speaker_id": "S1",
                "text": f"Segment {index}.",
            }
            for index in range(25)
        ]
        calls, result = self._run(paragraphs=paragraphs)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(result), 25)
        self.assertEqual(result["p0024"], "EN: Segment 24.")

    def test_local_endpoint_sends_no_authorization_or_thinking(self):
        calls, _ = self._run(api_key="")
        self.assertNotIn("thinking", calls[0])
        self.assertIn("Segments", calls[0]["messages"][1]["content"])

    def test_missing_translation_raises(self):
        def handler(request):
            return httpx.Response(
                200,
                request=request,
                json=_response('{"translations": []}'),
            )

        with self.assertRaisesRegex(translation.TranslationError, "缺少"):
            self._run(handler=handler)

    def test_invalid_json_raises(self):
        def handler(request):
            return httpx.Response(
                200,
                request=request,
                json=_response("not json at all"),
            )

        with self.assertRaisesRegex(translation.TranslationError, "有效 JSON"):
            self._run(handler=handler)

    def test_truncated_response_raises(self):
        def handler(request):
            return httpx.Response(
                200,
                request=request,
                json=_response('{"translations": [', "length"),
            )

        with self.assertRaisesRegex(translation.TranslationError, "截断"):
            self._run(handler=handler)


class TranslationRouteTests(unittest.TestCase):
    def test_route_persists_translation_without_changing_transcript(self):
        with isolated_jobs_dir():
            job_id = "translate-route"
            save_job(make_job(job_id, "done"))

            def fake_translate(paragraphs, target_language, **kwargs):
                return {"p0001": "翻译后的文本。"}

            async def scenario():
                with patch.object(main, "translate_paragraphs", side_effect=fake_translate):
                    return await main.translate(
                        job_id,
                        main.TranslateRequest(target_language="简体中文"),
                    )

            result = asyncio.run(scenario())
            self.assertEqual(
                result["translations"]["简体中文"]["texts"]["p0001"],
                "翻译后的文本。",
            )
            saved = load_job(job_id)
            self.assertEqual(
                saved["translations"]["简体中文"]["texts"]["p0001"],
                "翻译后的文本。",
            )
            self.assertEqual(saved["paragraphs"][0]["text"], "Original approved text.")

    def test_route_rejects_result_when_transcript_changed_during_translation(self):
        with isolated_jobs_dir():
            job_id = "translate-race"
            save_job(make_job(job_id, "done"))
            entered, release = threading.Event(), threading.Event()

            def fake_translate(paragraphs, target_language, **kwargs):
                entered.set()
                self.assertTrue(release.wait(10), "test barrier timed out")
                return {"p0001": "翻译后的文本。"}

            async def scenario():
                with patch.object(main, "translate_paragraphs", side_effect=fake_translate):
                    pending = asyncio.create_task(
                        main.translate(
                            job_id,
                            main.TranslateRequest(target_language="简体中文"),
                        )
                    )
                    self.assertTrue(
                        await asyncio.to_thread(entered.wait, 10),
                        "translation worker did not start",
                    )
                    await asyncio.to_thread(
                        main.update_paragraphs,
                        job_id,
                        main.ParagraphsUpdate(
                            texts=["Edited during translation."],
                            speaker_ids=["S1"],
                        ),
                    )
                    release.set()
                    with self.assertRaises(HTTPException) as caught:
                        await pending
                    self.assertEqual(caught.exception.status_code, 409)

            asyncio.run(scenario())
            saved = load_job(job_id)
            self.assertEqual(saved["paragraphs"][0]["text"], "Edited during translation.")
            self.assertNotIn("简体中文", saved.get("translations") or {})

    def test_editing_text_invalidates_only_changed_translation(self):
        with isolated_jobs_dir():
            job_id = "translate-invalidate"
            job = make_job(job_id, "done")
            job["translations"] = {
                "English": {
                    "model": "deepseek-flash",
                    "texts": {"p0001": "Original translation."},
                }
            }
            save_job(job)

            main.update_paragraphs(
                job_id,
                main.ParagraphsUpdate(
                    texts=["Changed original."],
                    speaker_ids=["S1"],
                ),
            )

            saved = load_job(job_id)
            self.assertNotIn("p0001", saved["translations"]["English"]["texts"])


if __name__ == "__main__":
    unittest.main()
