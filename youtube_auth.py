"""Autoriza localmente el canal de YouTube y valida su identidad."""

from __future__ import annotations

import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

ROOT = Path(__file__).resolve().parent
CLIENT_FILE = ROOT / "youtube_oauth_client.json"
TOKEN_FILE = ROOT / "youtube_token.json"
EXPECTED_CHANNEL_ID = "UC9RCDWg3Y-LO_CimqqyMuqA"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

def load_credentials() -> Credentials:
    credentials = None
    if TOKEN_FILE.exists():
        credentials = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials or not credentials.valid:
        if not CLIENT_FILE.exists():
            raise FileNotFoundError(f"No se encuentra {CLIENT_FILE.name}")
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
        credentials = flow.run_local_server(port=0, open_browser=True)
    TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")
    return credentials

def main() -> None:
    youtube = build("youtube", "v3", credentials=load_credentials())
    response = youtube.channels().list(part="snippet", mine=True).execute()
    channels = response.get("items", [])
    if not channels:
        raise RuntimeError("La cuenta autorizada no tiene ningún canal de YouTube accesible.")
    channel = channels[0]
    channel_id = channel["id"]
    title = channel["snippet"]["title"]
    if channel_id != EXPECTED_CHANNEL_ID:
        TOKEN_FILE.unlink(missing_ok=True)
        raise RuntimeError(
            f"Se autorizó el canal equivocado: {title} ({channel_id}). "
            "Se ha descartado la autorización; repite el proceso seleccionando Fotos del Ayer."
        )
    print(json.dumps({"autorizado": True, "canal": title, "channel_id": channel_id}, ensure_ascii=False))

if __name__ == "__main__":
    main()
