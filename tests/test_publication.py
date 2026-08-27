from __future__ import annotations

import json
from pathlib import Path

from models.entity import ResolvedEntity
from services.production_store import ProductionStore
from services.publication_store import PublicationStore
from services.short_validation import validate_short
from services.youtube_client import YouTubeClient


def _project(database: Path) -> int:
    return ProductionStore(database).create_project("Historia", "Persona", [], ResolvedEntity("Q1", "Persona"))


def test_publication_queue_persists_metadata_and_state(tmp_path):
    database = tmp_path / "queue.db"
    project_id = _project(database)
    store = PublicationStore(database)
    saved = store.save_draft(project_id, video_path="render.mp4", title="Título", tags=["Persona"], hashtags=["#Shorts"], publish_at="2026-09-01T17:00:00Z")
    assert saved.status == "pendiente"
    updated = store.update_result(project_id, status="validado", validation_json={"ok": True})
    reopened = PublicationStore(database).get(project_id)
    assert updated.status == "validado"
    assert reopened is not None and reopened.validation_json["ok"] is True
    assert reopened.tags == ["Persona"]


def test_social_publications_are_independent_per_network(tmp_path):
    database = tmp_path / "queue.db"
    project_id = _project(database)
    store = PublicationStore(database)
    posts = store.ensure_social_publications(project_id, {"facebook": "Texto FB", "instagram": "Texto IG"})
    assert {post.network for post in posts} == {"facebook", "instagram", "tiktok"}
    updated = store.update_social_publication(project_id, "facebook", status="publicado", remote_id="fb-1")
    instagram = next(post for post in store.social_publications(project_id) if post.network == "instagram")
    assert updated.status == "publicado"
    assert instagram.status == "pendiente"


def test_validation_reports_missing_video(tmp_path):
    result = validate_short(tmp_path / "missing.mp4")
    assert not result.ok
    assert result.video_exists is False
    assert "No existe" in result.errors[0]


def test_youtube_client_requires_explicit_private_credentials(tmp_path):
    client = YouTubeClient(client_file=tmp_path / "oauth.json", token_file=tmp_path / "token.json")
    status = client.connection_status()
    assert not status.connected
    assert "conectado" in status.message


def test_youtube_upload_is_private_and_uses_metadata(monkeypatch, tmp_path):
    class Request:
        def next_chunk(self):
            return None, {"id": "video-1"}

    class Videos:
        def insert(self, **kwargs):
            self.kwargs = kwargs
            return Request()

    class Service:
        def __init__(self):
            self.videos_api = Videos()
        def videos(self): return self.videos_api
        def thumbnails(self): raise AssertionError("No portada esperada")
        def playlistItems(self): raise AssertionError("No playlist esperada")

    service = Service()
    client = YouTubeClient(client_file=tmp_path / "oauth.json", token_file=tmp_path / "token.json")
    monkeypatch.setattr(client, "service", lambda interactive=False: service)

    class Job:
        title = "Historia #Shorts"; description = "Texto"; tags = ["Historia"]
        video_path = str(tmp_path / "video.mp4"); thumbnail_path = ""; playlist_id = ""
    Path(Job.video_path).write_bytes(b"test")
    response = client.upload_private(Job())
    assert response["privacy_status"] == "private"
    assert service.videos_api.kwargs["body"]["status"]["privacyStatus"] == "private"
