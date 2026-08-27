from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class ResolvedEntity:
    qid: str
    label: str
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    commons_category: str = ""
    image_filename: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def wikidata_url(self) -> str:
        return f"https://www.wikidata.org/wiki/{self.qid}"
