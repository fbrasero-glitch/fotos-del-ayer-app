from __future__ import annotations

from urllib.parse import unquote, urlparse

from models.entity import ResolvedEntity
from models.photo import Photo
from .text_utils import normalize_text


MIN_ENTITY_RELEVANCE = 75
DISQUALIFYING_TITLE_TERMS = {
    "memorial", "fountain", "fontaine", "clematis", "flower", "flowers", "rose",
    "statue", "sculpture", "wax figure", "ship", "boat", "park", "garden",
    "playground", "tribute", "church", "window", "vitrail", "stained glass",
    "plaque", "cup", "saucer", "plate", "madame tussaud", "contact sheet",
}


def _identity_terms(entity: ResolvedEntity) -> list[str]:
    terms: list[str] = []
    for value in [entity.label, *entity.aliases]:
        normalized = normalize_text(value)
        if len(normalized.split()) >= 2 and normalized not in terms:
            terms.append(normalized)
    return terms


def assess_entity_relevance(photo: Photo, entity: ResolvedEntity) -> tuple[int, str]:
    title = normalize_text(photo.title)
    if any(term in title for term in DISQUALIFYING_TITLE_TERMS):
        return 0, "El título describe un monumento, objeto, planta o lugar, no al personaje."
    if entity.qid in photo.depicts_qids:
        return 100, f"Structured Data on Commons: depicts (P180)={entity.qid}"
    if photo.depicts_qids:
        return 0, "Los datos estructurados representan otras entidades, no el personaje bloqueado."

    if entity.image_filename:
        image_name = normalize_text(unquote(urlparse(photo.image_url).path.rsplit("/", 1)[-1]))
        expected = normalize_text(entity.image_filename)
        if expected and (expected in image_name or expected in title):
            return 100, f"Imagen principal (P18) de {entity.qid}"

    terms = _identity_terms(entity)
    for term in terms:
        if term in title:
            return 86, f"Nombre/alias no ambiguo en el título: {term}"

    categories_text = normalize_text(" ".join(photo.categories))
    has_specific_category = any(term in categories_text for term in terms)
    person_title_cues = {"princess", "princesa", "lady", "diana", "spencer"}
    if has_specific_category and len(person_title_cues.intersection(title.split())) >= 2:
        return 80, "Título personal + categoría específica de la entidad."

    return 0, "Sin vínculo verificable con la entidad bloqueada."
