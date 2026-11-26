from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from .utils import format_ts, parse_ts


class ChapterError(RuntimeError):
    pass


def _unique_title(title: str, used: set[str]) -> str:
    title = re.sub(r"\s+", " ", title).strip(" #*-—:：")
    if not title:
        title = "未命名章节"
    title = title[:40]
    if title not in used:
        used.add(title)
        return title
    for i in range(2, 10):
        candidate = f"{title}（{i}）"
        if candidate not in used:
            used.add(candidate)
            return candidate
    used.add(title)
    return title


def _guess_title(text: str, index: int, used: set[str]) -> str:
    lowered = text.lower()
    rules = [
        (("data center", "thermal", "cooling", "hvac", "reality"), "数据中心与散热"),
        (("car", "automotive", "vehicle", "crash", "porsche", "volvo"), "汽车与仿真"),
        (("china", "chinese"), "中国市场"),
        (("super agent", "chipstack", "innostack", "virastack", "aurastack", "systemstack"), "AI 智能体"),
        (("packag", "pcb", "board", "allegro", "sigrity"), "封装与 PCB"),
        (("hiring", "hire", "talent", "engineer", "fundamental", "degree"), "工程师与人才"),
        (("optimization ai", "surrogate", "model", "ai can"), "AI 与仿真"),
        (("simulation", "cfd", "nastran", "adams", "mujoco"), "仿真与多物理场"),
    ]
    for keywords, title in rules:
        if any(keyword in lowered for keyword in keywords):
            return _unique_title(title, used)
    return _unique_title(f"第 {index + 1} 部分", used)


def make_basic_chapters(
    paragraphs: list[dict[str, Any]],
    duration: float,
) -> list[dict[str, Any]]:
    if not paragraphs:
        return []
    if len(paragraphs) <= 3 or duration < 360:
        return [{"start": 0.0, "title": "全文"}]

    target = max(3, min(10, round(duration / 480)))
    starts: list[float] = [0.0]
    last_index = 0
    for i in range(1, target):
        target_time = duration * i / target
        candidates = [
            (idx, p["start"])
            for idx, p in enumerate(paragraphs)
            if p["start"] <= target_time and idx >= last_index + 2
        ]
        if not candidates:
            continue
        idx, start = max(candidates, key=lambda item: item[1])
        if start - starts[-1] < 90:
            continue
        starts.append(float(start))
        last_index = idx

    if len(starts) < 2:
        return [{"start": 0.0, "title": "全文"}]

    used: set[str] = set()
    chapters: list[dict[str, Any]] = []
    for i, start in enumerate(starts):
        source = next((p["text"] for p in paragraphs if p["start"] >= start), "")
        chapters.append({"start": start, "title": _guess_title(source, i, used)})
    return chapters


