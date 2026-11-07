from __future__ import annotations

import re
from typing import Any

from .utils import sentence_count

SPEAKER_COLORS = ["#0f766e", "#b45309", "#1d4ed8", "#9d174d"]

# Common ASR variants seen in Cadence/EDA interviews.  These are intentionally
# conservative: only replace forms that are very unlikely to be ordinary words.
GLOSSARY: list[tuple[str, str]] = [
    (r"kaden'?s|kadence|cades|cadence", "Cadence"),
    (r"cadence\s+simulation\s+conference", "Cadence Simulation Conference"),
    (r"\bcomms driving\b", "autonomous driving"),
    (r"chip\s*stack", "ChipStack"),
    (r"inno\s*stack", "InnoStack"),
    (r"vira\s*stack|avera\s*stack", "ViraStack"),
    (r"aura\s*stack|orastac", "AuraStack"),
    (r"system\s*stack", "SystemStack"),
    (r"sigridi", "Sigrity"),
    (r"inno\s*vis", "Innovus"),
    (r"celsius\s*easy|celsius\s*ec", "Celsius EC"),
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


def clean_text(text: str, language: str | None = None) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return text
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


def _ends_sentence(text: str) -> bool:
    return bool(re.search(r"[.!?。！？][\"')\]]?$", (text or "").strip()))


def _starts_sentence(text: str) -> bool:
    return bool(re.match(r"[\"'(\[]?[A-Z0-9\u4e00-\u9fff]", (text or "").strip()))


def build_paragraphs(
    segments: list[dict[str, Any]],
    speaker_indices: list[int],
    language: str | None = None,
    max_sentences: int = 10,
    max_duration: float = 120.0,
    pause_split: float = 5.0,
) -> list[dict[str, Any]]:
    """Merge consecutive segments by speaker and split very long turns."""
    paragraphs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if not current:
            return
        text = clean_text(" ".join(current.pop("_texts")), language)
        current.pop("_sentences", None)
        if text:
            current["text"] = text
            paragraphs.append(current)
        current = None

    for segment, speaker in zip(segments, speaker_indices):
        start = float(segment["start"])
        end = float(segment["end"])
        text = (segment.get("text") or "").strip()
        if not text:
            continue

        should_split = False
        if current is not None:
            if speaker != current["speaker"]:
                should_split = True
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
            }
        else:
            current["end"] = end
            current["_texts"].append(text)
            current["_sentences"] += sentence_count(text)

    flush()
    return paragraphs
