from __future__ import annotations

import re
from typing import Any, Iterator

from .translation import translation_languages, translation_texts

MAX_SUBTITLE_CHARS = 42
BREAK_CHARS = "，。！？；、,.!?;:： "
_EPSILON = 0.01


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _clean_multiline(text: Any) -> str:
    return "\n".join(
        line
        for line in (_clean(part) for part in str(text or "").splitlines())
        if line
    )


def _split_text(text: str, max_chars: int = MAX_SUBTITLE_CHARS) -> list[str]:
    text = _clean(text)
    if not text:
        return []

    chunks: list[str] = []
    while len(text) > max_chars:
        window = text[:max_chars]
        cut = -1
        lower = max(8, int(max_chars * 0.55))
        for index in range(len(window) - 1, lower - 1, -1):
            if window[index] in BREAK_CHARS:
                cut = index + 1
                break
        if cut <= 0:
            for index, char in enumerate(text[max_chars:max_chars + 12]):
                if char in BREAK_CHARS:
                    cut = max_chars + index + 1
                    break
        if cut <= 0:
            cut = max_chars
        chunk = text[:cut].strip()
        if chunk:
            chunks.append(chunk)
        text = text[cut:].strip()

    if text:
        chunks.append(text)
    return chunks


def _find_cut(text: str, target: int) -> int:
    if target <= 0:
        return 1
    if target >= len(text):
        return len(text)
    for index in range(target, max(0, target // 2) - 1, -1):
        if index > 0 and text[index - 1] in BREAK_CHARS:
            return index
    for index in range(target, min(len(text), target + 10)):
        if text[index] in BREAK_CHARS:
            return index + 1
    return target


def _split_by_weights(text: str, weights: list[int]) -> list[str]:
    text = _clean(text)
    count = len(weights)
    if count <= 1:
        return [text]
    if not text:
        return [""] * count

    chunks: list[str] = []
    remaining = text
    for index in range(count - 1):
        remaining_slots = count - index - 1
        remaining_weight = sum(weights[index:]) or (count - index)
        target = round(len(remaining) * weights[index] / remaining_weight)
        target = max(1, min(len(remaining) - remaining_slots, target))
        cut = _find_cut(remaining, target)
        chunk = remaining[:cut].strip()
        chunks.append(chunk)
        remaining = remaining[cut:].strip()
    chunks.append(remaining)
    return chunks


def _timed_lines(
    text: str,
    start: float,
    end: float,
    speaker_id: str = "",
) -> Iterator[tuple[float, float, str, str]]:
    chunks = _split_text(text)
    if not chunks:
        return
    start = max(0.0, float(start or 0))
    end = float(end if end is not None else start)
    if end <= start:
        end = start + max(0.8, len(_clean(text)) * 0.12)

    total_chars = sum(len(chunk) for chunk in chunks) or len(chunks)
    span = end - start
    cursor = start
    for index, chunk in enumerate(chunks):
        if index == len(chunks) - 1:
            chunk_end = end
        else:
            chunk_end = cursor + span * len(chunk) / total_chars
        if chunk_end <= cursor:
            chunk_end = cursor + 0.2
        yield cursor, chunk_end, chunk, speaker_id
        cursor = chunk_end


def subtitle_lines(job: dict[str, Any]) -> list[tuple[float, float, str, str]]:
    segments = job.get("segments") or []
    if isinstance(segments, list) and segments:
        lines: list[tuple[float, float, str, str]] = []
        for segment in sorted(segments, key=lambda item: float(item.get("start") or 0)):
            if not isinstance(segment, dict):
                continue
            lines.extend(
                _timed_lines(
                    segment.get("text", ""),
                    float(segment.get("start") or 0),
                    float(segment.get("end") or 0),
                    str(segment.get("speaker_id") or ""),
                )
            )
        if lines:
            return lines

    lines = []
    for paragraph in sorted(
        job.get("paragraphs") or [],
        key=lambda item: float(item.get("start") or 0),
    ):
        if not isinstance(paragraph, dict):
            continue
        lines.extend(
            _timed_lines(
                paragraph.get("text", ""),
                float(paragraph.get("start") or 0),
                float(paragraph.get("end") or 0),
                str(paragraph.get("speaker_id") or ""),
            )
        )
    return lines


def _bilingual_subtitle_lines(
    job: dict[str, Any],
) -> list[tuple[float, float, str, str]] | None:
    """Build subtitle lines with the original text and available translations."""
    translations = job.get("translations")
    if not isinstance(translations, dict) or not translations:
        return None
    languages = translation_languages(job)
    if not languages:
        return None

    paragraphs = job.get("paragraphs") or []
    segments = job.get("segments") or []
    translated_by_segment: dict[int, list[str]] = {}

    if isinstance(segments, list) and segments:
        for paragraph in paragraphs:
            if not isinstance(paragraph, dict):
                continue
            items = translation_texts(job, paragraph, languages)
            if not items:
                continue
            indices = paragraph.get("segment_indices")
            if not isinstance(indices, list) or not indices:
                continue
            group: list[tuple[int, str]] = []
            for index in indices:
                if isinstance(index, int) and 0 <= index < len(segments):
                    segment = segments[index]
                    if isinstance(segment, dict):
                        group.append((index, _clean(segment.get("text"))))
            if not group:
                continue
            weights = [max(1, len(text)) for _, text in group]
            for _, translated in items:
                for (index, _), chunk in zip(
                    group,
                    _split_by_weights(translated, weights),
                ):
                    chunk = _clean(chunk)
                    if chunk:
                        translated_by_segment.setdefault(index, []).append(chunk)

        if translated_by_segment:
            translated_lines: list[tuple[float, float, str, str]] = []
            ordered = sorted(
                enumerate(segments),
                key=lambda item: (
                    float(item[1].get("start") or 0)
                    if isinstance(item[1], dict)
                    else 0.0
                ),
            )
            for index, segment in ordered:
                if not isinstance(segment, dict):
                    continue
                text = _clean(segment.get("text"))
                if not text:
                    continue
                translated = translated_by_segment.get(index) or []
                if translated:
                    text = "\n".join([text] + translated)
                translated_lines.append(
                    (
                        float(segment.get("start") or 0),
                        float(segment.get("end") or 0),
                        text,
                        str(segment.get("speaker_id") or ""),
                    )
                )
            if translated_lines:
                return translated_lines

    # Paragraph fallback for jobs without segment data.
    paragraph_lines: list[tuple[float, float, str, str]] = []
    has_translation = False
    for paragraph in sorted(
        paragraphs,
        key=lambda item: (
            float(item.get("start") or 0) if isinstance(item, dict) else 0.0
        ),
    ):
        if not isinstance(paragraph, dict):
            continue
        text = _clean(paragraph.get("text"))
        if not text:
            continue
        items = translation_texts(job, paragraph, languages)
        if items:
            has_translation = True
            text = "\n".join([text] + [translated for _, translated in items])
        start = max(0.0, float(paragraph.get("start") or 0))
        end = float(paragraph.get("end") if paragraph.get("end") is not None else start)
        if end <= start:
            end = start + max(0.8, len(text) * 0.12)
        paragraph_lines.append(
            (start, end, text, str(paragraph.get("speaker_id") or ""))
        )
    return paragraph_lines if has_translation else None


def _timestamp(seconds: float, separator: str) -> str:
    milliseconds = int(round(max(0.0, float(seconds)) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def _speaker_name(job: dict[str, Any], speaker_id: str) -> str:
    for speaker in job.get("speakers") or []:
        if speaker.get("id") == speaker_id:
            return str(speaker.get("name") or "").strip()
    return ""


def render_srt(job: dict[str, Any], include_speakers: bool = False) -> str:
    lines = _bilingual_subtitle_lines(job) or subtitle_lines(job)
    output: list[str] = []
    index = 1
    for start, end, text, speaker_id in lines:
        text = _clean_multiline(text)
        if not text:
            continue
        if include_speakers:
            name = _speaker_name(job, speaker_id)
            if name:
                text = f"{name}: {text}"
        output.extend(
            [
                str(index),
                f"{_timestamp(start, ',')} --> {_timestamp(end, ',')}",
                text,
                "",
            ]
        )
        index += 1
    return "\n".join(output).strip() + ("\n" if output else "")


def render_vtt(job: dict[str, Any], include_speakers: bool = False) -> str:
    output: list[str] = ["WEBVTT", ""]
    for start, end, text, speaker_id in (
        _bilingual_subtitle_lines(job) or subtitle_lines(job)
    ):
        text = _clean_multiline(text)
        if not text:
            continue
        if include_speakers:
            name = _speaker_name(job, speaker_id)
            if name:
                text = f"<v {name}>{text}</v>"
        output.extend(
            [
                f"{_timestamp(start, '.')} --> {_timestamp(end, '.')}",
                text,
                "",
            ]
        )
    return "\n".join(output).strip() + "\n"


def sync_subtitle_segments(
    job: dict[str, Any],
    paragraph_ids: list[str] | None = None,
) -> None:
    """Keep subtitle segment text in sync after transcript edits."""
    segments = job.get("segments") or []
    if not segments:
        return
    selected = set(paragraph_ids) if paragraph_ids is not None else None

    for paragraph in job.get("paragraphs") or []:
        paragraph_id = str(paragraph.get("id") or "")
        if selected is not None and paragraph_id not in selected:
            continue

        indices = paragraph.get("segment_indices")
        if isinstance(indices, list) and indices:
            group = [
                segments[index]
                for index in indices
                if isinstance(index, int) and 0 <= index < len(segments)
            ]
        else:
            start = float(paragraph.get("start") or 0)
            end = float(paragraph.get("end") or start)
            group = [
                segment
                for segment in segments
                if start - _EPSILON <= float(segment.get("start") or 0) <= end + _EPSILON
            ]
        if not group:
            continue

        text = _clean(paragraph.get("text"))
        if not text:
            for segment in group:
                segment["text"] = ""
        elif len(group) == 1:
            group[0]["text"] = text
        else:
            weights = [max(1, len(_clean(segment.get("text")))) for segment in group]
            for segment, chunk in zip(group, _split_by_weights(text, weights)):
                segment["text"] = chunk

        speaker_id = str(paragraph.get("speaker_id") or "")
        if speaker_id:
            for segment in group:
                segment["speaker_id"] = speaker_id
