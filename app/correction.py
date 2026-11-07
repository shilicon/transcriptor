from __future__ import annotations

import json
import os
import re
import time
from difflib import SequenceMatcher
from typing import Any, Callable

import httpx

from .config import DEFAULT_DEEPSEEK_BASE_URL, DEFAULT_DEEPSEEK_MODEL

ProgressFn = Callable[[str, float], None]

ALLOWED_TYPES = {"proper_noun", "homophone", "punctuation", "casing", "boundary", "duplicate"}
GLOSSARY_TERMS = [
    "Cadence", "EDA", "IC", "CFD", "PCB", "MSC", "BETA CAE",
    "ChipStack", "ViraStack", "InnoStack", "AuraStack", "SystemStack",
    "Sigrity", "Virtuoso", "Innovus", "Verisium", "Celsius EC",
    "Reality", "Fidelity", "Nastran", "Adams", "MuJoCo", "VTD",
    "ANSA", "META", "Intelligent System Design", "AI",
]
MAX_BATCH = 15
MAX_EDITS_PER_PARAGRAPH = 5
MIN_CONFIDENCE = 0.9
MAX_WORD_SPAN = 6


class CorrectionError(RuntimeError):
    pass


def _chat_url(base_url: str) -> str:
    base = (base_url or DEFAULT_DEEPSEEK_BASE_URL).strip().rstrip("/")
    if base.endswith("/v1"):
        return base + "/chat/completions"
    if "deepseek.com" in base:
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def _system_prompt() -> str:
    return f"""你是一个逐字稿校对员。输入是 ASR 生成的访谈逐字稿。
你的唯一任务是修正明显的识别错误，绝不改写内容。

允许的修改类型：
- proper_noun：人名、公司名、产品名、专有名词
- homophone：明显听错的词
- punctuation：标点
- casing：大小写
- boundary：句子边界
- duplicate：意外重复的词

严禁：
- 改写、润色、调整语序、总结、补充解释
- 删除口语词、语气词、重复表达
- 合并或拆分段落
- 改变说话人、数字、日期、时间戳、百分比、型号
- 用同义词替换原本合理的词
- 删除整句或增加整句

不确定时不要修改。宁可漏改，不可错改。

术语表：
{", ".join(GLOSSARY_TERMS)}

只返回 JSON，不要 markdown 代码块，不要解释。格式：
{{"edits":[{{"paragraph_id":"p0001","original":"原文片段","replacement":"修正后片段","occurrence":1,"type":"proper_noun","confidence":0.99,"note":"简短原因"}}]}}
如果没有需要修改的地方，返回 {{"edits":[]}}。"""


def _paragraph_outline(paragraphs: list[dict[str, Any]], speakers: dict[str, str]) -> str:
    lines = []
    for paragraph in paragraphs:
        speaker = speakers.get(paragraph.get("speaker_id", ""), "")
        lines.append(
            f"[{paragraph['id']}] [{_mmss(paragraph.get('start', 0))}] {speaker}: {paragraph.get('text', '')}"
        )
    return "\n".join(lines)


def _mmss(seconds: float) -> str:
    total = max(0, int(float(seconds or 0)))
    return f"{total // 60:02d}:{total % 60:02d}"


def _extract_json(text: str) -> Any:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start:end + 1])
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        return {"edits": json.loads(text[start:end + 1])}
    raise CorrectionError("AI 返回的内容不是有效 JSON。")


def _normalized_words(text: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (text or "").lower())


def _sentence_count(text: str) -> int:
    return max(1, len(re.findall(r"[.!?。！？]+", text or "")))


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?%?", text or ""))


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalized_words(a), _normalized_words(b)).ratio()


def _is_glossary(replacement: str) -> bool:
    value = (replacement or "").strip().lower()
    return any(value == term.lower() for term in GLOSSARY_TERMS)


