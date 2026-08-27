from __future__ import annotations

from pathlib import Path

from services.downloader import download_photo
from services.fast_search import FastPhotoSearch
from services.photo_state_store import stable_photo_key
from services.production_store import ProductionStore
from services.project_files import slot_directory


DB = "data/fotos_de_ayer.db"
ROOT = Path("proyectos_fotos")

# slot_id: (source prefix, rank within the source after the same local shortlist used by the app)
SELECTIONS = {
    33: ("Brave Images", 3),
    37: ("Google Images", 3),
    38: ("Brave Images", 1),
    39: ("Brave Images", 1),
    40: ("Google Images", 1),
    41: ("Brave Images", 3),
    34: ("Brave Images", 3),
    35: ("Brave Images", 1),
    42: ("Brave Images", 2),
    43: ("Brave Images", 2),
    44: ("Brave Images", 1),
    45: ("Brave Images", 2),
    46: ("Brave Images", 2),
    36: ("Brave Images", 1),
}


def main() -> None:
    store = ProductionStore(DB)
    store.initialize()
    for slot_id, (source_prefix, rank) in SELECTIONS.items():
        slot = store.get_slot(slot_id)
        project = store.get_project(slot.project_id)
        photos = [
            photo
            for photo in store.slot_photos(slot_id)
            if photo.source.startswith(source_prefix)
        ]
        photos = FastPhotoSearch.shortlist(photos, 12)
        photo = photos[rank - 1]
        local_path = store.get_download(slot_id, photo)
        if not local_path or not Path(local_path).is_file():
            local_path = str(download_photo(photo, slot_directory(ROOT, project, slot)))
            store.save_download(slot_id, photo, local_path)
        store.choose_candidate(slot_id, photo)
        store.record_decision(
            slot_id,
            photo,
            "use",
            "experimental",
            photo.traffic_light or "unchecked",
            local_path,
        )
        print(f"{slot_id}\t{slot.label}\t{photo.source}\t{photo.title}\t{local_path}", flush=True)


if __name__ == "__main__":
    main()
