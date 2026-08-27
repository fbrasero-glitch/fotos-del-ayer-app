import json
import sqlite3

from database.database import Database
from models.entity import ResolvedEntity
from models.photo import Photo
from models.project import Project
from models.scene import Scene
from services.query_builder import add_queries
from services.script_parser import parse_script
from utils.licenses import assess_license
from utils.relevance import MIN_ENTITY_RELEVANCE, assess_entity_relevance
from utils.scoring import score_photo


ENTITY = ResolvedEntity(
    qid="Q9685",
    label="Diana, Princess of Wales",
    aliases=["Princess Diana", "Lady Di", "Diana Spencer"],
    commons_category="Diana, Princess of Wales",
    image_filename="Diana, Princess of Wales 1997 (2) (cropped).jpg",
)

SCRIPT = """La mujer más fotografiada caminaba por la calle.

Paparazzi apuntaban desde el coche.

A veces tenía que correr por Londres.

Ni el gimnasio era un lugar privado.

Se sentaba sola mirando al mar.

Nunca tuvo un momento solo suyo."""


def make_photo(**overrides) -> Photo:
    values = {
        "id": "wikimedia:test",
        "title": "Princess Diana portrait",
        "thumbnail_url": "https://example.test/thumb.jpg",
        "image_url": "https://example.test/image.jpg",
        "original_page_url": "https://example.test/page",
        "author": "Jane Doe",
        "date": "1995",
        "source": "Wikimedia Commons",
        "institution": "Archive",
        "license": "CC BY 4.0",
        "license_description": "Commercial use allowed",
        "commercial_use": True,
        "attribution_required": True,
        "traffic_light": "green",
        "width": 3000,
        "height": 4000,
        "description": "Iconic close up portrait of Princess Diana",
        "categories": ["Diana, Princess of Wales"],
    }
    values.update(overrides)
    return Photo(**values)


def test_queries_always_keep_entity_lock() -> None:
    scenes = add_queries("Lady Di", parse_script(SCRIPT, 6), [], ENTITY)
    assert len(scenes) == 6
    assert scenes[0].query == "haswbstatement:P180=Q9685 portrait"
    identity_markers = ("Q9685", "Diana, Princess of Wales", "Princess Diana", "Lady Di")
    for scene in scenes:
        assert len(scene.query_variants) >= 2
        for query in scene.query_variants:
            assert any(marker in query for marker in identity_markers)
            assert query.strip() not in {"sea", "London", "car", "alone"}


def test_entity_relevance_blocks_false_positives() -> None:
    structured = make_photo(depicts_qids=["Q9685"], title="England 1986")
    score, evidence = assess_entity_relevance(structured, ENTITY)
    assert score == 100
    assert "P180" in evidence

    flower = make_photo(title="Clematis Princess Diana flower", depicts_qids=[])
    memorial = make_photo(title="Princess Diana Memorial Fountain", depicts_qids=[])
    wrong_person = make_photo(title="Princess Diana and guest", depicts_qids=["Q9682"])
    commemorative_cup = make_photo(title="Princess Diana commemorative cup", depicts_qids=["Q9685"])
    assert assess_entity_relevance(flower, ENTITY)[0] < MIN_ENTITY_RELEVANCE
    assert assess_entity_relevance(memorial, ENTITY)[0] < MIN_ENTITY_RELEVANCE
    assert assess_entity_relevance(wrong_person, ENTITY)[0] < MIN_ENTITY_RELEVANCE
    assert assess_entity_relevance(commemorative_cup, ENTITY)[0] < MIN_ENTITY_RELEVANCE


def test_query_match_alone_is_not_scene_relevance() -> None:
    scene = Scene(index=1, label="Gimnasio", text="En el gimnasio", keywords=["gimnasio"])
    photo = make_photo(
        title="Princess Diana at a private dinner",
        description="Diana greeting guests",
        matched_scene_keys=[scene.key],
        depicts_qids=["Q9685"],
    )
    score_photo(photo, scene)
    assert photo.scene_relevance == 0


def test_license_traffic_light_is_conservative() -> None:
    green = [
        ("Public domain", ""),
        ("CC0", "https://creativecommons.org/publicdomain/zero/1.0/"),
        ("", "https://creativecommons.org/licenses/by/4.0/"),
        ("", "https://creativecommons.org/licenses/by-sa/4.0/"),
    ]
    for name, url in green:
        assert assess_license(name, url).traffic_light == "green"
    assert assess_license("CC BY-NC 4.0").traffic_light == "red"
    assert assess_license("Licencia desconocida").traffic_light == "red"
    assert assess_license("No known copyright restrictions").traffic_light == "yellow"


def test_scores_keep_identity_separate_from_scene_and_technical() -> None:
    scene = Scene(
        1,
        "Gimnasio",
        "En el gimnasio",
        ["gimnasio"],
        "haswbstatement:P180=Q9685 gym",
        False,
        ["haswbstatement:P180=Q9685 gym"],
    )
    strong = make_photo(
        title="Princess Diana at the gym",
        description="Princess Diana gym workout",
        matched_scene_keys=[scene.key],
        entity_relevance=100,
    )
    strong.score = score_photo(strong, scene)
    assert strong.entity_relevance == 100
    assert strong.scene_relevance > 0
    assert strong.technical_score > 0
    assert 0 <= strong.score <= 100


def test_database_persists_entity_and_legal_traceability(tmp_path) -> None:
    database_path = tmp_path / "test.db"
    database = Database(database_path)
    scene = Scene(
        0,
        "Gancho",
        "Texto",
        ["portrait"],
        "haswbstatement:P180=Q9685 portrait",
        True,
        ["haswbstatement:P180=Q9685 portrait"],
    )
    photo = make_photo(
        score=91,
        entity_relevance=100,
        entity_evidence="P180=Q9685",
        scene_relevance=85,
        technical_score=92,
    )
    project_id = database.save_project(
        Project(
            "Lady Di",
            "Texto",
            [scene],
            {"hook": photo},
            ["Princess Diana"],
            "ready",
            "Q9685",
            "Diana, Princess of Wales",
        )
    )

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    saved = connection.execute(
        """
        SELECT p.entity_qid, p.entity_label, ph.license, ph.entity_relevance,
               ph.scene_relevance, ph.technical_score, ph.metadata_json
        FROM projects p
        JOIN selections s ON s.project_id = p.id
        JOIN photos ph ON ph.id = s.photo_id
        WHERE p.id = ?
        """,
        (project_id,),
    ).fetchone()
    connection.close()

    assert saved["entity_qid"] == "Q9685"
    assert saved["entity_label"] == "Diana, Princess of Wales"
    assert saved["license"] == "CC BY 4.0"
    assert saved["entity_relevance"] == 100
    assert saved["scene_relevance"] == 85
    assert saved["technical_score"] == 92
    assert json.loads(saved["metadata_json"])["entity_evidence"] == "P180=Q9685"