def _find_occurrences(text: str, original: str) -> list[int]:
    if not original:
        return []
    return [match.start() for match in re.finditer(re.escape(original), text)]


def _validate_edit(
    text: str,
    edit: dict[str, Any],
) -> tuple[int, int, str] | None:
    original = str(edit.get("original") or "")
    replacement = str(edit.get("replacement") or "")
    edit_type = str(edit.get("type") or "").strip().lower()
    try:
        confidence = float(edit.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0

    if edit_type not in ALLOWED_TYPES:
        return None
    if confidence < MIN_CONFIDENCE:
        return None
    if "\n" in original or "\n" in replacement or "\r" in original or "\r" in replacement:
        return None
    if not replacement and edit_type != "punctuation":
        return None

    if edit_type in {"punctuation", "casing", "boundary"}:
        if _normalized_words(original) != _normalized_words(replacement):
            return None
    elif edit_type == "duplicate":
        if _normalized_words(replacement) not in _normalized_words(original):
            return None
        if len(_normalized_words(replacement)) >= len(_normalized_words(original)):
            return None
    else:
        if len(original.split()) > MAX_WORD_SPAN or len(replacement.split()) > MAX_WORD_SPAN:
            return None
        if _similarity(original, replacement) < 0.45 and not _is_glossary(replacement):
            return None

    positions = _find_occurrences(text, original)
    if not positions:
        return None
    occurrence = edit.get("occurrence")
    if len(positions) > 1:
        if occurrence is None:
            return None
        try:
            occurrence_index = int(occurrence) - 1
        except (TypeError, ValueError):
            return None
        if occurrence_index < 0 or occurrence_index >= len(positions):
            return None
        position = positions[occurrence_index]
    else:
        position = positions[0]

    candidate = text[:position] + replacement + text[position + len(original):]
    old_sentences = _sentence_count(text)
    new_sentences = _sentence_count(candidate)
    if edit_type in {"punctuation", "boundary"}:
        if abs(new_sentences - old_sentences) > 1:
            return None
    elif new_sentences != old_sentences:
        return None
    min_ratio = 0.70 if edit_type == "duplicate" else 0.80
    if len(candidate) and not (min_ratio <= len(candidate) / max(1, len(text)) <= 1.20):
        return None
    if _numbers(candidate) != _numbers(text):
        return None

    return position, position + len(original), edit_type


def _apply_edits(paragraph: dict[str, Any], edits: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
    text = str(paragraph.get("text") or "")
    original_text = str(paragraph.get("original_text") or text)
    accepted: list[dict[str, Any]] = []

    planned: list[tuple[int, int, dict[str, Any]]] = []
    for edit in edits:
        validated = _validate_edit(text, edit)
        if not validated:
            continue
        start, end, edit_type = validated
        planned.append((start, end, {**edit, "type": edit_type}))

    # Apply from the end so earlier offsets stay valid.
    planned.sort(key=lambda item: item[0], reverse=True)
    used_ranges: list[tuple[int, int]] = []
    for start, end, edit in planned:
        if len(accepted) >= MAX_EDITS_PER_PARAGRAPH:
            break
        if any(not (end <= used_start or start >= used_end) for used_start, used_end in used_ranges):
            continue
        current = str(paragraph.get("text") or "")
        # Re-check against the current text in case earlier edits changed it.
        validated = _validate_edit(current, edit)
        if not validated:
            continue
        current_start, current_end, _ = validated
        original = str(edit.get("original") or "")
        replacement = str(edit.get("replacement") or "")
        paragraph["text"] = current[:current_start] + replacement + current[current_end:]
        used_ranges.append((current_start, current_end))
        accepted.append(
            {
                "from": original,
                "to": replacement,
                "type": edit["type"],
                "confidence": edit.get("confidence"),
                "note": edit.get("note") or "",
            }
        )

    if accepted:
        paragraph["original_text"] = original_text
        paragraph["correction_edits"] = accepted
        paragraph["correction_status"] = "corrected"
    else:
        paragraph.setdefault("original_text", original_text)
        paragraph["correction_status"] = "unchanged"
    return len(accepted), accepted


def _call_chat(
    paragraphs: list[dict[str, Any]],
    speakers: dict[str, str],
    api_key: str,
    base_url: str,
    model: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = {
        "model": model or DEFAULT_DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": _system_prompt()},
            {
                "role": "user",
                "content": "逐字稿片段：\n\n" + _paragraph_outline(paragraphs, speakers),
            },
        ],
        "temperature": 0.0,
        "max_tokens": 2500,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = _chat_url(base_url)

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=120) as client:
                response = client.post(url, headers=headers, json=payload)
                if response.status_code == 400 and "response_format" in payload:
                    payload.pop("response_format", None)
                    response = client.post(url, headers=headers, json=payload)
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise httpx.HTTPStatusError(
                        f"DeepSeek HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                if response.status_code == 401:
                    raise CorrectionError("DeepSeek API Key 无效或已过期。")
                if response.status_code >= 400:
                    detail = response.text[:500]
                    raise CorrectionError(f"DeepSeek 请求失败（HTTP {response.status_code}）：{detail}")
                data = response.json()
        except CorrectionError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise CorrectionError(f"无法连接 DeepSeek API：{exc}") from exc

        message = (data.get("choices") or [{}])[0].get("message") or {}
        content = message.get("content") or message.get("reasoning_content") or ""
        if not content:
            raise CorrectionError("DeepSeek 没有返回校正内容。")
        parsed = _extract_json(content)
        if isinstance(parsed, list):
            edits = parsed
        elif isinstance(parsed, dict):
            edits = parsed.get("edits") or []
        else:
            edits = []
        if not isinstance(edits, list):
            raise CorrectionError("DeepSeek 返回的 edits 格式不正确。")
        return edits, data.get("usage") or {}

    raise CorrectionError(f"无法连接 DeepSeek API：{last_error}")


def correct_paragraphs(
    paragraphs: list[dict[str, Any]],
    speakers: dict[str, str],
    api_key: str,
    base_url: str = "",
    model: str = "",
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    if not api_key:
        raise CorrectionError(
            "未配置 DeepSeek API Key。请在设置中填写，或关闭 AI 校正。"
        )
    if not paragraphs:
        return {"applied": 0, "rejected": 0, "errors": [], "batches": 0, "model": model}

    applied = 0
    rejected = 0
    batches = 0
    errors: list[str] = []
    total = len(paragraphs)

    for offset in range(0, total, MAX_BATCH):
        batch = paragraphs[offset: offset + MAX_BATCH]
        batches += 1
        try:
            edits, _usage = _call_chat(batch, speakers, api_key, base_url, model)
        except CorrectionError as exc:
            errors.append(str(exc))
            continue

        by_id = {p["id"]: p for p in batch}
        grouped: dict[str, list[dict[str, Any]]] = {}
        for edit in edits:
            if not isinstance(edit, dict):
                rejected += 1
                continue
            paragraph_id = str(edit.get("paragraph_id") or "")
            if paragraph_id not in by_id:
                rejected += 1
                continue
            grouped.setdefault(paragraph_id, []).append(edit)

        for paragraph_id, paragraph_edits in grouped.items():
            count, _accepted = _apply_edits(by_id[paragraph_id], paragraph_edits)
            applied += count
            rejected += max(0, len(paragraph_edits) - count)

        if progress:
            pct = 88 + 8 * min(1.0, (offset + len(batch)) / total)
            progress("正在 AI 校正", pct)

    if applied == 0 and errors:
        raise CorrectionError(errors[0])
    return {
        "applied": applied,
        "rejected": rejected,
        "errors": errors,
        "batches": batches,
        "model": model or DEFAULT_DEEPSEEK_MODEL,
    }
