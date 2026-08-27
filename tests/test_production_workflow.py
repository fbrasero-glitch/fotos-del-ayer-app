import requests

from models.entity import ResolvedEntity
from models.photo import Photo
from services.photo_state_store import stable_photo_key
from services.production_store import ProductionStore
from services.rights_inspector import RightsInspector


def entity():
    return ResolvedEntity("Q9685", "Diana, Princess of Wales", aliases=["Lady Di"])


def photo(url="https://images.test/diana.jpg", page="https://example.test/story"):
    return Photo(
        id="photo-1",
        title="Princess Diana",
        thumbnail_url=url,
        image_url=url,
        original_page_url=page,
        source="Brave Images",
        metadata={"discovery_only": True},
    )


def test_project_slots_searches_and_candidates_are_persistent(tmp_path):
    path = tmp_path / "production.db"
    store = ProductionStore(path)
    project_id = store.create_project(
        "Lady Di, la mujer más fotografiada", "Lady Di", ["Diana Spencer"], entity()
    )
    scene_id = store.add_scene(project_id, "Sola en el yate")

    slots = store.list_slots(project_id)
    assert [slot.kind for slot in slots] == ["hook", "scene", "final"]
    assert scene_id == slots[1].id

    query = "Princess Diana yacht diving board 1997"
    fingerprint = store.fingerprint("Q9685", "Brave Images", query)
    saved = store.save_search(
        fingerprint,
        "Q9685",
        "Brave Images",
        query,
        "yacht diving board 1997",
        False,
        30,
        [photo()],
    )
    store.attach_search(scene_id, saved.id)
    store.add_candidate(scene_id, saved.photos[0])
    store.choose_candidate(scene_id, saved.photos[0])
    store.save_download(scene_id, saved.photos[0], "project/02-escena/foto.jpg")

    reopened = ProductionStore(path)
    reused = reopened.get_search(fingerprint)
    assert reused is not None
    assert len(reused.photos) == 1
    assert len(reopened.slot_photos(scene_id)) == 1
    assert reopened.list_candidates(scene_id)[0][1] is True
    assert reopened.get_download(scene_id, saved.photos[0]).endswith("foto.jpg")


def test_same_search_fingerprint_ignores_spacing_and_case(tmp_path):
    store = ProductionStore(tmp_path / "fingerprint.db")
    first = store.fingerprint("Q9685", "Brave Images", "Princess Diana   YACHT")
    second = store.fingerprint("Q9685", "brave images", "princess diana yacht")
    assert first == second


def test_rights_mode_and_photo_decisions_are_persistent(tmp_path):
    store = ProductionStore(tmp_path / "decisions.db")
    project_id = store.create_project("Lady Di", "Lady Di", [], entity())
    slot_id = store.list_slots(project_id)[0].id
    selected = photo()

    store.set_rights_mode(project_id, "strict")
    store.record_decision(
        slot_id,
        selected,
        "use",
        "strict",
        rights_level="green",
        local_path="project/photo.jpg",
    )

    reopened = ProductionStore(tmp_path / "decisions.db")
    assert reopened.get_rights_mode(project_id) == "strict"
    assert reopened.decision_map(slot_id) == {
        stable_photo_key(selected): "use"
    }


def test_rights_inspector_marks_stock_agency_as_high_risk(monkeypatch):
    def unavailable(*args, **kwargs):
        raise requests.RequestException("offline")

    monkeypatch.setattr("services.rights_inspector.requests.get", unavailable)
    stock_photo = photo(page="https://www.gettyimages.com/detail/123")

    report = RightsInspector().inspect(stock_photo)

    assert report["level"] == "red"
    assert "licencia" in report["decision"].casefold()
    assert any("Brave/Google" in issue for issue in report["issues"])
