from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from models.entity import ResolvedEntity
from models.photo import Photo
from models.scene import Scene
from services.fast_search import MAX_RESULTS, FastPhotoSearch
from services.gemini_service import GeminiError
from services.local_vision_service import LocalVisionError, LocalVisionService
from services.photo_state_store import stable_photo_key
from services.production_store import ProductionStore
from utils.text_utils import normalize_text


VISION_DISQUALIFYING_TERMS = (
    "cartel",
    "poster",
    "portada",
    "album cover",
    "vinilo",
    "infografia",
    "collage",
    "captura",
    "screenshot",
    "texto superpuesto",
    "marca de agua grande",
)


@dataclass(slots=True)
class PhotoQualityResult:
    photos: list[Photo] = field(default_factory=list)
    analyzed_count: int = 0
    cache_hits: int = 0
    good_count: int = 0
    warnings: list[str] = field(default_factory=list)
    vision_available: bool = False


class PhotoQualityPipeline:
    """Technical prefilter plus cached local visual ranking for mobile Shorts."""

    def __init__(
        self,
        store: ProductionStore,
        vision: LocalVisionService | object | None = None,
    ) -> None:
        self.store = store
        self.vision = vision or LocalVisionService()

    @staticmethod
    def scene_signature(entity: ResolvedEntity, scene_text: str, is_hook: bool) -> str:
        normalized = " ".join(scene_text.casefold().split())
        raw = f"{entity.qid}\u241f{int(is_hook)}\u241f{normalized}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _score(value: Any, default: int = 0) -> int:
        try:
            return max(0, min(100, int(value)))
        except (TypeError, ValueError):
            return default

    @classmethod
    def apply_analysis(cls, photo: Photo, analysis: dict[str, Any]) -> None:
        person = cls._score(analysis.get("person_match"))
        scene = cls._score(analysis.get("scene_match"))
        impact = cls._score(analysis.get("visual_impact"))
        mobile = cls._score(
            analysis.get("mobile_crop"), FastPhotoSearch.mobile_fit_score(photo)
        )
        clean = cls._score(analysis.get("clean_image"), 50)
        real_photo = bool(analysis.get("real_photo", True))
        issues = analysis.get("quality_issues", [])
        if not isinstance(issues, list):
            issues = []
        description = str(analysis.get("description", "") or "")
        vision_text = normalize_text(" ".join([description, *[str(item) for item in issues]]))
        if any(normalize_text(term) in vision_text for term in VISION_DISQUALIFYING_TERMS):
            real_photo = False
            issues = [*issues, "El análisis identifica material gráfico o texto, no una foto limpia."]

        final = round(
            person * 0.35
            + scene * 0.30
            + impact * 0.15
            + mobile * 0.12
            + clean * 0.08
        )
        if not real_photo:
            final = min(final, 25)

        recommended = bool(analysis.get("recommended")) and all(
            (real_photo, person >= 65, scene >= 45, clean >= 35, mobile >= 30)
        )
        photo.entity_relevance = person
        photo.scene_relevance = scene
        photo.visual_impact = impact
        photo.technical_score = round(mobile * 0.65 + clean * 0.35)
        photo.final_score = final
        photo.score = final
        photo.ai_recommended = recommended
        photo.ai_description = description
        photo.relevance_reason = photo.ai_description
        photo.metadata.update(
            {
                "vision_mobile_crop": mobile,
                "vision_clean_image": clean,
                "vision_real_photo": real_photo,
                "vision_quality_issues": [str(item) for item in issues[:4]],
            }
        )

    def rank(
        self,
        slot_id: int,
        character: str,
        entity: ResolvedEntity,
        scene_text: str,
        is_hook: bool,
        photos: list[Photo],
        *,
        analyze_missing: bool = True,
        limit: int = MAX_RESULTS,
        batch_size: int = 4,
        max_new_analyses: int | None = None,
    ) -> PhotoQualityResult:
        selected = FastPhotoSearch.shortlist(photos, limit, entity)
        result = PhotoQualityResult(photos=selected)
        if not selected:
            return result

        signature = self.scene_signature(entity, scene_text, is_hook)
        model = self.vision.model
        keys = [stable_photo_key(photo) for photo in selected]
        cached = self.store.get_visual_analyses(slot_id, signature, model, keys)
        result.cache_hits = len(cached)
        for photo in selected:
            analysis = cached.get(stable_photo_key(photo))
            if analysis:
                self.apply_analysis(photo, analysis)
                photo.metadata["vision_model"] = model

        pending = [photo for photo in selected if stable_photo_key(photo) not in cached]
        if max_new_analyses is not None:
            pending = pending[: max(0, int(max_new_analyses))]
        result.vision_available = self.vision.configured if analyze_missing else bool(cached)
        if analyze_missing and pending and result.vision_available:
            scene = Scene(
                index=0,
                label="Gancho" if is_hook else "Escena",
                text=scene_text or "young portrait",
                keywords=(scene_text or "young portrait").split(),
                visual_concepts=(scene_text or "young portrait").split(),
                is_hook=is_hook,
            )
            for start in range(0, len(pending), max(1, batch_size)):
                batch = pending[start : start + max(1, batch_size)]
                try:
                    analyses = self.vision.analyze_images(character, entity, scene, batch)
                except (GeminiError, LocalVisionError) as exc:
                    result.warnings.append(str(exc))
                    break
                for item in analyses:
                    try:
                        photo = batch[int(item.get("candidate_index", -1))]
                    except (IndexError, TypeError, ValueError):
                        continue
                    self.apply_analysis(photo, item)
                    photo.metadata["vision_model"] = model
                    self.store.save_visual_analysis(slot_id, signature, photo, model, item)
                    result.analyzed_count += 1
        elif analyze_missing and pending and not result.vision_available:
            result.warnings.append(
                f"Ollama no está disponible con {model}; se mantiene el orden técnico."
            )

        result.photos.sort(
            key=lambda photo: (
                int(photo.ai_recommended),
                photo.final_score,
                FastPhotoSearch.mobile_fit_score(photo),
                max(0, int(photo.width or 0)) * max(0, int(photo.height or 0)),
            ),
            reverse=True,
        )
        result.good_count = sum(
            photo.ai_recommended and photo.final_score >= 62 for photo in result.photos
        )
        return result
