"""Cliente de YouTube Data API; las credenciales siempre viven fuera del proyecto."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ("https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl")


class YouTubeConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class YouTubeConnection:
    connected: bool
    channel_title: str = ""
    channel_id: str = ""
    message: str = ""


class YouTubeClient:
    def __init__(self, client_file: str | Path | None = None, token_file: str | Path | None = None) -> None:
        private = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "FotosDelAyer" / "youtube"
        self.client_file = Path(client_file or os.environ.get("YOUTUBE_OAUTH_CLIENT_FILE", private / "oauth_client.json"))
        self.token_file = Path(token_file or os.environ.get("YOUTUBE_OAUTH_TOKEN_FILE", private / "token.json"))

    def _credentials(self, interactive: bool = False) -> Credentials:
        credentials = Credentials.from_authorized_user_file(str(self.token_file), SCOPES) if self.token_file.is_file() else None
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            self.token_file.write_text(credentials.to_json(), encoding="utf-8")
        if credentials and credentials.valid:
            return credentials
        if not interactive:
            raise YouTubeConfigurationError("YouTube no está conectado todavía.")
        if not self.client_file.is_file():
            raise YouTubeConfigurationError("Falta el archivo OAuth privado. Configura YOUTUBE_OAUTH_CLIENT_FILE fuera del proyecto.")
        flow = InstalledAppFlow.from_client_secrets_file(str(self.client_file), SCOPES)
        credentials = flow.run_local_server(port=0, open_browser=True)
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(credentials.to_json(), encoding="utf-8")
        return credentials

    def service(self, interactive: bool = False):
        return build("youtube", "v3", credentials=self._credentials(interactive), cache_discovery=False)

    def connection_status(self) -> YouTubeConnection:
        try:
            item = self.service().channels().list(part="snippet", mine=True).execute().get("items", [])[0]
            return YouTubeConnection(True, item["snippet"].get("title", ""), item.get("id", ""))
        except (IndexError, Exception) as exc:
            return YouTubeConnection(False, message=str(exc))

    def connect(self) -> YouTubeConnection:
        service = self.service(interactive=True)
        item = service.channels().list(part="snippet", mine=True).execute().get("items", [])[0]
        return YouTubeConnection(True, item["snippet"].get("title", ""), item.get("id", ""))

    def upload_private(self, job) -> dict:
        service = self.service()
        request = service.videos().insert(
            part="snippet,status",
            body={"snippet": {"title": job.title, "description": job.description, "tags": job.tags, "categoryId": "24", "defaultLanguage": "es", "defaultAudioLanguage": "es"},
                  "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "embeddable": True}},
            media_body=MediaFileUpload(job.video_path, mimetype="video/*", chunksize=8 * 1024 * 1024, resumable=True),
        )
        response = None
        while response is None:
            _, response = request.next_chunk()
        video_id = response["id"]
        if job.thumbnail_path and Path(job.thumbnail_path).is_file():
            service.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(job.thumbnail_path, mimetype="image/jpeg")).execute()
        if job.playlist_id:
            service.playlistItems().insert(part="snippet", body={"snippet": {"playlistId": job.playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
        return {"youtube_video_id": video_id, "youtube_url": f"https://www.youtube.com/watch?v={video_id}", "privacy_status": "private", "processing_status": "processing"}

    def video_status(self, video_id: str) -> dict:
        item = self.service().videos().list(part="status,processingDetails", id=video_id).execute().get("items", [])[0]
        return {"privacy_status": item.get("status", {}).get("privacyStatus", "private"), "processing_status": item.get("processingDetails", {}).get("processingStatus", "unknown"), "upload_status": item.get("status", {}).get("uploadStatus", "")}

    def schedule(self, video_id: str, publish_at: str) -> None:
        self.service().videos().update(part="status", body={"id": video_id, "status": {"privacyStatus": "private", "publishAt": publish_at, "selfDeclaredMadeForKids": False}}).execute()
