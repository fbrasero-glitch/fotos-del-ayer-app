from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class Scene:
    index: int
    label: str
    text: str
    keywords: list[str] = field(default_factory=list)
    query: str = ""
    is_hook: bool = False
    query_variants: list[str] = field(default_factory=list)
    visual_concepts: list[str] = field(default_factory=list)
    analysis_note: str = ""

    @property
    def key(self) -> str:
        return "hook" if self.is_hook else f"scene_{self.index}"

    def to_dict(self) -> dict:
        return asdict(self)
