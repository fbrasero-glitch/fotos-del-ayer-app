from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from models.entity import ResolvedEntity
from models.photo import Photo
from services.api_usage import ApiQuotaExceeded, ApiUsageStore, UsageSnapshot
from services.search_providers import ProviderContext
from services.story_search import (
    BRAVE_SOURCE,
    GOOGLE_SOURCE,
    LIMIT_SETTINGS,
    build_story_registry,
)
from utils.text_utils import normalize_text


DUCKDUCKGO_SOURCE = "DuckDuckGo Images"
ARCHIVE_SOURCES = ("Wikimedia Commons", "Europeana")
PRIMARY_DISCOVERY_SOURCES = (DUCKDUCKGO_SOURCE,)
FALLBACK_DISCOVERY_SOURCES = (BRAVE_SOURCE, GOOGLE_SOURCE)
OPTIONAL_DISCOVERY_SOURCES = ARCHIVE_SOURCES
FREE_DISCOVERY_SOURCES = (*PRIMARY_DISCOVERY_SOURCES,)
PEXELS_SOURCE = "Pexels"
PROVIDER_FETCH_LIMIT = 40
QUICK_FETCH_LIMIT = 20
MAX_RESULTS = 18
MAX_FINALISTS = 8
MAX_VISUAL_CANDIDATES = 6
QUALITY_TARGET = 4

DISQUALIFYING_RESULT_TERMS = {
    "album", "vinyl", "lp", "single", "discogs", "cover", "poster", "cartel",
    "youtube", "screenshot", "captura", "screen shot", "lyrics", "letra",
    "spotify", "genius", "musica.com", "discos", "pin de", "pinimg", "statue", "estatua",
    "sculpture", "escultura", "monument", "monumento", "memorial", "bust",
    "busto", "wax figure", "figura de cera", "museum exhibit", "exhibition",
    "bathroom", "bano", "shower", "ducha", "tile", "azulejo", "pinterest pin",
}


@dataclass(slots=True)
class FastSearchResult:
    photos: list[Photo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)
    api_calls: dict[str, int] = field(default_factory=dict)
    usage: dict[str, UsageSnapshot] = field(default_factory=dict)
    by_source: dict[str, list[Photo]] = field(default_factory=dict)


