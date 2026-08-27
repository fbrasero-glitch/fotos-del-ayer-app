"""Criba visual de hasta ocho candidatos por escena para Grace Kelly."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from models.scene import Scene
from services.gemini_service import GeminiService
from services.production_store import ProductionStore

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "fotos_de_ayer.db"
load_dotenv(ROOT / ".env")

TARGETS = {
    "scene-1": "Grace Kelly as a young Hollywood actress, preferably at the 1955 Academy Awards or holding her Oscar",
    "scene-2": "Grace Kelly meeting Prince Rainier III at the Monaco palace in 1955",
    "scene-3": "Grace Kelly wedding to Prince Rainier III in Monaco in 1956",
    "scene-4": "Princess Grace of Monaco during royal family life, preferably with her children or at the palace",
    "scene-5": "Princess Grace of Monaco in her mature final years, around 1981 or 1982",
    "final": "Iconic, beautiful and emotionally warm portrait of Princess Grace of Monaco",
    "hook": "Striking beautiful portrait of young Grace Kelly during her Hollywood years",
}


def main(source: str = "", slot_keys: list[str] | None = None) -> None:
    store = ProductionStore(DB_PATH)
    project = next(p for p in store.list_projects() if p.character == "Grace Kelly")
    gemini = GeminiService()
    for slot in store.list_slots(project.id):
        if slot_keys and slot.slot_key not in slot_keys:
            continue
        photos = store.slot_photos(slot.id)
        if source:
            photos = [photo for photo in photos if photo.source == source]
        if not photos:
            continue
        photos = sorted(
            photos,
            key=lambda p: (p.width > 0 and p.height > 0, p.width * p.height),
            reverse=True,
        )[:8]
        scene = Scene(
            index=slot.position,
            label=slot.label,
            text=TARGETS[slot.slot_key],
            keywords=TARGETS[slot.slot_key].split(),
            is_hook=slot.kind == "hook",
            visual_concepts=TARGETS[slot.slot_key].split(),
        )
        analyses = gemini.analyze_images(project.character, project.entity, scene, photos)
        ranked = []
        for analysis in analyses:
            try:
                photo = photos[int(analysis["candidate_index"])]
            except (IndexError, KeyError, TypeError, ValueError):
                continue
            person = max(0, min(100, int(analysis.get("person_match", 0))))
            scene_score = max(0, min(100, int(analysis.get("scene_match", 0))))
            impact = max(0, min(100, int(analysis.get("visual_impact", 0))))
            photo.entity_relevance = person
            photo.scene_relevance = scene_score
            photo.visual_impact = impact
            photo.final_score = round(person * 0.40 + scene_score * 0.35 + impact * 0.25)
            photo.score = photo.final_score
            photo.ai_recommended = bool(analysis.get("recommended", False))
            photo.ai_description = str(analysis.get("description", ""))
            ranked.append(photo)
        ranked.sort(
            key=lambda p: (p.ai_recommended, p.final_score, p.width * p.height),
            reverse=True,
        )
        finalists = [p for p in ranked if p.ai_recommended and p.entity_relevance >= 75][:3]
        for photo in finalists:
            store.add_candidate(slot.id, photo)
        print(f"--- {slot.slot_key} | revisadas={len(photos)} | finalistas={len(finalists)}")
        for photo in finalists:
            print(
                f"{photo.final_score} | persona={photo.entity_relevance} "
                f"escena={photo.scene_relevance} impacto={photo.visual_impact} | "
                f"{photo.title} | {photo.original_page_url}"
            )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="")
    parser.add_argument("--slots", nargs="*")
    args = parser.parse_args()
    main(args.source, args.slots)
