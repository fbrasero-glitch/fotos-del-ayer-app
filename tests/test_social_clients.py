from pathlib import Path

import services.social_clients as social_clients
from services.social_clients import SocialPublisher


def test_facebook_reel_upload_sends_file_metadata(monkeypatch, tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"video-bytes")
    monkeypatch.setenv("META_PAGE_ID", "page-1")
    monkeypatch.setenv("META_FB_PAGE_ACCESS_TOKEN", "page-token")

    client = SocialPublisher()
    api_calls = []
    upload_calls = []

    def fake_request(method, url, **kwargs):
        api_calls.append((method, url, kwargs))
        if kwargs["data"].get("upload_phase") == "start":
            return {"video_id": "video-1", "upload_url": "https://upload.test/video-1"}
        return {"id": "video-1"}

    class UploadResponse:
        ok = True

    def fake_post(url, **kwargs):
        upload_calls.append((url, kwargs))
        return UploadResponse()

    monkeypatch.setattr(client, "_request", fake_request)
    monkeypatch.setattr(social_clients.requests, "post", fake_post)

    result = client._facebook(video, "Texto")

    assert result["remote_id"] == "video-1"
    assert upload_calls[0][1]["headers"] == {
        "Authorization": "OAuth page-token",
        "offset": "0",
        "file_size": str(video.stat().st_size),
        "Content-Type": "application/octet-stream",
    }
