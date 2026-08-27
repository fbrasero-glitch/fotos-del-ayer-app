from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from services.production_store import ProductionProject, ProductionSlot
from utils.text_utils import normalize_text, safe_filename


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff", ".bmp"}


@dataclass(frozen=True, slots=True)
class LocalProjectImage:
    path: Path
    relative_path: Path
    slot_id: int | None
    detected_by: str


def project_directory(root: str | Path, project: ProductionProject) -> Path:
    return Path(root) / safe_filename(project.name, f"proyecto-{project.id}")


def slot_folder_name(slot: ProductionSlot) -> str:
    if slot.kind == "hook":
        return "01-gancho"
    if slot.kind == "final":
        return "99-foto-final"
    return f"{slot.position + 1:02d}-escena-{slot.position}-{safe_filename(slot.label)}"


def slot_directory(
    root: str | Path,
    project: ProductionProject,
    slot: ProductionSlot,
) -> Path:
    return project_directory(root, project) / slot_folder_name(slot)


def ensure_project_structure(
    root: str | Path,
    project: ProductionProject,
    slots: list[ProductionSlot],
) -> dict[int, Path]:
    base = project_directory(root, project)
    base.mkdir(parents=True, exist_ok=True)
    directories: dict[int, Path] = {}
    for slot in slots:
        directory = slot_directory(root, project, slot)
        directory.mkdir(parents=True, exist_ok=True)
        directories[slot.id] = directory
    return directories


def _match_manual_folder(name: str, slots: list[ProductionSlot]) -> int | None:
    normalized = normalize_text(name)
    if "gancho" in normalized:
        hook = next((slot for slot in slots if slot.kind == "hook"), None)
        return hook.id if hook else None
    if "final" in normalized:
        final = next((slot for slot in slots if slot.kind == "final"), None)
        return final.id if final else None
    scene_number = re.search(r"\bescena\s*(\d+)\b", normalized)
    if scene_number:
        position = int(scene_number.group(1))
        scene = next(
            (slot for slot in slots if slot.kind == "scene" and slot.position == position),
            None,
        )
        if scene:
            return scene.id
    for slot in slots:
        label = normalize_text(slot.label)
        if label and (label in normalized or normalized in label):
            return slot.id
    return None


def scan_project_images(
    root: str | Path,
    project: ProductionProject,
    slots: list[ProductionSlot],
) -> list[LocalProjectImage]:
    base = project_directory(root, project)
    if not base.exists():
        return []
    exact_folders = {slot_folder_name(slot).casefold(): slot.id for slot in slots}
    found: list[LocalProjectImage] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix.casefold() not in IMAGE_EXTENSIONS:
            continue
        relative = path.relative_to(base)
        folder = relative.parts[0] if len(relative.parts) > 1 else ""
        slot_id = exact_folders.get(folder.casefold())
        detected_by = "carpeta del programa"
        if slot_id is None and folder:
            slot_id = _match_manual_folder(folder, slots)
            detected_by = "nombre de carpeta manual" if slot_id else "sin asignar"
        elif slot_id is None:
            detected_by = "sin asignar"
        found.append(
            LocalProjectImage(
                path=path.resolve(),
                relative_path=relative,
                slot_id=slot_id,
                detected_by=detected_by,
            )
        )
    return found
