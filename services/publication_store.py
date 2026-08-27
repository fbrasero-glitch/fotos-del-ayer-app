"""Cola persistente y metadatos de publicación para Fotos del Ayer.

No guarda credenciales: solo datos editoriales y el resultado devuelto por YouTube.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


STATUSES = ("pendiente", "validado", "subiendo", "procesando", "programado", "publicado", "error")
SOCIAL_NETWORKS = ("facebook", "instagram", "tiktok")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class PublicationJob:
    id: int
    project_id: int
    video_path: str
    title: str
    description: str
    tags: list[str]
    hashtags: list[str]
    playlist_id: str
    pinned_comment: str
    thumbnail_path: str
    publish_at: str
    status: str
    youtube_video_id: str
    youtube_url: str
    privacy_status: str
    processing_status: str
    last_error: str
    validation_json: dict
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class SocialPublication:
    id: int
    project_id: int
    network: str
    caption: str
    status: str
    remote_id: str
    remote_url: str
    last_error: str
    published_at: str
    created_at: str
    updated_at: str


class PublicationStore:
    def __init__(self, path: str | Path = "data/fotos_de_ayer.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS publication_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL REFERENCES production_projects(id) ON DELETE CASCADE,
                    video_path TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]', hashtags_json TEXT NOT NULL DEFAULT '[]',
                    playlist_id TEXT NOT NULL DEFAULT '', pinned_comment TEXT NOT NULL DEFAULT '',
                    thumbnail_path TEXT NOT NULL DEFAULT '', publish_at TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pendiente'
                        CHECK(status IN ('pendiente','validado','subiendo','procesando','programado','publicado','error')),
                    youtube_video_id TEXT NOT NULL DEFAULT '', youtube_url TEXT NOT NULL DEFAULT '',
                    privacy_status TEXT NOT NULL DEFAULT 'private', processing_status TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '', validation_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(project_id)
                );
                CREATE TABLE IF NOT EXISTS social_publications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL REFERENCES production_projects(id) ON DELETE CASCADE,
                    network TEXT NOT NULL CHECK(network IN ('facebook','instagram','tiktok')),
                    caption TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pendiente'
                        CHECK(status IN ('pendiente','subiendo','procesando','publicado','error')),
                    remote_id TEXT NOT NULL DEFAULT '', remote_url TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '', published_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(project_id, network)
                );
                """
            )

    @staticmethod
    def _job(row: sqlite3.Row) -> PublicationJob:
        data = dict(row)
        for field in ("tags", "hashtags", "validation"):
            data[f"{field}_json"] = json.loads(data[f"{field}_json"] or ("{}" if field == "validation" else "[]"))
        return PublicationJob(
            **{key: value for key, value in data.items() if not key.endswith("_json")},
            tags=data["tags_json"], hashtags=data["hashtags_json"], validation_json=data["validation_json"],
        )

    def get(self, project_id: int) -> PublicationJob | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM publication_jobs WHERE project_id=?", (project_id,)).fetchone()
        return self._job(row) if row else None

    def list_jobs(self) -> list[PublicationJob]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM publication_jobs ORDER BY updated_at DESC").fetchall()
        return [self._job(row) for row in rows]

    def save_draft(self, project_id: int, **fields: object) -> PublicationJob:
        allowed = {"video_path", "title", "description", "playlist_id", "pinned_comment", "thumbnail_path", "publish_at"}
        payload = {key: str(value or "") for key, value in fields.items() if key in allowed}
        for name in ("tags", "hashtags"):
            if name in fields:
                payload[f"{name}_json"] = json.dumps(fields[name] or [], ensure_ascii=False)
        timestamp = _now()
        existing = self.get(project_id)
        if existing is None:
            values = {"video_path": "", "title": "", "description": "", "playlist_id": "", "pinned_comment": "", "thumbnail_path": "", "publish_at": "", "tags_json": "[]", "hashtags_json": "[]"}
            values.update(payload)
            with self.connect() as connection:
                connection.execute(
                    """INSERT INTO publication_jobs(project_id, video_path, title, description, tags_json, hashtags_json,
                    playlist_id, pinned_comment, thumbnail_path, publish_at, created_at, updated_at)
                    VALUES (:project_id, :video_path, :title, :description, :tags_json, :hashtags_json,
                    :playlist_id, :pinned_comment, :thumbnail_path, :publish_at, :created_at, :updated_at)""",
                    {"project_id": project_id, **values, "created_at": timestamp, "updated_at": timestamp},
                )
        elif payload:
            assignments = ", ".join(f"{key}=?" for key in payload)
            with self.connect() as connection:
                connection.execute(
                    f"UPDATE publication_jobs SET {assignments}, updated_at=? WHERE project_id=?",
                    (*payload.values(), timestamp, project_id),
                )
        return self.get(project_id)  # type: ignore[return-value]

    def update_result(self, project_id: int, *, status: str | None = None, **fields: object) -> PublicationJob:
        if status is not None and status not in STATUSES:
            raise ValueError("Estado de publicación no válido.")
        allowed = {"youtube_video_id", "youtube_url", "privacy_status", "processing_status", "last_error", "publish_at", "validation_json"}
        payload = {key: value for key, value in fields.items() if key in allowed}
        if "validation_json" in payload:
            payload["validation_json"] = json.dumps(payload["validation_json"], ensure_ascii=False)
        if status is not None:
            payload["status"] = status
        if not payload:
            return self.get(project_id)  # type: ignore[return-value]
        payload["updated_at"] = _now()
        assignments = ", ".join(f"{key}=?" for key in payload)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE publication_jobs SET {assignments} WHERE project_id=?",
                (*payload.values(), project_id),
            )
        return self.get(project_id)  # type: ignore[return-value]

    @staticmethod
    def _social(row: sqlite3.Row) -> SocialPublication:
        return SocialPublication(**dict(row))

    def social_publications(self, project_id: int) -> list[SocialPublication]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM social_publications WHERE project_id=? ORDER BY network", (project_id,)
            ).fetchall()
        return [self._social(row) for row in rows]

    def ensure_social_publications(self, project_id: int, captions: dict[str, str]) -> list[SocialPublication]:
        timestamp = _now()
        with self.connect() as connection:
            for network in SOCIAL_NETWORKS:
                connection.execute(
                    """INSERT INTO social_publications(project_id, network, caption, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?) ON CONFLICT(project_id, network) DO NOTHING""",
                    (project_id, network, str(captions.get(network, "")), timestamp, timestamp),
                )
        return self.social_publications(project_id)

    def update_social_publication(self, project_id: int, network: str, **fields: object) -> SocialPublication:
        if network not in SOCIAL_NETWORKS:
            raise ValueError("Red social no válida.")
        allowed = {"caption", "status", "remote_id", "remote_url", "last_error", "published_at"}
        payload = {key: str(value or "") for key, value in fields.items() if key in allowed}
        if payload.get("status") and payload["status"] not in {"pendiente", "subiendo", "procesando", "publicado", "error"}:
            raise ValueError("Estado social no válido.")
        if not payload:
            return next(item for item in self.social_publications(project_id) if item.network == network)
        payload["updated_at"] = _now()
        assignments = ", ".join(f"{key}=?" for key in payload)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE social_publications SET {assignments} WHERE project_id=? AND network=?",
                (*payload.values(), project_id, network),
            )
            row = connection.execute(
                "SELECT * FROM social_publications WHERE project_id=? AND network=?", (project_id, network)
            ).fetchone()
        if row is None:
            raise LookupError("No existe la publicación social.")
        return self._social(row)
