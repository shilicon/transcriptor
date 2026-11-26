from __future__ import annotations

import re
from typing import Any

from .utils import sentence_count

SPEAKER_COLORS = ["#0f766e", "#b45309", "#1d4ed8", "#9d174d"]

# Design rule: a transcript paragraph should contain roughly ten sentences.
# Paragraphs never mix speakers, so short speaker turns remain short, but a
# long turn must be split instead of becoming one giant block.
TARGET_SENTENCES_PER_PARAGRAPH = 10

# Common ASR variants seen in Cadence/EDA interviews.  These are intentionally
# conservative: only replace forms that are very unlikely to be ordinary words.
GLOSSARY: list[tuple[str, str | Callable[[re.Match[str]], str]]] = [
    (r"\b(?:kaden'?s|kadence|cades|cadence)\b", "Cadence"),
    (r"\bcadence\s+simulation\s+conference\b", "Cadence Simulation Conference"),
    (r"\bcomms driving\b", "autonomous driving"),
    (r"\bchip\s*stack\b", "ChipStack"),
    (r"\binno\s*stack\b", "InnoStack"),
    (r"\b(?:vira\s*stack|avera\s*stack)\b", "ViraStack"),
    (r"\b(?:aura\s*stack|orastac)\b", "AuraStack"),
    (r"\bsystem\s*stack\b", "SystemStack"),
    (r"\bsigridi\b", "Sigrity"),
    (r"\binno\s*vis\b", "Innovus"),
    (r"\b(?:celsius\s*easy|celsius\s*ec)\b", "Celsius EC"),
    (r"\bastran\b", "Nastran"),
    (r"\bmujoco\b", "MuJoCo"),
    (r"\bbeta\s*cae\b", "BETA CAE"),
    (r"\bintelligent\s+system\s+design\b", "Intelligent System Design"),
    (r"\bgen[- ]?tik\s+ai\b", "generative AI"),
    (r"\buvms?\b", "UVM"),
    (r"\bcae\b", "CAE"),
    (r"\bhvac\b", "HVAC"),
    (r"\bvtd\b", "VTD"),
    (r"\bansa\b", "ANSA"),
    (r"\bed a\b", "EDA"),
    (r"\beda\b", "EDA"),
    (r"\bcfd\b", "CFD"),
    (r"\bpcbs?\b", lambda m: "PCBs" if m.group(0).lower().endswith("s") else "PCB"),
    (r"\bmsc\b", "MSC"),
    (r"\bic\b", "IC"),
    (r"\bai\b", "AI"),
]

_COMPILED_GLOSSARY = [
    (re.compile(pattern, re.IGNORECASE), replacement)
    for pattern, replacement in GLOSSARY
]


def apply_glossary(text: str) -> str:
    for regex, replacement in _COMPILED_GLOSSARY:
        text = regex.sub(replacement, text)
    return text


def _capitalize_sentences(text: str) -> str:
    text = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text


def _ensure_terminal_punctuation(text: str, language: str | None) -> str:
    if not text:
        return text
    if language == "zh":
        return text
    if not re.search(r"[A-Za-z0-9]$", text):
        return text
    if text[-1] not in ".!?。！？":
        text += "."
    return text


def clean_text(text: str, language: str | None = None, glossary: bool = False) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return text
    if glossary:
        text = apply_glossary(text)
    # Remove accidental comma-separated duplicate words ("dynamics, dynamics").
    text = re.sub(r"\b([A-Za-z]{5,}),\s+\1\b", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<![A-Za-z])i(?![A-Za-z])", "I", text)
    text = _capitalize_sentences(text)
    text = _ensure_terminal_punctuation(text, language)
    return text


def parse_speaker_names(raw: str, count: int) -> list[str]:
    parts = [p.strip() for p in re.split(r"[,，;；\n]+", raw or "") if p.strip()]
    names: list[str] = []
    for i in range(count):
        if i < len(parts):
            names.append(parts[i][:40])
        else:
            names.append(f"说话人 {i + 1}")
    return names


def build_speakers(names: list[str]) -> list[dict[str, str]]:
    return [
        {
            "id": f"S{i + 1}",
            "name": name,
            "color": SPEAKER_COLORS[i % len(SPEAKER_COLORS)],
        }
        for i, name in enumerate(names)
    ]


_SENTENCE_RE = re.compile(
    r".*?(?:[!?。！？]+|\.(?!\d))[\"')\]]*(?=\s|$|[\u4e00-\u9fffA-Z])",
    re.DOTALL,
)


