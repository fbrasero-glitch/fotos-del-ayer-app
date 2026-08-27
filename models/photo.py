from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Photo:
    id: str
    title: str
    thumbnail_url: str
    image_url: str
    original_page_url: str
    author: str = "Autor desconocido"
    date: str = "Fecha desconocida"
    source: str = ""
    institution: str = ""
    license: str = "Licencia desconocida"
    license_url: str = ""
    license_description: str = ""
    commercial_use: bool | None = None
    attribution_required: bool | None = None
    traffic_light: str = "yellow"
    width: int = 0
    height: int = 0
    description: str = ""
    categories: list[str] = field(default_factory=list)
    depicts_qids: list[str] = field(default_factory=list)
    matched_scene_keys: list[str] = field(default_factory=list)
    matched_searches: list[str] = field(default_factory=list)
    search_relevance: int = 0
    relevance_reason: str = ""
    perceptual_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    rights_status: str = "Revisar licencia"
    entity_relevance: int = 0
    entity_evidence: str = ""
    scene_relevance: int = 0
    technical_score: int = 0
    visual_impact: int = 0
    ai_description: str = ""
    ai_recommended: bool = False
    final_score: int = 0
    score: int = 0
    local_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Photo":
        fields = cls.__dataclass_fields__
        return cls(**{key: value for key, value in data.items() if key in fields})
