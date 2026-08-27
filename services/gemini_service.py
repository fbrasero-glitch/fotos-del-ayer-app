from __future__ import annotations

import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from models.entity import ResolvedEntity
from models.photo import Photo
from models.scene import Scene


SCRIPT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "scenes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "scene_index": {"type": "INTEGER"},
                    "visual_concepts": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "search_phrases": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "explanation": {"type": "STRING"},
                },
                "required": ["scene_index", "visual_concepts", "search_phrases", "explanation"],
            },
        }
    },
    "required": ["scenes"],
}

IMAGE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "candidates": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "candidate_index": {"type": "INTEGER"},
                    "person_match": {"type": "INTEGER"},
                    "scene_match": {"type": "INTEGER"},
                    "visual_impact": {"type": "INTEGER"},
                    "description": {"type": "STRING"},
                    "recommended": {"type": "BOOLEAN"},
                },
                "required": [
                    "candidate_index", "person_match", "scene_match",
                    "visual_impact", "description", "recommended",
                ],
            },
        }
    },
    "required": ["candidates"],
}


class GeminiError(RuntimeError):
    pass


class GeminiService:
    endpoint_template = (
        "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    )

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int = 45,
    ) -> None:
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        self.model = (model or os.getenv("GEMINI_MODEL", "") or "gemini-3.1-flash-lite").strip()
        self.timeout = timeout
        self.configured = bool(self.api_key)
        self.session = requests.Session()
        retry = Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update({"User-Agent": "FotosDeAyer/2.0"})

    def _generate(self, parts: list[dict[str, Any]], schema: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise GeminiError("Gemini no está configurado.")
        response = requests.post(
            self.endpoint_template.format(model=self.model),
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            json={
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                    "responseSchema": schema,
                },
            },
            timeout=(10, min(30, self.timeout)),
        )
        if response.status_code >= 400:
            raise GeminiError(f"Gemini devolvió HTTP {response.status_code}.")
        try:
            text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise GeminiError("Gemini devolvió una respuesta no válida.") from exc

    def analyze_script(
        self,
        character: str,
        entity: ResolvedEntity,
        scenes: list[Scene],
    ) -> dict[str, Any]:
        scene_payload = [
            {"scene_index": scene.index, "text": scene.text, "is_hook": scene.is_hook}
            for scene in scenes
        ]
        prompt = (
            "Actúa como investigador fotográfico histórico. Analiza las escenas para encontrar "
            "fotografías reales, no ilustraciones ni metáforas. La identidad está bloqueada en "
            f"{entity.label} ({entity.qid}), conocida como {character}. "
            "Para cada escena devuelve de 3 a 6 conceptos visuales concretos en inglés y de 3 a 5 "
            "frases de búsqueda. Cada frase DEBE contener el nombre de la persona. No propongas "
            "términos genéricos aislados. Escenas JSON: "
            + json.dumps(scene_payload, ensure_ascii=False)
        )
        return self._generate([{"text": prompt}], SCRIPT_SCHEMA)

    def _download_thumbnail(self, photo: Photo) -> tuple[str, str] | None:
        url = photo.thumbnail_url or photo.image_url
        if not url:
            return None
        response = requests.get(url, timeout=(3, 6), headers={"User-Agent": "FotosDeAyer/2.0"})
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "image/jpeg").split(";")[0]
        if not content_type.startswith("image/") or len(response.content) > 5_000_000:
            return None
        return content_type, base64.b64encode(response.content).decode("ascii")

    def analyze_images(
        self,
        character: str,
        entity: ResolvedEntity,
        scene: Scene,
        photos: list[Photo],
    ) -> list[dict[str, Any]]:
        if not photos:
            return []
        downloaded: dict[int, tuple[str, str]] = {}
        with ThreadPoolExecutor(max_workers=min(6, len(photos))) as executor:
            jobs = {
                executor.submit(self._download_thumbnail, photo): index
                for index, photo in enumerate(photos)
            }
            for future in as_completed(jobs):
                try:
                    value = future.result()
                except Exception:
                    value = None
                if value:
                    downloaded[jobs[future]] = value
        if not downloaded:
            return []

        prompt = (
            "Evalúa fotografías candidatas para investigación histórica. Persona obligatoria: "
            f"{entity.label} ({entity.qid}), alias de entrada {character}. Escena objetivo: "
            f"{scene.text}. Conceptos: {', '.join(scene.visual_concepts or scene.keywords)}. "
            "Para cada imagen decide si la persona real es visible (no estatua, flor, objeto, "
            "memorial, edificio ni otra persona), si representa visualmente la escena y su impacto. "
            "Puntúa 0-100. person_match inferior a 75 si no puedes identificar razonablemente a la "
            "persona. recommended debe ser false para identidades dudosas o escenas no representadas. "
            "Devuelve exactamente un elemento por candidato disponible."
        )
        parts: list[dict[str, Any]] = [{"text": prompt}]
        for index in sorted(downloaded):
            photo = photos[index]
            mime, data = downloaded[index]
            parts.append(
                {
                    "text": (
                        f"CANDIDATE_{index}. Metadata: title={photo.title!r}; "
                        f"description={photo.description[:500]!r}; source={photo.source!r}"
                    )
                }
            )
            parts.append({"inlineData": {"mimeType": mime, "data": data}})
        result = self._generate(parts, IMAGE_SCHEMA)
        return list(result.get("candidates", []))
