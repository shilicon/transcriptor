from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np

ProgressFn = Callable[[str, float], None]

_encoder = None


def _get_encoder():
    global _encoder
    if _encoder is None:
        from resemblyzer import VoiceEncoder

        _encoder = VoiceEncoder("cpu")
    return _encoder


def diarize_segments(
    segments: list[dict[str, Any]],
    wav_path: Path,
    num_speakers: int,
    progress: ProgressFn | None = None,
) -> tuple[list[int], int]:
    """Return a speaker index for every segment, plus the number of speakers."""
    num_speakers = max(1, min(4, int(num_speakers)))
    if num_speakers == 1 or len(segments) <= 1:
        return [0] * len(segments), 1

    if progress:
        progress("正在提取说话人声纹", 62)

    import soundfile as sf
    from resemblyzer import preprocess_wav
    from sklearn.cluster import KMeans

    audio, sample_rate = sf.read(str(wav_path), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    encoder = _get_encoder()

    embeddings: list[np.ndarray] = []
    valid_indices: list[int] = []
    for idx, segment in enumerate(segments):
        duration = float(segment["end"]) - float(segment["start"])
        if duration < 0.8:
            continue
        start = max(0, int(float(segment["start"]) * sample_rate))
        end = min(len(audio), int(float(segment["end"]) * sample_rate))
        if end - start < int(sample_rate * 0.5):
            continue
        try:
            clip = preprocess_wav(audio[start:end], source_sr=sample_rate)
            if len(clip) < int(sample_rate * 0.4):
                continue
            emb = encoder.embed_utterance(clip)
        except Exception:
            continue
        norm = np.linalg.norm(emb)
        if norm <= 1e-8:
            continue
        embeddings.append(emb / norm)
        valid_indices.append(idx)

    if len(embeddings) < num_speakers:
        return [0] * len(segments), 1

    matrix = np.vstack(embeddings)
    n_clusters = min(num_speakers, len(matrix))
    if n_clusters == 1:
        return [0] * len(segments), 1

    if progress:
        progress("正在聚类说话人", 70)

    try:
        model = KMeans(n_clusters=n_clusters, random_state=0, n_init=20)
        labels = model.fit_predict(matrix)
    except Exception:
        return [0] * len(segments), 1

    # Order clusters by first appearance so the first voice is speaker 1.
    first_seen: dict[int, float] = {}
    for idx, label in zip(valid_indices, labels):
        first_seen.setdefault(int(label), float(segments[idx]["start"]))
    order = sorted(first_seen, key=lambda c: first_seen[c])
    remap = {old: new for new, old in enumerate(order)}

    result = [0] * len(segments)
    for idx, label in zip(valid_indices, labels):
        result[idx] = remap[int(label)]

    # Short segments were skipped above. Give them the label of the nearest
    # analysed segment in time, which is usually accurate for interjections.
    if valid_indices:
        valid_set = set(valid_indices)
        starts = np.array([float(segments[i]["start"]) for i in valid_indices])
        for idx, segment in enumerate(segments):
            if idx in valid_set:
                continue
            nearest = int(np.argmin(np.abs(starts - float(segment["start"]))))
            result[idx] = result[valid_indices[nearest]]

    return result, len(order)
