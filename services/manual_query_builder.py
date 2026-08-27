from __future__ import annotations

import hashlib
import json
import re

from models.entity import ResolvedEntity
from models.manual_search import ManualSearch
from services.gemini_service import GeminiError, GeminiService
from services.research_cache import ResearchCache
from utils.text_utils import normalize_text, words


TOKEN_TRANSLATIONS = {
    "gancho": "portrait",
    "joven": "young",
    "retrato": "portrait",
    "coche": "car",
    "enfadada": "angry",
    "enfadado": "angry",
    "dentro": "inside",
    "corriendo": "running",
    "correr": "running",
    "gimnasio": "gym",
    "sola": "alone",
    "solo": "alone",
    "mar": "sea",
    "playa": "beach",
    "paparazzi": "paparazzi",
    "fotografos": "photographers",
    "fotógrafos": "photographers",
    "calle": "street",
    "llorando": "crying",
    "sonriendo": "smiling",
    "familia": "family",
    "boda": "wedding",
    "hospital": "hospital",
    "palacio": "palace",
}
DROP_TOKENS = {"foto", "fotografia", "fotografía", "imagen", "de", "del", "la", "el", "una", "un", "person", "people", "hook"}

MANUAL_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "queries": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "index": {"type": "INTEGER"},
                    "english_query": {"type": "STRING"},
                    "concepts": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["index", "english_query", "concepts"],
            },
        }
    },
    "required": ["queries"],
}


def parse_manual_lines(value: str) -> list[str]:
    lines: list[str] = []
    for raw in value.splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", raw).strip()
        if line and line not in lines:
            lines.append(line)
    return lines


def _local_translation(value: str) -> tuple[str, list[str]]:
    translated: list[str] = []
    for token in words(value):
        normalized = normalize_text(token)
        if normalized in DROP_TOKENS:
            continue
        translated.append(TOKEN_TRANSLATIONS.get(normalized, token))
    compact = " ".join(dict.fromkeys(translated)).strip() or value.strip()
    return compact, compact.split()


def _identity_name(entity: ResolvedEntity) -> str:
    if entity.qid == "Q9685":
        return "Princess Diana"
    aliases = [entity.label, *entity.aliases]
    return next((name for name in aliases if len(name.split()) >= 2), entity.label)


def build_manual_searches(
    lines: list[str],
    entity: ResolvedEntity,
    gemini: GeminiService | None = None,
    cache: ResearchCache | None = None,
) -> tuple[list[ManualSearch], list[str]]:
    warnings: list[str] = []
    local = {index: _local_translation(line) for index, line in enumerate(lines)}
    ai_data: dict[int, dict] = {}

    if gemini and gemini.configured and lines:
        signature = hashlib.sha256(json.dumps(lines, ensure_ascii=False).encode()).hexdigest()
        cached = cache.get_gemini("manual_queries", entity.qid, signature, gemini.model) if cache else None
        if cached is None:
            prompt = (
                "Translate each user-defined historical photo search into a compact literal English "
                "image-search phrase. Do not invent scenes, events or facts. Remove generic words such "
                "as photo/image. Keep emotions, actions, places and shot type. Return the same indexes. "
                "Input JSON: "
                + json.dumps(
                    [{"index": i, "query": line} for i, line in enumerate(lines)],
                    ensure_ascii=False,
                )
            )
            try:
                cached = gemini._generate([{"text": prompt}], MANUAL_SCHEMA)
                if cache:
                    cache.set_gemini(
                        "manual_queries", cached, entity.qid, signature, gemini.model
                    )
            except GeminiError as exc:
                warnings.append(f"Gemini no pudo reformular las búsquedas; se usó traducción local. {exc}")
        if isinstance(cached, dict):
            ai_data = {
                int(item.get("index", -1)): item
                for item in cached.get("queries", [])
                if isinstance(item, dict)
            }

    identity = _identity_name(entity)
    searches: list[ManualSearch] = []
    for index, original in enumerate(lines):
        fallback, fallback_concepts = local[index]
        item = ai_data.get(index, {})
        translated = str(item.get("english_query", "")).strip() or fallback
        translated = re.sub(
            rf"^\s*(?:{re.escape(identity)}|{re.escape(entity.label)})\s+",
            "",
            translated,
            flags=re.IGNORECASE,
        ).strip()
        clean_tokens = [
            TOKEN_TRANSLATIONS.get(normalize_text(token), token)
            for token in words(translated)
            if normalize_text(token) not in DROP_TOKENS
        ]
        if "gancho" in normalize_text(original) and "portrait" not in clean_tokens:
            clean_tokens.append("portrait")
        translated = " ".join(dict.fromkeys(clean_tokens)).strip() or fallback

        raw_concepts = [
            str(value).strip()
            for value in item.get("concepts", [])
            if str(value).strip()
        ] or fallback_concepts
        concepts = [
            TOKEN_TRANSLATIONS.get(normalize_text(value), value)
            for value in raw_concepts
            if normalize_text(value) not in DROP_TOKENS
        ]
        concepts = list(dict.fromkeys([*translated.split(), *concepts]))[:8]
        variants = [f'"{identity}" {translated}'.strip()]
        variants.extend(f'"{identity}" {concept}' for concept in concepts[:4])
        searches.append(
            ManualSearch(
                index=index,
                original=original,
                translated=translated,
                concepts=concepts,
                query_variants=list(dict.fromkeys(variants)),
            )
        )
    return searches, warnings
