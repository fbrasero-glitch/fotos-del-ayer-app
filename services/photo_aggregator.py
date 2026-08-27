from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import requests

from models.entity import ResolvedEntity
from models.manual_search import ManualSearch
from models.photo import Photo
from services.entity_resolver import EntityResolver
from services.gemini_service import GeminiService
from services.image_deduplicator import ImageDeduplicator
from services.manual_query_builder import build_manual_searches
from services.research_cache import ResearchCache
from services.search_providers import ProviderContext, build_provider_registry
from utils.relevance import (
    DISQUALIFYING_TITLE_TERMS,
    assess_entity_relevance,
)
from utils.text_utils import normalize_text, words


SOURCE_PRIORITY = {
    "Bing Images": 0,
    "Google Images": 1,
    "Wikimedia Commons": 2,
    "Europeana": 3,
    "Flickr Commons": 4,
    "Pinterest (descubrimiento)": 5,
}


@dataclass(slots=True)
class AggregatorResults:
    photos: list[Photo] = field(default_factory=list)
    by_search: dict[str, list[Photo]] = field(default_factory=dict)
    searches: list[ManualSearch] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    entity: ResolvedEntity | None = None
    total_raw: int = 0
    unique_before_dedup: int = 0
    duplicates_removed: int = 0
    cache_hits: int = 0
    providers_used: list[str] = field(default_factory=list)


