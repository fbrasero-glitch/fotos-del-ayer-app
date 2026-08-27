from __future__ import annotations

from models.photo import Photo
from services.europeana import EuropeanaService
from .base import ProviderContext


class EuropeanaProvider:
    name = "Europeana"

    def __init__(self, api_key: str, timeout: int = 20) -> None:
        self.configured = bool(api_key)
        self.service = EuropeanaService(api_key, timeout) if api_key else None

    def search(
        self,
        query: str,
        limit: int = 20,
        context: ProviderContext | None = None,
    ) -> list[Photo]:
        if not self.service:
            return []
        return self.service.search(query, limit)
