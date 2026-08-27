"""Sube, valida y programa los Shorts de Elvis y Audrey Hepburn."""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timezone
import json
from pathlib import Path
import time as time_module
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from youtube_auth import EXPECTED_CHANNEL_ID, load_credentials


ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "data" / "youtube_upload" / "two_shorts_state.json"
PLAYLIST_STATE_PATH = ROOT / "data" / "youtube_upload" / "state.json"
MADRID = ZoneInfo("Europe/Madrid")

VIDEOS = {
    "elvis": {
        "file": ROOT / "proyectos_fotos" / "elvis-el-nino-que-nunca-dejo-de-cantar" / "edicion" / "render" / "short_final_con_musica_primer_fotograma_ok.mp4",
        "thumbnail": ROOT / "proyectos_fotos" / "elvis-el-nino-que-nunca-dejo-de-cantar" / "edicion" / "portada_9x16.jpg",
        "title": "Elvis Presley: cantó toda su vida contra el vacío #Shorts",
        "description": (
            "Elvis Presley nació junto a un hermano gemelo que no sobrevivió. De la pobreza de Tupelo "
            "a Graceland, la fama nunca logró llenar del todo aquel primer silencio.\n\n"
            "¿Qué fotografía de Elvis se te ha quedado grabada para siempre?\n\n"
            "Suscríbete a Fotos del Ayer para descubrir la historia humana detrás de cada imagen.\n\n"
            "#ElvisPresley #HistoriaDeLaMusica #FotosDelAyer"
        ),
        "tags": [
            "Elvis Presley", "Elvis", "The King", "Graceland", "Gladys Presley",
            "Tupelo", "1968 Comeback Special", "historia de la música", "fotos antiguas",
            "Fotos del Ayer",
        ],
        "playlists": ["vidas", "hollywood"],
        "comment": "¿Qué momento de la vida de Elvis crees que explica mejor la tristeza que había detrás del mito?",
    },
    "audrey": {
        "file": ROOT / "proyectos_fotos" / "audrey-hepburn-del-hambre-a-la-esperanza" / "edicion" / "render" / "short_final_con_musica_primer_fotograma_ok.mp4",
        "thumbnail": ROOT / "proyectos_fotos" / "audrey-hepburn-del-hambre-a-la-esperanza" / "edicion" / "portada_9x16.jpg",
        "title": "Audrey Hepburn: del hambre a la esperanza #Shorts",
        "description": (
            "Audrey Hepburn sobrevivió al hambre durante la ocupación nazi. Décadas después, convirtió "
            "aquel recuerdo en una misión junto a UNICEF para ayudar a niños que sufrían lo mismo.\n\n"
            "¿La recuerdas más como estrella de cine o por su labor humanitaria?\n\n"
            "Suscríbete a Fotos del Ayer para conocer las vidas que esconden las fotografías.\n\n"
            "#AudreyHepburn #HollywoodClasico #FotosDelAyer"
        ),
        "tags": [
            "Audrey Hepburn", "Vacaciones en Roma", "Desayuno con diamantes", "UNICEF",
            "Hollywood clásico", "Segunda Guerra Mundial", "cine clásico", "fotos antiguas",
            "Fotos del Ayer",
        ],
        "playlists": ["vidas", "hollywood"],
        "comment": "¿Qué legado de Audrey Hepburn te emociona más: sus películas o la esperanza que llevó a tantos niños?",
    },
}


def load_state() -> dict:
    if STATE_PATH.is_file():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"videos": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def playlist_ids() -> dict[str, str]:
    data = json.loads(PLAYLIST_STATE_PATH.read_text(encoding="utf-8"))
    return dict(data["playlists"])


def assert_channel(youtube) -> None:
    response = youtube.channels().list(part="snippet", mine=True).execute()
    items = response.get("items", [])
    if not items or items[0]["id"] != EXPECTED_CHANNEL_ID:
        raise RuntimeError("La autorización no corresponde al canal Fotos del Ayer.")


