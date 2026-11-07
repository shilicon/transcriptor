from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.correction import CorrectionError, _apply_edits, correct_paragraphs


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

    def test_missing_api_key(self):
        with self.assertRaises(CorrectionError):
            correct_paragraphs(
                [{"id": "p0001", "text": "Hello.", "speaker_id": "S1", "start": 0}],
                {"S1": "Speaker"},
                api_key="",
            )


if __name__ == "__main__":
    unittest.main()
