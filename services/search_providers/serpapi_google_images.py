from __future__ import annotations

import hashlib
from typing import Any

import requests

from models.photo import Photo
from .base import ProviderContext


class SerpApiGoogleImagesProvider:
    name = "Google Images · SerpAPI"
    endpoint = "https://serpapi.com/search.json"

    def __init__(self, api_key: str, timeout: int = 20) -> None:
        self.api_key = api_key.strip()
        self.configured = bool(self.api_key)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "FotosDeAyer/3.0"})

    def search(
        self,
        query: str,
        limit: int = 20,
        context: ProviderContext | None = None,
    ) -> list[Photo]:
        if not self.configured:
            return []
        response = self.session.get(
            self.endpoint,
            params={
                "engine": "google_images",
                "q": query,
                "api_key": self.api_key,
                "google_domain": "google.es",
                "gl": "es",
                "hl": "es",
                "safe": "active",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise RuntimeError(str(payload["error"]))
        items = payload.get("images_results", [])[: max(1, min(100, limit))]
        return [self._normalize(item) for item in items]

    def _normalize(self, item: dict[str, Any]) -> Photo:
        image_url = str(item.get("original", "") or item.get("thumbnail", ""))
        page_url = str(item.get("link", "") or item.get("source", ""))
        identifier = hashlib.sha1((image_url or page_url).encode()).hexdigest()[:16]
        return Photo(
            id=f"serpapi-google:{identifier}",
            title=str(item.get("title", "") or "Sin título"),
            thumbnail_url=str(item.get("thumbnail", "") or image_url),
            image_url=image_url,
            original_page_url=page_url,
            author=str(item.get("source", "") or "Autor desconocido"),
            source=self.name,
            institution=str(item.get("source", "") or ""),
            license="Requiere revisión de derechos",
            license_description=(
                "Google Images descubre la imagen; comprueba los derechos en la página original."
            ),
            commercial_use=None,
            attribution_required=None,
            traffic_light="yellow",
            width=int(item.get("original_width", 0) or 0),
            height=int(item.get("original_height", 0) or 0),
            description=str(item.get("snippet", "") or ""),
            rights_status="Revisar derechos",
            metadata={"discovery_only": True},
        )
