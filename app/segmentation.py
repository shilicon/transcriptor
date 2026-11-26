from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from .config import DEFAULT_DEEPSEEK_BASE_URL, DEFAULT_DEEPSEEK_MODEL
from .formatting import TARGET_SENTENCES_PER_PARAGRAPH


class SegmentationError(RuntimeError):
    pass


# A safety ceiling.  The AI decides semantic boundaries; this only rejects
# plans that would recreate oversized paragraphs.
MAX_PARAGRAPH_SENTENCES = 12
MAX_PROMPT_CHARS = 28000


def _speaker_turns(
    segments: list[dict[str, Any]],
) -> list[list[int]]:
    turns: list[list[int]] = []
    previous_speaker: str | None = None
    for index, segment in enumerate(segments):
        speaker = str(segment.get("speaker_id") or "")
        if previous_speaker is None or speaker != previous_speaker:
            turns.append([index])
            previous_speaker = speaker
        else:
            turns[-1].append(index)
    return turns


def _chunk_turns(
    turns: list[list[int]],
    segments: list[dict[str, Any]],
):
    chunk: list[list[int]] = []
    chars = 0
    for turn in turns:
        size = 80 + sum(
            len(str(segments[index].get("text") or "")) + 20
            for index in turn
        )
        if chunk and chars + size > MAX_PROMPT_CHARS:
            yield chunk
            chunk = []
            chars = 0
        chunk.append(turn)
        chars += size
    if chunk:
        yield chunk


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
    raise SegmentationError("AI 分段返回的不是有效 JSON。")


def _repair_breaks(
    breaks: set[int],
    turns: list[list[int]],
    target_sentences: int,
) -> set[int]:
    allowed = {
        index
        for turn in turns
        for index in turn[1:]
    }
    breaks = {value for value in breaks if value in allowed}
    for turn in turns:
        starts = [turn[0]] + [
            value for value in sorted(breaks) if value in turn
        ]
        ends = starts[1:] + [turn[-1] + 1]
        for start, end in zip(starts, ends):
            if end - start <= MAX_PARAGRAPH_SENTENCES:
                continue
            # The model missed a boundary.  Keep its semantic breaks but add
            # safety breaks so one oversized group cannot survive.
            step = max(1, target_sentences)
            cursor = start + step
            while cursor < end:
                if cursor in allowed:
                    breaks.add(cursor)
                cursor += step
    return breaks


def _request_breaks(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    model: str,
    target_sentences: int,
    turns: list[list[int]],
    segments: list[dict[str, Any]],
) -> set[int]:
    payload_turns = []
    for number, turn in enumerate(turns, start=1):
        payload_turns.append(
            {
                "turn": number,
                "speaker": str(segments[turn[0]].get("speaker_id") or ""),
                "segments": [
                    {
                        "i": index,
                        "text": str(segments[index].get("text") or ""),
                    }
                    for index in turn
                ],
            }
        )

    system = (
        "You are an expert interview editor. You receive speaker turns from a "
        "transcript. Each segment is one sentence-level chunk with a stable "
        "index `i`. Decide where new paragraphs should start. Break only at "
        "genuine topic, argument or narrative shifts; do not split mechanically "
        "every N sentences. Aim for about "
        f"{target_sentences} sentences per paragraph and never exceed "
        f"{MAX_PARAGRAPH_SENTENCES}. Never place a break between a sentence and "
        "its continuation. Never cross a speaker turn. If a turn has more "
        f"than {MAX_PARAGRAPH_SENTENCES} segments, you MUST return enough "
        "breaks so every resulting paragraph stays within that limit. Return "
        "ONLY a JSON object with the shape {\"breaks\":[index,...]}, where "
        "each index is the first segment of a new paragraph. Do not return a "
        "turn's first segment. Do not add markdown or explanations."
    )
    user = "Speaker turns:\n\n" + json.dumps(
        payload_turns,
        ensure_ascii=False,
    )
    payload: dict[str, Any] = {
        "model": model or DEFAULT_DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.1,
        "max_tokens": 2000,
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
                raise SegmentationError("API Key 无效或已过期。")
            if response.status_code in {429, 500, 502, 503, 504}:
                raise httpx.HTTPStatusError(
                    f"LLM HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )
            if response.status_code >= 400:
                raise SegmentationError(
                    f"AI 分段请求失败（HTTP {response.status_code}）："
                    f"{response.text[:300]}"
                )
            data = response.json()
            break
        except SegmentationError:
            raise
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise SegmentationError(f"无法连接 AI 分段接口：{exc}") from exc
    if data is None:
        raise SegmentationError(f"无法连接 AI 分段接口：{last_error}")

    choices = data.get("choices") or [{}]
    choice = choices[0] or {}
    message = choice.get("message") or {}
    content = message.get("content") or message.get("reasoning_content") or ""
    if not content:
        raise SegmentationError("AI 没有返回分段计划。")
    if choice.get("finish_reason") == "length":
        raise SegmentationError("AI 分段输出被截断。")

    parsed = _extract_json(content)
    if isinstance(parsed, dict):
        parsed = parsed.get("breaks") or parsed.get("paragraph_starts") or []
    if not isinstance(parsed, list):
        raise SegmentationError("AI 返回的分段格式不正确。")

    breaks: set[int] = set()
    for value in parsed:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            breaks.add(value)
        elif isinstance(value, str) and value.strip().isdigit():
            breaks.add(int(value.strip()))
    return _repair_breaks(breaks, turns, target_sentences)


def semantic_paragraph_breaks(
    segments: list[dict[str, Any]],
    api_key: str,
    base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
    model: str = DEFAULT_DEEPSEEK_MODEL,
    target_sentences: int = TARGET_SENTENCES_PER_PARAGRAPH,
    timeout: float = 120.0,
) -> set[int] | None:
    """Return AI-selected sentence indices that start new paragraphs.

    Only unusually long same-speaker turns are sent to the model.  Short turns
    do not need semantic splitting.  ``None`` means no AI plan is available and
    the deterministic paragraph builder should be used.
    """
    if not segments or not (api_key or "").strip():
        return None
    turns = _speaker_turns(segments)
    long_turns = [
        turn for turn in turns if len(turn) > target_sentences
    ]
    if not long_turns:
        return set()

    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }
    url = f"{(base_url or DEFAULT_DEEPSEEK_BASE_URL).strip().rstrip('/')}/chat/completions"
    breaks: set[int] = set()
    try:
        with httpx.Client(timeout=timeout) as client:
            for chunk in _chunk_turns(long_turns, segments):
                breaks.update(
                    _request_breaks(
                        client,
                        url,
                        headers,
                        model,
                        target_sentences,
                        chunk,
                        segments,
                    )
                )
    except SegmentationError:
        raise
    except Exception as exc:
        raise SegmentationError(f"AI 分段失败：{exc}") from exc
    return breaks
