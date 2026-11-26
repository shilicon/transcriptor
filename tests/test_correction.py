from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import correction
from app.correction import (
    CorrectionCancelled,
    CorrectionError,
    _apply_edits,
    correct_paragraphs,
)
from unittest.mock import patch

import httpx


class CorrectionValidationTests(unittest.TestCase):
    def test_proper_noun(self):
        paragraph = {"id": "p0001", "text": "Kaden's tools are here."}
        count, accepted = _apply_edits(
            paragraph,
            [{
                "original": "Kaden's",
                "replacement": "Cadence",
                "type": "proper_noun",
                "confidence": 0.99,
            }],
        )
        self.assertEqual(count, 1)
        self.assertEqual(paragraph["text"], "Cadence tools are here.")
        self.assertEqual(paragraph["original_text"], "Kaden's tools are here.")
        self.assertEqual(paragraph["ai_text"], "Cadence tools are here.")
        self.assertEqual(accepted[0]["type"], "proper_noun")

    def test_duplicate_word(self):
        paragraph = {"id": "p0001", "text": "thermal fluid dynamics, dynamics all the way."}
        count, _ = _apply_edits(
            paragraph,
            [{
                "original": "dynamics, dynamics",
                "replacement": "dynamics",
                "type": "duplicate",
                "confidence": 0.99,
            }],
        )
        self.assertEqual(count, 1)
        self.assertEqual(paragraph["text"], "thermal fluid dynamics all the way.")

    def test_punctuation_boundary(self):
        paragraph = {"id": "p0001", "text": "Okay, can we start? Yes we can."}
        count, _ = _apply_edits(
            paragraph,
            [{
                "original": "Okay, can we start?",
                "replacement": "Okay. Can we start?",
                "type": "boundary",
                "confidence": 0.99,
            }],
        )
        self.assertEqual(count, 1)
        self.assertTrue(paragraph["text"].startswith("Okay. Can we start?"))

    def test_reject_number_change(self):
        paragraph = {"id": "p0001", "text": "We have 20% more compute."}
        count, _ = _apply_edits(
            paragraph,
            [{
                "original": "20%",
                "replacement": "thirty percent",
                "type": "proper_noun",
                "confidence": 0.99,
            }],
        )
        self.assertEqual(count, 0)
        self.assertEqual(paragraph["text"], "We have 20% more compute.")

    def test_reject_full_sentence_rewrite(self):
        original = "Why Cadence as a chip software company hosting conference has seems to have nothing to do with chips?"
        paragraph = {"id": "p0001", "text": original}
        count, _ = _apply_edits(
            paragraph,
            [{
                "original": original,
                "replacement": "Why does Cadence host a conference about chips?",
                "type": "proper_noun",
                "confidence": 0.99,
            }],
        )
        self.assertEqual(count, 0)
        self.assertEqual(paragraph["text"], original)

    def test_reject_number_reordering(self):
        original = "We increased from 10 to 20 units."
        paragraph = {"id": "p0001", "text": original}
        count, _ = _apply_edits(
            paragraph,
            [{
                "original": "from 10 to 20",
                "replacement": "from 20 to 10",
                "type": "homophone",
                "confidence": 0.99,
            }],
        )
        self.assertEqual(count, 0)
        self.assertEqual(paragraph["text"], original)

    def test_reject_number_count_change(self):
        original = "We tested 10, 10, and then 20."
        paragraph = {"id": "p0001", "text": original}
        count, _ = _apply_edits(
            paragraph,
            [{
                "original": "10, 10",
                "replacement": "10",
                "type": "duplicate",
                "confidence": 0.99,
            }],
        )
        self.assertEqual(count, 0)
        self.assertEqual(paragraph["text"], original)

    def test_reject_negation_deletion(self):
        original = "This change is not safe for customers."
        for edit_type in ("duplicate", "homophone", "proper_noun"):
            with self.subTest(edit_type=edit_type):
                paragraph = {"id": "p0001", "text": original}
                count, _ = _apply_edits(
                    paragraph,
                    [{
                        "original": "not safe",
                        "replacement": "safe",
                        "type": edit_type,
                        "confidence": 0.99,
                    }],
                )
                self.assertEqual(count, 0)
                self.assertEqual(paragraph["text"], original)

    def test_reject_chinese_sentence_rewrite(self):
        original = "我们认为这个方案不能上线，因为目前还有严重风险。"
        paragraph = {"id": "p0001", "text": original}
        count, _ = _apply_edits(
            paragraph,
            [{
                "original": "我们认为这个方案不能上线，因为目前还有严重风险",
                "replacement": "我们认为这个方案可以上线，因为目前已经没有风险",
                "type": "homophone",
                "confidence": 0.99,
            }],
        )
        self.assertEqual(count, 0)
        self.assertEqual(paragraph["text"], original)

    def test_reject_chinese_negation_change(self):
        original = "这个方案不能上线。"
        paragraph = {"id": "p0001", "text": original}
        count, _ = _apply_edits(
            paragraph,
            [{
                "original": "不能上线",
                "replacement": "可以上线",
                "type": "homophone",
                "confidence": 0.99,
            }],
        )
        self.assertEqual(count, 0)
        self.assertEqual(paragraph["text"], original)

    def test_accept_chinese_local_correction(self):
        paragraph = {"id": "p0001", "text": "最近两年生成是人工智能很火。"}
        count, _ = _apply_edits(
            paragraph,
            [{
                "original": "生成是",
                "replacement": "生成式",
                "type": "homophone",
                "confidence": 0.99,
            }],
        )
        self.assertEqual(count, 1)
        self.assertEqual(paragraph["text"], "最近两年生成式人工智能很火。")

    def test_reject_chinese_single_negation_deletion(self):
        for text, original, replacement in [
            ("我不赞成这个方案。", "不赞成", "赞成"),
            ("这个操作不安全。", "不安全", "安全"),
        ]:
            with self.subTest(text=text):
                paragraph = {"id": "p0001", "text": text}
                count, _ = _apply_edits(
                    paragraph,
                    [{
                        "original": original,
                        "replacement": replacement,
                        "type": "homophone",
                        "confidence": 0.99,
                    }],
                )
                self.assertEqual(count, 0)
                self.assertEqual(paragraph["text"], text)

    def test_reject_english_curly_apostrophe_negation_deletion(self):
        text = "This change isn’t safe for customers."
        paragraph = {"id": "p0001", "text": text}
        count, _ = _apply_edits(
            paragraph,
            [{
                "original": "isn’t safe",
                "replacement": "is safe",
                "type": "homophone",
                "confidence": 0.99,
            }],
        )
        self.assertEqual(count, 0)
        self.assertEqual(paragraph["text"], text)

    def test_accept_legitimate_local_correction_without_negation(self):
        paragraph = {"id": "p0001", "text": "我们使用 Kaden's 工具。"}
        count, _ = _apply_edits(
            paragraph,
            [{
                "original": "Kaden's",
                "replacement": "Cadence",
                "type": "proper_noun",
                "confidence": 0.99,
            }],
        )
        self.assertEqual(count, 1)
        self.assertEqual(paragraph["text"], "我们使用 Cadence 工具。")

    def test_missing_api_key(self):
        with self.assertRaises(CorrectionError):
            correct_paragraphs(
                [{"id": "p0001", "text": "Hello.", "speaker_id": "S1", "start": 0}],
                {"S1": "Speaker"},
                api_key="",
            )

    def test_cancellation_stops_following_batches(self):
        paragraphs = [
            {"id": f"p{index:04d}", "text": f"Paragraph {index}.", "speaker_id": "S1"}
            for index in range(30)
        ]
        calls = 0

        def fake_chat(*args, **kwargs):
            nonlocal calls
            calls += 1
            return [], {}

        with patch("app.correction._call_chat", side_effect=fake_chat):
            with self.assertRaises(CorrectionCancelled):
                correct_paragraphs(
                    paragraphs,
                    {"S1": "Speaker"},
                    api_key="dummy",
                    should_cancel=lambda: calls >= 1,
                )
        self.assertEqual(calls, 1)

    def _retry_cancel_case(self, first_status: int):
        calls = []
        cancelled = False

        def handle(request):
            nonlocal cancelled
            calls.append(request)
            if len(calls) == 1:
                cancelled = True
                return httpx.Response(
                    first_status,
                    request=request,
                    json={"error": "mock error"},
                )
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
                correct_paragraphs(
                    [{"id": "p1", "start": 0, "text": "Synthetic private transcript"}],
                    {},
                    "dummy-key",
                    "http://audit.invalid",
                    "mock",
                    should_cancel=lambda: cancelled,
                )
        self.assertEqual(len(calls), 1)

    def test_cancellation_blocks_retry_after_503(self):
        self._retry_cancel_case(503)

    def test_cancellation_blocks_response_format_fallback_after_400(self):
        self._retry_cancel_case(400)


if __name__ == "__main__":
    unittest.main()
