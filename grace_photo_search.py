"""Búsqueda controlada de fotografías para el proyecto Grace Kelly."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from services.fast_search import ARCHIVE_SOURCES, BRAVE_SOURCE, FastPhotoSearch
from services.production_store import ProductionStore

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "fotos_de_ayer.db"
load_dotenv(ROOT / ".env")

FREE_QUERIES = {
    "hook": "young portrait 1954",
    "scene-1": "Academy Award Oscar 1955 actress",
    "scene-2": "Prince Rainier Monaco palace 1955 Cannes",
    "scene-3": "wedding Prince Rainier Monaco 1956",
    "scene-4": "Princess Monaco children family palace",
    "scene-5": "Princess Monaco 1981 1982",
    "final": "Princess Monaco portrait smiling",
}

BRAVE_QUERIES = {
    "hook": "young portrait 1954 high quality",
    "scene-1": "Academy Awards Oscar 1955",
    "scene-2": "first meeting Prince Rainier Monaco palace 1955",
    "scene-3": "wedding Prince Rainier Monaco 1956",
    "scene-4": "Princess Grace Monaco children family candid",
    "scene-5": "Princess Grace Monaco 1981 1982 last photos",
    "final": "Princess Grace Monaco iconic portrait smiling",
}


def project_and_slots(store: ProductionStore):
    project = next(p for p in store.list_projects() if p.character == "Grace Kelly")
    return project, {slot.slot_key: slot for slot in store.list_slots(project.id)}


def search_and_store(source: str, slot_key: str, query: str, count: int = 12) -> int:
    store = ProductionStore(DB_PATH)
    searcher = FastPhotoSearch(str(DB_PATH))
    project, slots = project_and_slots(store)
    slot = slots[slot_key]
    is_hook = slot.kind == "hook"
    built_query = searcher.query_for(source, project.entity, query, is_hook)
    fingerprint = store.fingerprint(project.entity.qid, source, built_query)
    saved = store.get_search(fingerprint)
    if not saved:
        result = searcher.search(project.entity, query, [source], is_hook, count)
        if source not in result.sources_used:
            print(f"{slot_key} | {source} | 0 | {'; '.join(result.warnings)}")
            return 0
        saved = store.save_search(
            fingerprint,
            project.entity.qid,
            source,
            built_query,
            query,
            is_hook,
            count,
            result.photos,
        )
    store.attach_search(slot.id, saved.id)
    print(f"{slot_key} | {source} | {len(saved.photos)} | {built_query}")
    return len(saved.photos)


def run_free() -> None:
    for slot_key, query in FREE_QUERIES.items():
        for source in ARCHIVE_SOURCES:
            search_and_store(source, slot_key, query)


def run_brave(slot_keys: list[str]) -> None:
    for slot_key in slot_keys:
        search_and_store(BRAVE_SOURCE, slot_key, BRAVE_QUERIES[slot_key])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--brave", nargs="*", choices=list(BRAVE_QUERIES))
    parser.add_argument("--slot", choices=list(BRAVE_QUERIES))
    parser.add_argument("--query")
    args = parser.parse_args()
    if args.slot and args.query:
        search_and_store(BRAVE_SOURCE, args.slot, args.query)
    elif args.brave is None:
        run_free()
    else:
        run_brave(args.brave)
