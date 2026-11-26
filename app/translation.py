from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from .config import DEFAULT_DEEPSEEK_BASE_URL, DEFAULT_DEEPSEEK_MODEL


class TranslationError(RuntimeError):
    pass


MAX_BATCH_CHARS = 5000
MAX_BATCH_ITEMS = 20


def _has_translation_text(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    texts = entry.get("texts")
    return isinstance(texts, dict) and any(
        str(value or "").strip() for value in texts.values()
    )


def translation_languages(job: dict[str, Any]) -> list[str]:
    translations = job.get("translations")
    if not isinstance(translations, dict):
        return []
    ordered = [
        language
        for language in ("简体中文", "English")
        if _has_translation_text(translations.get(language))
    ]
    ordered.extend(
        language
        for language in sorted(translations)
        if language not in ordered
        and _has_translation_text(translations.get(language))
    )
    return ordered


def translation_texts(
    job: dict[str, Any],
    paragraph: dict[str, Any],
    languages: list[str] | None = None,
) -> list[tuple[str, str]]:
    translations = job.get("translations")
    if not isinstance(translations, dict):
        return []
    paragraph_id = str(paragraph.get("id") or "")
    if not paragraph_id:
        return []

    items: list[tuple[str, str]] = []
    for language in languages if languages is not None else translation_languages(job):
        entry = translations.get(language)
        if not isinstance(entry, dict):
            continue
        texts = entry.get("texts")
        if not isinstance(texts, dict):
            continue
        text = re.sub(r"\s+", " ", str(texts.get(paragraph_id) or "")).strip()
        if text:
            items.append((language, text))
    return items


def _chat_url(base_url: str) -> str:
    base = (base_url or DEFAULT_DEEPSEEK_BASE_URL).strip().rstrip("/")
    return f"{base}/chat/completions"


def _batches(paragraphs: list[dict[str, Any]]):
    batch: list[dict[str, str]] = []
    chars = 0
    for index, paragraph in enumerate(paragraphs):
        text = str(paragraph.get("text") or "").strip()
        if not text:
            continue
        item = {
            "id": str(paragraph.get("id") or f"idx-{index}"),
            "text": text,
        }
        if batch and (
            len(batch) >= MAX_BATCH_ITEMS
            or chars + len(text) > MAX_BATCH_CHARS
        ):
            yield batch
            batch = []
            chars = 0
        batch.append(item)
        chars += len(text)
    if batch:
        yield batch


def _extract_json(text: str) -> Any:
    text = (text or "").lstrip("\ufeff").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass
    raise TranslationError("AI 返回的译文不是有效 JSON，请重试。")


def _request_batch(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    model: str,
    target_language: str,
    batch: list[dict[str, str]],
) -> dict[str, str]:
    system = (
        "You are a professional translator working on an interview transcript. "
        f"Translate every segment into {target_language}. "
        "Keep names, product names, technical terms, numbers and meaning accurate. "
        "Do not merge, split, omit, summarize or add segments. "
        "Return ONLY a JSON object with the shape "
        "{\"translations\":[{\"id\":\"p0001\",\"text\":\"...\"}]} "
        "and no markdown fences or explanations."
    )
    user = "Segments:\n\n" + json.dumps(batch, ensure_ascii=False)
    payload: dict[str, Any] = {
        "model": model or DEFAULT_DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 6000,
        "response_format": {"type": "json_object"},
    }
    if headers.get("Authorization"):
        payload["thinking"] = {"type": "disabled"}

    last_error: Exception | None = None
    data: dict[str, Any] | None = None
    for attempt in range(3):
        try:
            response = client.post(url, json=payload, headers=headers or None)
            if response.status_code in {400, 422} and "thinking" in payload:
                payload.pop("thinking", None)
                response = client.post(url, json=payload, headers=headers or None)
            if response.status_code in {400, 422} and "response_format" in payload:
                payload.pop("response_format", None)
                response = client.post(url, json=payload, headers=headers or None)
            if response.status_code == 401:
                raise TranslationError("API Key 无效或已过期，请在设置中检查。")
            if response.status_code in {429, 500, 502, 503, 504}:
                raise httpx.HTTPStatusError(
                    f"LLM HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )
            if response.status_code >= 400:
                raise TranslationError(
                    f"AI 翻译请求失败（HTTP {response.status_code}）："
                    f"{response.text[:300]}"
                )
            data = response.json()
            break
        except TranslationError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise TranslationError(
                f"无法连接翻译接口：{exc}\n"
                "请检查 API Key、Base URL 和模型 ID。"
            ) from exc
    if data is None:
        raise TranslationError(f"无法连接翻译接口：{last_error}")

    choices = data.get("choices") or [{}]
    choice = choices[0] or {}
    message = choice.get("message") or {}
    content = message.get("content") or message.get("reasoning_content") or ""
    if not content:
        if choice.get("finish_reason") == "length":
            raise TranslationError("AI 输出被截断，没有生成完整译文。请重试。")
        raise TranslationError("AI 没有返回译文。请重试。")
    if choice.get("finish_reason") == "length":
        raise TranslationError("AI 输出被截断，译文不完整。请重试。")

    parsed = _extract_json(content)
    if isinstance(parsed, dict):
        parsed = parsed.get("translations") or parsed.get("items") or []
    if isinstance(parsed, dict):
        parsed = [
            {"id": segment_id, "text": text}
            for segment_id, text in parsed.items()
        ]
    if not isinstance(parsed, list):
        raise TranslationError("AI 返回的译文格式不正确。")

    translated: dict[str, str] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        segment_id = str(item.get("id") or "").strip()
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if segment_id and text:
            translated[segment_id] = text

    expected = {item["id"] for item in batch}
    missing = expected - translated.keys()
    if missing:
        raise TranslationError(
            f"AI 未返回完整译文（缺少 {len(missing)} 段），请重试。"
        )
    return translated


def translate_paragraphs(
    paragraphs: list[dict[str, Any]],
    target_language: str,
    api_key: str = "",
    base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
    model: str = DEFAULT_DEEPSEEK_MODEL,
    timeout: float = 180.0,
) -> dict[str, str]:
    if not paragraphs:
        raise TranslationError("逐字稿为空，无法翻译。")
    language = (target_language or "").strip()
    if not language:
        raise TranslationError("请选择目标语言。")

    headers = (
        {"Authorization": f"Bearer {api_key}"}
        if (api_key or "").strip()
        else {}
    )
    texts: dict[str, str] = {}
    try:
        with httpx.Client(timeout=timeout) as client:
            for batch in _batches(paragraphs):
                texts.update(
                    _request_batch(
                        client,
                        _chat_url(base_url),
                        headers,
                        model,
                        language,
                        batch,
                    )
                )
    except TranslationError:
        raise
    except Exception as exc:
        raise TranslationError(f"翻译失败：{exc}") from exc

    if not texts:
        raise TranslationError("逐字稿为空，无法翻译。")
    return texts
