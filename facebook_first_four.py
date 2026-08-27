"""Publica los cuatro primeros Shorts de Fotos del Ayer en orden inverso.

El estado local permite reanudar la tanda sin volver a subir un vídeo ya
aceptado por Facebook. El script usa el mismo cliente oficial que la pantalla
de Publicación de la app.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from services.social_clients import SocialPublisher
from youtube_upload import UPLOAD_DIR, VIDEOS


ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "data" / "facebook_upload" / "state.json"

# YouTube publicó los cuatro primeros como Marilyn, Lady Di, James y Jackie.
FACEBOOK_ORDER = ["jackie", "james", "diana", "marilyn"]
PROJECT_IDS = {"jackie": 3, "james": 4, "diana": 1, "marilyn": 2}


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"network": "facebook", "order": FACEBOOK_ORDER, "videos": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    load_dotenv(ROOT / ".env")
    publisher = SocialPublisher()
    connection = publisher.connection("facebook")
    if not connection.connected:
        raise RuntimeError(connection.message)

    by_key = {item["key"]: item for item in VIDEOS[:4]}
    state = load_state()
    for key in FACEBOOK_ORDER:
        item = by_key[key]
        previous = state["videos"].get(key, {})
        video_path = UPLOAD_DIR / item["file"]
        if previous.get("remote_id"):
            print(json.dumps({"key": key, "status": "omitido", "remote_id": previous["remote_id"]}, ensure_ascii=False))
            continue
        if not video_path.is_file():
            raise FileNotFoundError(f"No se encuentra {video_path}")

        caption = f"{item['title']}\n\n{item['description']}"
        result = publisher.publish("facebook", str(video_path), caption)
        state["videos"][key] = {
            "project_id": PROJECT_IDS[key],
            "file": str(video_path),
            "title": item["title"],
            "caption": caption,
            "remote_id": result["remote_id"],
            "remote_url": result.get("remote_url", ""),
            "status": result.get("status", "publicado"),
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
        save_state(state)
        print(json.dumps({"key": key, **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
