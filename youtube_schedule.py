"""Programa el lote privado de Fotos del Ayer en horario peninsular español."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build

from youtube_auth import load_credentials

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "data" / "youtube_upload" / "state.json"
ORDER = ["marilyn", "diana", "james", "jackie"]
TIMES = [time(11, 0), time(15, 0), time(19, 0), time(22, 0)]
MADRID = ZoneInfo("Europe/Madrid")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="Fecha de publicación YYYY-MM-DD")
    args = parser.parse_args()
    day = date.fromisoformat(args.date)
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    youtube = build("youtube", "v3", credentials=load_credentials())

    for key, local_time in zip(ORDER, TIMES):
        video_id = state["videos"][key]
        local_dt = datetime.combine(day, local_time, tzinfo=MADRID)
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
        print(json.dumps({"key": key, "video_id": video_id, "hora_madrid": local_dt.isoformat()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
