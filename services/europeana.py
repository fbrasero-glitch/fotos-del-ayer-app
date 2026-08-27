from __future__ import annotations

import hashlib
from typing import Any

import requests

from models.photo import Photo
from utils.licenses import assess_license
from utils.text_utils import first_value, strip_html


class EuropeanaService:
    name = "Europeana"
    endpoint = "https://api.europeana.eu/record/v2/search.json"

    def __init__(self, api_key: str, timeout: int = 20) -> None:
        if not api_key:
            raise ValueError("Europeana necesita EUROPEANA_API_KEY")
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "FotosDeAyer/0.1"})

    def search(self, query: str, limit: int = 12) -> list[Photo]:
        params = {
            "wskey": self.api_key,
            "query": query,
            "rows": max(1, min(100, limit)),
            "profile": "rich",
            "media": "true",
        }
        response = self.session.get(self.endpoint, params=params, timeout=self.timeout)
        response.raise_for_status()
        items = response.json().get("items", [])
        return [photo for item in items if (photo := self._normalize(item))]

    def _normalize(self, item: dict[str, Any]) -> Photo | None:
        thumbnail = first_value(item.get("edmPreview"))
        image_url = first_value(item.get("edmIsShownBy")) or thumbnail
        if not image_url:
            return None
        original_url = first_value(item.get("guid")) or first_value(item.get("edmIsShownAt"))
        rights = first_value(item.get("rights"), "Licencia desconocida")
        assessment = assess_license(rights, rights, rights)
        raw_id = first_value(item.get("id")) or image_url
        identifier = hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:16]

        return Photo(
            id=f"europeana:{identifier}",
            title=strip_html(first_value(item.get("title"), "Sin título")),
            thumbnail_url=thumbnail or image_url,
            image_url=image_url,
            original_page_url=original_url,
            author=strip_html(first_value(item.get("dcCreator"), "Autor desconocido")),
            date=strip_html(first_value(item.get("year"), "Fecha desconocida")),
            source=self.name,
            institution=strip_html(
                first_value(item.get("dataProvider")) or first_value(item.get("provider"))
            ),
            license=rights,
            license_url=rights if rights.startswith("http") else "",
            license_description=assessment.description,
            commercial_use=assessment.commercial_use,
            attribution_required=assessment.attribution_required,
            traffic_light=assessment.traffic_light,
            description=strip_html(first_value(item.get("dcDescription"))),
        )

