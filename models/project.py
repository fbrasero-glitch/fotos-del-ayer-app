from __future__ import annotations

from dataclasses import dataclass, field

from .photo import Photo
from .scene import Scene


@dataclass(slots=True)
class Project:
    character: str
    script: str
    scenes: list[Scene]
    selections: dict[str, Photo] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)
    status: str = "draft"
    entity_qid: str = ""
    entity_label: str = ""
