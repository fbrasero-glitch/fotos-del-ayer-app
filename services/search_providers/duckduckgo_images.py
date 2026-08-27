from __future__ import annotations

import hashlib
import os
from typing import Any

from models.photo import Photo
from .base import ProviderContext


class DuckDuckGoImagesProvider:
    """Experimental image discovery through the maintained ``ddgs`` package."""

    name = "DuckDuckGo Images"

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout
        try:
            from ddgs import DDGS  # noqa: F401
        except ImportError:
            self.configured = False
        else:
            self.configured = True

    def search(
        self,
        query: str,
        limit: int = 20,
        context: ProviderContext | None = None,
    ) -> list[Photo]:
        if not self.configured:
            return []
        from ddgs import DDGS

        rows = DDGS(timeout=self.timeout).images(
            query,
            region=os.getenv("DDGS_REGION", "es-es"),
            safesearch="moderate",
            size="Large",
            type_image="photo",
            max_results=max(1, min(100, limit)),
        )
        return [self._normalize(item) for item in rows]

    def _normalize(self, item: dict[str, Any]) -> Photo:
        image_url = str(item.get("image", "") or "")
        thumbnail_url = str(item.get("thumbnail", "") or image_url)
        page_url = str(item.get("url", "") or "")
        upstream = str(item.get("source", "") or "Web")
        identifier = hashlib.sha1((image_url or page_url).encode()).hexdigest()[:16]
        return Photo(
            id=f"ddgs:{identifier}",
            title=str(item.get("title", "") or "Sin título"),
            thumbnail_url=thumbnail_url,
            image_url=image_url or thumbnail_url,
            original_page_url=page_url,
            author=upstream,
            source=self.name,
            institution=upstream,
            license="Requiere revisión de derechos",
            license_description=(
                "DuckDuckGo descubre la imagen; comprueba los derechos en la página original."
            ),
            traffic_light="yellow",
            width=int(item.get("width", 0) or 0),
            height=int(item.get("height", 0) or 0),
            rights_status="Revisar derechos",
            metadata={
                "discovery_only": True,
                "experimental_provider": True,
                "upstream_source": upstream,
            },
        )