def upload_private(youtube, item: dict) -> str:
    media = MediaFileUpload(str(item["file"]), chunksize=8 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": item["title"],
                "description": item["description"],
                "tags": item["tags"],
                "categoryId": "24",
                "defaultLanguage": "es",
                "defaultAudioLanguage": "es",
            },
            "status": {
                "privacyStatus": "private",
                "selfDeclaredMadeForKids": False,
                "embeddable": True,
                "license": "youtube",
                "publicStatsViewable": True,
            },
        },
        media_body=media,
    )
    response = None
    while response is None:
        progress, response = request.next_chunk()
        if progress:
            print(f"UPLOAD {progress.progress() * 100:.0f}%", flush=True)
    return response["id"]


def ensure_thumbnail(youtube, video_id: str, thumbnail: Path) -> None:
    youtube.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(str(thumbnail), mimetype="image/jpeg"),
    ).execute()


def ensure_playlist(youtube, video_id: str, playlist_id: str) -> None:
    existing = youtube.playlistItems().list(
        part="id", playlistId=playlist_id, videoId=video_id, maxResults=1
    ).execute()
    if existing.get("items"):
        return
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute()


def video_status(youtube, video_id: str) -> dict:
    response = youtube.videos().list(
        part="snippet,status,processingDetails,contentDetails,suggestions",
        id=video_id,
    ).execute()
    items = response.get("items", [])
    if not items:
        raise RuntimeError(f"YouTube no devolvió el vídeo {video_id}.")
    item = items[0]
    status = item.get("status", {})
    processing = item.get("processingDetails", {})
    return {
        "video_id": video_id,
        "title": item.get("snippet", {}).get("title"),
        "privacy": status.get("privacyStatus"),
        "upload_status": status.get("uploadStatus"),
        "failure_reason": status.get("failureReason"),
        "rejection_reason": status.get("rejectionReason"),
        "processing_status": processing.get("processingStatus"),
        "processing_progress": processing.get("processingProgress"),
        "region_restriction": item.get("contentDetails", {}).get("regionRestriction"),
        "suggestions": item.get("suggestions", {}),
    }


def wait_processed(youtube, video_id: str, timeout_seconds: int = 1200) -> dict:
    deadline = time_module.time() + timeout_seconds
    while True:
        status = video_status(youtube, video_id)
        print(json.dumps(status, ensure_ascii=False), flush=True)
        if status["failure_reason"] or status["rejection_reason"]:
            raise RuntimeError(json.dumps(status, ensure_ascii=False))
        if status["upload_status"] == "processed" and status["processing_status"] == "succeeded":
            return status
        if status["processing_status"] in {"failed", "terminated"}:
            raise RuntimeError(json.dumps(status, ensure_ascii=False))
        if time_module.time() >= deadline:
            raise TimeoutError(f"El procesamiento de {video_id} no terminó dentro del plazo.")
        time_module.sleep(10)


def schedule(youtube, video_id: str, day: date) -> str:
    local_dt = datetime.combine(day, time(16, 0), tzinfo=MADRID)
    if local_dt <= datetime.now(MADRID):
        raise ValueError("Las 16:00 de la fecha elegida ya han pasado.")
    publish_at = local_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    youtube.videos().update(
        part="status",
        body={
            "id": video_id,
            "status": {
                "privacyStatus": "private",
                "publishAt": publish_at,
                "selfDeclaredMadeForKids": False,
            },
        },
    ).execute()
    return local_dt.isoformat()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now(MADRID).date().isoformat())
    parser.add_argument("--status-only", action="store_true")
    args = parser.parse_args()
    day = date.fromisoformat(args.date)
    youtube = build("youtube", "v3", credentials=load_credentials())
    assert_channel(youtube)
    state = load_state()
    playlists = playlist_ids()

    for key, item in VIDEOS.items():
        saved = state["videos"].setdefault(key, {})
        video_id = saved.get("video_id")
        if args.status_only:
            if video_id:
                print(json.dumps({"key": key, **video_status(youtube, video_id)}, ensure_ascii=False))
            continue
        if not video_id:
            video_id = upload_private(youtube, item)
            saved["video_id"] = video_id
            saved["uploaded_private"] = True
            save_state(state)
        ensure_thumbnail(youtube, video_id, item["thumbnail"])
        for playlist_key in item["playlists"]:
            ensure_playlist(youtube, video_id, playlists[playlist_key])
        processed = wait_processed(youtube, video_id)
        saved["processed"] = processed
        saved["scheduled_madrid"] = schedule(youtube, video_id, day)
        saved["comment_pending"] = item["comment"]
        save_state(state)
        print(json.dumps({"key": key, **saved}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
