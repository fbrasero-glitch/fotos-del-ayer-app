"""Metadatos editoriales para la publicación, sin publicar en redes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from services.short_edit_store import ShortEditStore

TONES = ("emocional", "nostálgico", "intrigante", "sobrio")
SOCIAL_NETWORKS = ("instagram", "facebook", "tiktok")
LIMITS = {"youtube_title": 100, "youtube_description": 5000, "pinned_comment": 500, "instagram": 2200, "facebook": 5000, "tiktok": 2200}


def final_script(database: str | Path, project_dir: str | Path, project_id: int) -> str:
    """Prioriza la narración editada; el archivo de guion es solo respaldo."""
    narration = "\n\n".join(segment.narration.strip() for segment in ShortEditStore(database).list_segments(project_id) if segment.narration.strip())
    if narration:
        return narration
    path = Path(project_dir) / "guion.md"
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def _text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def _items(value: object, prefix: str = "") -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = _text(item, 80)
        if text:
            result.append(text if not prefix or text.startswith(prefix) else prefix + text.lstrip(prefix))
    return list(dict.fromkeys(result))[:15]


def validate_metadata(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("La respuesta de Gemini no es un objeto JSON.")
    required = ("youtube_title", "youtube_description", "tags", "hashtags", "pinned_comment", "social")
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError("Faltan campos de metadatos: " + ", ".join(missing) + ".")
    social = payload["social"]
    if not isinstance(social, dict) or any(network not in social for network in SOCIAL_NETWORKS):
        raise ValueError("Faltan textos para Instagram, Facebook o TikTok.")
    title, description = _text(payload["youtube_title"], LIMITS["youtube_title"]), _text(payload["youtube_description"], LIMITS["youtube_description"])
    if not title or not description:
        raise ValueError("El título y la descripción de YouTube son obligatorios.")
    return {"youtube_title": title, "youtube_description": description, "tags": _items(payload["tags"]), "hashtags": _items(payload["hashtags"], "#"), "pinned_comment": _text(payload["pinned_comment"], LIMITS["pinned_comment"]), "social": {network: _text(social[network], LIMITS[network]) for network in SOCIAL_NETWORKS}}


def metadata_schema() -> dict[str, Any]:
    return {"type": "OBJECT", "properties": {"youtube_title": {"type": "STRING"}, "youtube_description": {"type": "STRING"}, "tags": {"type": "ARRAY", "items": {"type": "STRING"}}, "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}}, "pinned_comment": {"type": "STRING"}, "social": {"type": "OBJECT", "properties": {network: {"type": "STRING"} for network in SOCIAL_NETWORKS}, "required": list(SOCIAL_NETWORKS)}}, "required": ["youtube_title", "youtube_description", "tags", "hashtags", "pinned_comment", "social"]}
