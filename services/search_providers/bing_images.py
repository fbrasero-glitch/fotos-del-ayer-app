from __future__ import annotations

import hashlib
from typing import Any

import requests

from models.photo import Photo
from utils.licenses import assess_license
from .base import ProviderContext


class BingImagesProvider:
    name = "Bing Images"
    endpoint = "https://api.bing.microsoft.com/v7.0/images/search"

    def __init__(self, api_key: str, timeout: int = 20) -> None:
        self.api_key = api_key.strip()
        self.configured = bool(self.api_key)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "FotosDeAyer/2.0"})

    def search(
        self,
        query: str,
        limit: int = 20,
        context: ProviderContext | None = None,
    ) -> list[Photo]:
        if not self.configured:
            return []
        response = self.session.get(
            self.endpoint,
            headers={"Ocp-Apim-Subscription-Key": self.api_key},
            params={
                "q": query,
                "count": max(1, min(100, limit)),
                "safeSearch": "Moderate",
                "imageType": "Photo",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return [self._normalize(item) for item in response.json().get("value", [])]

    def _normalize(self, item: dict[str, Any]) -> Photo:
        image_url = str(item.get("contentUrl", ""))
        page_url = str(item.get("hostPageUrl", ""))
        identifier = hashlib.sha1((image_url or page_url).encode()).hexdigest()[:16]
        license_name = str(item.get("license", "") or "Requiere revisión de derechos")
        assessment = assess_license(license_name, "", "")
        return Photo(
            id=f"bing:{identifier}",
            title=str(item.get("name", "") or "Sin título"),
            thumbnail_url=str(item.get("thumbnailUrl", "") or image_url),
            image_url=image_url,
            original_page_url=page_url,
            author=str(item.get("hostPageDisplayUrl", "") or "Autor desconocido"),
            source=self.name,
            institution=str(item.get("hostPageDomainFriendlyName", "") or ""),
            license=license_name,
            license_description=assessment.description,
            commercial_use=assessment.commercial_use,
            attribution_required=assessment.attribution_required,
            traffic_light=assessment.traffic_light,
            width=int(item.get("width", 0) or 0),
            height=int(item.get("height", 0) or 0),
            description=str(item.get("name", "") or ""),
            metadata={"provider_payload": {"content_size": item.get("contentSize", "")}},
            rights_status="Revisar licencia",
        )
