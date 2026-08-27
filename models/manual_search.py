from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class ManualSearch:
    index: int
    original: str
    translated: str
    concepts: list[str] = field(default_factory=list)
    query_variants: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"manual_{self.index}"

    def to_dict(self) -> dict:
        return asdict(self)
