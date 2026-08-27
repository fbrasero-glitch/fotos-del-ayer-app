from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

from models.entity import ResolvedEntity
from models.manual_search import ManualSearch
from models.photo import Photo
from models.scene import Scene
from services.api_usage import ApiQuotaExceeded, ApiUsageStore, UsageSnapshot
from services.entity_resolver import EntityResolver
from services.gemini_service import GeminiError, GeminiService
from services.image_deduplicator import ImageDeduplicator
from services.local_vision_service import LocalVisionError, LocalVisionService
from services.manual_query_builder import build_manual_searches
from services.research_cache import ResearchCache
from services.search_providers import ProviderContext
from services.search_providers.brave_images import BraveImagesProvider
from services.search_providers.duckduckgo_images import DuckDuckGoImagesProvider
from services.search_providers.europeana import EuropeanaProvider
from services.search_providers.flickr_commons import FlickrCommonsProvider
from services.search_providers.pexels import PexelsProvider
from services.search_providers.serpapi_google_images import SerpApiGoogleImagesProvider
from services.search_providers.wikimedia import WikimediaProvider
from utils.relevance import assess_entity_relevance
from utils.text_utils import normalize_text, words


FREE_SOURCES = (
    "Wikimedia Commons",
    "Europeana",
    "DuckDuckGo Images",
    "Flickr Commons",
    "Pexels",
)
BRAVE_SOURCE = "Brave Images"
GOOGLE_SOURCE = "Google Images · SerpAPI"
SOURCE_PRIORITY = {
    "Wikimedia Commons": 0,
    "Europeana": 1,
    "DuckDuckGo Images": 2,
    "Flickr Commons": 3,
    "Pexels": 4,
    BRAVE_SOURCE: 5,
    GOOGLE_SOURCE: 6,
}
LIMIT_SETTINGS = {
    "Pexels": ("PEXELS_MONTHLY_LIMIT", 18_000),
    BRAVE_SOURCE: ("BRAVE_MONTHLY_LIMIT", 900),
    GOOGLE_SOURCE: ("SERPAPI_MONTHLY_LIMIT", 200),
}
CACHE_DAYS = {
    "Wikimedia Commons": 7,
    "Europeana": 7,
    "DuckDuckGo Images": 1,
    "Flickr Commons": 7,
    "Pexels": 1,
    BRAVE_SOURCE: 0,
    GOOGLE_SOURCE: 1,
}


@dataclass(slots=True)
class StorySearchResult:
    photos: list[Photo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)
    api_calls: dict[str, int] = field(default_factory=dict)
    cache_hits: int = 0
    usage: dict[str, UsageSnapshot] = field(default_factory=dict)
    search: ManualSearch | None = None
    entity: ResolvedEntity | None = None
    visual_analyzed: int = 0


def build_story_registry(timeout: int = 20) -> dict[str, object]:
    return {
        "Wikimedia Commons": WikimediaProvider(timeout),
        "Europeana": EuropeanaProvider(os.getenv("EUROPEANA_API_KEY", ""), timeout),
        "DuckDuckGo Images": DuckDuckGoImagesProvider(timeout),
        "Flickr Commons": FlickrCommonsProvider(os.getenv("FLICKR_API_KEY", ""), timeout),
        "Pexels": PexelsProvider(os.getenv("PEXELS_API_KEY", ""), timeout),
        BRAVE_SOURCE: BraveImagesProvider(os.getenv("BRAVE_SEARCH_API_KEY", ""), timeout),
        GOOGLE_SOURCE: SerpApiGoogleImagesProvider(
            os.getenv("SERPAPI_API_KEY", ""), timeout
        ),
    }


