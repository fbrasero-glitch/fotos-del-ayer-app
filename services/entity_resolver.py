from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from models.entity import ResolvedEntity
from utils.text_utils import normalize_text


KNOWN_ENTITIES = {
    "lady di": "Q9685",
    "lady diana": "Q9685",
    "princesa diana": "Q9685",
    "princess diana": "Q9685",
    "diana spencer": "Q9685",
    "diana princess of wales": "Q9685",
}
KNOWN_ENTITY_ALIASES = {
    "Q9685": [
        "Diana, Princess of Wales",
        "Princess Diana",
        "Lady Di",
        "Lady Diana",
        "Diana Spencer",
    ]
}


class EntityResolutionError(RuntimeError):
    pass


class EntityResolver:
    endpoint = "https://www.wikidata.org/w/api.php"

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update({"User-Agent": "FotosDeAyer/0.2 (entity resolution)"})

    @staticmethod
    def _claim_values(entity: dict, property_id: str) -> list[object]:
        values: list[object] = []
        for claim in entity.get("claims", {}).get(property_id, []):
            value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
            if value is not None:
                values.append(value)
        return values

    def _fetch(self, qid: str, character: str, manual_aliases: list[str]) -> ResolvedEntity:
        response = self.session.get(
            self.endpoint,
            params={
                "action": "wbgetentities",
                "format": "json",
                "ids": qid,
                "props": "labels|aliases|descriptions|claims",
                "languages": "en|es",
                "languagefallback": 1,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        entity = response.json().get("entities", {}).get(qid, {})
        if not entity or entity.get("missing") is not None:
            raise EntityResolutionError(f"Wikidata no devolvió la entidad {qid}.")

        instances = {
            value.get("id")
            for value in self._claim_values(entity, "P31")
            if isinstance(value, dict)
        }
        if "Q5" not in instances:
            raise EntityResolutionError(
                f"La entidad resuelta ({qid}) no está identificada como persona en Wikidata."
            )

        labels = entity.get("labels", {})
        label = labels.get("en", {}).get("value") or labels.get("es", {}).get("value") or character
        descriptions = entity.get("descriptions", {})
        description = (
            descriptions.get("es", {}).get("value")
            or descriptions.get("en", {}).get("value")
            or ""
        )
        aliases = [character, *manual_aliases, *KNOWN_ENTITY_ALIASES.get(qid, [])]
        for language_aliases in entity.get("aliases", {}).values():
            aliases.extend(item.get("value", "") for item in language_aliases)
        aliases = list(dict.fromkeys(item.strip() for item in aliases if item.strip()))

        categories = self._claim_values(entity, "P373")
        images = self._claim_values(entity, "P18")
        return ResolvedEntity(
            qid=qid,
            label=label,
            description=description,
            aliases=aliases,
            commons_category=str(categories[0]) if categories else "",
            image_filename=str(images[0]) if images else "",
        )

    def resolve(self, character: str, manual_aliases: list[str] | None = None) -> ResolvedEntity:
        character = character.strip()
        aliases = manual_aliases or []
        if not character:
            raise EntityResolutionError("Introduce un personaje antes de resolver la entidad.")

        for candidate in [character, *aliases]:
            qid = KNOWN_ENTITIES.get(normalize_text(candidate))
            if qid:
                return self._fetch(qid, character, aliases)

        search_terms = list(dict.fromkeys([character, *aliases]))
        candidates: list[dict] = []
        for search_term in search_terms:
            response = self.session.get(
                self.endpoint,
                params={
                    "action": "wbsearchentities",
                    "format": "json",
                    "search": search_term,
                    "language": "es",
                    "uselang": "es",
                    "type": "item",
                    "limit": 8,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            candidates.extend(response.json().get("search", []))

        targets = {normalize_text(item) for item in search_terms}
        for candidate in candidates:
            names = {
                normalize_text(candidate.get("label", "")),
                *[normalize_text(item) for item in candidate.get("aliases", [])],
            }
            if targets.isdisjoint(names):
                continue
            try:
                return self._fetch(candidate["id"], character, aliases)
            except EntityResolutionError:
                continue

        raise EntityResolutionError(
            "No se pudo vincular el nombre a una persona concreta de Wikidata. "
            "Añade un alias más específico."
        )
