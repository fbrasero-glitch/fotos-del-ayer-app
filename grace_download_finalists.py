"""Descarga las finalistas de Grace Kelly y elige la mejor disponible por escena."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from services.downloader import download_photo
from services.production_store import ProductionStore
from services.project_files import slot_directory

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "fotos_de_ayer.db"
DOWNLOAD_ROOT = ROOT / "proyectos_fotos"


def main() -> None:
    store = ProductionStore(DB_PATH)
    project = next(p for p in store.list_projects() if p.character == "Grace Kelly")
    for slot in store.list_slots(project.id):
        candidates = [photo for photo, _ in store.list_candidates(slot.id)]
        candidates.sort(
            key=lambda p: (p.ai_recommended, p.final_score, p.width * p.height),
            reverse=True,
        )
        downloaded = []
        for photo in candidates[:3]:
            try:
                path = download_photo(photo, slot_directory(DOWNLOAD_ROOT, project, slot))
                with Image.open(path) as image:
                    width, height = image.size
                store.save_download(slot.id, photo, str(path))
                downloaded.append((photo, path, width, height))
                print(
                    f"OK | {slot.slot_key} | {width}x{height} | "
                    f"{photo.final_score} | {path.name}"
                )
            except Exception as exc:
                print(f"FALLO | {slot.slot_key} | {type(exc).__name__} | {photo.title}")
        usable = [item for item in downloaded if max(item[2], item[3]) >= 800]
        if usable:
            best = max(
                usable,
                key=lambda item: (
                    item[0].final_score,
                    item[2] * item[3],
                ),
            )
            store.choose_candidate(slot.id, best[0])
            print(f"ELEGIDA | {slot.slot_key} | {best[1].name}")


if __name__ == "__main__":
    main()
