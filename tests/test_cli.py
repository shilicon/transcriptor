from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cli import build_parser


class CLITest(unittest.TestCase):
    def test_file_options_match_new_job_fields(self):
        args = build_parser().parse_args([
            "--file", "interview.mp4",
            "--title", "产品访谈",
            "--speakers", "主持人,嘉宾",
            "--num-speakers", "2",
            "--language", "zh",
            "--model", "turbo",
            "--initial-prompt", "产品名：Transcriptor",
            "--no-ai-correction",
            "--no-chapters",
            "--deepseek-base-url", "http://localhost:8000",
            "--deepseek-model", "test-model",
            "--deepseek-api-key", "secret",
            "--output-dir", "exports",
            "--output-name", "result",
            "--json",
        ])

        self.assertEqual(args.file, Path("interview.mp4"))
        self.assertEqual(args.title, "产品访谈")
        self.assertEqual(args.speakers, "主持人,嘉宾")
        self.assertEqual(args.num_speakers, 2)
        self.assertEqual(args.language, "zh")
        self.assertEqual(args.model, "turbo")
        self.assertEqual(args.initial_prompt, "产品名：Transcriptor")
        self.assertFalse(args.ai_correction)
        self.assertFalse(args.chapters)
        self.assertEqual(args.deepseek_base_url, "http://localhost:8000")
        self.assertEqual(args.deepseek_model, "test-model")
        self.assertEqual(args.deepseek_api_key, "secret")
        self.assertEqual(args.output_dir, Path("exports"))
        self.assertEqual(args.output_name, "result")
        self.assertTrue(args.json)

    def test_youtube_source_is_supported(self):
        args = build_parser().parse_args([
            "--youtube", "https://youtu.be/example",
            "--num-speakers", "1",
        ])
        self.assertIsNone(args.file)
        self.assertEqual(args.youtube, "https://youtu.be/example")
        self.assertEqual(args.num_speakers, 1)

    def test_file_and_youtube_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build_parser().parse_args([
                    "--file", "interview.mp4",
                    "--youtube", "https://youtu.be/example",
                ])


if __name__ == "__main__":
    unittest.main()
