from __future__ import annotations

import base64
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from models.entity import ResolvedEntity
from models.photo import Photo
from models.scene import Scene


LOCAL_IMAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_index": {"type": "integer"},
                    "person_match": {"type": "integer"},
                    "scene_match": {"type": "integer"},
                    "visual_impact": {"type": "integer"},
                    "mobile_crop": {"type": "integer"},
                    "clean_image": {"type": "integer"},
                    "real_photo": {"type": "boolean"},
                    "quality_issues": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "description": {"type": "string"},
                    "recommended": {"type": "boolean"},
                },
                "required": [
                    "candidate_index",
                    "person_match",
                    "scene_match",
                    "visual_impact",
                    "mobile_crop",
                    "clean_image",
                    "real_photo",
                    "quality_issues",
                    "description",
                    "recommended",
                ],
            },
        }
    },
    "required": ["candidates"],
}


class LocalVisionError(RuntimeError):
    pass


class LocalVisionService:
    """Small Ollama client for local visual ranking of photo candidates."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        ).rstrip("/")
        self.model = (
            model or os.getenv("LOCAL_VISION_MODEL", "qwen2.5vl:7b")
        ).strip()
        self.timeout = timeout or int(os.getenv("LOCAL_VISION_TIMEOUT", "180"))
        self.session = requests.Session()

    @property
    def label(self) -> str:
        return f"Visión local · {self.model}"

    @property
    def configured(self) -> bool:
        """True only when Ollama is responding and the configured model exists."""
        try:
            response = self.session.get(
                f"{self.base_url}/api/tags", timeout=(1.5, 2.5)
            )
            response.raise_for_status()
            models = response.json().get("models", [])
            return any(
                item.get("name") == self.model or item.get("model") == self.model
                for item in models
                if isinstance(item, dict)
            )
        except (requests.RequestException, ValueError, TypeError):
            return False

    @staticmethod
    def _download_thumbnail(photo: Photo) -> tuple[str, str] | None:
        url = photo.thumbnail_url or photo.image_url
        if not url:
            return None
        response = requests.get(
            url,
            timeout=(3, 8),
            headers={"User-Agent": "FotosDeAyer/2.0"},
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "image/jpeg").split(";", 1)[0]
        if not content_type.startswith("image/") or len(response.content) > 5_000_000:
            return None
        return content_type, base64.b64encode(response.content).decode("ascii")

    def _chat(self, prompt: str, images: list[str]) -> list[dict[str, Any]]:
        try:
            response = self.session.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "keep_alive": "10m",
                    "format": LOCAL_IMAGE_SCHEMA,
                    "options": {
                        "temperature": 0,
                        "num_predict": 700,
                        "num_ctx": 8192,
                    },
                    "messages": [{"role": "user", "content": prompt, "images": images}],
                },
                timeout=(5, self.timeout),
            )
            response.raise_for_status()
            content = response.json()["message"]["content"]
            payload = json.loads(content)
            candidates = payload.get("candidates", [])
            if not isinstance(candidates, list):
                raise ValueError("La respuesta no contiene una lista de candidatos.")
            return [item for item in candidates if isinstance(item, dict)]
        except (requests.RequestException, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LocalVisionError(f"Ollama no devolvió un análisis válido: {exc}") from exc

    def analyze_images(
        self,
        character: str,
        entity: ResolvedEntity,
        scene: Scene,
        photos: list[Photo],
    ) -> list[dict[str, Any]]:
        if not photos:
            return []
        if not self.configured:
            raise LocalVisionError(
                f"Ollama no está disponible o no tiene descargado {self.model}."
            )

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

        metadata_lines = []
        for sent_position, original_index in enumerate(sorted(downloaded)):
            photo = photos[original_index]
            metadata_lines.append(
                f"CANDIDATE_{sent_position}: título={photo.title!r}; "
                f"fuente={photo.source!r}; tamaño={photo.width}x{photo.height}; "
                f"descripción={photo.description[:240]!r}."
            )

        prompt = (
            "Evalúa estas fotografías candidatas para una búsqueda histórica. "
            f"La persona buscada es {entity.label} ({entity.qid}); el alias introducido fue "
            f"{character}. La escena objetivo es: {scene.text}. Conceptos: "
            f"{', '.join(scene.visual_concepts or scene.keywords)}. "
            "Las imágenes se entregan en el orden CANDIDATE_0, CANDIDATE_1, etc. "
            + " ".join(metadata_lines)
            + " "
            "Devuelve exactamente un objeto por cada imagen, usando ese índice. "
            "Puntúa de 0 a 100: person_match solo si la persona real aparece y la identidad "
            "es razonablemente compatible; usa una puntuación baja si hay duda, estatua, "
            "edificio, memorial u otra persona. scene_match mide si la imagen representa la "
            "escena. visual_impact mide su utilidad visual para un Short. mobile_crop "
            "mide si el sujeto principal sobrevivirá centrado a un recorte vertical 9:16, "
            "sin cortar cabeza, cara o elemento importante. clean_image puntúa nitidez y "
            "ausencia de marcas de agua grandes, texto, collage, bordes o artefactos. "
            "real_photo debe ser false para dibujos, carteles, estatuas, figuras de cera, "
            "capturas de páginas o recreaciones. Enumera problemas breves en quality_issues. "
            "recommended solo puede ser true si la identidad y la escena son suficientemente "
            "claras, es una foto real, está limpia y funciona razonablemente en vertical. "
            "No inventes datos históricos."
        )
        ordered_images = [downloaded[index][1] for index in sorted(downloaded)]
        analyses = self._chat(prompt, ordered_images)

        # El modelo puede omitir una imagen aunque el esquema la pida. Reindexamos según
        # el orden real enviado para que la capa de búsqueda no confunda candidatas.
        sent_indexes = list(sorted(downloaded))
        normalized: list[dict[str, Any]] = []
        for position, item in enumerate(analyses):
            try:
                candidate_index = int(item.get("candidate_index", position))
            except (TypeError, ValueError):
                candidate_index = position
            if 0 <= candidate_index < len(sent_indexes):
                item = dict(item)
                item["candidate_index"] = sent_indexes[candidate_index]
                normalized.append(item)
        return normalized
