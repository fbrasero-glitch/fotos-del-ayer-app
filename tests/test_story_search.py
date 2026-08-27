from models.entity import ResolvedEntity
from models.photo import Photo
from services.api_usage import ApiQuotaExceeded, ApiUsageStore
from services.story_search import StoryPhotoSearch


ENTITY = ResolvedEntity(
    qid="Q9685",
    label="Diana, Princess of Wales",
    aliases=["Princess Diana", "Lady Di"],
)


class DisabledGemini:
    configured = False
    model = "disabled"


class KeepAllDeduplicator:
    def deduplicate(self, photos):
        return photos, 0


class FakeProvider:
    configured = True
    name = "Pexels"

    def __init__(self):
        self.queries = []

    def search(self, query, limit, context=None):
        self.queries.append(query)
        return [
            Photo(
                id="fake:1",
                title="Princess Diana angry inside a car",
                thumbnail_url="https://example.test/t.jpg",
                image_url="https://example.test/i.jpg",
                original_page_url="https://example.test/p",
                source=self.name,
                description="Princess Diana angry inside a car",
                width=1200,
                height=1800,
            )
        ]


def test_story_search_uses_one_precise_query_per_source(tmp_path, monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(
        "services.story_search.build_story_registry",
        lambda timeout=20: {"Pexels": provider},
    )
    searcher = StoryPhotoSearch(
        cache_path=str(tmp_path / "story.db"), gemini=DisabledGemini()
    )
    searcher.deduplicator = KeepAllDeduplicator()
    result = searcher.search_scene(
        "Lady Di",
        "enfadada dentro de un coche",
        ["Pexels"],
        ["Princess Diana"],
        ENTITY,
        limit=40,
    )
    assert len(provider.queries) == 1
    assert "Princess Diana" in provider.queries[0]
    assert result.api_calls == {"Pexels": 1}
    assert len(result.photos) == 1


def test_local_usage_limit_stops_before_extra_call(tmp_path):
    store = ApiUsageStore(tmp_path / "usage.db")
    snapshot = store.reserve("Example", 1)
    assert snapshot.used == 1
    assert snapshot.remaining == 0
    try:
        store.reserve("Example", 1)
    except ApiQuotaExceeded:
        pass
    else:
        raise AssertionError("La segunda llamada debía quedar bloqueada")


def test_failed_reservation_can_be_released(tmp_path):
    store = ApiUsageStore(tmp_path / "usage.db")
    store.reserve("Example", 2)
    store.release("Example")
    assert store.snapshot("Example", 2).used == 0