class StoryPhotoSearch:
    """Búsqueda de una escena, una sola petición por fuente y bajo control del usuario."""

    def __init__(
        self,
        cache_path: str = "data/fotos_de_ayer.db",
        timeout: int | None = None,
        gemini: GeminiService | None = None,
        local_vision: LocalVisionService | None = None,
    ) -> None:
        self.timeout = timeout or int(os.getenv("HTTP_TIMEOUT", "20"))
        self.cache = ResearchCache(cache_path)
        self.usage_store = ApiUsageStore(cache_path)
        self.deduplicator = ImageDeduplicator(cache_path)
        self.gemini = gemini or GeminiService(timeout=max(30, self.timeout))
        self.local_vision = local_vision or LocalVisionService()

    @property
    def vision_service(self) -> GeminiService | LocalVisionService:
        preferred = os.getenv("VISION_BACKEND", "local").strip().casefold()
        if preferred != "gemini" and self.local_vision.configured:
            return self.local_vision
        return self.gemini

    @property
    def vision_configured(self) -> bool:
        return bool(self.vision_service.configured)

    @property
    def vision_label(self) -> str:
        service = self.vision_service
        return getattr(service, "label", f"Gemini · {service.model}")

    @staticmethod
    def _limit(source: str) -> int | None:
        setting = LIMIT_SETTINGS.get(source)
        if not setting:
            return None
        env_name, default = setting
        try:
            return max(0, int(os.getenv(env_name, str(default))))
        except ValueError:
            return default

    def usage_snapshot(self) -> dict[str, UsageSnapshot]:
        snapshots: dict[str, UsageSnapshot] = {}
        for source in LIMIT_SETTINGS:
            limit = self._limit(source)
            if limit is not None:
                snapshots[source] = self.usage_store.snapshot(source, limit)
        return snapshots

    @staticmethod
    def configured_sources() -> dict[str, bool]:
        return {
            name: bool(provider.configured)
            for name, provider in build_story_registry().items()
        }

    @staticmethod
    def _query_for(source: str, search: ManualSearch, entity: ResolvedEntity) -> str:
        if source == "Wikimedia Commons":
            return f"haswbstatement:P180={entity.qid} {search.translated}".strip()
        return search.query_variants[0]

    def _search_source(
        self,
        source: str,
        provider: object,
        query: str,
        entity: ResolvedEntity,
        limit: int,
    ) -> tuple[list[Photo], bool, bool]:
        cache_days = CACHE_DAYS.get(source, 7)
        cached = (
            self.cache.get_search(source, query, limit, entity.qid, days=cache_days)
            if cache_days > 0
            else None
        )
        if cached is not None:
            return cached, True, False

        monthly_limit = self._limit(source)
        if monthly_limit is not None:
            self.usage_store.reserve(source, monthly_limit)
        try:
            photos = provider.search(
                query,
                limit,
                ProviderContext(entity=entity, scene_key="manual_scene"),
            )
        except Exception:
            if monthly_limit is not None:
                self.usage_store.release(source)
            raise
        if cache_days > 0:
            self.cache.set_search(source, query, limit, entity.qid, photos)
        return photos, False, True

    @staticmethod
    def _score(photo: Photo, search: ManualSearch, entity: ResolvedEntity) -> bool:
        entity_score, evidence = assess_entity_relevance(photo, entity)
        if photo.depicts_qids and entity.qid not in photo.depicts_qids:
            return False
        if entity_score == 0:
            entity_score = 55
            evidence = "Identidad sugerida por la consulta; necesita comprobación visual."

        haystack = normalize_text(
            " ".join(
                (
                    photo.title,
                    photo.description,
                    " ".join(photo.categories),
                    str(photo.metadata),
                )
            )
        )
        tokens = list(
            dict.fromkeys(
                normalize_text(token)
                for token in words(search.translated)
                if len(normalize_text(token)) > 2
            )
        )
        matched = [token for token in tokens if token in haystack]
        semantic = 25 if not tokens else min(100, 25 + round(75 * len(matched) / len(tokens)))
        photo.entity_relevance = entity_score
        photo.scene_relevance = semantic
        photo.search_relevance = round(entity_score * 0.65 + semantic * 0.35)
        photo.final_score = photo.search_relevance
        photo.score = photo.final_score
        photo.entity_evidence = evidence
        photo.relevance_reason = (
            f"{evidence} "
            + (
                "Conceptos coincidentes: " + ", ".join(matched)
                if matched
                else "Metadatos escasos; revisar la escena visualmente."
            )
        )
        if photo.metadata.get("discovery_only"):
            photo.rights_status = "Revisar derechos · fuente de descubrimiento"
        elif photo.traffic_light == "green":
            photo.rights_status = f"Licencia favorable · {photo.license}"
        else:
            photo.rights_status = "Revisar derechos"
        return True

    def search_scene(
        self,
        character: str,
        scene_text: str,
        sources: list[str],
        aliases: list[str] | None = None,
        entity: ResolvedEntity | None = None,
        is_hook: bool = False,
        limit: int = 40,
    ) -> StorySearchResult:
        entity = entity or EntityResolver(self.timeout).resolve(character, aliases or [])
        manual_text = f"foto gancho retrato joven {scene_text}" if is_hook else scene_text
        searches, warnings = build_manual_searches(
            [manual_text], entity, self.gemini, self.cache
        )
        result = StorySearchResult(warnings=warnings, entity=entity)
        if not searches:
            result.warnings.append("La escena no contiene una búsqueda válida.")
            return result
        result.search = searches[0]
        registry = build_story_registry(self.timeout)
        raw: list[Photo] = []
        request_limit = max(10, min(100, int(limit)))

        for source in sources:
            provider = registry.get(source)
            if provider is None:
                result.warnings.append(f"Fuente desconocida: {source}.")
                continue
            if not provider.configured:
                result.warnings.append(f"{source} no está configurado y se omitió.")
                continue
            query = self._query_for(source, result.search, entity)
            try:
                photos, cache_hit, api_called = self._search_source(
                    source, provider, query, entity, request_limit
                )
            except ApiQuotaExceeded as exc:
                result.warnings.append(str(exc))
                continue
            except Exception as exc:
                result.warnings.append(f"{source} falló ({type(exc).__name__}).")
                continue
            result.sources_used.append(source)
            result.cache_hits += int(cache_hit)
            if api_called:
                result.api_calls[source] = result.api_calls.get(source, 0) + 1
            for photo in photos:
                photo.matched_searches = list(
                    dict.fromkeys([*photo.matched_searches, "manual_scene"])
                )
            raw.extend(photos)

        exact: dict[str, Photo] = {}
        for photo in raw:
            key = (photo.image_url or photo.thumbnail_url).split("?", 1)[0].casefold()
            if not key:
                continue
            current = exact.get(key)
            if current:
                current.metadata.setdefault("duplicate_sources", []).append(
                    {"source": photo.source, "url": photo.original_page_url, "title": photo.title}
                )
            else:
                exact[key] = photo
        scored = [
            photo for photo in exact.values() if self._score(photo, result.search, entity)
        ]
        scored.sort(
            key=lambda photo: (
                photo.search_relevance,
                -SOURCE_PRIORITY.get(photo.source, 99),
                photo.width * photo.height,
            ),
            reverse=True,
        )
        result.photos, _removed = self.deduplicator.deduplicate(scored)
        result.photos.sort(
            key=lambda photo: (
                photo.search_relevance,
                -SOURCE_PRIORITY.get(photo.source, 99),
            ),
            reverse=True,
        )
        result.usage = self.usage_snapshot()
        return result

    def analyze_visuals(
        self,
        result: StorySearchResult,
        scene_text: str,
        is_hook: bool,
        maximum: int = 8,
    ) -> StorySearchResult:
        vision = self.vision_service
        if not result.entity or not result.photos or not vision.configured:
            result.warnings.append(
                f"{getattr(vision, 'label', 'Visión')} no está configurado o no hay candidatos."
            )
            return result
        candidates = result.photos[: max(1, min(12, maximum))]
        scene = Scene(
            index=0 if is_hook else 1,
            label="Gancho" if is_hook else "Escena manual",
            text=scene_text,
            keywords=list(result.search.concepts if result.search else []),
            is_hook=is_hook,
            visual_concepts=list(result.search.concepts if result.search else []),
        )
        try:
            analyses = vision.analyze_images(
                result.entity.label, result.entity, scene, candidates
            )
        except (GeminiError, LocalVisionError) as exc:
            result.warnings.append(f"El análisis visual no pudo completarse: {exc}")
            return result
        for item in analyses:
            try:
                photo = candidates[int(item.get("candidate_index", -1))]
            except (IndexError, TypeError, ValueError):
                continue
            def bounded(name: str) -> int:
                try:
                    return max(0, min(100, int(item.get(name, 0))))
                except (TypeError, ValueError):
                    return 0
            photo.entity_relevance = bounded("person_match")
            photo.scene_relevance = bounded("scene_match")
            photo.visual_impact = bounded("visual_impact")
            photo.ai_description = str(item.get("description", "")).strip()
            photo.ai_recommended = bool(item.get("recommended", False))
            photo.final_score = round(
                photo.entity_relevance * 0.40
                + photo.scene_relevance * 0.35
                + photo.visual_impact * 0.25
            )
            photo.search_relevance = photo.final_score
            photo.score = photo.final_score
            photo.relevance_reason = (
                f"Cribado visual: identidad {photo.entity_relevance}/100, "
                f"escena {photo.scene_relevance}/100, impacto {photo.visual_impact}/100. "
                f"{photo.ai_description}"
            )
        result.visual_analyzed += len(analyses)
        result.photos.sort(
            key=lambda photo: (
                photo.ai_recommended,
                photo.final_score,
                photo.search_relevance,
            ),
            reverse=True,
        )
        return result

    def merge(self, left: StorySearchResult, right: StorySearchResult) -> StorySearchResult:
        combined = StorySearchResult(
            photos=[
                replace(
                    photo,
                    categories=list(photo.categories),
                    depicts_qids=list(photo.depicts_qids),
                    matched_searches=list(photo.matched_searches),
                    metadata=dict(photo.metadata),
                )
                for photo in [*left.photos, *right.photos]
            ],
            warnings=list(dict.fromkeys([*left.warnings, *right.warnings])),
            sources_used=list(dict.fromkeys([*left.sources_used, *right.sources_used])),
            api_calls=dict(left.api_calls),
            cache_hits=left.cache_hits + right.cache_hits,
            usage=right.usage or left.usage,
            search=left.search or right.search,
            entity=left.entity or right.entity,
            visual_analyzed=left.visual_analyzed + right.visual_analyzed,
        )
        for source, calls in right.api_calls.items():
            combined.api_calls[source] = combined.api_calls.get(source, 0) + calls
        exact: dict[str, Photo] = {}
        for photo in combined.photos:
            key = (photo.image_url or photo.thumbnail_url).split("?", 1)[0].casefold()
            if key and key not in exact:
                exact[key] = photo
        combined.photos, _removed = self.deduplicator.deduplicate(list(exact.values()))
        combined.photos.sort(
            key=lambda photo: (photo.ai_recommended, photo.final_score, photo.search_relevance),
            reverse=True,
        )
        return combined
