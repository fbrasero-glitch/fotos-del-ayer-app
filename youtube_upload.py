"""Sube el lote inicial de Fotos del Ayer de forma privada e idempotente."""

from __future__ import annotations

import json
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from youtube_auth import load_credentials

ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT / "data" / "youtube_upload"
STATE_FILE = UPLOAD_DIR / "state.json"

PLAYLISTS = {
    "vidas": ("Vidas detrás del mito", "Historias humanas detrás de las figuras que marcaron una época."),
    "hollywood": ("Hollywood eterno", "Estrellas, imágenes y secretos del Hollywood que permanece en nuestra memoria."),
    "realeza": ("Realeza bajo los flashes", "La vida de la realeza frente a las cámaras y detrás de la imagen pública."),
}

VIDEOS = [
    {
        "key": "marilyn",
        "file": "01_marilyn_monroe.mp4",
        "thumbnail": "proyectos_fotos/la-mascara-y-la-mujer/01-gancho/sin-titulo-870e7da8a6.jpg",
        "title": "Marilyn Monroe: la mujer que quedó detrás del mito #Shorts",
        "description": (
            "Antes de Marilyn Monroe estuvo Norma Jeane. Esta es la historia de cómo "
            "Hollywood creó una imagen perfecta y del precio que pagó la mujer que vivía detrás de ella.\n\n"
            "Suscríbete a Fotos del Ayer para descubrir las historias humanas escondidas tras las imágenes más inolvidables.\n\n"
            "#MarilynMonroe #HollywoodClasico #FotosDelAyer"
        ),
        "tags": ["Marilyn Monroe", "Norma Jeane", "Hollywood clásico", "historia de Hollywood", "fotos antiguas", "Fotos del Ayer"],
        "playlists": ["vidas", "hollywood"],
    },
    {
        "key": "diana",
        "file": "02_lady_di.mp4",
        "thumbnail": "proyectos_fotos/ladi-dy-la-mujer-mas-fotografiada-del-mundo/01-gancho/GettyImages-3136750-scaled.jpg",
        "title": "Lady Di: el precio de ser la mujer más fotografiada #Shorts",
        "description": (
            "Lady Di vivió rodeada de cámaras. Cada calle, cada coche y hasta sus momentos de paz podían convertirse "
            "en una exclusiva. La fama también tuvo un precio.\n\n"
            "Suscríbete a Fotos del Ayer para recordar las vidas que marcaron nuestra memoria.\n\n"
            "#LadyDi #PrincesaDiana #FotosDelAyer"
        ),
        "tags": ["Lady Di", "Princesa Diana", "Diana de Gales", "familia real británica", "fotos históricas", "Fotos del Ayer"],
        "playlists": ["vidas", "realeza"],
    },
    {
        "key": "james",
        "file": "03_james_dean.mp4",
        "thumbnail": "proyectos_fotos/juventud-revelde/01-gancho/rebel-without-a-cause-movie-james-dean-red-jacket-997ffd56dd.jpg",
        "title": "James Dean: 24 años y un mito eterno #Shorts",
        "description": (
            "James Dean necesitó solo tres películas para convertirse en el rostro de una generación. "
            "Murió con 24 años, antes de ver estrenadas dos de ellas, y quedó eternamente joven en nuestra memoria.\n\n"
            "Suscríbete a Fotos del Ayer para descubrir la historia detrás de cada imagen.\n\n"
            "#JamesDean #HollywoodClasico #FotosDelAyer"
        ),
        "tags": ["James Dean", "Rebelde sin causa", "Hollywood clásico", "Porsche 550", "cine clásico", "Fotos del Ayer"],
        "playlists": ["vidas", "hollywood"],
    },
    {
        "key": "jackie",
        "file": "04_jackie_kennedy.mp4",
        "thumbnail": "proyectos_fotos/elegancia-absoluta/01-gancho/jacqueline-kennedy-onassis-former-wife-of-late-u-s-president-john-f-kennedy-967e856a28.jpg",
        "title": "Jackie Kennedy: cuando la elegancia ocultaba el dolor #Shorts",
        "description": (
            "La elegancia de Jacqueline Kennedy no estaba solo en sus vestidos, sino en la dignidad con la que sostuvo "
            "a sus hijos y a una nación después de Dallas.\n\n"
            "Suscríbete a Fotos del Ayer para conocer las historias que viven detrás de las fotografías.\n\n"
            "#JackieKennedy #Historia #FotosDelAyer"
        ),
        "tags": ["Jackie Kennedy", "Jacqueline Kennedy", "John F Kennedy", "Casa Blanca", "historia contemporánea", "Fotos del Ayer"],
        "playlists": ["vidas", "realeza"],
    },
]


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"playlists": {}, "videos": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_playlists(youtube, state: dict) -> None:
    for key, (title, description) in PLAYLISTS.items():
        if key in state["playlists"]:
            continue
        response = youtube.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {"title": title, "description": description, "defaultLanguage": "es"},
                "status": {"privacyStatus": "public"},
            },
        ).execute()
        state["playlists"][key] = response["id"]
        save_state(state)


def upload_video(youtube, item: dict) -> str:
    media = MediaFileUpload(str(UPLOAD_DIR / item["file"]), chunksize=8 * 1024 * 1024, resumable=True)
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
        _, response = request.next_chunk()
    return response["id"]


def main() -> None:
    youtube = build("youtube", "v3", credentials=load_credentials())
    state = load_state()
    ensure_playlists(youtube, state)
    for item in VIDEOS:
        video_id = state["videos"].get(item["key"])
        if not video_id:
            video_id = upload_video(youtube, item)
            state["videos"][item["key"]] = video_id
            save_state(state)
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(ROOT / item["thumbnail"]), mimetype="image/jpeg"),
            ).execute()
            for playlist_key in item["playlists"]:
                youtube.playlistItems().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "playlistId": state["playlists"][playlist_key],
                            "resourceId": {"kind": "youtube#video", "videoId": video_id},
                        }
                    },
                ).execute()
        print(json.dumps({"key": item["key"], "video_id": video_id, "privacy": "private"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
