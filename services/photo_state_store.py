from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from models.photo import Photo




def stable_photo_key(photo: Photo) -> str:
    url = (photo.image_url or photo.thumbnail_url).split("?", 1)[0].casefold()
    return hashlib.sha256(url.encode("utf-8")).hexdigest()

@dataclass(slots=True)
class PhotoResearchState:
    favorite: bool = False
    discarded: bool = False
    video_candidate: bool = False


class PhotoStateStore:
    def __init__(self, path: str | Path = "data/fotos_de_ayer.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS photo_research_state (
                    photo_key TEXT PRIMARY KEY,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    discarded INTEGER NOT NULL DEFAULT 0,
                    video_candidate INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def get_many(self, photo_keys: list[str]) -> dict[str, PhotoResearchState]:
        if not photo_keys:
            return {}
        result: dict[str, PhotoResearchState] = {}
        with self._connect() as connection:
            for start in range(0, len(photo_keys), 400):
                batch = photo_keys[start : start + 400]
                placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(
                    f"""
                    SELECT photo_key, favorite, discarded, video_candidate
                    FROM photo_research_state
                    WHERE photo_key IN ({placeholders})
                    """,
                    batch,
                )
                for key, favorite, discarded, video_candidate in rows:
                    result[str(key)] = PhotoResearchState(
                        bool(favorite), bool(discarded), bool(video_candidate)
                    )
        return result

    def set(self, photo_key: str, state: PhotoResearchState) -> None:
        if state.discarded:
            state.favorite = False
            state.video_candidate = False
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO photo_research_state(
                    photo_key, favorite, discarded, video_candidate, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(photo_key) DO UPDATE SET
                    favorite=excluded.favorite,
                    discarded=excluded.discarded,
                    video_candidate=excluded.video_candidate,
                    updated_at=excluded.updated_at
                """,
                (
                    photo_key,
                    int(state.favorite),
                    int(state.discarded),
                    int(state.video_candidate),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
