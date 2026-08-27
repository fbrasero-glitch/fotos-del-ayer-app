from models.entity import ResolvedEntity
from services.production_store import ProductionStore
from services.project_files import (
    ensure_project_structure,
    scan_project_images,
    slot_directory,
)


def make_project(tmp_path):
    store = ProductionStore(tmp_path / "project.db")
    project_id = store.create_project(
        "Lady Di, la mujer más fotografiada",
        "Lady Di",
        ["Princess Diana"],
        ResolvedEntity("Q9685", "Diana, Princess of Wales"),
    )
    store.add_scene(project_id, "Paparazzi en el coche")
    store.add_scene(project_id, "Sola en el yate")
    return store.get_project(project_id), store.list_slots(project_id)


def test_creates_every_folder_before_any_download(tmp_path):
    project, slots = make_project(tmp_path)
    directories = ensure_project_structure(tmp_path / "photos", project, slots)

    assert len(directories) == 4
    assert all(directory.is_dir() for directory in directories.values())
    assert directories[slots[0].id].name == "01-gancho"
    assert directories[slots[-1].id].name == "99-foto-final"


def test_detects_manual_image_in_program_scene_folder(tmp_path):
    project, slots = make_project(tmp_path)
    scene = next(slot for slot in slots if slot.kind == "scene" and slot.position == 2)
    directory = slot_directory(tmp_path / "photos", project, scene)
    directory.mkdir(parents=True)
    manual = directory / "foto-que-puse-yo.jpg"
    manual.write_bytes(b"image")

    found = scan_project_images(tmp_path / "photos", project, slots)

    assert len(found) == 1
    assert found[0].path == manual.resolve()
    assert found[0].slot_id == scene.id


def test_detects_image_in_manually_named_scene_folder(tmp_path):
    project, slots = make_project(tmp_path)
    ensure_project_structure(tmp_path / "photos", project, slots)
    project_root = next(iter((tmp_path / "photos").iterdir()))
    manual_folder = project_root / "Escena 2"
    manual_folder.mkdir()
    manual = manual_folder / "yate.png"
    manual.write_bytes(b"image")

    found = scan_project_images(tmp_path / "photos", project, slots)
    detected = next(item for item in found if item.path == manual.resolve())
    scene = next(slot for slot in slots if slot.kind == "scene" and slot.position == 2)

    assert detected.slot_id == scene.id
    assert detected.detected_by == "nombre de carpeta manual"
