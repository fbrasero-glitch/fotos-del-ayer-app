from __future__ import annotations

from models.photo import Photo
from .base import ProviderContext, SearchProvider


class PinterestDiscoveryProvider:
    name = "Pinterest (descubrimiento)"

    def __init__(self, upstream: SearchProvider | None = None) -> None:
        self.upstream = upstream
        self.configured = bool(upstream and upstream.configured)

    def search(
        self,
        query: str,
        limit: int = 20,
        context: ProviderContext | None = None,
    ) -> list[Photo]:
        if not self.upstream:
            return []
        photos = self.upstream.search(f"site:pinterest.com {query}", limit, context)
        for photo in photos:
            photo.source = self.name
            photo.license = "Requiere revisión de derechos"
            photo.license_description = "Pinterest se usa solo para descubrir la posible fuente original."
            photo.commercial_use = None
            photo.traffic_light = "yellow"
            photo.rights_status = "Requiere revisión de derechos"
            photo.metadata["discovery_only"] = True
        return photos
