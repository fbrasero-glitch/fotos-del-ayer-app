from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from models.photo import Photo


class ResearchCache:
    def __init__(self, path: str | Path = "data/fotos_de_ayer.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS search_cache (
                    cache_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    query TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gemini_cache (
                    cache_key TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def key(*parts: object) -> str:
        raw = "\u241f".join(str(part) for part in parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _fresh(created_at: str, days: int) -> bool:
        try:
            created = datetime.fromisoformat(created_at)
        except ValueError:
            return False
        return created >= datetime.now(timezone.utc) - timedelta(days=days)

    def get_search(
        self,
        provider: str,
        query: str,
        limit: int,
        entity_qid: str,
        days: int = 7,
    ) -> list[Photo] | None:
        key = self.key(provider, query, limit, entity_qid)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json, created_at FROM search_cache WHERE cache_key = ?", (key,)
            ).fetchone()
        if not row or days <= 0 or not self._fresh(row[1], days):
            return None
        try:
            return [Photo.from_dict(item) for item in json.loads(row[0])]
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def set_search(
        self,
        provider: str,
        query: str,
        limit: int,
        entity_qid: str,
        photos: list[Photo],
    ) -> None:
        key = self.key(provider, query, limit, entity_qid)
        payload = json.dumps([photo.to_dict() for photo in photos], ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO search_cache(cache_key, provider, query, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    created_at=excluded.created_at
                """,
                (key, provider, query, payload, datetime.now(timezone.utc).isoformat()),
            )

    def get_gemini(self, kind: str, *parts: object) -> dict[str, Any] | list[Any] | None:
        key = self.key(kind, *parts)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json, created_at FROM gemini_cache WHERE cache_key = ?", (key,)
            ).fetchone()
        if not row or not self._fresh(row[1], 30):
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

    def set_gemini(self, kind: str, payload: Any, *parts: object) -> None:
        key = self.key(kind, *parts)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO gemini_cache(cache_key, kind, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    created_at=excluded.created_at
                """,
                (
                    key,
                    kind,
                    json.dumps(payload, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
