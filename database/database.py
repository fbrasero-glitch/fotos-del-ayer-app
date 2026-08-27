from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from models.project import Project


class Database:
    def __init__(self, path: str | Path = "data/fotos_de_ayer.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    character TEXT NOT NULL,
                    script TEXT NOT NULL,
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_at TEXT NOT NULL,
                    entity_qid TEXT NOT NULL DEFAULT '',
                    entity_label TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS scenes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    scene_key TEXT NOT NULL,
                    scene_index INTEGER NOT NULL,
                    label TEXT NOT NULL,
                    narrative_text TEXT NOT NULL,
                    keywords_json TEXT NOT NULL,
                    query TEXT NOT NULL,
                    query_variants_json TEXT NOT NULL DEFAULT '[]',
                    is_hook INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(project_id, scene_key)
                );

                CREATE TABLE IF NOT EXISTS photos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    thumbnail_url TEXT NOT NULL,
                    image_url TEXT NOT NULL,
                    original_page_url TEXT NOT NULL,
                    author TEXT,
                    photo_date TEXT,
                    source TEXT,
                    institution TEXT,
                    license TEXT,
                    license_url TEXT,
                    license_description TEXT,
                    commercial_use INTEGER,
                    attribution_required INTEGER,
                    traffic_light TEXT,
                    width INTEGER,
                    height INTEGER,
                    description TEXT,
                    entity_relevance INTEGER NOT NULL DEFAULT 0,
                    entity_evidence TEXT NOT NULL DEFAULT '',
                    scene_relevance INTEGER NOT NULL DEFAULT 0,
                    technical_score INTEGER NOT NULL DEFAULT 0,
                    visual_impact INTEGER NOT NULL DEFAULT 0,
                    ai_description TEXT NOT NULL DEFAULT '',
                    ai_recommended INTEGER NOT NULL DEFAULT 0,
                    final_score INTEGER NOT NULL DEFAULT 0,
                    rights_status TEXT NOT NULL DEFAULT 'Revisar licencia',
                    score INTEGER,
                    local_path TEXT,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS selections (
                    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    scene_id INTEGER NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
                    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
                    PRIMARY KEY(project_id, scene_id)
                );
                """
            )
            migrations = [
                ("projects", "entity_qid", "TEXT NOT NULL DEFAULT ''"),
                ("projects", "entity_label", "TEXT NOT NULL DEFAULT ''"),
                ("scenes", "query_variants_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("photos", "entity_relevance", "INTEGER NOT NULL DEFAULT 0"),
                ("photos", "entity_evidence", "TEXT NOT NULL DEFAULT ''"),
                ("photos", "scene_relevance", "INTEGER NOT NULL DEFAULT 0"),
                ("photos", "technical_score", "INTEGER NOT NULL DEFAULT 0"),
                ("photos", "visual_impact", "INTEGER NOT NULL DEFAULT 0"),
                ("photos", "ai_description", "TEXT NOT NULL DEFAULT ''"),
                ("photos", "ai_recommended", "INTEGER NOT NULL DEFAULT 0"),
                ("photos", "final_score", "INTEGER NOT NULL DEFAULT 0"),
                ("photos", "rights_status", "TEXT NOT NULL DEFAULT 'Revisar licencia'"),
            ]
            for table, column, definition in migrations:
                self._ensure_column(connection, table, column, definition)

    @staticmethod
    def _nullable_bool(value: bool | None) -> int | None:
        return None if value is None else int(value)

    def save_project(self, project: Project) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO projects(
                    character, script, aliases_json, status, created_at, entity_qid, entity_label
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project.character,
                    project.script,
                    json.dumps(project.aliases, ensure_ascii=False),
                    project.status,
                    created_at,
                    project.entity_qid,
                    project.entity_label,
                ),
            )
            project_id = int(cursor.lastrowid)
            scene_ids: dict[str, int] = {}
            for scene in project.scenes:
                scene_cursor = connection.execute(
                    """
                    INSERT INTO scenes(
                        project_id, scene_key, scene_index, label, narrative_text,
                        keywords_json, query, query_variants_json, is_hook
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        scene.key,
                        scene.index,
                        scene.label,
                        scene.text,
                        json.dumps(scene.keywords, ensure_ascii=False),
                        scene.query,
                        json.dumps(scene.query_variants, ensure_ascii=False),
                        int(scene.is_hook),
                    ),
                )
                scene_ids[scene.key] = int(scene_cursor.lastrowid)

            for scene_key, photo in project.selections.items():
                if scene_key not in scene_ids:
                    continue
                metadata = photo.to_dict()
                photo_cursor = connection.execute(
                    """
                    INSERT INTO photos(
                        provider_id, title, thumbnail_url, image_url, original_page_url,
                        author, photo_date, source, institution, license, license_url,
                        license_description, commercial_use, attribution_required, traffic_light,
                        width, height, description, entity_relevance, entity_evidence,
                        scene_relevance, technical_score, visual_impact, ai_description,
                        ai_recommended, final_score, rights_status, score, local_path, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        photo.id,
                        photo.title,
                        photo.thumbnail_url,
                        photo.image_url,
                        photo.original_page_url,
                        photo.author,
                        photo.date,
                        photo.source,
                        photo.institution,
                        photo.license,
                        photo.license_url,
                        photo.license_description,
                        self._nullable_bool(photo.commercial_use),
                        self._nullable_bool(photo.attribution_required),
                        photo.traffic_light,
                        photo.width,
                        photo.height,
                        photo.description,
                        photo.entity_relevance,
                        photo.entity_evidence,
                        photo.scene_relevance,
                        photo.technical_score,
                        photo.visual_impact,
                        photo.ai_description,
                        int(photo.ai_recommended),
                        photo.final_score,
                        photo.rights_status,
                        photo.score,
                        photo.local_path,
                        json.dumps(metadata, ensure_ascii=False),
                    ),
                )
                connection.execute(
                    "INSERT INTO selections(project_id, scene_id, photo_id) VALUES (?, ?, ?)",
                    (project_id, scene_ids[scene_key], int(photo_cursor.lastrowid)),
                )
            return project_id
