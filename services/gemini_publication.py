"""Asistente opcional de Gemini para metadatos editoriales.

Este módulo no publica en ninguna red: solo genera, valida y guarda borradores/versiones.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.gemini_service import GeminiError, GeminiService
from services.short_edit_store import ShortEditStore

TONES = ("emocional", "nostálgico", "intrigante", "sobrio")
NETWORKS = ("instagram", "facebook", "tiktok")
LIMITS = {"title": 100, "description": 5000, "comment": 500, "instagram": 2200, "facebook": 5000, "tiktok": 2200}


def metadata_schema() -> dict[str, Any]:
    return {"type": "OBJECT", "properties": {"youtube_title": {"type": "STRING"}, "youtube_description": {"type": "STRING"}, "tags": {"type": "ARRAY", "items": {"type": "STRING"}}, "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}}, "pinned_comment": {"type": "STRING"}, "social": {"type": "OBJECT", "properties": {name: {"type": "STRING"} for name in NETWORKS}, "required": list(NETWORKS)}}, "required": ["youtube_title", "youtube_description", "tags", "hashtags", "pinned_comment", "social"]}


def _text(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def validate_metadata(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Gemini no devolvió un objeto JSON.")
    required = ("youtube_title", "youtube_description", "tags", "hashtags", "pinned_comment", "social")
    missing = [key for key in required if key not in value]
    if missing:
        raise ValueError("Faltan campos: " + ", ".join(missing) + ".")
    social = value["social"]
    if not isinstance(social, dict) or any(name not in social for name in NETWORKS):
        raise ValueError("Faltan textos sociales.")
    title = _text(value["youtube_title"], LIMITS["title"])
    description = _text(value["youtube_description"], LIMITS["description"])
    if not title or not description:
        raise ValueError("Título y descripción son obligatorios.")
    def items(raw: object, prefix: str = "") -> list[str]:
        if not isinstance(raw, list):
            return []
        result = []
        for item in raw[:15]:
            text = _text(item, 80)
            if text:
                result.append(text if not prefix or text.startswith(prefix) else prefix + text.lstrip(prefix))
        return list(dict.fromkeys(result))
    return {"youtube_title": title, "youtube_description": description, "tags": items(value["tags"]), "hashtags": items(value["hashtags"], "#"), "pinned_comment": _text(value["pinned_comment"], LIMITS["comment"]), "social": {name: _text(social[name], LIMITS[name]) for name in NETWORKS}}


def final_script(database: str | Path, project_dir: str | Path, project_id: int) -> str:
    segments = ShortEditStore(database).list_segments(project_id)
    narration = "\n\n".join(segment.narration.strip() for segment in segments if segment.narration.strip())
    if narration:
        return narration
    path = Path(project_dir) / "guion.md"
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def generate(service: GeminiService, project_name: str, character: str, script: str, tone: str) -> dict[str, Any]:
    if not service.configured:
        raise GeminiError("Gemini no está configurado.")
    if not script.strip():
        raise GeminiError("No hay un guion final guardado para este proyecto.")
    prompt = ("Genera metadatos editoriales en español para Fotos del Ayer usando únicamente el guion final y datos dados. "
              "No investigues ni inventes hechos. Devuelve solo JSON según el esquema. Tono: " + tone + ". Proyecto: " + project_name + ". Personaje: " + character + ". Incluye título YouTube (máx. 100), descripción, hasta 15 etiquetas, hasta 15 hashtags, comentario fijado y textos independientes para Instagram, Facebook y TikTok. Guion: " + script)
    return validate_metadata(service._generate([{"text": prompt}], metadata_schema()))


class PublicationMetadataStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a"):
            pass
        import sqlite3
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS publication_gemini_metadata (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, metadata_json TEXT NOT NULL, tone TEXT NOT NULL, model TEXT NOT NULL, generated_at TEXT NOT NULL)")

    def save(self, project_id: int, metadata: dict[str, Any], tone: str, model: str) -> dict[str, Any]:
        stamp = datetime.now(timezone.utc).isoformat()
        import sqlite3
        with sqlite3.connect(self.path) as connection:
            connection.execute("INSERT INTO publication_gemini_metadata(project_id, metadata_json, tone, model, generated_at) VALUES (?, ?, ?, ?, ?)", (project_id, json.dumps(validate_metadata(metadata), ensure_ascii=False), tone, model, stamp))
        return {"metadata": metadata, "tone": tone, "model": model, "generated_at": stamp}

    def versions(self, project_id: int) -> list[dict[str, Any]]:
        import sqlite3
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute("SELECT id, metadata_json, tone, model, generated_at FROM publication_gemini_metadata WHERE project_id=? ORDER BY id DESC", (project_id,)).fetchall()
        return [{"id": row[0], "metadata": json.loads(row[1]), "tone": row[2], "model": row[3], "generated_at": row[4]} for row in rows]
