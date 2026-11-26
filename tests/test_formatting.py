from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.formatting import (
    TARGET_SENTENCES_PER_PARAGRAPH,
    build_paragraphs,
    clean_text,
    split_segments_at_sentences,
    split_sentences,
)
from app.utils import sentence_count


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

    def test_sentence_split_handles_chinese_punctuation_and_decimals(self):
        self.assertEqual(
            split_sentences("中文第一句。中文第二句！还有吗？"),
            ["中文第一句。", "中文第二句！", "还有吗？"],
        )
        self.assertEqual(
            split_sentences("The value is 3.5. Next sentence."),
            ["The value is 3.5.", "Next sentence."],
        )

    def test_split_segments_at_sentences_preserves_text_and_timing(self):
        original = {
            "start": 10.0,
            "end": 20.0,
            "text": "First sentence. Second sentence. A trailing fragment",
            "speaker_id": "S1",
        }
        split = split_segments_at_sentences([original])
        self.assertEqual(
            [segment["text"] for segment in split],
            ["First sentence.", "Second sentence.", "A trailing fragment"],
        )
        self.assertTrue(all(segment["speaker_id"] == "S1" for segment in split))
        self.assertEqual(split[0]["start"], 10.0)
        self.assertEqual(split[-1]["end"], 20.0)
        self.assertTrue(
            all(
                left["end"] <= right["start"] + 0.001
                for left, right in zip(split, split[1:])
            )
        )

    def test_long_speaker_turn_stays_near_ten_sentences(self):
        segments = []
        for index in range(12):
            segments.append(
                {
                    "start": index * 15.0,
                    "end": index * 15.0 + 14.0,
                    "text": (
                        "Alpha one. Beta two. Gamma three. "
                        "This fragment continues"
                    ),
                    "speaker_id": "S1",
                }
            )
        split = split_segments_at_sentences(segments)
        paragraphs = build_paragraphs(split, [0] * len(split), "en")
        self.assertGreater(len(paragraphs), 1)
        self.assertTrue(
            all(
                sentence_count(paragraph["text"])
                <= TARGET_SENTENCES_PER_PARAGRAPH
                for paragraph in paragraphs
            )
        )
        self.assertTrue(
            all(paragraph["speaker"] == 0 for paragraph in paragraphs)
        )


if __name__ == "__main__":
    unittest.main()
