from services.search_providers.brave_images import BraveImagesProvider
from services.search_providers.pexels import PexelsProvider
from services.search_providers.serpapi_google_images import SerpApiGoogleImagesProvider


def test_pexels_normalizes_original_and_attribution():
    provider = PexelsProvider("test")
    photo = provider._normalize(
        {
            "id": 42,
            "url": "https://www.pexels.com/photo/42/",
            "photographer": "Jane",
            "photographer_url": "https://www.pexels.com/@jane",
            "width": 2000,
            "height": 3000,
            "alt": "Portrait",
            "src": {
                "original": "https://images.pexels.com/42.jpg",
                "medium": "https://images.pexels.com/42-medium.jpg",
            },
        }
    )
    assert photo.image_url.endswith("42.jpg")
    assert photo.attribution_required is True
    assert photo.traffic_light == "green"


def test_brave_keeps_original_image_and_source_page():
    provider = BraveImagesProvider("test")
    photo = provider._normalize(
        {
            "title": "Historic portrait",
            "url": "https://example.test/article",
            "source": "Example",
            "thumbnail": {"src": "https://proxy.test/thumb.jpg"},
            "properties": {
                "url": "https://example.test/original.jpg",
                "width": 1600,
                "height": 1200,
            },
        }
    )
    assert photo.image_url == "https://example.test/original.jpg"
    assert photo.original_page_url == "https://example.test/article"
    assert photo.metadata["session_only"] is True


def test_serpapi_keeps_google_original_and_context_page():
    provider = SerpApiGoogleImagesProvider("test")
    photo = provider._normalize(
        {
            "title": "Diana in a car",
            "original": "https://example.test/diana.jpg",
            "thumbnail": "https://serpapi.test/thumb.jpg",
            "link": "https://example.test/story",
            "source": "Example",
            "original_width": 1800,
            "original_height": 1200,
        }
    )
    assert photo.image_url == "https://example.test/diana.jpg"
    assert photo.original_page_url == "https://example.test/story"
    assert photo.width == 1800
