from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from models.entity import ResolvedEntity
from models.photo import Photo


@dataclass(slots=True)
class ProviderContext:
    entity: ResolvedEntity
    scene_key: str | None = None
    discovery_only: bool = False


class SearchProvider(Protocol):
    name: str
    configured: bool

    def search(
        self,
        query: str,
        limit: int = 20,
        context: ProviderContext | None = None,
    ) -> list[Photo]:
        ...


def identity_query(entity: ResolvedEntity, concept: str = "") -> str:
    label = entity.label.replace('"', "")
    return f'"{label}" {concept}'.strip()
