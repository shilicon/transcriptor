from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.download import DownloadError, _download_options, normalize_youtube_url
from app.subtitles import (
    render_srt,
    render_vtt,
    subtitle_lines,
    sync_subtitle_segments,
)


def sample_job():
    return {
        "title": "测试",
        "speakers": [{"id": "S1", "name": "张三"}],
        "segments": [
            {"start": 0.0, "end": 2.5, "text": "第一句话。", "speaker_id": "S1"},
            {"start": 2.5, "end": 5.0, "text": "第二句话。", "speaker_id": "S1"},
        ],
        "paragraphs": [
            {
                "id": "p0001",
                "start": 0.0,
                "end": 5.0,
                "text": "第一句话。第二句话。",
                "speaker_id": "S1",
                "segment_indices": [0, 1],
            }
        ],
    }


class YouTubeURLTests(unittest.TestCase):
    def test_accepts_common_youtube_urls(self):
        urls = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://m.youtube.com/shorts/abc123",
            "www.youtube.com/watch?v=dQw4w9WgXcQ",
            "music.youtube.com/watch?v=dQw4w9WgXcQ",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertTrue(normalize_youtube_url(url).startswith("https://"))

    def test_rejects_non_youtube_and_playlists(self):
        for url in [
            "https://example.com/video",
            "ftp://youtube.com/watch?v=abc",
            "https://www.youtube.com/playlist?list=PL123",
            "https://www.youtube.com/@somechannel",
        ]:
            with self.subTest(url=url):
                with self.assertRaises(DownloadError):
                    normalize_youtube_url(url)

    def test_download_only_requests_audio(self):
        options = _download_options(Path("/tmp/transcriptor-test"), None)
        self.assertEqual(options["format"], "bestaudio/best")
        self.assertTrue(options["noplaylist"])
        self.assertFalse(options["writesubtitles"])
        self.assertFalse(options["writeautomaticsub"])
        self.assertFalse(options["writethumbnail"])
        self.assertFalse(options["writeinfojson"])


class SubtitleExportTests(unittest.TestCase):
    def test_srt_format(self):
        text = render_srt(sample_job())
        self.assertIn("1\n00:00:00,000 --> 00:00:02,500", text)
        self.assertIn("第一句话。", text)
        self.assertIn("2\n00:00:02,500 --> 00:00:05,000", text)
        self.assertTrue(text.endswith("\n"))

    def test_vtt_format(self):
        text = render_vtt(sample_job())
        self.assertTrue(text.startswith("WEBVTT\n"))
        self.assertIn("00:00:00.000 --> 00:00:02.500", text)
        self.assertIn("第一句话。", text)

    def test_fallback_to_paragraphs(self):
        job = sample_job()
        job["segments"] = []
        lines = subtitle_lines(job)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0][0], 0.0)
        self.assertEqual(lines[0][1], 5.0)
        self.assertIn("第一句话", lines[0][2])

    def test_long_segment_is_split(self):
        job = sample_job()
        job["segments"] = [
            {
                "start": 0.0,
                "end": 12.0,
                "text": "这是一段很长的字幕文本，需要在标点附近切分，" * 4,
                "speaker_id": "S1",
            }
        ]
        lines = subtitle_lines(job)
        self.assertGreater(len(lines), 1)
        self.assertEqual(lines[0][0], 0.0)
        self.assertAlmostEqual(lines[-1][1], 12.0, places=2)
        self.assertTrue(all(len(item[2]) <= 42 for item in lines))

    def test_sync_after_text_edit(self):
        job = sample_job()
        job["paragraphs"][0]["text"] = "第一句改过了。第二句话也改了。"
        sync_subtitle_segments(job, ["p0001"])
        combined = "".join(segment["text"] for segment in job["segments"])
        self.assertIn("第一句改过了", combined)
        self.assertIn("第二句话也改了", combined)

    def test_sync_updates_speaker(self):
        job = sample_job()
        job["paragraphs"][0]["speaker_id"] = "S2"
        sync_subtitle_segments(job, ["p0001"])
        self.assertTrue(all(segment["speaker_id"] == "S2" for segment in job["segments"]))


if __name__ == "__main__":
    unittest.main()
