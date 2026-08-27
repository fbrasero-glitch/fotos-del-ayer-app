from __future__ import annotations

import hashlib
from typing import Any

import requests

from models.photo import Photo
from .base import ProviderContext


class PexelsProvider:
    name = "Pexels"
    endpoint = "https://api.pexels.com/v1/search"

    def __init__(self, api_key: str, timeout: int = 20) -> None:
        self.api_key = api_key.strip()
        self.configured = bool(self.api_key)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": self.api_key, "User-Agent": "FotosDeAyer/3.0"}
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
                "query": query,
                "per_page": max(1, min(80, limit)),
                "locale": "es-ES",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return [self._normalize(item) for item in response.json().get("photos", [])]

    def _normalize(self, item: dict[str, Any]) -> Photo:
        src = item.get("src", {}) or {}
        image_url = str(src.get("original", "") or src.get("large2x", ""))
        page_url = str(item.get("url", ""))
        identifier = str(item.get("id") or hashlib.sha1(image_url.encode()).hexdigest()[:16])
        photographer = str(item.get("photographer", "") or "Autor desconocido")
        return Photo(
            id=f"pexels:{identifier}",
            title=str(item.get("alt", "") or "Sin título"),
            thumbnail_url=str(src.get("medium", "") or src.get("small", "") or image_url),
            image_url=image_url,
            original_page_url=page_url,
            author=photographer,
            source=self.name,
            institution="Pexels",
            license="Licencia de Pexels",
            license_url="https://www.pexels.com/license/",
            license_description=(
                "Uso sujeto a la licencia de Pexels; enlaza a Pexels y acredita al fotógrafo."
            ),
            commercial_use=True,
            attribution_required=True,
            traffic_light="green",
            width=int(item.get("width", 0) or 0),
            height=int(item.get("height", 0) or 0),
            description=str(item.get("alt", "") or ""),
            rights_status="Licencia favorable; revisar ficha original",
            metadata={
                "photographer_url": item.get("photographer_url", ""),
                "avg_color": item.get("avg_color", ""),
            },
        )
