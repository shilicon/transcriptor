from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import segmentation
from app.formatting import build_paragraphs


def _segments(count: int, speaker: str = "S1", prefix: str = "Sentence"):
    return [
        {
            "start": index * 3.0,
            "end": index * 3.0 + 2.5,
            "text": f"{prefix} {index + 1}.",
            "speaker_id": speaker,
        }
        for index in range(count)
    ]


def _success(content: str) -> dict:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": content},
            }
        ]
    }


class SemanticSegmentationTests(unittest.TestCase):
    def _plan(
        self,
        segments,
        breaks,
        api_key="sk-synthetic",
        content: str | None = None,
    ):
        calls = []

        def handle(request):
            payload = json.loads(request.content)
            calls.append(payload)
            return httpx.Response(
                200,
                request=request,
                json=_success(
                    content
                    if content is not None
                    else json.dumps({"breaks": breaks})
                ),
            )

        transport = httpx.MockTransport(handle)
        real_client = httpx.Client

        def mock_client(*args, **kwargs):
            return real_client(*args, transport=transport, **kwargs)

        with patch.object(segmentation.httpx, "Client", side_effect=mock_client):
            result = segmentation.semantic_paragraph_breaks(
                segments,
                api_key=api_key,
                base_url="https://api.deepseek.com",
                model="deepseek-flash",
            )
        return calls, result

    def test_no_key_falls_back_without_calling_ai(self):
        calls, result = self._plan(_segments(24), [8, 16], api_key="")
        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_short_turns_do_not_need_ai(self):
        calls, result = self._plan(_segments(10), [])
        self.assertEqual(result, set())
        self.assertEqual(calls, [])

    def test_ai_breaks_create_semantic_paragraphs(self):
        segments = _segments(24)
        calls, result = self._plan(segments, [8, 16])
        self.assertEqual(result, {8, 16})
        self.assertEqual(len(calls), 1)
        system = calls[0]["messages"][0]["content"]
        self.assertIn("genuine topic, argument or narrative shifts", system)
        self.assertIn("never exceed 12", system)
        self.assertEqual(calls[0]["response_format"], {"type": "json_object"})
        self.assertEqual(calls[0]["thinking"], {"type": "disabled"})

        paragraphs = build_paragraphs(
            segments,
            [0] * len(segments),
            "en",
            break_before=result,
        )
        self.assertEqual(len(paragraphs), 3)
        self.assertTrue(
            all(len(paragraph["segment_indices"]) == 8 for paragraph in paragraphs)
        )

    def test_only_long_turns_are_sent_to_ai(self):
        segments = (
            _segments(16, "S1", "First turn")
            + _segments(4, "S2", "Second turn")
            + _segments(16, "S1", "Third turn")
        )
        # Re-index because _segments starts at zero.
        for index, segment in enumerate(segments):
            segment["start"] = index * 3.0
            segment["end"] = index * 3.0 + 2.5
        calls, result = self._plan(segments, [8, 28])
        self.assertEqual(result, {8, 28})
        user = calls[0]["messages"][1]["content"]
        self.assertIn("First turn", user)
        self.assertIn("Third turn", user)
        self.assertNotIn("Second turn", user)

    def test_undersized_break_plan_is_rejected(self):
        with self.assertRaisesRegex(segmentation.SegmentationError, "过长"):
            self._plan(_segments(24), [8])

    def test_fenced_json_is_parsed(self):
        calls, result = self._plan(
            _segments(24),
            [],
            content="```json\n{\"breaks\":[8,16]}\n```",
        )
        self.assertEqual(result, {8, 16})
        self.assertTrue(calls)


if __name__ == "__main__":
    unittest.main()
