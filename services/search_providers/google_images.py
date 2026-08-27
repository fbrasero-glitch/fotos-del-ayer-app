from __future__ import annotations

import hashlib
from typing import Any

import requests

from models.photo import Photo
from .base import ProviderContext


class GoogleImagesProvider:
    name = "Google Images"
    endpoint = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, api_key: str, engine_id: str, timeout: int = 20) -> None:
        self.api_key = api_key.strip()
        self.engine_id = engine_id.strip()
        self.configured = bool(self.api_key and self.engine_id)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "FotosDeAyer/2.0"})

    def search(
        self,
        query: str,
        limit: int = 20,
        context: ProviderContext | None = None,
    ) -> list[Photo]:
        if not self.configured:
            return []
        photos: list[Photo] = []
        remaining = max(1, min(100, limit))
        start = 1
        while remaining > 0:
            count = min(10, remaining)
            response = self.session.get(
                self.endpoint,
                params={
                    "key": self.api_key,
                    "cx": self.engine_id,
                    "q": query,
                    "searchType": "image",
                    "num": count,
                    "start": start,
                    "safe": "active",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            items = response.json().get("items", [])
            photos.extend(self._normalize(item) for item in items)
            if len(items) < count:
                break
            remaining -= count
            start += count
        return photos

    def _normalize(self, item: dict[str, Any]) -> Photo:
        image_url = str(item.get("link", ""))
        image = item.get("image", {}) or {}
        page_url = str(image.get("contextLink", ""))
        identifier = hashlib.sha1((image_url or page_url).encode()).hexdigest()[:16]
        return Photo(
            id=f"google:{identifier}",
            title=str(item.get("title", "") or "Sin título"),
            thumbnail_url=str(image.get("thumbnailLink", "") or image_url),
            image_url=image_url,
            original_page_url=page_url,
            author=str(item.get("displayLink", "") or "Autor desconocido"),
            source=self.name,
            institution=str(item.get("displayLink", "") or ""),
            license="Requiere revisión de derechos",
            license_description="Google descubre la imagen; los derechos deben comprobarse en la página original.",
            commercial_use=None,
            attribution_required=None,
            traffic_light="yellow",
            width=int(image.get("width", 0) or 0),
            height=int(image.get("height", 0) or 0),
            description=str(item.get("snippet", "") or ""),
            metadata={"mime": item.get("mime", "")},
            rights_status="Revisar licencia",
        )