class PhotoAggregator:
    def __init__(
        self,
        timeout: int | None = None,
        cache_path: str = "data/fotos_de_ayer.db",
        gemini: GeminiService | None = None,
    ) -> None:
        self.timeout = timeout or int(os.getenv("HTTP_TIMEOUT", "20"))
        self.cache = ResearchCache(cache_path)
        self.deduplicator = ImageDeduplicator(cache_path)
        self.gemini = gemini or GeminiService(timeout=max(30, self.timeout))

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            return f"HTTP {exc.response.status_code}"
        if isinstance(exc, requests.Timeout):
            return "tiempo de espera agotado"
        return type(exc).__name__

    @staticmethod
    def _provider_queries(
        provider_name: str,
        search: ManualSearch,
        entity: ResolvedEntity,
    ) -> list[str]:
        identity_queries = search.query_variants[:3]
        if provider_name == "Wikimedia Commons":
            structured = [
                f"haswbstatement:P180={entity.qid} {term}"
                for term in [search.translated, *search.concepts[:1]]
                if term
            ]
            return list(dict.fromkeys([*structured, *identity_queries[:2]]))
        return list(dict.fromkeys(identity_queries))

    def _search_one(
        self,
        provider_name: str,
        provider: object,
        query: str,
        search_key: str | None,
        entity: ResolvedEntity,
        limit: int,
    ) -> tuple[list[Photo], bool]:
        cached = self.cache.get_search(provider_name, query, limit, entity.qid)
        if cached is not None:
            photos = cached
            hit = True
        else:
            photos = provider.search(
                query,
                limit,
                ProviderContext(entity=entity, scene_key=search_key),
            )
            self.cache.set_search(provider_name, query, limit, entity.qid, photos)
            hit = False
        if search_key:
            for photo in photos:
                if search_key not in photo.matched_searches:
                    photo.matched_searches.append(search_key)
        return photos, hit

    @staticmethod
    def _merge_exact(target: Photo, incoming: Photo) -> None:
        target.matched_searches = list(
            dict.fromkeys([*target.matched_searches, *incoming.matched_searches])
        )
        target.depicts_qids = list(
            dict.fromkeys([*target.depicts_qids, *incoming.depicts_qids])
        )
        target.categories = list(dict.fromkeys([*target.categories, *incoming.categories]))
        if incoming.width * incoming.height > target.width * target.height:
            target.thumbnail_url = incoming.thumbnail_url or target.thumbnail_url
            target.image_url = incoming.image_url or target.image_url
            target.width = incoming.width
            target.height = incoming.height

    @staticmethod
    def _disqualified(photo: Photo) -> bool:
        haystack = normalize_text(f"{photo.title} {photo.description}")
        return any(term in haystack for term in DISQUALIFYING_TITLE_TERMS)

    @staticmethod
    def _semantic_score(photo: Photo, search: ManualSearch) -> tuple[int, list[str]]:
        haystack = normalize_text(
            " ".join(
                [
                    photo.title,
                    photo.description,
                    " ".join(photo.categories),
                    str(photo.metadata),
                ]
            )
        )
        tokens = [
            normalize_text(token)
            for token in words(search.translated)
            if len(normalize_text(token)) > 2
        ]
        tokens = list(dict.fromkeys(tokens))
        matched = [token for token in tokens if token in haystack]
        if not tokens:
            return 25, []
        return min(100, 25 + round(75 * len(matched) / len(tokens))), matched

    def _score_photo(
        self,
        photo: Photo,
        entity: ResolvedEntity,
        searches_by_key: dict[str, ManualSearch],
    ) -> bool:
        if self._disqualified(photo):
            return False

        entity_score, entity_evidence = assess_entity_relevance(photo, entity)
        if photo.depicts_qids and entity.qid not in photo.depicts_qids:
            return False
        if entity_score == 0:
            entity_score = 60
            entity_evidence = (
                "Identidad inferida por una consulta exacta con el personaje; "
                "requiere verificación visual."
            )

        best_score = 0
        best_reason = entity_evidence
        relevance_by_search: dict[str, int] = {}
        for key in photo.matched_searches:
            search = searches_by_key.get(key)
            if not search:
                continue
            semantic, matched = self._semantic_score(photo, search)
            relevance = round(entity_score * 0.65 + semantic * 0.35)
            relevance_by_search[key] = relevance
            if relevance > best_score:
                best_score = relevance
                concept_reason = (
                    "Conceptos coincidentes: " + ", ".join(matched)
                    if matched
                    else "Coincide con la consulta, pero los metadatos son escasos."
                )
                best_reason = f"{entity_evidence} {concept_reason}"

        photo.entity_relevance = entity_score
        photo.search_relevance = best_score or round(entity_score * 0.65 + 25 * 0.35)
        photo.relevance_reason = best_reason
        photo.metadata["relevance_by_search"] = relevance_by_search

        license_known = bool(
            photo.license
            and "desconocid" not in photo.license.casefold()
            and "revis" not in photo.license.casefold()
        )
        if photo.metadata.get("discovery_only"):
            photo.rights_status = "Revisar derechos · solo descubrimiento"
        elif license_known:
            photo.rights_status = f"Conocida · {photo.license}"
        else:
            photo.rights_status = "Revisar derechos"
        return True

    def search(
        self,
        character: str,
        aliases: list[str],
        manual_lines: list[str],
        sources: list[str],
        per_query_limit: int = 50,
        entity: ResolvedEntity | None = None,
    ) -> AggregatorResults:
        entity = entity or EntityResolver(self.timeout).resolve(character, aliases)
        result = AggregatorResults(entity=entity)
        searches, query_warnings = build_manual_searches(
            manual_lines,
            entity,
            self.gemini,
            self.cache,
        )
        result.searches = searches
        result.by_search = {search.key: [] for search in searches}
        result.warnings.extend(query_warnings)
        if not searches:
            result.warnings.append("No hay búsquedas manuales.")
            return result

        registry, configuration_warnings = build_provider_registry(self.timeout)
        selected: dict[str, object] = {}
        for name in sorted(sources, key=lambda value: SOURCE_PRIORITY.get(value, 99)):
            provider = registry.get(name)
            if provider and provider.configured:
                selected[name] = provider
            elif name in registry:
                result.warnings.append(f"{name} no está configurado y se omitió.")
        if not selected:
            result.warnings.append("No hay fuentes configuradas para buscar.")
            return result
        result.providers_used = list(selected)

        limit = max(1, min(100, int(per_query_limit)))
        jobs = {}
        all_search_keys = [search.key for search in searches]
        identity_name = "Princess Diana" if entity.qid == "Q9685" else entity.label
        with ThreadPoolExecutor(max_workers=min(12, max(1, len(selected) * 4))) as executor:
            for provider_name, provider in selected.items():
                broad_query = (
                    f"haswbstatement:P180={entity.qid}"
                    if provider_name == "Wikimedia Commons"
                    else f'"{identity_name}"'
                )
                broad_future = executor.submit(
                    self._search_one,
                    provider_name,
                    provider,
                    broad_query,
                    None,
                    entity,
                    limit,
                )
                jobs[broad_future] = (provider_name, None)
                for search in searches:
                    for query in self._provider_queries(provider_name, search, entity):
                        future = executor.submit(
                            self._search_one,
                            provider_name,
                            provider,
                            query,
                            search.key,
                            entity,
                            limit,
                        )
                        jobs[future] = (provider_name, search.key)

            raw: list[Photo] = []
            for future in as_completed(jobs):
                provider_name, search_key = jobs[future]
                try:
                    photos, cache_hit = future.result()
                except Exception as exc:
                    result.warnings.append(
                        f"{provider_name} falló ({self._safe_error(exc)})."
                    )
                    continue
                result.cache_hits += int(cache_hit)
                result.total_raw += len(photos)
                if search_key is None:
                    for photo in photos:
                        photo.metadata["broad_identity_pool"] = True
                raw.extend(photos)

        exact_pool: dict[str, Photo] = {}
        for photo in raw:
            key = (photo.image_url or photo.thumbnail_url).split("?", 1)[0].casefold()
            if not key:
                continue
            existing = exact_pool.get(key)
            if existing:
                self._merge_exact(existing, photo)
            else:
                exact_pool[key] = photo
        result.unique_before_dedup = len(exact_pool)

        searches_by_key = {search.key: search for search in searches}
        relevant = [
            photo
            for photo in exact_pool.values()
            if self._score_photo(photo, entity, searches_by_key)
        ]
        relevant.sort(
            key=lambda photo: (
                photo.search_relevance,
                -SOURCE_PRIORITY.get(photo.source, 99),
                photo.width * photo.height,
            ),
            reverse=True,
        )

        result.photos, result.duplicates_removed = self.deduplicator.deduplicate(relevant)
        result.photos.sort(
            key=lambda photo: (
                photo.search_relevance,
                -SOURCE_PRIORITY.get(photo.source, 99),
            ),
            reverse=True,
        )
        for photo in result.photos:
            for key in photo.matched_searches:
                if key in result.by_search:
                    result.by_search[key].append(photo)
        return result
