from services.search_providers.duckduckgo_images import DuckDuckGoImagesProvider


def test_duckduckgo_normalizes_discovery_result_without_network():
    provider = DuckDuckGoImagesProvider()
    photo = provider._normalize(
        {
            "title": "Historic portrait",
            "image": "https://images.test/full.jpg",
            "thumbnail": "https://images.test/thumb.jpg",
            "url": "https://archive.test/page",
            "height": 1800,
            "width": 1000,
            "source": "Archive",
        }
    )

    assert photo.source == "DuckDuckGo Images"
    assert photo.width == 1000
    assert photo.height == 1800
    assert photo.metadata["discovery_only"] is True
    assert photo.metadata["experimental_provider"] is True
    assert photo.original_page_url == "https://archive.test/page"
