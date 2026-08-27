from __future__ import annotations

import re


def estimate_duration_seconds(text: str, words_per_minute: int = 132) -> float:
    words = re.findall(r"\b[\wÁÉÍÓÚÜÑáéíóúüñ'-]+\b", text, flags=re.UNICODE)
    if not words:
        return 0.0
    punctuation_pauses = len(re.findall(r"[.!?;:]", text)) * 0.18
    return len(words) * 60 / words_per_minute + punctuation_pauses


def split_script(text: str, segment_count: int) -> list[str]:
    """Divide un guion conservando el orden y procurando bloques equilibrados."""
    if segment_count <= 0:
        return []
    clean = text.strip()
    if not clean:
        return [""] * segment_count

    explicit = [
        " ".join(part.split())
        for part in re.split(r"\n\s*(?:---+)?\s*\n", clean)
        if part.strip()
    ]
    if len(explicit) == segment_count:
        return explicit

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?…])\s+", " ".join(clean.split()))
        if sentence.strip()
    ]
    if not sentences:
        sentences = [" ".join(clean.split())]

    total_words = sum(len(sentence.split()) for sentence in sentences)
    target = max(1.0, total_words / segment_count)
    groups: list[list[str]] = [[] for _ in range(segment_count)]
    group_index = 0
    group_words = 0
    for sentence_index, sentence in enumerate(sentences):
        words = len(sentence.split())
        remaining_sentences = len(sentences) - sentence_index
        remaining_groups = segment_count - group_index
        if (
            group_index < segment_count - 1
            and groups[group_index]
            and group_words + words > target
            and remaining_sentences >= remaining_groups
        ):
            group_index += 1
            group_words = 0
        groups[group_index].append(sentence)
        group_words += words
    return [" ".join(group) for group in groups]
