from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.formatting import build_paragraphs, clean_text


class FormattingSafetyTests(unittest.TestCase):
    def test_decades_is_not_replaced_with_glossary_off(self):
        text = "We have decades of experience."
        paragraphs = build_paragraphs(
            [{"start": 0, "end": 10, "text": text}],
            [0],
            "en",
            glossary=False,
        )
        self.assertEqual(paragraphs[0]["text"], text)
        self.assertEqual(paragraphs[0]["raw_text"], text)
        self.assertEqual(paragraphs[0]["formatted_text"], text)

    def test_decades_is_not_replaced_with_glossary_on(self):
        text = "We have decades of experience."
        paragraphs = build_paragraphs(
            [{"start": 0, "end": 10, "text": text}],
            [0],
            "en",
            glossary=True,
        )
        self.assertEqual(paragraphs[0]["text"], text)

    def test_glossary_is_opt_in_and_keeps_raw_text(self):
        text = "We use chip stack every day."
        plain = build_paragraphs(
            [{"start": 0, "end": 10, "text": text}],
            [0],
            "en",
            glossary=False,
        )
        self.assertEqual(plain[0]["text"], text)
        self.assertEqual(plain[0]["raw_text"], text)

        corrected = build_paragraphs(
            [{"start": 0, "end": 10, "text": text}],
            [0],
            "en",
            glossary=True,
        )
        self.assertEqual(corrected[0]["text"], "We use ChipStack every day.")
        self.assertEqual(corrected[0]["raw_text"], text)
        self.assertEqual(corrected[0]["formatted_text"], "We use ChipStack every day.")

    def test_clean_text_does_not_apply_glossary_by_default(self):
        self.assertEqual(clean_text("chip stack", "en"), "Chip stack.")
        self.assertEqual(clean_text("chip stack", "en", glossary=True), "ChipStack.")

    def test_paragraph_segment_indices_are_kept(self):
        paragraphs = build_paragraphs(
            [
                {"start": 0, "end": 2, "text": "Hello."},
                {"start": 2, "end": 4, "text": "World."},
            ],
            [0, 0],
            "en",
        )
        self.assertEqual(paragraphs[0]["segment_indices"], [0, 1])


if __name__ == "__main__":
    unittest.main()