def split_sentences(text: str) -> list[str]:
    """Split ASR text into sentence-sized chunks, keeping punctuation."""
    text = (text or "").strip()
    if not text:
        return []
    sentences: list[str] = []
    consumed = 0
    for match in _SENTENCE_RE.finditer(text):
        sentence = match.group(0).strip()
        if sentence:
            sentences.append(sentence)
        consumed = match.end()
    remainder = text[consumed:].strip()
    if remainder:
        sentences.append(remainder)
    return sentences


def split_segments_at_sentences(
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Split Whisper segments at sentence boundaries.

    Whisper often emits fragments that end mid-sentence.  Splitting here
    gives paragraph building real sentence boundaries to work with, while
    timestamps are apportioned by character length.
    """
    result: list[dict[str, Any]] = []
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        sentences = split_sentences(text)
        if len(sentences) <= 1:
            result.append(dict(segment))
            continue

        start = float(segment.get("start") or 0)
        end = float(segment.get("end") or start)
        if end <= start:
            end = start + 0.2
        span = end - start
        total_chars = sum(len(sentence) for sentence in sentences) or len(sentences)
        cursor = start
        consumed = 0
        for index, sentence in enumerate(sentences):
            consumed += len(sentence)
            if index == len(sentences) - 1:
                chunk_end = end
            else:
                chunk_end = start + span * consumed / total_chars
                chunk_end = min(end, max(cursor, chunk_end))
            chunk = dict(segment)
            chunk["start"] = cursor
            chunk["end"] = chunk_end
            chunk["text"] = sentence
            result.append(chunk)
            cursor = chunk_end
    return result


def _ends_sentence(text: str) -> bool:
    return bool(re.search(r"[.!?。！？][\"')\]]?$", (text or "").strip()))


def _starts_sentence(text: str) -> bool:
    return bool(re.match(r"[\"'(\[]?[A-Z0-9\u4e00-\u9fff]", (text or "").strip()))


def build_paragraphs(
    segments: list[dict[str, Any]],
    speaker_indices: list[int],
    language: str | None = None,
    max_sentences: int = TARGET_SENTENCES_PER_PARAGRAPH,
    max_duration: float = 120.0,
    pause_split: float = 5.0,
    glossary: bool = False,
    break_before: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Merge consecutive segments by speaker and split long turns.

    Design rule: keep paragraphs around ``TARGET_SENTENCES_PER_PARAGRAPH``
    sentences and never mix speakers.  Call ``split_segments_at_sentences``
    before this function when segments come straight from Whisper, otherwise
    fragments that end mid-sentence can defeat the sentence limit.

    When ``break_before`` is provided it is an AI semantic plan: those segment
    indices start new paragraphs, and the sentence-count heuristic is skipped.

    ``raw_text`` is the untouched ASR text for the paragraph.  ``text`` and
    ``formatted_text`` contain the deterministic formatting result.  Domain
    glossary substitution is opt-in because it is not safe for general
    interviews.
    """
    paragraphs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if not current:
            return
        raw_text = " ".join(current.pop("_texts")).strip()
        text = clean_text(raw_text, language, glossary=glossary)
        current.pop("_sentences", None)
        current["segment_indices"] = current.pop("_segment_indices", [])
        if text:
            current["raw_text"] = raw_text
            current["formatted_text"] = text
            current["text"] = text
            paragraphs.append(current)
        current = None

    for index, (segment, speaker) in enumerate(zip(segments, speaker_indices)):
        start = float(segment["start"])
        end = float(segment["end"])
        text = (segment.get("text") or "").strip()
        if not text:
            continue

        should_split = False
        if current is not None:
            if speaker != current["speaker"]:
                should_split = True
            elif break_before is not None:
                should_split = index in break_before
            else:
                previous_text = current["_texts"][-1] if current["_texts"] else ""
                reached_limit = (
                    current["_sentences"] >= max_sentences
                    or end - current["start"] >= max_duration
                    or start - current["end"] >= pause_split
                )
                # Do not cut in the middle of a sentence just because a
                # Whisper segment ended.  Wait for a real sentence boundary.
                if reached_limit and _ends_sentence(previous_text) and _starts_sentence(text):
                    should_split = True

        if current is None or should_split:
            flush()
            current = {
                "speaker": int(speaker),
                "start": start,
                "end": end,
                "_texts": [text],
                "_sentences": sentence_count(text),
                "_segment_indices": [index],
            }
        else:
            current["end"] = end
            current["_texts"].append(text)
            current["_sentences"] += sentence_count(text)
            current["_segment_indices"].append(index)

    flush()
    return paragraphs
