from __future__ import annotations

from models.entity import ResolvedEntity
from models.scene import Scene
from utils.text_utils import normalize_text


TRANSLATIONS = {
    "coche": "car", "calle": "street", "correr": "running", "corriendo": "running",
    "gimnasio": "gym", "mar": "sea", "playa": "beach", "fotografo": "photographer",
    "camara": "camera", "retrato": "portrait", "ventana": "window", "cristal": "car window",
    "multitud": "crowd", "familia": "family", "boda": "wedding", "tren": "train",
    "avion": "airplane", "casa": "home", "palacio": "palace", "jardin": "garden",
    "escenario": "stage", "sola": "alone", "solo": "alone", "llorando": "crying",
    "sonriendo": "smiling", "policia": "police", "guerra": "war", "desfile": "parade",
    "discurso": "speech", "persecucion": "paparazzi", "observada": "candid",
    "londres": "London", "semaforo": "traffic lights",
}


def parse_aliases(value: str | list[str]) -> list[str]:
    values = value if isinstance(value, list) else value.replace("\n", ",").split(",")
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))


def _translate(term: str) -> str:
    return TRANSLATIONS.get(normalize_text(term), term)


def _safe_names(entity: ResolvedEntity) -> list[str]:
    priority = ["Princess Diana" if entity.qid == "Q9685" else "", entity.label, *entity.aliases]
    names: list[str] = []
    normalized_names: set[str] = set()
    for name in priority:
        normalized = normalize_text(name)
        if not normalized or len(normalized.split()) < 2 or normalized in normalized_names:
            continue
        normalized_names.add(normalized)
        names.append(name)
    return names[:3]


def build_query_variants(entity: ResolvedEntity, scene: Scene) -> list[str]:
    concepts = list(dict.fromkeys(_translate(term) for term in scene.keywords))[:3]
    if scene.is_hook:
        concepts = ["portrait"]
    elif not concepts:
        concepts = ["photograph"]
    variants = [
        f"haswbstatement:P180={entity.qid} {concept}"
        for concept in concepts[:2]
    ]
    variants.append(f'"{_safe_names(entity)[0]}" {concepts[0]}')
    return list(dict.fromkeys(variant.strip() for variant in variants if variant.strip()))


def add_queries(
    character: str,
    scenes: list[Scene],
    aliases: list[str] | None = None,
    entity: ResolvedEntity | None = None,
) -> list[Scene]:
    if entity is None:
        from .entity_resolver import EntityResolver
        entity = EntityResolver().resolve(character, aliases or [])
    for scene in scenes:
        scene.query_variants = build_query_variants(entity, scene)
        scene.query = scene.query_variants[0]
    return scenes
