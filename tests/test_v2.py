from models.entity import ResolvedEntity
from models.photo import Photo
from models.scene import Scene
from services.research_cache import ResearchCache
from services.research_orchestrator import ResearchOrchestrator
from services.search_providers.registry import build_provider_registry
from utils.scoring import score_research_technical


ENTITY = ResolvedEntity(
    qid="Q9685",
    label="Diana, Princess of Wales",
    aliases=["Princess Diana", "Lady Di"],
)


def photo(**values):
    defaults = dict(
        id="test:1",
        title="Princess Diana leaving a car",
        thumbnail_url="https://example.test/t.jpg",
        image_url="https://example.test/i.jpg",
        original_page_url="https://example.test/p",
        width=2000,
        height=1500,
        author="Archive",
        date="1985",
        institution="Collection",
        description="Princess Diana",
    )
    defaults.update(values)
    return Photo(**defaults)


def test_research_technical_score_is_independent_of_license():
    green = photo(traffic_light="green", license="Public domain")
    red = photo(traffic_light="red", license="All rights reserved")
    assert score_research_technical(green) == score_research_technical(red)


def test_research_cache_roundtrip(tmp_path):
    cache = ResearchCache(tmp_path / "cache.db")
    original = photo(metadata={"source": "unit"})
    cache.set_search("Example", "Princess Diana car", 10, "Q9685", [original])
    restored = cache.get_search("Example", "Princess Diana car", 10, "Q9685")
    assert restored and restored[0].metadata == {"source": "unit"}

    payload = {"person_match": 98, "recommended": True}
    cache.set_gemini("image", payload, "Q9685", "scene", "image")
    assert cache.get_gemini("image", "Q9685", "scene", "image") == payload


def test_v2_score_formula(tmp_path):
    candidate = photo(technical_score=80)
    ResearchOrchestrator._apply_ai(
        candidate,
        {
            "person_match": 100,
            "scene_match": 80,
            "visual_impact": 90,
            "description": "Diana is visible leaving a car.",
            "recommended": True,
        },
    )
    assert candidate.final_score == 90
    assert candidate.score == candidate.final_score
    assert candidate.ai_recommended is True


def test_provider_registry_contains_all_v2_sources(monkeypatch):
    for name in (
        "EUROPEANA_API_KEY",
        "BING_IMAGE_API_KEY",
        "GOOGLE_SEARCH_API_KEY",
        "GOOGLE_SEARCH_ENGINE_ID",
        "FLICKR_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    providers, _ = build_provider_registry()
    assert set(providers) == {
        "Wikimedia Commons",
        "Europeana",
        "Bing Images",
        "Google Images",
        "Flickr Commons",
        "Pinterest (descubrimiento)",
    }
    assert providers["Wikimedia Commons"].configured is True
    assert providers["Pinterest (descubrimiento)"].configured is False


def test_research_queries_never_drop_entity(tmp_path):
    class DisabledGemini:
        configured = False
        model = "test"

    orchestrator = ResearchOrchestrator(
        cache_path=str(tmp_path / "cache.db"),
        gemini=DisabledGemini(),
    )
    scene = Scene(
        index=1,
        label="Coche",
        text="Paparazzi junto al coche",
        keywords=["paparazzi", "coche"],
        visual_concepts=["paparazzi", "car window"],
    )
    queries = orchestrator._provider_queries("Google Images", ENTITY, [scene])
    assert queries
    assert all("Diana" in query for _, query in queries)


def test_blank_gemini_model_uses_default(monkeypatch):
    from services.gemini_service import GeminiService

    monkeypatch.setenv("GEMINI_MODEL", "")
    service = GeminiService(api_key="test")
    assert service.model == "gemini-3.1-flash-lite"