def _outline(paragraphs: list[dict[str, Any]], speakers: dict[str, str]) -> str:
    # Keep the prompt compact for long interviews while retaining coverage.
    items = paragraphs
    if len(items) > 180:
        step = max(1, len(items) // 180)
        items = items[::step]
        if paragraphs[-1] not in items:
            items.append(paragraphs[-1])
    lines = []
    for p in items:
        text = re.sub(r"\s+", " ", p["text"]).strip()
        if len(text) > 110:
            text = text[:107].rstrip() + "..."
        name = speakers.get(p.get("speaker_id", ""), p.get("speaker_name", "Speaker"))
        lines.append(f"[{format_ts(p['start'])}] {name}: {text}")
    return "\n".join(lines)


def _discover_model(
    client: httpx.Client,
    base_url: str,
    headers: dict[str, str] | None = None,
) -> str:
    try:
        response = client.get(
            f"{base_url.rstrip('/')}/models",
            headers=headers or None,
        )
        response.raise_for_status()
        data = response.json()
        models = data.get("data") or []
        ids = [str(item.get("id") or "") for item in models if item.get("id")]
        if ids:
            # Prefer non-reasoning chat models.  Qwen reasoning models often
            # spend the entire token budget on reasoning_content.
            preferred = [
                model_id for model_id in ids
                if any(key in model_id.lower() for key in ("gemma", "glm", "llama", "mistral", "phi"))
            ]
            return preferred[0] if preferred else ids[0]
    except Exception:
        pass
    return ""


_MISSING = object()


def _json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return _MISSING


def _without_trailing_commas(text: str) -> str:
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _balanced_json(text: str, start: int) -> str | None:
    """Return the complete JSON object/array beginning at ``start``."""
    opener = text[start]
    closer = "]" if opener == "[" else "}"
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def _parse_json_candidate(candidate: str) -> Any:
    for value in (candidate, _without_trailing_commas(candidate)):
        parsed = _json_loads(value)
        if parsed is not _MISSING:
            if isinstance(parsed, str):
                nested = _json_loads(parsed)
                if nested is not _MISSING:
                    return nested
            return parsed
    return _MISSING


def _extract_json(text: str) -> Any:
    text = (text or "").lstrip("\ufeff").strip()
    candidates = [text]
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(
            r"```(?:json)?\s*(.*?)```",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )
    for candidate in candidates:
        parsed = _parse_json_candidate(candidate)
        if parsed is not _MISSING:
            return parsed

    # Models sometimes explain their answer before the JSON and mention
    # timestamps such as "[00:00]".  Try every balanced object/array instead
    # of blindly taking the first "[" and last "]".
    for opener in ("[", "{"):
        offset = 0
        while True:
            start = text.find(opener, offset)
            if start < 0:
                break
            block = _balanced_json(text, start)
            if block:
                parsed = _parse_json_candidate(block)
                if parsed is not _MISSING:
                    return parsed
            offset = start + 1
    raise ChapterError("AI 返回的内容不是有效 JSON。请重试。")


def _snap_start(value: Any, paragraph_starts: list[float]) -> float:
    start = parse_ts(value)
    if start is None:
        return 0.0
    if not paragraph_starts:
        return start
    return float(min(paragraph_starts, key=lambda candidate: abs(candidate - start)))


def auto_chapters_llm(
    paragraphs: list[dict[str, Any]],
    speakers: dict[str, str],
    endpoint: str,
    model: str = "",
    title_language: str = "zh",
    timeout: float = 180.0,
    api_key: str = "",
    instructions: str = "",
) -> list[dict[str, Any]]:
    if not paragraphs:
        return []
    endpoint = (endpoint or "").strip().rstrip("/")
    if not endpoint:
        raise ChapterError("未配置 LLM 接口地址。")

    custom = re.sub(r"\s+", " ", (instructions or "")).strip()[:500]
    max_chapters = 30 if custom else 12
    merge_window = 15.0 if custom else 45.0
    language_instruction = (
        "章节标题使用简体中文，简洁、具体，不超过 18 个汉字。"
        if title_language != "en"
        else "Write chapter titles in English, concise and specific, at most 10 words."
    )
    range_instruction = (
        "Follow the additional user requirements below for chapter granularity, "
        "with up to 30 chapters."
        if custom
        else "Divide it into 6 to 12 chapters."
    )
    system = (
        "You are an expert interview editor. You receive a timestamped transcript outline. "
        + range_instruction
        + " Return ONLY a JSON object with the shape "
        "{\"chapters\":[{\"start\":\"MM:SS\",\"title\":\"...\"}]}, with no markdown fences. "
        "The first chapter must start at 00:00. Every start timestamp must be one of the "
        "timestamps that appears in the outline. Do not repeat timestamps. "
        "Chapters should cover the whole interview in order. "
        + language_instruction
    )
    if custom:
        system += (
            " Additional requirements from the user for chapter granularity and titles: "
            + custom
            + " Follow these requirements, but never change the required JSON shape, "
            "timestamp rules, or output format."
        )
    user = "Transcript outline:\n\n" + _outline(paragraphs, speakers)

    payload: dict[str, Any] = {
        "model": model or "",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 4000 if custom else 2500,
        "response_format": {"type": "json_object"},
    }
    if (api_key or "").strip():
        # DeepSeek reasoning models otherwise spend output tokens on
        # reasoning_content, which can truncate the chapter JSON.
        payload["thinking"] = {"type": "disabled"}
    headers = (
        {"Authorization": f"Bearer {api_key}"}
        if (api_key or "").strip()
        else {}
    )
    url = f"{endpoint}/chat/completions"
    data: dict[str, Any] | None = None
    last_error: Exception | None = None

    try:
        with httpx.Client(timeout=timeout) as client:
            if not payload["model"]:
                payload["model"] = _discover_model(client, endpoint, headers)
            if not payload["model"]:
                raise ChapterError(
                    "LLM 接口没有可用模型。请先在 LM Studio / Ollama 中加载一个模型，"
                    "或在 DeepSeek 配置中填写模型 ID。"
                )
            for attempt in range(3):
                try:
                    response = client.post(url, json=payload, headers=headers or None)
                    if response.status_code in {400, 422} and "thinking" in payload:
                        # `thinking` is DeepSeek-specific; retain JSON mode if
                        # the server supports it.
                        payload.pop("thinking", None)
                        response = client.post(
                            url,
                            json=payload,
                            headers=headers or None,
                        )
                    if response.status_code in {400, 422} and "response_format" in payload:
                        # Older OpenAI-compatible servers reject JSON mode.
                        payload.pop("response_format", None)
                        response = client.post(
                            url,
                            json=payload,
                            headers=headers or None,
                        )
                    if response.status_code == 401:
                        raise ChapterError("API Key 无效或已过期，请在设置中检查。")
                    if response.status_code in {429, 500, 502, 503, 504}:
                        raise httpx.HTTPStatusError(
                            f"LLM HTTP {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                    if response.status_code >= 400:
                        detail = response.text[:300]
                        raise ChapterError(
                            f"AI 请求失败（HTTP {response.status_code}）：{detail}"
                        )
                    data = response.json()
                    break
                except ChapterError:
                    raise
                except Exception as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(2 * (attempt + 1))
                        continue
                    raise ChapterError(
                        f"无法连接 LLM 接口：{exc}\n"
                        "请确认 LM Studio / Ollama 的本地服务已启动，或检查 "
                        "DeepSeek API Key、模型 ID 和 Base URL。"
                    ) from exc
            if data is None:
                raise ChapterError(f"无法连接 LLM 接口：{last_error}")
    except ChapterError:
        raise
    except Exception as exc:
        raise ChapterError(
            f"无法连接 LLM 接口：{exc}\n"
            "请确认 LM Studio / Ollama 的本地服务已启动，或检查 DeepSeek API Key、"
            "模型 ID 和 Base URL。"
        ) from exc

    choices = data.get("choices") or [{}]
    choice = choices[0] or {}
    message = choice.get("message") or {}
    content = message.get("content") or message.get("reasoning_content") or ""
    if not content:
        if choice.get("finish_reason") == "length":
            raise ChapterError("AI 输出被截断，没有生成完整章节。请重试。")
        raise ChapterError(
            "LLM 没有返回章节内容。如果使用的是 Qwen 等推理模型，请换用非推理模型"
            "（例如 Gemma），或在 LM Studio 中关闭 thinking。"
        )

    if choice.get("finish_reason") == "length":
        raise ChapterError("AI 输出被截断，章节 JSON 不完整。请重试。")

    parsed = _extract_json(content)
    if isinstance(parsed, dict):
        parsed = parsed.get("chapters") or parsed.get("items") or []
    if not isinstance(parsed, list):
        raise ChapterError("AI 返回的章节格式不正确。")

    paragraph_starts = [float(p["start"]) for p in paragraphs]
    used_titles: set[str] = set()
    chapters: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        if not title:
            continue
        start = _snap_start(item.get("start") or item.get("time"), paragraph_starts)
        chapters.append({"start": start, "title": _unique_title(title, used_titles)})

    duration = float(paragraphs[-1].get("end") or paragraphs[-1].get("start") or 0)
    if len(chapters) < 2 and duration >= 360:
        raise ChapterError("LLM 只返回了不足两个章节，请重试。")

    chapters.sort(key=lambda c: c["start"])
    # Merge chapters that start too close together, keeping the later title.
    cleaned: list[dict[str, Any]] = []
    for chapter in chapters:
        if cleaned and chapter["start"] - cleaned[-1]["start"] < merge_window:
            cleaned[-1]["title"] = chapter["title"]
            continue
        cleaned.append(chapter)
    if cleaned:
        cleaned[0]["start"] = 0.0
    if len(cleaned) < 2 and duration >= 360:
        raise ChapterError("章节时间点过于集中，请重试。")
    return _merge_chapters(cleaned, max_chapters)


def _merge_chapters(chapters: list[dict[str, Any]], max_count: int) -> list[dict[str, Any]]:
    """Merge the closest chapter starts until the list fits max_count."""
    chapters = list(chapters)
    while len(chapters) > max_count:
        gaps = [
            (float(chapters[i + 1]["start"]) - float(chapters[i]["start"]), i)
            for i in range(len(chapters) - 1)
        ]
        _, index = min(gaps)
        chapters.pop(index + 1)
    return chapters
