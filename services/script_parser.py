from __future__ import annotations

import re
from collections import Counter

from models.scene import Scene
from utils.text_utils import normalize_text, words


STOPWORDS = {
    "que", "del", "las", "los", "una", "uno", "unos", "unas", "por", "para", "con",
    "sin", "como", "pero", "sus", "era", "fue", "han", "hay", "muy", "más", "mas",
    "también", "tambien", "cada", "desde", "hasta", "entre", "sobre", "este", "esta",
    "estos", "estas", "ese", "esa", "aquel", "aquella", "todo", "toda", "nunca", "aun",
    "aún", "the", "and", "with", "from", "that", "this", "was", "were",
}

VISUAL_TERMS = {
    "coche", "car", "calle", "street", "londres", "london", "correr", "corriendo", "running",
    "gimnasio", "gym", "mar", "sea", "playa", "beach", "paparazzi", "fotografo", "fotógrafo",
    "camara", "cámara", "retrato", "portrait", "ventana", "window", "cristal", "multitud",
    "crowd", "familia", "family", "boda", "wedding", "tren", "train", "avion", "avión",
    "hospital", "casa", "home", "palacio", "palace", "jardin", "jardín", "garden", "escenario",
    "stage", "alone", "sola", "solo", "llorando", "crying", "sonriendo", "smiling", "policia",
    "policía", "police", "guerra", "war", "desfile", "parade", "discurso", "speech",
}


def _paragraphs(script: str) -> list[str]:
    blocks = [" ".join(block.split()) for block in re.split(r"\n\s*\n+", script.strip()) if block.strip()]
    if len(blocks) <= 1:
        blocks = [part.strip() for part in re.split(r"(?<=[.!?])\s+", script.strip()) if part.strip()]
    return blocks


def _fit_blocks(blocks: list[str], count: int) -> list[str]:
    if count <= 0:
        return []
    if not blocks:
        return [""] * count
    if len(blocks) <= count:
        return blocks

    # Agrupación contigua y estable para conservar el arco narrativo.
    groups: list[list[str]] = [[] for _ in range(count)]
    for index, block in enumerate(blocks):
        target = min(count - 1, index * count // len(blocks))
        groups[target].append(block)
    return [" ".join(group) for group in groups if group]


def extract_keywords(text: str, limit: int = 5) -> list[str]:
    raw_tokens = words(text)
    visual_tokens = {normalize_text(term) for term in VISUAL_TERMS}
    candidates = [
        token
        for token in raw_tokens
        if token not in STOPWORDS and (len(token) > 3 or token in visual_tokens)
    ]
    counts = Counter(candidates)
    ordered: list[str] = []

    for token in candidates:
        if token in visual_tokens and token not in ordered:
            ordered.append(token)
    for token, _ in counts.most_common():
        if token not in ordered:
            ordered.append(token)
    return ordered[:limit]


def _label(keywords: list[str], index: int) -> str:
    if not keywords:
        return f"Momento {index}"
    return " · ".join(word.capitalize() for word in keywords[:2])


def parse_script(script: str, desired_photos: int = 6) -> list[Scene]:
    total = max(2, min(8, int(desired_photos)))
    blocks = _paragraphs(script)
    story_blocks = _fit_blocks(blocks, total - 1)
    opening = blocks[0] if blocks else script.strip()
    hook_keywords = extract_keywords(opening, 4)
    scenes = [
        Scene(
            index=0,
            label="Gancho visual",
            text=opening,
            keywords=hook_keywords,
            is_hook=True,
        )
    ]
    for index, block in enumerate(story_blocks, start=1):
        keywords = extract_keywords(block)
        scenes.append(Scene(index=index, label=_label(keywords, index), text=block, keywords=keywords))
    return scenes

