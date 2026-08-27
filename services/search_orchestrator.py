from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace

from models.entity import ResolvedEntity
from models.photo import Photo
from models.scene import Scene
from utils.relevance import MIN_ENTITY_RELEVANCE, assess_entity_relevance
from utils.scoring import score_photo
from .entity_resolver import EntityResolver
from .europeana import EuropeanaService
from .query_builder import add_queries
from .wikimedia import WikimediaService


@dataclass(slots=True)
class SearchResults:
    by_scene: dict[str, list[Photo]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    entity: ResolvedEntity | None = None
    verified_pool_size: int = 0
    rejected_count: int = 0


class SearchOrchestrator:
    def __init__(self, timeout: int | None = None) -> None:
        self.timeout = timeout or int(os.getenv("HTTP_TIMEOUT", "20"))

    def _providers(self, sources: list[str]) -> tuple[list[object], list[str]]:
        providers: list[object] = []
        warnings: list[str] = []
        if "Wikimedia Commons" in sources:
            providers.append(WikimediaService(self.timeout))
        if "Europeana" in sources:
            key = os.getenv("EUROPEANA_API_KEY", "").strip()
            if key:
                providers.append(EuropeanaService(key, self.timeout))
            else:
                warnings.append("Europeana se omitió porque falta EUROPEANA_API_KEY en .env.")
        return providers, warnings

    @staticmethod
    def _queries_for_provider(
        provider: object,
        entity: ResolvedEntity,
        scenes: list[Scene],
    ) -> list[tuple[str | None, str]]:
        queries: list[tuple[str | None, str]] = []
        if isinstance(provider, WikimediaService):
            queries.append((None, f"haswbstatement:P180={entity.qid}"))
        else:
            queries.append((None, f'"{entity.label}"'))
        for scene in scenes:
            for query in scene.query_variants:
                if not isinstance(provider, WikimediaService) and query.startswith("haswbstatement:"):
                    continue
                queries.append((scene.key, query))
        return list(dict.fromkeys(queries))

    @staticmethod
    def _merge_photo(target: Photo, incoming: Photo) -> None:
        target.depicts_qids = list(dict.fromkeys([*target.depicts_qids, *incoming.depicts_qids]))
        target.categories = list(dict.fromkeys([*target.categories, *incoming.categories]))
        target.matched_scene_keys = list(
            dict.fromkeys([*target.matched_scene_keys, *incoming.matched_scene_keys])
        )
        if incoming.entity_relevance > target.entity_relevance:
            target.entity_relevance = incoming.entity_relevance
            target.entity_evidence = incoming.entity_evidence

    def search(
        self,
        character: str,
        scenes: list[Scene],
        sources: list[str],
        commercial_only: bool = True,
        candidates_per_scene: int = 10,
        aliases: list[str] | None = None,
        entity: ResolvedEntity | None = None,
    ) -> SearchResults:
        entity = entity or EntityResolver(self.timeout).resolve(character, aliases or [])
        if any(not scene.query_variants for scene in scenes):
            add_queries(character, scenes, aliases or [], entity)

        providers, warnings = self._providers(sources)
        result = SearchResults(warnings=warnings, entity=entity)
        result.by_scene = {scene.key: [] for scene in scenes}
        if not providers:
            result.warnings.append("No hay ninguna fuente disponible para buscar.")
            return result

        jobs = {}
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(providers) * 3))) as executor:
            for provider in providers:
                for scene_key, query in self._queries_for_provider(provider, entity, scenes):
                    if isinstance(provider, WikimediaService):
                        future = executor.submit(provider.search, query, 30, entity)
                    else:
                        future = executor.submit(provider.search, query, 30)
                    jobs[future] = (provider.name, scene_key)

            raw_pool: dict[str, Photo] = {}
            for future in as_completed(jobs):
                provider_name, scene_key = jobs[future]
                try:
                    photos = future.result()
                except Exception as exc:
                    warnings.append(f"{provider_name} falló durante ENTITY LOCK: {exc}")
                    continue
                for photo in photos:
                    if scene_key and scene_key not in photo.matched_scene_keys:
                        photo.matched_scene_keys.append(scene_key)
                    existing = raw_pool.get(photo.image_url)
                    if existing:
                        self._merge_photo(existing, photo)
                    else:
                        raw_pool[photo.image_url] = photo

        verified_pool: list[Photo] = []
        for photo in raw_pool.values():
            if not photo.entity_relevance:
                photo.entity_relevance, photo.entity_evidence = assess_entity_relevance(photo, entity)
            if photo.entity_relevance < MIN_ENTITY_RELEVANCE:
                result.rejected_count += 1
                continue
            if commercial_only and photo.traffic_light != "green":
                continue
            verified_pool.append(photo)
        result.verified_pool_size = len(verified_pool)

        rankings: dict[str, list[Photo]] = {}
        for scene in scenes:
            ranked: list[Photo] = []
            for base in verified_pool:
                photo = replace(
                    base,
                    categories=list(base.categories),
                    depicts_qids=list(base.depicts_qids),
                    matched_scene_keys=list(base.matched_scene_keys),
                )
                photo.score = score_photo(photo, scene, character)
                minimum = 35 if scene.is_hook else 50
                if photo.scene_relevance >= minimum:
                    ranked.append(photo)
            rankings[scene.key] = sorted(
                ranked,
                key=lambda item: (item.entity_relevance, item.score, item.technical_score),
                reverse=True,
            )

        # Asignación global: una misma imagen no aparece en dos escenas.
        used_urls: set[str] = set()
        per_scene_limit = min(6, max(1, candidates_per_scene))
        for _ in range(per_scene_limit):
            for scene in scenes:
                next_photo = next(
                    (photo for photo in rankings[scene.key] if photo.image_url not in used_urls),
                    None,
                )
                if next_photo is None:
                    continue
                result.by_scene[scene.key].append(next_photo)
                used_urls.add(next_photo.image_url)
        return result
