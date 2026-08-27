"""Verifica la publicación y añade una vez los comentarios de los dos Shorts."""

from __future__ import annotations

import json

from googleapiclient.discovery import build

from youtube_auth import EXPECTED_CHANNEL_ID, load_credentials
from youtube_two_shorts import STATE_PATH, VIDEOS, video_status


def main() -> None:
    youtube = build("youtube", "v3", credentials=load_credentials())
    channel = youtube.channels().list(part="snippet", mine=True).execute().get("items", [])
    if not channel or channel[0]["id"] != EXPECTED_CHANNEL_ID:
        raise RuntimeError("La autorización no corresponde al canal Fotos del Ayer.")
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    for key, item in VIDEOS.items():
        saved = state["videos"][key]
        video_id = saved["video_id"]
        status = video_status(youtube, video_id)
        if status["privacy"] != "public" or status["upload_status"] != "processed":
            raise RuntimeError(json.dumps({"key": key, **status}, ensure_ascii=False))
        if not saved.get("comment_id"):
            response = youtube.commentThreads().insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId": video_id,
                        "topLevelComment": {
                            "snippet": {"textOriginal": saved.get("comment_pending") or item["comment"]}
                        },
                    }
                },
            ).execute()
            saved["comment_id"] = response["id"]
            STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"key": key, **status, "comment_id": saved["comment_id"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
