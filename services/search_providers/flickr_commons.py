from __future__ import annotations

import hashlib
from typing import Any

import requests

from models.photo import Photo
from utils.licenses import assess_license
from .base import ProviderContext


class FlickrCommonsProvider:
    name = "Flickr Commons"
    endpoint = "https://www.flickr.com/services/rest/"

    def __init__(self, api_key: str = "", timeout: int = 20) -> None:
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
            params={
                "method": "flickr.photos.search",
                "api_key": self.api_key,
                "text": query,
                "media": "photos",
                "content_type": 1,
                "safe_search": 1,
                "extras": "description,license,owner_name,date_taken,url_q,url_c,url_o",
                "per_page": max(1, min(100, limit)),
                "format": "json",
                "nojsoncallback": 1,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return [p for item in response.json().get("photos", {}).get("photo", []) if (p := self._normalize(item))]

    def _normalize(self, item: dict[str, Any]) -> Photo | None:
        image_url = item.get("url_o") or item.get("url_c") or item.get("url_q")
        if not image_url:
            return None
        page_url = f"https://www.flickr.com/photos/{item.get('owner','')}/{item.get('id','')}"
        license_name = f"Flickr license code {item.get('license', '')}"
        assessment = assess_license(license_name, page_url, "")
        identifier = hashlib.sha1(image_url.encode()).hexdigest()[:16]
        return Photo(
            id=f"flickr:{identifier}",
            title=str(item.get("title", "") or "Sin título"),
            thumbnail_url=str(item.get("url_q") or image_url),
            image_url=str(image_url),
            original_page_url=page_url,
            author=str(item.get("ownername", "") or "Autor desconocido"),
            date=str(item.get("datetaken", "") or "Fecha desconocida"),
            source=self.name,
            license=license_name,
            license_description=assessment.description,
            commercial_use=assessment.commercial_use,
            attribution_required=assessment.attribution_required,
            traffic_light=assessment.traffic_light,
            description=str((item.get("description") or {}).get("_content", "") or ""),
            width=int(item.get("width_o") or item.get("width_c") or 0),
            height=int(item.get("height_o") or item.get("height_c") or 0),
            rights_status="Revisar licencia",
        )
