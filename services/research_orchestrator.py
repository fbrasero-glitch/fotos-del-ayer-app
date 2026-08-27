from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit, urlunsplit

from models.entity import ResolvedEntity
from models.photo import Photo
from models.scene import Scene
from services.entity_resolver import EntityResolver
from services.gemini_service import GeminiError, GeminiService
from services.query_builder import add_queries
from services.research_cache import ResearchCache
from services.search_providers import ProviderContext, build_provider_registry
from utils.relevance import MIN_ENTITY_RELEVANCE, assess_entity_relevance
from utils.scoring import score_research_technical, score_scene_relevance
from utils.text_utils import normalize_text


@dataclass(slots=True)
class ResearchResults:
    by_scene: dict[str, list[Photo]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    entity: ResolvedEntity | None = None
    total_found: int = 0
    analyzed_count: int = 0
    excellent_count: int = 0
    rights_review_count: int = 0
    discarded_count: int = 0
    cache_hits: int = 0
    gemini_enabled: bool = False


class ResearchOrchestrator:
    def __init__(
        self,
        timeout: int | None = None,
        cache_path: str = "data/fotos_de_ayer.db",
        gemini: GeminiService | None = None,
    ) -> None:
        self.timeout = timeout or int(os.getenv("HTTP_TIMEOUT", "20"))
        self.cache = ResearchCache(cache_path)
        self.gemini = gemini or GeminiService(timeout=max(30, self.timeout))

    @staticmethod
    def _canonical_url(url: str) -> str:
        try:
            split = urlsplit(url)
            return urlunsplit((split.scheme.lower(), split.netloc.lower(), split.path, "", ""))
        except ValueError:
            return url

    @staticmethod
    def _merge_photo(target: Photo, incoming: Photo) -> None:
        target.depicts_qids = list(dict.fromkeys([*target.depicts_qids, *incoming.depicts_qids]))
        target.categories = list(dict.fromkeys([*target.categories, *incoming.categories]))
        target.matched_scene_keys = list(
            dict.fromkeys([*target.matched_scene_keys, *incoming.matched_scene_keys])
        )
        target.metadata.update(incoming.metadata)
        if incoming.entity_relevance > target.entity_relevance:
            target.entity_relevance = incoming.entity_relevance
            target.entity_evidence = incoming.entity_evidence

    @staticmethod
    def _identity_names(entity: ResolvedEntity) -> list[str]:
        names = [entity.label, *entity.aliases]
        return list(dict.fromkeys(name.strip() for name in names if len(name.split()) >= 2))[:4]

    def _analyze_script(
        self,
        character: str,
        script: str,
        entity: ResolvedEntity,
        scenes: list[Scene],
        warnings: list[str],
    ) -> None:
        signature = hashlib.sha256(script.encode("utf-8")).hexdigest()
        data = self.cache.get_gemini("script", entity.qid, signature, self.gemini.model)
        if data is None and self.gemini.configured:
            try:
                data = self.gemini.analyze_script(character, entity, scenes)
                self.cache.set_gemini(
                    "script", data, entity.qid, signature, self.gemini.model
                )
            except GeminiError as exc:
                warnings.append(f"Gemini no pudo analizar el guion; se usó el parser local. {exc}")
        if not isinstance(data, dict):
            for scene in scenes:
                scene.visual_concepts = list(scene.keywords)
            return

        by_index = {
            int(item.get("scene_index", -1)): item
            for item in data.get("scenes", [])
            if isinstance(item, dict)
        }
        names = [normalize_text(name) for name in self._identity_names(entity)]
        for scene in scenes:
            item = by_index.get(scene.index, {})
            concepts = [
                str(value).strip()
                for value in item.get("visual_concepts", [])
                if str(value).strip()
            ][:6]
            safe_phrases = []
            for value in item.get("search_phrases", []):
                phrase = str(value).strip()
                normalized = normalize_text(phrase)
                if phrase and any(name in normalized for name in names):
                    safe_phrases.append(phrase)
            scene.visual_concepts = concepts or list(scene.keywords)
            scene.analysis_note = str(item.get("explanation", "")).strip()
            scene.query_variants = list(
                dict.fromkeys([*scene.query_variants, *safe_phrases[:5]])
            )

    def _provider_queries(
        self,
        provider_name: str,
        entity: ResolvedEntity,
        scenes: list[Scene],
    ) -> list[tuple[str | None, str]]:
        name = self._identity_names(entity)[0]
        pairs: list[tuple[str | None, str]] = []
        if provider_name == "Wikimedia Commons":
            pairs.append((None, f"haswbstatement:P180={entity.qid}"))
        else:
            pairs.append((None, f'"{name}"'))

        for scene in scenes:
            concepts = (scene.visual_concepts or scene.keywords or ["portrait"])[:1]
            for concept in concepts:
                if provider_name == "Wikimedia Commons":
                    pairs.append((scene.key, f"haswbstatement:P180={entity.qid} {concept}"))
                pairs.append((scene.key, f'"{name}" {concept}'))
            for phrase in scene.query_variants[:1]:
                if not phrase.startswith("haswbstatement:"):
                    pairs.append((scene.key, phrase))
        return list(dict.fromkeys(pairs))

    def _search_one(
        self,
        provider_name: str,
        provider: object,
        query: str,
        entity: ResolvedEntity,
        scene_key: str | None,
        limit: int,
    ) -> tuple[list[Photo], bool]:
        cached = self.cache.get_search(provider_name, query, limit, entity.qid)
        if cached is not None:
            photos = cached
            cache_hit = True
        else:
            photos = provider.search(
                query,
                limit,
                ProviderContext(entity=entity, scene_key=scene_key),
            )
            self.cache.set_search(provider_name, query, limit, entity.qid, photos)
            cache_hit = False
        for photo in photos:
            if scene_key and scene_key not in photo.matched_scene_keys:
                photo.matched_scene_keys.append(scene_key)
        return photos, cache_hit

    @staticmethod
    def _metadata_scene_match(photo: Photo, scene: Scene) -> int:
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
        if scene.is_hook:
            portrait_terms = ("portrait", "retrato", "close up", "headshot", "princess diana")
            return 100 if any(term in haystack for term in portrait_terms) else 40

        concepts = scene.visual_concepts or scene.keywords
        matched = sum(
            1 for concept in concepts
            if normalize_text(concept) and normalize_text(concept) in haystack
        )
        ratio = matched / len(concepts) if concepts else 0
        query_evidence = 20 if scene.key in photo.matched_scene_keys else 0
        return min(100, round(80 * ratio) + query_evidence)

    @staticmethod
    def _apply_ai(photo: Photo, data: dict) -> None:
        def bounded(name: str) -> int:
            try:
                return max(0, min(100, int(data.get(name, 0))))
            except (TypeError, ValueError):
                return 0

        photo.entity_relevance = bounded("person_match")
        photo.scene_relevance = bounded("scene_match")
        photo.visual_impact = bounded("visual_impact")
        photo.ai_description = str(data.get("description", "")).strip()
        photo.ai_recommended = bool(data.get("recommended", False))
        photo.final_score = round(
            photo.entity_relevance * 0.40
            + photo.scene_relevance * 0.30
            + photo.visual_impact * 0.20
            + photo.technical_score * 0.10
        )
        photo.score = photo.final_score
        photo.entity_evidence = (
            f"Gemini vision: person_match={photo.entity_relevance}/100. "
            + photo.ai_description
        )

    def _analyze_scene(
        self,
        character: str,
        entity: ResolvedEntity,
        scene: Scene,
        candidates: list[Photo],
        warnings: list[str],
    ) -> list[Photo]:
        pending: list[Photo] = []
        for photo in candidates:
            cached = self.cache.get_gemini(
                "image",
                entity.qid,
                scene.text,
                self._canonical_url(photo.thumbnail_url or photo.image_url),
                self.gemini.model,
            )
            if isinstance(cached, dict):
                self._apply_ai(photo, cached)
            else:
                pending.append(photo)

        if pending and self.gemini.configured:
            try:
                analyses = self.gemini.analyze_images(character, entity, scene, pending)
            except GeminiError as exc:
                warnings.append(f"Gemini no pudo analizar imágenes de {scene.label}: {exc}")
                analyses = []
            for item in analyses:
                try:
                    photo = pending[int(item.get("candidate_index", -1))]
                except (IndexError, TypeError, ValueError):
                    continue
                self._apply_ai(photo, item)
                self.cache.set_gemini(
                    "image",
                    item,
                    entity.qid,
                    scene.text,
                    self._canonical_url(photo.thumbnail_url or photo.image_url),
                    self.gemini.model,
                )

        accepted: list[Photo] = []
        for photo in candidates:
            if self.gemini.configured:
                if photo.entity_relevance < MIN_ENTITY_RELEVANCE:
                    continue
                if scene.is_hook:
                    pass
                elif photo.scene_relevance < 45 or not photo.ai_recommended:
                    continue
            else:
                photo.scene_relevance = score_scene_relevance(photo, scene)
                photo.visual_impact = 60 if scene.is_hook else 50
                photo.final_score = round(
                    photo.entity_relevance * 0.40
                    + photo.scene_relevance * 0.30
                    + photo.visual_impact * 0.20
                    + photo.technical_score * 0.10
                )
                photo.score = photo.final_score
                if photo.entity_relevance < MIN_ENTITY_RELEVANCE:
                    continue
            accepted.append(photo)
        return sorted(
            accepted,
            key=lambda photo: (
                photo.final_score,
                photo.entity_relevance,
                photo.scene_relevance,
                photo.visual_impact,
            ),
            reverse=True,
        )

    def search(
        self,
        character: str,
        script: str,
        scenes: list[Scene],
        sources: list[str],
        aliases: list[str] | None = None,
        entity: ResolvedEntity | None = None,
        candidates_per_scene: int = 10,
        pool_limit_per_query: int = 25,
        ai_analysis_limit: int = 6,
    ) -> ResearchResults:
        entity = entity or EntityResolver(self.timeout).resolve(character, aliases or [])
        if any(not scene.query_variants for scene in scenes):
            add_queries(character, scenes, aliases or [], entity)

        result = ResearchResults(
            entity=entity,
            by_scene={scene.key: [] for scene in scenes},
            gemini_enabled=self.gemini.configured,
        )
        self._analyze_script(character, script, entity, scenes, result.warnings)

        registry, configuration_warnings = build_provider_registry(self.timeout)
        selected = {
            name: registry[name]
            for name in sources
            if name in registry and registry[name].configured
        }
        for warning in configuration_warnings:
            source_name = warning.split(" no está", 1)[0]
            if source_name in sources:
                result.warnings.append(warning)
        if not selected:
            result.warnings.append("No hay proveedores configurados para el modo investigación.")
            return result

        jobs = {}
        with ThreadPoolExecutor(max_workers=min(10, max(1, len(selected) * 4))) as executor:
            for provider_name, provider in selected.items():
                for scene_key, query in self._provider_queries(provider_name, entity, scenes):
                    future = executor.submit(
                        self._search_one,
                        provider_name,
                        provider,
                        query,
                        entity,
                        scene_key,
                        pool_limit_per_query,
                    )
                    jobs[future] = (provider_name, query)

            pool: dict[str, Photo] = {}
            for future in as_completed(jobs):
                provider_name, _query = jobs[future]
                try:
                    photos, cache_hit = future.result()
                except Exception as exc:
                    result.warnings.append(f"{provider_name} falló: {exc}")
                    continue
                result.cache_hits += int(cache_hit)
                for photo in photos:
                    key = self._canonical_url(photo.image_url or photo.thumbnail_url)
                    if not key:
                        continue
                    existing = pool.get(key)
                    if existing:
                        self._merge_photo(existing, photo)
                    else:
                        pool[key] = photo

        result.total_found = len(pool)
        preverified: list[Photo] = []
        for photo in pool.values():
            score, evidence = assess_entity_relevance(photo, entity)
            photo.entity_relevance = max(photo.entity_relevance, score)
            if evidence:
                photo.entity_evidence = evidence
            photo.technical_score = score_research_technical(photo)
            if photo.traffic_light == "green":
                photo.rights_status = "Licencia favorable; revisar ficha original"
            elif photo.traffic_light == "red":
                photo.rights_status = "Uso restringido"
            else:
                photo.rights_status = "Revisar licencia"
            # En fuentes de descubrimiento Gemini decide visualmente la identidad.
            if score >= MIN_ENTITY_RELEVANCE or (
                self.gemini.configured and not photo.metadata.get("discovery_only", False)
            ):
                preverified.append(photo)

        candidates_by_scene: dict[str, list[Photo]] = {}
        for scene in scenes:
            candidates_by_scene[scene.key] = sorted(
                [
                    replace(
                        photo,
                        categories=list(photo.categories),
                        depicts_qids=list(photo.depicts_qids),
                        matched_scene_keys=list(photo.matched_scene_keys),
                        metadata=dict(photo.metadata),
                    )
                    for photo in preverified
                ],
                key=lambda photo: (
                    self._metadata_scene_match(photo, scene),
                    photo.entity_relevance,
                    photo.technical_score,
                ),
                reverse=True,
            )[: max(1, min(20, ai_analysis_limit))]

        rankings: dict[str, list[Photo]] = {}
        with ThreadPoolExecutor(max_workers=min(3, max(1, len(scenes)))) as executor:
            analysis_jobs = {
                executor.submit(
                    self._analyze_scene,
                    character,
                    entity,
                    scene,
                    candidates_by_scene[scene.key],
                    result.warnings,
                ): scene.key
                for scene in scenes
            }
            for future in as_completed(analysis_jobs):
                scene_key = analysis_jobs[future]
                try:
                    rankings[scene_key] = future.result()
                except Exception as exc:
                    result.warnings.append(f"Análisis visual falló para {scene_key}: {exc}")
                    rankings[scene_key] = []

        used_urls: set[str] = set()
        limit = max(1, min(10, candidates_per_scene))
        displayed: list[Photo] = []
        for _ in range(limit):
            for scene in scenes:
                photo = next(
                    (
                        item for item in rankings[scene.key]
                        if self._canonical_url(item.image_url) not in used_urls
                    ),
                    None,
                )
                if photo is None:
                    continue
                result.by_scene[scene.key].append(photo)
                displayed.append(photo)
                used_urls.add(self._canonical_url(photo.image_url))

        result.analyzed_count = sum(len(items) for items in candidates_by_scene.values())
        result.excellent_count = sum(photo.final_score >= 80 for photo in displayed)
        result.rights_review_count = sum(photo.traffic_light != "green" for photo in displayed)
        result.discarded_count = max(0, result.total_found - len(used_urls))
        return result
