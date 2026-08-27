from __future__ import annotations

import hashlib
from typing import Any

import requests

from models.photo import Photo
from .base import ProviderContext


class BraveImagesProvider:
    name = "Brave Images"
    endpoint = "https://api.search.brave.com/res/v1/images/search"

    def __init__(self, api_key: str, timeout: int = 20) -> None:
        self.api_key = api_key.strip()
        self.configured = bool(self.api_key)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": self.api_key,
                "User-Agent": "FotosDeAyer/3.0",
            }
        )

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
                "q": query,
                "count": max(1, min(200, limit)),
                "country": "ES",
                "search_lang": "es",
                "safesearch": "strict",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return [self._normalize(item) for item in response.json().get("results", [])]

    def _normalize(self, item: dict[str, Any]) -> Photo:
        properties = item.get("properties", {}) or {}
        thumbnail = item.get("thumbnail", {}) or {}
        if isinstance(thumbnail, str):
            thumbnail_url = thumbnail
        else:
            thumbnail_url = str(thumbnail.get("src", "") or thumbnail.get("original", ""))
        image_url = str(
            properties.get("url", "")
            or item.get("image_url", "")
            or thumbnail.get("original", "") if isinstance(thumbnail, dict) else ""
        )
        page_url = str(item.get("url", "") or item.get("source_url", ""))
        identifier = hashlib.sha1((image_url or page_url).encode()).hexdigest()[:16]
        return Photo(
            id=f"brave:{identifier}",
            title=str(item.get("title", "") or "Sin título"),
            thumbnail_url=thumbnail_url or image_url,
            image_url=image_url or thumbnail_url,
            original_page_url=page_url,
            author=str(item.get("source", "") or "Autor desconocido"),
            source=self.name,
            institution=str(item.get("source", "") or ""),
            license="Requiere revisión de derechos",
            license_description=(
                "Brave descubre la imagen; comprueba los derechos en la página original."
            ),
            commercial_use=None,
            attribution_required=None,
            traffic_light="yellow",
            width=int(properties.get("width", 0) or item.get("width", 0) or 0),
            height=int(properties.get("height", 0) or item.get("height", 0) or 0),
            description=str(item.get("description", "") or ""),
            rights_status="Revisar derechos",
            metadata={"discovery_only": True, "session_only": True},
        )
