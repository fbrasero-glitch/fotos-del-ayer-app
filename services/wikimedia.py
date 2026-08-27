from __future__ import annotations

import hashlib
import re
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from models.entity import ResolvedEntity
from models.photo import Photo
from utils.licenses import assess_license
from utils.relevance import assess_entity_relevance
from utils.text_utils import strip_html


class WikimediaService:
    name = "Wikimedia Commons"
    endpoint = "https://commons.wikimedia.org/w/api.php"

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
        self.session.headers.update(
            {"User-Agent": "FotosDeAyer/0.2 (entity-locked historical-photo research)"}
        )

    @staticmethod
    def _metadata_value(metadata: dict[str, Any], key: str, default: str = "") -> str:
        item = metadata.get(key, {})
        if isinstance(item, dict):
            return strip_html(str(item.get("value", default)))
        return strip_html(str(item or default))

    @staticmethod
    def _claim_qids(entity_data: dict, property_id: str) -> list[str]:
        qids: list[str] = []
        for claim in (entity_data.get("claims") or entity_data.get("statements") or {}).get(property_id, []):
            value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
            if isinstance(value, dict) and value.get("id"):
                qids.append(value["id"])
        return qids

    def _attach_structured_data(self, pages: list[dict[str, Any]]) -> None:
        page_ids = [page.get("pageid") for page in pages if page.get("pageid")]
        if not page_ids:
            return
        response = self.session.get(
            self.endpoint,
            params={
                "action": "wbgetentities",
                "format": "json",
                "ids": "|".join(f"M{page_id}" for page_id in page_ids[:50]),
                "props": "claims",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        entities = response.json().get("entities", {})
        for page in pages:
            media_info = entities.get(f"M{page.get('pageid')}", {})
            page["_depicts_qids"] = self._claim_qids(media_info, "P180")

    def search(
        self,
        query: str,
        limit: int = 20,
        entity: ResolvedEntity | None = None,
    ) -> list[Photo]:
        if not query.strip():
            return []
        params = {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "generator": "search",
            "gsrsearch": query.strip(),
            "gsrnamespace": 6,
            "gsrlimit": max(1, min(50, limit)),
            "prop": "imageinfo|categories",
            "cllimit": "max",
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": 640,
            "iiextmetadatalanguage": "es",
            "iiextmetadatafilter": (
                "ImageDescription|ObjectName|Artist|Credit|DateTimeOriginal|DateTime|"
                "LicenseShortName|UsageTerms|LicenseUrl|Copyrighted|AttributionRequired|"
                "Restrictions|Institution"
            ),
        }
        response = self.session.get(self.endpoint, params=params, timeout=self.timeout)
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", [])
        structured_match = re.search(r"haswbstatement:P180=(Q\d+)", query)
        if structured_match:
            # El propio índice de Commons garantiza que estos resultados tienen ese P180.
            for page in pages:
                page["_depicts_qids"] = [structured_match.group(1)]
        else:
            self._attach_structured_data(pages)

        photos: list[Photo] = []
        for page in pages:
            photo = self._normalize(page)
            if not photo:
                continue
            if entity:
                photo.entity_relevance, photo.entity_evidence = assess_entity_relevance(photo, entity)
            photos.append(photo)
        return photos

    def _normalize(self, page: dict[str, Any]) -> Photo | None:
        image_info = (page.get("imageinfo") or [{}])[0]
        image_url = image_info.get("url", "")
        if not image_url:
            return None
        mime = image_info.get("mime", "")
        if mime and not mime.startswith("image/"):
            return None

        metadata = image_info.get("extmetadata", {})
        license_name = self._metadata_value(metadata, "LicenseShortName") or self._metadata_value(
            metadata, "UsageTerms", "Licencia desconocida"
        )
        license_url = self._metadata_value(metadata, "LicenseUrl")
        assessment = assess_license(
            license_name,
            license_url,
            " ".join((
                self._metadata_value(metadata, "Copyrighted"),
                self._metadata_value(metadata, "Restrictions"),
            )),
        )
        attribution_raw = self._metadata_value(metadata, "AttributionRequired").lower()
        attribution = assessment.attribution_required
        if attribution_raw in {"true", "yes", "1"}:
            attribution = True
        elif attribution_raw in {"false", "no", "0"}:
            attribution = False

        raw_title = page.get("title", "")
        title = self._metadata_value(metadata, "ObjectName") or raw_title.removeprefix("File:")
        identifier = hashlib.sha1(image_url.encode("utf-8")).hexdigest()[:16]
        return Photo(
            id=f"wikimedia:{identifier}",
            title=title,
            thumbnail_url=image_info.get("thumburl", image_url),
            image_url=image_url,
            original_page_url=image_info.get("descriptionurl", ""),
            author=self._metadata_value(metadata, "Artist", "Autor desconocido"),
            date=self._metadata_value(metadata, "DateTimeOriginal")
            or self._metadata_value(metadata, "DateTime", "Fecha desconocida"),
            source=self.name,
            institution=self._metadata_value(metadata, "Institution")
            or self._metadata_value(metadata, "Credit"),
            license=license_name,
            license_url=license_url,
            license_description=assessment.description,
            commercial_use=assessment.commercial_use,
            attribution_required=attribution,
            traffic_light=assessment.traffic_light,
            width=int(image_info.get("width", 0) or 0),
            height=int(image_info.get("height", 0) or 0),
            description=self._metadata_value(metadata, "ImageDescription"),
            categories=[
                item.get("title", "").removeprefix("Category:")
                for item in page.get("categories", [])
                if item.get("title")
            ],
            depicts_qids=list(dict.fromkeys(page.get("_depicts_qids", []))),
        )
