from __future__ import annotations

import math

from models.photo import Photo
from models.scene import Scene
from .text_utils import normalize_text


LICENSE_POINTS = {"green": 30, "yellow": 10, "red": 0}
VISUAL_EQUIVALENTS = {
    "coche": {"car", "vehicle", "automobile", "limousine"},
    "calle": {"street", "road", "london"},
    "correr": {"run", "running", "jogging"},
    "corriendo": {"run", "running", "jogging"},
    "gimnasio": {"gym", "gymnasium", "workout", "fitness"},
    "mar": {"sea", "beach", "coast", "ocean", "shore"},
    "playa": {"sea", "beach", "coast", "shore"},
    "paparazzi": {"paparazzi", "photographer", "press"},
    "fotografo": {"photographer", "paparazzi", "press"},
    "sola": {"alone", "solitary"},
    "solo": {"alone", "solitary"},
    "londres": {"london"},
    "palacio": {"palace", "kensington"},
    "familia": {"family", "children", "son"},
    "boda": {"wedding", "marriage"},
}


def _resolution_points(photo: Photo, maximum: int = 25) -> int:
    pixels = max(0, photo.width * photo.height)
    if not pixels:
        return 0
    return min(maximum, round(maximum * math.log10(1 + pixels) / math.log10(1 + 12_000_000)))


def score_technical(photo: Photo, is_hook: bool = False) -> int:
    fields = (photo.author, photo.date, photo.institution, photo.license, photo.description)
    metadata = round(20 * sum(bool(x and "desconocid" not in x.lower()) for x in fields) / 5)
    haystack = normalize_text(" ".join((photo.title, photo.description)))
    clarity = 15 if photo.width >= 1000 and photo.height >= 1000 else 7
    impact = 10 if is_hook and any(
        term in haystack for term in ("portrait", "retrato", "close up", "face", "head")
    ) else 4
    return min(100, _resolution_points(photo) + LICENSE_POINTS.get(photo.traffic_light, 0) + metadata + clarity + impact)


def score_research_technical(photo: Photo) -> int:
    """Calidad técnica pura para investigación; la licencia se puntúa aparte."""
    fields = (photo.author, photo.date, photo.institution, photo.description)
    metadata = round(
        20 * sum(bool(value and "desconocid" not in value.lower()) for value in fields) / 4
    )
    pixels = max(0, photo.width * photo.height)
    resolution = 0 if not pixels else min(
        55, round(55 * math.log10(1 + pixels) / math.log10(1 + 12_000_000))
    )
    clarity = 25 if photo.width >= 1000 and photo.height >= 800 else (12 if pixels else 0)
    return min(100, resolution + metadata + clarity)


def score_scene_relevance(photo: Photo, scene: Scene) -> int:
    haystack = normalize_text(" ".join((photo.title, photo.description, " ".join(photo.categories))))
    if scene.is_hook:
        portrait = any(term in haystack for term in ("portrait", "retrato", "close up", "face", "head"))
        return min(100, 55 + (30 if portrait else 0) + (15 if scene.key in photo.matched_scene_keys else 0))

    groups: list[set[str]] = []
    for keyword in scene.keywords:
        normalized = normalize_text(keyword)
        equivalents = VISUAL_EQUIVALENTS.get(normalized, {normalized})
        meaningful = {term for term in equivalents if len(term) > 2}
        if meaningful:
            groups.append(meaningful)
    matched = sum(1 for group in groups if any(term in haystack for term in group))
    if matched == 0:
        return 0
    ratio = matched / len(groups) if groups else 0
    return min(100, round(70 * ratio) + (30 if scene.key in photo.matched_scene_keys else 0))


def score_photo(photo: Photo, scene: Scene, character: str = "") -> int:
    photo.technical_score = score_technical(photo, scene.is_hook)
    photo.scene_relevance = score_scene_relevance(photo, scene)
    return round(photo.scene_relevance * 0.6 + photo.technical_score * 0.4)