class FastPhotoSearch:
    """Una consulta por fuente, sin análisis ni descargas de miniaturas en el servidor."""

    def __init__(
        self,
        usage_path: str = "data/fotos_de_ayer.db",
        timeout: int | None = None,
    ) -> None:
        self.timeout = timeout or int(os.getenv("HTTP_TIMEOUT", "15"))
        self.usage_store = ApiUsageStore(usage_path)

    @staticmethod
    def configured_sources() -> dict[str, bool]:
        return {
            name: bool(provider.configured)
            for name, provider in build_story_registry().items()
        }

    @staticmethod
    def canonical_name(entity: ResolvedEntity) -> str:
        if entity.qid == "Q9685":
            return "Princess Diana"
        candidates = [entity.label, *entity.aliases]
        return next(
            (name.strip() for name in candidates if len(name.strip().split()) >= 2),
            entity.label,
        )

    @staticmethod
    def query_for(
        source: str,
        entity: ResolvedEntity,
        keywords: str,
        is_hook: bool,
    ) -> str:
        clean = " ".join(keywords.strip().split())
        if is_hook and not clean:
            clean = "young portrait"
        name = FastPhotoSearch.canonical_name(entity)
        if source == PEXELS_SOURCE:
            # Pexels se usa únicamente como recurso ambiental, sin identidad.
            return clean or "historical atmosphere"
        if source == "Wikimedia Commons":
            archive_terms = " ".join(clean.split()[:2])
            return f"haswbstatement:P180={entity.qid} {archive_terms}".strip()
        if source == DUCKDUCKGO_SOURCE:
            detail = clean or ("young portrait" if is_hook else "historical photograph")
            return f'"{name}" {detail} photograph'.strip()
        return f'"{name}" {clean}'.strip()

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

    def _search_one(
        self,
        source: str,
        provider: object,
        query: str,
        entity: ResolvedEntity,
        count: int,
    ) -> tuple[str, list[Photo]]:
        monthly_limit = self._limit(source)
        if monthly_limit is not None:
            self.usage_store.reserve(source, monthly_limit)
        try:
            photos = provider.search(
                query,
                max(1, min(PROVIDER_FETCH_LIMIT, count)),
                ProviderContext(entity=entity, scene_key="fast"),
            )
        except Exception:
            if monthly_limit is not None:
                self.usage_store.release(source)
            raise
        for position, photo in enumerate(photos, start=1):
            photo.metadata["search_position"] = position
            if photo.metadata.get("discovery_only"):
                photo.rights_status = "Revisar derechos en la fuente original"
            elif photo.traffic_light == "green":
                photo.rights_status = f"Licencia favorable · {photo.license}"
            else:
                photo.rights_status = "Revisar derechos"
        return source, photos

    @staticmethod
    def mobile_fit_score(photo: Photo) -> int:
        """Score how well a known image will survive a 9:16 mobile crop."""
        width = max(0, int(photo.width or 0))
        height = max(0, int(photo.height or 0))
        if not width or not height:
            return 0

        minimum_side = min(width, height)
        resolution = min(45, round(45 * minimum_side / 1400))
        if height < width:
            return resolution

        # A 9:16 source needs almost no crop. A 4:5 portrait is still much
        # better than a landscape source because the subject remains legible.
        aspect = width / height
        distance = abs(aspect - (9 / 16))
        framing = round(35 * max(0.0, 1.0 - distance / 0.45))
        return min(100, resolution + 20 + framing)

    @classmethod
    def _local_score(
        cls,
        photo: Photo,
        entity: ResolvedEntity | None = None,
    ) -> tuple[int, int, int, int, int]:
        """Order by mobile fit, resolution and known metadata without downloads."""
        width = max(0, int(photo.width or 0))
        height = max(0, int(photo.height or 0))
        known_size = int(bool(width and height))
        large_enough = int(not known_size or min(width, height) >= 500)
        pixels = width * height
        haystack = normalize_text(
            " ".join(
                (
                    photo.title,
                    photo.description,
                    photo.institution,
                    photo.original_page_url,
                    photo.image_url,
                    photo.thumbnail_url,
                )
            )
        )
        identity = 0
        if entity:
            identity = int(
                any(
                    normalize_text(term) in haystack
                    for term in [entity.label, *entity.aliases]
                    if normalize_text(term)
                )
            )
        artifact_free = int(
            not any(normalize_text(term) in haystack for term in DISQUALIFYING_RESULT_TERMS)
        )
        return (
            artifact_free,
            identity,
            large_enough,
            cls.mobile_fit_score(photo),
            pixels,
        )

    @classmethod
    def shortlist(
        cls,
        photos: list[Photo],
        limit: int = MAX_RESULTS,
        entity: ResolvedEntity | None = None,
    ) -> list[Photo]:
        """Elimina duplicados evidentes y devuelve las mejores candidatas técnicas."""
        exact: dict[str, Photo] = {}
        for photo in photos:
            key = (photo.image_url or photo.thumbnail_url).split("?", 1)[0].casefold()
            if not key:
                continue
            width = max(0, int(photo.width or 0))
            height = max(0, int(photo.height or 0))
            if width and height and min(width, height) < 500:
                continue
            haystack = normalize_text(
                " ".join(
                    (
                        photo.title,
                        photo.description,
                        photo.institution,
                        photo.original_page_url,
                        photo.image_url,
                        photo.thumbnail_url,
                    )
                )
            )
            if any(normalize_text(term) in haystack for term in DISQUALIFYING_RESULT_TERMS):
                photo.metadata["local_prefilter"] = "Descartada por artefacto o resultado no fotográfico."
                continue
            exact.setdefault(key, photo)
        ranked = sorted(
            exact.values(),
            key=lambda photo: cls._local_score(photo, entity),
            reverse=True,
        )
        return ranked[: max(1, min(PROVIDER_FETCH_LIMIT, limit))]

    def search(
        self,
        entity: ResolvedEntity,
        keywords: str,
        sources: list[str],
        is_hook: bool = False,
        count: int = MAX_RESULTS,
    ) -> FastSearchResult:
        count = max(1, min(PROVIDER_FETCH_LIMIT, int(count)))
        result = FastSearchResult()
        registry = build_story_registry(self.timeout)
        selected: list[tuple[str, object, str]] = []
        for source in sources:
            provider = registry.get(source)
            if provider is None:
                result.warnings.append(f"Fuente desconocida: {source}.")
            elif not provider.configured:
                result.warnings.append(f"{source} no está configurado.")
            else:
                selected.append(
                    (source, provider, self.query_for(source, entity, keywords, is_hook))
                )
        if not selected:
            result.usage = self.usage_snapshot()
            return result

        raw: list[Photo] = []
        with ThreadPoolExecutor(max_workers=len(selected)) as executor:
            jobs = {
                executor.submit(
                    self._search_one, source, provider, query, entity, count
                ): source
                for source, provider, query in selected
            }
            for future in as_completed(jobs):
                source = jobs[future]
                try:
                    used_source, photos = future.result()
                except ApiQuotaExceeded as exc:
                    result.warnings.append(str(exc))
                    continue
                except Exception as exc:
                    result.warnings.append(f"{source} falló ({type(exc).__name__}).")
                    continue
                result.sources_used.append(used_source)
                result.api_calls[used_source] = 1
                result.by_source[used_source] = photos
                raw.extend(photos)

        result.photos = self.shortlist(raw, count, entity)
        result.usage = self.usage_snapshot()
        return result
