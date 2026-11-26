from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docx import Document

from app import main
from app.exporters import render_markdown, write_exports
from app.subtitles import render_srt, render_vtt
from app.utils import save_job
from tests.support import isolated_jobs_dir, make_job


def translated_job():
    job = make_job("exports", "done", text="Original approved text.")
    job["translations"] = {
        "English": {
            "model": "deepseek-flash",
            "created_at": "2026-11-11T00:00:00+08:00",
            "texts": {"p0001": "Translated approved text."},
        }
    }
    return job


class TranslationExportTests(unittest.TestCase):
    def test_markdown_includes_translation_without_per_paragraph_labels(self):
        markdown = render_markdown(translated_job())
        self.assertIn("Original approved text.", markdown)
        self.assertIn("> Translated approved text.", markdown)
        self.assertNotIn("译文（English）", markdown)
        self.assertIn("**译文：** English", markdown)

    def test_srt_and_vtt_include_bilingual_cues(self):
        job = translated_job()
        srt = render_srt(job)
        vtt = render_vtt(job)
        self.assertIn("Original approved text.\nTranslated approved text.", srt)
        self.assertIn("Original approved text.\nTranslated approved text.", vtt)

    def test_write_exports_includes_translation_in_all_files(self):
        job = translated_job()
        with tempfile.TemporaryDirectory(prefix="transcriptor-export-test-") as tmp:
            folder = Path(tmp)
            files = write_exports(job, folder)

            markdown = (folder / files["md"]).read_text(encoding="utf-8")
            srt = (folder / files["srt"]).read_text(encoding="utf-8")
            vtt = (folder / files["vtt"]).read_text(encoding="utf-8")
            self.assertIn("Translated approved text.", markdown)
            self.assertIn("Translated approved text.", srt)
            self.assertIn("Translated approved text.", vtt)

            document = Document(str(folder / files["docx"]))
            word_text = "\n".join(
                paragraph.text for paragraph in document.paragraphs
            )
            self.assertIn("Original approved text.", word_text)
            self.assertIn("Translated approved text.", word_text)
            self.assertNotIn("译文（English）", word_text)

    def test_export_can_exclude_translation(self):
        with isolated_jobs_dir():
            job_id = "export-original"
            save_job(translated_job() | {"id": job_id})
            _, md_path = main._prepare_export(job_id, "md", False, None)
            markdown = md_path.read_text(encoding="utf-8")
            self.assertIn("Original approved text.", markdown)
            self.assertNotIn("Translated approved text.", markdown)
            self.assertNotIn("**译文：**", markdown)

    def test_export_can_select_one_translation_language(self):
        with isolated_jobs_dir():
            job_id = "export-language"
            job = translated_job() | {"id": job_id}
            job["translations"]["简体中文"] = {
                "texts": {"p0001": "翻译后的文本。"}
            }
            save_job(job)
            _, md_path = main._prepare_export(
                job_id,
                "md",
                True,
                "简体中文",
            )
            markdown = md_path.read_text(encoding="utf-8")
            self.assertIn("翻译后的文本。", markdown)
            self.assertNotIn("Translated approved text.", markdown)


if __name__ == "__main__":
    unittest.main()
