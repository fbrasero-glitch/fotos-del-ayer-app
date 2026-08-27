import json

import pytest

from models.entity import ResolvedEntity
from services.gemini_publication import PublicationMetadataStore, validate_metadata
from services.production_store import ProductionStore


def sample():
    return {"youtube_title": "Historia de ayer", "youtube_description": "Una memoria breve.", "tags": ["Historia"], "hashtags": ["FotosDelAyer"], "pinned_comment": "¿Qué recuerdas?", "social": {"instagram": "Una memoria.", "facebook": "Una memoria.", "tiktok": "Una memoria."}}


def test_validate_metadata_normalizes_limits_and_hashtags():
    result = validate_metadata({**sample(), "youtube_title": "x" * 300, "hashtags": ["Fotos", "#Ayer"]})
    assert len(result["youtube_title"]) == 100
    assert result["hashtags"] == ["#Fotos", "#Ayer"]


def test_validate_metadata_rejects_missing_social():
    value = sample()
    del value["social"]["tiktok"]
    with pytest.raises(ValueError):
        validate_metadata(value)


def test_metadata_versions_persist(tmp_path):
    db = tmp_path / "metadata.db"
    ProductionStore(db).create_project("Historia", "Persona", [], ResolvedEntity("Q1", "Persona"))
    project_id = 1
    store = PublicationMetadataStore(db)
    store.save(project_id, sample(), "nostálgico", "gemini-test")
    versions = store.versions(project_id)
    assert versions[0]["metadata"]["youtube_title"] == "Historia de ayer"
    assert versions[0]["tone"] == "nostálgico"
