from models.entity import ResolvedEntity
from models.photo import Photo
from services.fast_search import BRAVE_SOURCE, PEXELS_SOURCE, FastPhotoSearch


class FakeProvider:
    configured = True

    def __init__(self):
        self.calls = []

    def search(self, query, count, context):
        self.calls.append((query, count, context))
        return [
            Photo(
                id="1",
                title="Princess Diana portrait",
                thumbnail_url="https://images.test/diana-small.jpg",
                image_url="https://images.test/diana.jpg",
                original_page_url="https://example.test/diana",
                source="Brave",
            )
        ]


def diana():
    return ResolvedEntity(
        qid="Q9685",
        label="Diana, Princess of Wales",
        aliases=["Lady Di", "Diana Spencer"],
    )


def test_fast_search_makes_one_request_for_one_source(monkeypatch, tmp_path):
    provider = FakeProvider()
    monkeypatch.setattr(
        "services.fast_search.build_story_registry",
        lambda timeout=None: {BRAVE_SOURCE: provider},
    )
    monkeypatch.setattr(FastPhotoSearch, "_limit", staticmethod(lambda source: None))

    result = FastPhotoSearch(str(tmp_path / "usage.db")).search(
        diana(), "young portrait 1980s", [BRAVE_SOURCE], is_hook=True, count=30
    )

    assert len(provider.calls) == 1
    assert provider.calls[0][0] == '"Princess Diana" young portrait 1980s'
    assert provider.calls[0][1] == 30
    assert result.api_calls == {BRAVE_SOURCE: 1}
    assert result.by_source[BRAVE_SOURCE][0].id == "1"
    assert len(result.photos) == 1


def test_pexels_query_is_generic_and_does_not_include_the_character():
    query = FastPhotoSearch.query_for(
        PEXELS_SOURCE, diana(), "vintage car at night", is_hook=False
    )

    assert query == "vintage car at night"
    assert "Diana" not in query


def test_empty_hook_gets_a_simple_default_query():
    query = FastPhotoSearch.query_for(BRAVE_SOURCE, diana(), "", is_hook=True)

    assert query == '"Princess Diana" young portrait'


def test_shortlist_filters_tiny_images_and_caps_results():
    photos = [
        Photo(
            id=str(index),
            title=f"Photo {index}",
            thumbnail_url=f"https://images.test/{index}.jpg",
            image_url=f"https://images.test/{index}.jpg",
            original_page_url="",
            width=120 if index == 0 else 800,
            height=120 if index == 0 else 1200,
        )
        for index in range(20)
    ]

    shortlisted = FastPhotoSearch.shortlist(photos)

    assert len(shortlisted) == 18
    assert all(photo.id != "0" for photo in shortlisted)


def test_shortlist_prefers_high_quality_portraits_for_mobile():
    photos = [
        Photo(
            id="landscape",
            title="Wide archive photo",
            thumbnail_url="https://images.test/landscape.jpg",
            image_url="https://images.test/landscape.jpg",
            original_page_url="",
            width=2400,
            height=1600,
        ),
        Photo(
            id="portrait",
            title="Vertical archive portrait",
            thumbnail_url="https://images.test/portrait.jpg",
            image_url="https://images.test/portrait.jpg",
            original_page_url="",
            width=1000,
            height=1600,
        ),
    ]

    shortlisted = FastPhotoSearch.shortlist(photos, limit=2)

    assert [photo.id for photo in shortlisted] == ["portrait", "landscape"]
    assert FastPhotoSearch.mobile_fit_score(shortlisted[0]) > FastPhotoSearch.mobile_fit_score(
        shortlisted[1]
    )
