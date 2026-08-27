from __future__ import annotations

from models.photo import Photo
from services.wikimedia import WikimediaService
from .base import ProviderContext


class WikimediaProvider:
    name = "Wikimedia Commons"
    configured = True

    def __init__(self, timeout: int = 20) -> None:
        self.service = WikimediaService(timeout)

    def search(
        self,
        query: str,
        limit: int = 20,
        context: ProviderContext | None = None,
    ) -> list[Photo]:
        entity = context.entity if context else None
        return self.service.search(query, limit, entity)
