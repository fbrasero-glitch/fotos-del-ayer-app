from models.entity import ResolvedEntity
from models.photo import Photo
from services.photo_quality_pipeline import PhotoQualityPipeline
from services.production_store import ProductionStore


class FakeVision:
    model = "fake-vision:latest"
    configured = True

    def __init__(self):
        self.calls = 0

    def analyze_images(self, character, entity, scene, photos):
        self.calls += 1
        return [
            {
                "candidate_index": index,
                "person_match": 92,
                "scene_match": 84,
                "visual_impact": 80,
                "mobile_crop": 88,
                "clean_image": 90,
                "real_photo": True,
                "quality_issues": [],
                "description": "Retrato limpio y adecuado para móvil.",
                "recommended": True,
            }
            for index, _photo in enumerate(photos)
        ]


def make_photo(index):
    return Photo(
        id=str(index),
        title=f"Candidate {index}",
        thumbnail_url=f"https://images.test/{index}-small.jpg",
        image_url=f"https://images.test/{index}.jpg",
        original_page_url=f"https://archive.test/{index}",
        width=1000,
        height=1600,
        source="Archive",
    )


def test_local_visual_ranking_is_saved_and_reused(tmp_path):
    store = ProductionStore(tmp_path / "quality.db")
    entity = ResolvedEntity(qid="Q1", label="Test Person")
    project_id = store.create_project("Test", "Test Person", [], entity)
    slot = store.list_slots(project_id)[0]
    vision = FakeVision()
    pipeline = PhotoQualityPipeline(store, vision)
    photos = [make_photo(1), make_photo(2)]

    first = pipeline.rank(
        slot.id, "Test Person", entity, "portrait at an event", True, photos
    )
    second = pipeline.rank(
        slot.id, "Test Person", entity, "portrait at an event", True, photos
    )

    assert first.analyzed_count == 2
    assert first.good_count == 2
    assert all(photo.ai_recommended for photo in first.photos)
    assert second.cache_hits == 2
    assert second.analyzed_count == 0
    assert vision.calls == 1


def test_non_photo_is_never_recommended():
    photo = make_photo(1)
    PhotoQualityPipeline.apply_analysis(
        photo,
        {
            "person_match": 99,
            "scene_match": 99,
            "visual_impact": 99,
            "mobile_crop": 99,
            "clean_image": 99,
            "real_photo": False,
            "recommended": True,
        },
    )

    assert photo.ai_recommended is False
    assert photo.final_score <= 25
