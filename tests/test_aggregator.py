from models.entity import ResolvedEntity
from models.photo import Photo
from services.image_deduplicator import ImageDeduplicator
from services.manual_query_builder import build_manual_searches, parse_manual_lines
from services.photo_aggregator import PhotoAggregator
from services.photo_state_store import PhotoResearchState, PhotoStateStore


ENTITY = ResolvedEntity(
    qid="Q9685",
    label="Diana, Princess of Wales",
    aliases=["Princess Diana", "Lady Di", "Diana Spencer"],
)


class DisabledGemini:
    configured = False
    model = "disabled"


class KeepAllDeduplicator:
    def deduplicate(self, photos):
        return photos, 0


class FakeProvider:
    configured = True
    name = "Bing Images"

    def search(self, query, limit, context=None):
        assert "Princess Diana" in query
        return [
            Photo(
                id=f"fake:{index}",
                title=f"Princess Diana gym photograph {index}",
                thumbnail_url=f"https://example.test/thumb-{index}.jpg",
                image_url=f"https://example.test/image-{index}.jpg",
                original_page_url=f"https://example.test/page-{index}",
                source=self.name,
                license="All rights reserved" if index % 2 else "CC BY 4.0",
                traffic_light="red" if index % 2 else "green",
                description="Princess Diana exercising at a gym",
                width=1200,
                height=900,
            )
            for index in range(42)
        ]


def test_manual_lines_and_queries_keep_identity(tmp_path):
    lines = parse_manual_lines(
        "- foto gancho joven\n2. foto dentro coche\n• foto sola mar"
    )
    assert lines == ["foto gancho joven", "foto dentro coche", "foto sola mar"]
    searches, warnings = build_manual_searches(
        lines,
        ENTITY,
        DisabledGemini(),
        None,
    )
    assert not warnings
    assert [item.translated for item in searches] == [
        "portrait young",
        "inside car",
        "alone sea",
    ]
    assert all("Princess Diana" in item.query_variants[0] for item in searches)


def test_aggregator_keeps_all_42_and_does_not_filter_license(tmp_path, monkeypatch):
    import services.photo_aggregator as module

    monkeypatch.setattr(
        module,
        "build_provider_registry",
        lambda timeout=20: ({"Bing Images": FakeProvider()}, []),
    )
    aggregator = PhotoAggregator(
        cache_path=str(tmp_path / "aggregator.db"),
        gemini=DisabledGemini(),
    )
    aggregator.deduplicator = KeepAllDeduplicator()
    result = aggregator.search(
        character="Lady Di",
        aliases=["Princess Diana"],
        manual_lines=["foto gimnasio"],
        sources=["Bing Images"],
        per_query_limit=50,
        entity=ENTITY,
    )
    assert result.total_raw >= 42
    assert len(result.photos) == 42
    assert len(result.by_search["manual_0"]) == 42
    assert any(photo.traffic_light == "red" for photo in result.photos)
    assert all(photo.search_relevance > 0 for photo in result.photos)


def test_perceptual_similarity_threshold():
    exact = "0000000000000000:0000000000000000:1.5"
    near = "0000000000000003:000000000000000f:1.5"
    far = "ffffffffffffffff:ffffffffffffffff:1.5"
    assert ImageDeduplicator._similar(exact, near)
    assert not ImageDeduplicator._similar(exact, far)


def test_photo_states_persist_and_discard_clears_selection(tmp_path):
    store = PhotoStateStore(tmp_path / "states.db")
    state = PhotoResearchState(favorite=True, video_candidate=True)
    store.set("photo-1", state)
    restored = store.get_many(["photo-1"])["photo-1"]
    assert restored.favorite and restored.video_candidate and not restored.discarded

    restored.discarded = True
    store.set("photo-1", restored)
    discarded = store.get_many(["photo-1"])["photo-1"]
    assert discarded.discarded
    assert not discarded.favorite
    assert not discarded.video_candidate
