from __future__ import annotations

import os

from .bing_images import BingImagesProvider
from .europeana import EuropeanaProvider
from .flickr_commons import FlickrCommonsProvider
from .google_images import GoogleImagesProvider
from .pinterest_discovery import PinterestDiscoveryProvider
from .wikimedia import WikimediaProvider


def build_provider_registry(timeout: int = 20) -> tuple[dict[str, object], list[str]]:
    providers: dict[str, object] = {
        "Wikimedia Commons": WikimediaProvider(timeout),
        "Europeana": EuropeanaProvider(os.getenv("EUROPEANA_API_KEY", ""), timeout),
        "Bing Images": BingImagesProvider(os.getenv("BING_IMAGE_API_KEY", ""), timeout),
        "Google Images": GoogleImagesProvider(
            os.getenv("GOOGLE_SEARCH_API_KEY", ""),
            os.getenv("GOOGLE_SEARCH_ENGINE_ID", ""),
            timeout,
        ),
        "Flickr Commons": FlickrCommonsProvider(os.getenv("FLICKR_API_KEY", ""), timeout),
    }
    upstream = providers["Google Images"] if providers["Google Images"].configured else providers["Bing Images"]
    providers["Pinterest (descubrimiento)"] = PinterestDiscoveryProvider(upstream)
    warnings = [
        f"{name} no está configurado y se omitirá."
        for name, provider in providers.items()
        if not provider.configured
    ]
    return providers, warnings
