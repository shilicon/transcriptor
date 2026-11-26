from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import chapters


PARAGRAPHS = [
    {"id": "p1", "start": 0.0, "end": 9.0, "speaker_id": "S1", "text": "开场内容"},
    {"id": "p2", "start": 600.0, "end": 609.0, "speaker_id": "S1", "text": "后半段内容"},
]
CHAPTERS = [
    {"start": "00:00", "title": "开场"},
    {"start": "10:00", "title": "后半段"},
]
FINE_CHAPTERS = [
    {"start": f"{index * 5:02d}:00", "title": f"第 {index + 1} 节"}
    for index in range(15)
]
FINE_PARAGRAPHS = [
    {
        "id": f"p{index}",
        "start": index * 300.0,
        "end": index * 300.0 + 9.0,
        "speaker_id": "S1",
        "text": f"第 {index + 1} 段内容",
    }
    for index in range(15)
]


def _success(content: str, finish_reason: str = "stop") -> dict:
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"content": content},
            }
        ]
    }


class ChapterLLMTests(unittest.TestCase):
    def _run(
        self,
        api_key: str,
        content: str | None = None,
        instructions: str = "",
        paragraphs: list | None = None,
    ):
        seen = {}

        def handle(request):
            seen["authorization"] = request.headers.get("authorization")
            seen["url"] = str(request.url)
            seen["payload"] = json.loads(request.content)
            return httpx.Response(
                200,
                request=request,
                json=_success(
                    content
                    if content is not None
                    else json.dumps(CHAPTERS, ensure_ascii=False)
                ),
            )

        transport = httpx.MockTransport(handle)
        real_client = httpx.Client

        def mock_client(*args, **kwargs):
            return real_client(*args, transport=transport, **kwargs)

        with patch.object(chapters.httpx, "Client", side_effect=mock_client):
            result = chapters.auto_chapters_llm(
                paragraphs or PARAGRAPHS,
                {"S1": "讲者"},
                "https://api.deepseek.com",
                "deepseek-flash",
                "zh",
                api_key=api_key,
                instructions=instructions,
            )
        return seen, result

    def test_deepseek_api_key_is_sent_as_bearer(self):
        seen, result = self._run("sk-synthetic")
        self.assertEqual(seen["authorization"], "Bearer sk-synthetic")
        self.assertEqual(seen["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(seen["payload"]["response_format"], {"type": "json_object"})
        self.assertEqual(seen["payload"]["thinking"], {"type": "disabled"})
        self.assertEqual(len(result), 2)

    def test_local_llm_without_key_sends_no_authorization(self):
        seen, result = self._run("")
        self.assertIsNone(seen["authorization"])
        self.assertNotIn("thinking", seen["payload"])
        self.assertEqual(len(result), 2)

    def test_json_object_with_leading_timestamp_prose_is_parsed(self):
        content = (
            "参考 [00:00] 和 [10:00] 后整理如下：\n"
            + json.dumps({"chapters": CHAPTERS}, ensure_ascii=False)
            + "\n以上。"
        )
        _, result = self._run("sk-synthetic", content=content)
        self.assertEqual([chapter["title"] for chapter in result], ["开场", "后半段"])

    def test_custom_instructions_allow_finer_chapters(self):
        content = json.dumps({"chapters": FINE_CHAPTERS}, ensure_ascii=False)
        seen, result = self._run(
            "sk-synthetic",
            content=content,
            instructions="每 5 分钟一章，按技术主题分得更细",
            paragraphs=FINE_PARAGRAPHS,
        )
        self.assertEqual(len(result), 15)
        system = seen["payload"]["messages"][0]["content"]
        self.assertIn("每 5 分钟一章", system)
        self.assertIn("up to 30 chapters", system)
        self.assertEqual(seen["payload"]["max_tokens"], 4000)

    def test_default_chapters_remain_capped_at_12(self):
        content = json.dumps({"chapters": FINE_CHAPTERS}, ensure_ascii=False)
        seen, result = self._run(
            "sk-synthetic",
            content=content,
            paragraphs=FINE_PARAGRAPHS,
        )
        self.assertEqual(len(result), 12)
        system = seen["payload"]["messages"][0]["content"]
        self.assertIn("6 to 12 chapters", system)
        self.assertEqual(seen["payload"]["max_tokens"], 2500)

    def test_fenced_json_object_is_parsed(self):
        content = "```json\n" + json.dumps(
            {"chapters": CHAPTERS},
            ensure_ascii=False,
        ) + "\n```"
        _, result = self._run("sk-synthetic", content=content)
        self.assertEqual(len(result), 2)

    def test_retries_without_optional_parameters_on_400(self):
        calls = []

        def handle(request):
            payload = json.loads(request.content)
            calls.append(payload)
            if "response_format" in payload or "thinking" in payload:
                return httpx.Response(
                    400,
                    request=request,
                    json={"detail": "unsupported parameter"},
                )
            return httpx.Response(
                200,
                request=request,
                json=_success(json.dumps(CHAPTERS, ensure_ascii=False)),
            )

        transport = httpx.MockTransport(handle)
        real_client = httpx.Client

        def mock_client(*args, **kwargs):
            return real_client(*args, transport=transport, **kwargs)

        with patch.object(chapters.httpx, "Client", side_effect=mock_client):
            result = chapters.auto_chapters_llm(
                PARAGRAPHS,
                {"S1": "讲者"},
                "http://127.0.0.1:1234/v1",
                "local-model",
                "zh",
            )
        self.assertEqual(len(calls), 2)
        self.assertNotIn("response_format", calls[-1])
        self.assertNotIn("thinking", calls[-1])
        self.assertEqual(len(result), 2)

    def test_retries_without_thinking_but_keeps_json_mode(self):
        calls = []

        def handle(request):
            payload = json.loads(request.content)
            calls.append(payload)
            if "thinking" in payload:
                return httpx.Response(
                    400,
                    request=request,
                    json={"detail": "unsupported parameter"},
                )
            return httpx.Response(
                200,
                request=request,
                json=_success(json.dumps({"chapters": CHAPTERS}, ensure_ascii=False)),
            )

        transport = httpx.MockTransport(handle)
        real_client = httpx.Client

        def mock_client(*args, **kwargs):
            return real_client(*args, transport=transport, **kwargs)

        with patch.object(chapters.httpx, "Client", side_effect=mock_client):
            result = chapters.auto_chapters_llm(
                PARAGRAPHS,
                {"S1": "讲者"},
                "https://api.deepseek.com",
                "deepseek-flash",
                "zh",
                api_key="sk-synthetic",
            )
        self.assertEqual(len(calls), 2)
        self.assertNotIn("thinking", calls[-1])
        self.assertEqual(calls[-1]["response_format"], {"type": "json_object"})
        self.assertEqual(len(result), 2)

    def test_truncated_response_reports_length_error(self):
        def handle(request):
            return httpx.Response(
                200,
                request=request,
                json=_success('[{"start":"00:00","title":"开场"},', "length"),
            )

        transport = httpx.MockTransport(handle)
        real_client = httpx.Client

        def mock_client(*args, **kwargs):
            return real_client(*args, transport=transport, **kwargs)

        with patch.object(chapters.httpx, "Client", side_effect=mock_client):
            with self.assertRaisesRegex(chapters.ChapterError, "截断"):
                chapters.auto_chapters_llm(
                    PARAGRAPHS,
                    {"S1": "讲者"},
                    "https://api.deepseek.com",
                    "deepseek-flash",
                    "zh",
                    api_key="sk-synthetic",
                )


if __name__ == "__main__":
    unittest.main()
