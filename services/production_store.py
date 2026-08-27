from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from models.entity import ResolvedEntity
from models.photo import Photo
from services.photo_state_store import stable_photo_key


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ProductionProject:
    id: int
    name: str
    character: str
    aliases: list[str]
    entity: ResolvedEntity
    created_at: str


@dataclass(slots=True)
class ProductionSlot:
    id: int
    project_id: int
    slot_key: str
    kind: str
    position: int
    label: str
    script_phrase: str = ""
    visual_brief: str = ""


@dataclass(slots=True)
class StoredSearch:
    id: int
    fingerprint: str
    source: str
    query: str
    keywords: str
    is_hook: bool
    photos: list[Photo]
    created_at: str


class ProductionStore:
    """Persistencia del flujo de Shorts, separada del motor de búsqueda."""

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
                CREATE TABLE IF NOT EXISTS production_projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    character TEXT NOT NULL,
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    entity_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS production_slots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL REFERENCES production_projects(id) ON DELETE CASCADE,
                    slot_key TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('hook', 'scene', 'final')),
                    position INTEGER NOT NULL,
                    label TEXT NOT NULL,
                    script_phrase TEXT NOT NULL DEFAULT '',
                    visual_brief TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, slot_key)
                );

                CREATE TABLE IF NOT EXISTS production_searches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    character_qid TEXT NOT NULL,
                    source TEXT NOT NULL,
                    query TEXT NOT NULL,
                    keywords TEXT NOT NULL,
                    is_hook INTEGER NOT NULL DEFAULT 0,
                    requested_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS production_search_results (
                    search_id INTEGER NOT NULL REFERENCES production_searches(id) ON DELETE CASCADE,
                    photo_key TEXT NOT NULL,
                    photo_json TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY(search_id, photo_key)
                );

                CREATE TABLE IF NOT EXISTS production_slot_searches (
                    slot_id INTEGER NOT NULL REFERENCES production_slots(id) ON DELETE CASCADE,
                    search_id INTEGER NOT NULL REFERENCES production_searches(id) ON DELETE CASCADE,
                    added_at TEXT NOT NULL,
                    PRIMARY KEY(slot_id, search_id)
                );

                CREATE TABLE IF NOT EXISTS production_candidates (
                    slot_id INTEGER NOT NULL REFERENCES production_slots(id) ON DELETE CASCADE,
                    photo_key TEXT NOT NULL,
                    photo_json TEXT NOT NULL,
                    chosen INTEGER NOT NULL DEFAULT 0,
                    added_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(slot_id, photo_key)
                );

                CREATE TABLE IF NOT EXISTS production_rights (
                    photo_key TEXT PRIMARY KEY,
                    report_json TEXT NOT NULL,
                    checked_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS production_downloads (
                    slot_id INTEGER NOT NULL REFERENCES production_slots(id) ON DELETE CASCADE,
                    photo_key TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    downloaded_at TEXT NOT NULL,
                    PRIMARY KEY(slot_id, photo_key)
                );

                CREATE TABLE IF NOT EXISTS production_project_settings (
                    project_id INTEGER PRIMARY KEY REFERENCES production_projects(id) ON DELETE CASCADE,
                    rights_mode TEXT NOT NULL DEFAULT 'experimental'
                        CHECK(rights_mode IN ('experimental', 'strict')),
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS production_decisions (
                    slot_id INTEGER NOT NULL REFERENCES production_slots(id) ON DELETE CASCADE,
                    photo_key TEXT NOT NULL,
                    photo_json TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK(decision IN ('use', 'discard', 'alternative')),
                    rights_mode TEXT NOT NULL CHECK(rights_mode IN ('experimental', 'strict')),
                    rights_level TEXT NOT NULL DEFAULT 'unchecked',
                    source_page_url TEXT NOT NULL DEFAULT '',
                    local_path TEXT NOT NULL DEFAULT '',
                    decided_at TEXT NOT NULL,
                    PRIMARY KEY(slot_id, photo_key)
                );

                CREATE TABLE IF NOT EXISTS production_visual_analyses (
                    slot_id INTEGER NOT NULL REFERENCES production_slots(id) ON DELETE CASCADE,
                    scene_signature TEXT NOT NULL,
                    photo_key TEXT NOT NULL,
                    model TEXT NOT NULL,
                    analysis_json TEXT NOT NULL,
                    analyzed_at TEXT NOT NULL,
                    PRIMARY KEY(slot_id, scene_signature, photo_key, model)
                );
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(production_slots)").fetchall()
            }
            for column in ("script_phrase", "visual_brief"):
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE production_slots ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    )

    @staticmethod
    def fingerprint(entity_qid: str, source: str, query: str) -> str:
        raw = "\u241f".join((entity_qid, source.casefold(), " ".join(query.casefold().split())))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def create_project(
        self,
        name: str,
        character: str,
        aliases: list[str],
        entity: ResolvedEntity,
    ) -> int:
        timestamp = _now()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO production_projects(
                    name, character, aliases_json, entity_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    name.strip(),
                    character.strip(),
                    json.dumps(aliases, ensure_ascii=False),
                    json.dumps(entity.to_dict(), ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
            project_id = int(cursor.lastrowid)
            slots = (
                ("hook", "hook", 0, "Gancho"),
                ("final", "final", 9999, "Foto final"),
            )
            connection.executemany(
                """
                INSERT INTO production_slots(
                    project_id, slot_key, kind, position, label, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (project_id, key, kind, position, label, timestamp)
                    for key, kind, position, label in slots
                ],
            )
            return project_id

    @staticmethod
    def _project(row: sqlite3.Row) -> ProductionProject:
        entity_data = json.loads(row["entity_json"])
        return ProductionProject(
            id=int(row["id"]),
            name=str(row["name"]),
            character=str(row["character"]),
            aliases=list(json.loads(row["aliases_json"])),
            entity=ResolvedEntity(**entity_data),
            created_at=str(row["created_at"]),
        )

    def list_projects(self) -> list[ProductionProject]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM production_projects ORDER BY updated_at DESC, id DESC"
            ).fetchall()
        return [self._project(row) for row in rows]

    def get_project(self, project_id: int) -> ProductionProject | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM production_projects WHERE id=?", (project_id,)
            ).fetchone()
        return self._project(row) if row else None

    def add_scene(self, project_id: int, label: str) -> int:
        timestamp = _now()
        with self.connect() as connection:
            position = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(position), 0) + 1
                    FROM production_slots WHERE project_id=? AND kind='scene'
                    """,
                    (project_id,),
                ).fetchone()[0]
            )
            cursor = connection.execute(
                """
                INSERT INTO production_slots(
                    project_id, slot_key, kind, position, label, created_at
                ) VALUES (?, ?, 'scene', ?, ?, ?)
                """,
                (
                    project_id,
                    f"scene-{position}",
                    position,
                    label.strip() or f"Escena {position}",
                    timestamp,
                ),
            )
            connection.execute(
                "UPDATE production_projects SET updated_at=? WHERE id=?",
                (timestamp, project_id),
            )
            return int(cursor.lastrowid)

    @staticmethod
    def _slot(row: sqlite3.Row) -> ProductionSlot:
        return ProductionSlot(
            id=int(row["id"]),
            project_id=int(row["project_id"]),
            slot_key=str(row["slot_key"]),
            kind=str(row["kind"]),
            position=int(row["position"]),
            label=str(row["label"]),
            script_phrase=str(row["script_phrase"] or ""),
            visual_brief=str(row["visual_brief"] or ""),
        )

    def list_slots(self, project_id: int) -> list[ProductionSlot]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM production_slots WHERE project_id=?
                ORDER BY CASE kind WHEN 'hook' THEN 0 WHEN 'scene' THEN 1 ELSE 2 END,
                         position, id
                """,
                (project_id,),
            ).fetchall()
        return [self._slot(row) for row in rows]

    def update_slot_brief(
        self,
        slot_id: int,
        script_phrase: str,
        visual_brief: str,
    ) -> None:
        timestamp = _now()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE production_slots
                SET script_phrase=?, visual_brief=?
                WHERE id=?
                """,
                (script_phrase.strip(), visual_brief.strip(), slot_id),
            )
            connection.execute(
                """
                UPDATE production_projects SET updated_at=?
                WHERE id=(SELECT project_id FROM production_slots WHERE id=?)
                """,
                (timestamp, slot_id),
            )

    def get_search(self, fingerprint: str) -> StoredSearch | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM production_searches WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
            if not row:
                return None
            photo_rows = connection.execute(
                """
                SELECT photo_json FROM production_search_results
                WHERE search_id=? ORDER BY position
                """,
                (int(row["id"]),),
            ).fetchall()
        return StoredSearch(
            id=int(row["id"]),
            fingerprint=str(row["fingerprint"]),
            source=str(row["source"]),
            query=str(row["query"]),
            keywords=str(row["keywords"]),
            is_hook=bool(row["is_hook"]),
            photos=[Photo.from_dict(json.loads(item["photo_json"])) for item in photo_rows],
            created_at=str(row["created_at"]),
        )

    def save_search(
        self,
        fingerprint: str,
        entity_qid: str,
        source: str,
        query: str,
        keywords: str,
        is_hook: bool,
        requested_count: int,
        photos: list[Photo],
    ) -> StoredSearch:
        timestamp = _now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO production_searches(
                    fingerprint, character_qid, source, query, keywords,
                    is_hook, requested_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fingerprint,
                    entity_qid,
                    source,
                    query,
                    keywords,
                    int(is_hook),
                    requested_count,
                    timestamp,
                ),
            )
            search_id = int(
                connection.execute(
                    "SELECT id FROM production_searches WHERE fingerprint=?", (fingerprint,)
                ).fetchone()[0]
            )
            for position, photo in enumerate(photos, start=1):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO production_search_results(
                        search_id, photo_key, photo_json, position
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        search_id,
                        stable_photo_key(photo),
                        json.dumps(photo.to_dict(), ensure_ascii=False),
                        position,
                    ),
                )
        return self.get_search(fingerprint)  # type: ignore[return-value]

    def attach_search(self, slot_id: int, search_id: int) -> None:
        timestamp = _now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO production_slot_searches(slot_id, search_id, added_at)
                VALUES (?, ?, ?)
                """,
                (slot_id, search_id, timestamp),
            )
            connection.execute(
                """
                UPDATE production_projects SET updated_at=? WHERE id=(
                    SELECT project_id FROM production_slots WHERE id=?
                )
                """,
                (timestamp, slot_id),
            )

    def slot_searches(self, slot_id: int) -> list[StoredSearch]:
        with self.connect() as connection:
            fingerprints = [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT s.fingerprint
                    FROM production_slot_searches ps
                    JOIN production_searches s ON s.id=ps.search_id
                    WHERE ps.slot_id=? ORDER BY ps.added_at DESC
                    """,
                    (slot_id,),
                )
            ]
        return [item for fp in fingerprints if (item := self.get_search(fp))]

    def slot_photos(self, slot_id: int, limit: int | None = None) -> list[Photo]:
        exact: dict[str, Photo] = {}
        for search in self.slot_searches(slot_id):
            for photo in search.photos:
                exact.setdefault(stable_photo_key(photo), photo)
        photos = list(exact.values())
        return photos[:limit] if limit is not None else photos

    def save_visual_analysis(
        self,
        slot_id: int,
        scene_signature: str,
        photo: Photo,
        model: str,
        analysis: dict,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO production_visual_analyses(
                    slot_id, scene_signature, photo_key, model, analysis_json, analyzed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(slot_id, scene_signature, photo_key, model) DO UPDATE SET
                    analysis_json=excluded.analysis_json,
                    analyzed_at=excluded.analyzed_at
                """,
                (
                    slot_id,
                    scene_signature,
                    stable_photo_key(photo),
                    model,
                    json.dumps(analysis, ensure_ascii=False),
                    _now(),
                ),
            )

    def get_visual_analyses(
        self,
        slot_id: int,
        scene_signature: str,
        model: str,
        photo_keys: list[str],
    ) -> dict[str, dict]:
        if not photo_keys:
            return {}
        placeholders = ",".join("?" for _ in photo_keys)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT photo_key, analysis_json FROM production_visual_analyses
                WHERE slot_id=? AND scene_signature=? AND model=?
                  AND photo_key IN ({placeholders})
                """,
                (slot_id, scene_signature, model, *photo_keys),
            ).fetchall()
        return {str(row[0]): json.loads(row[1]) for row in rows}

    def add_candidate(self, slot_id: int, photo: Photo) -> None:
        timestamp = _now()
        key = stable_photo_key(photo)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO production_candidates(
                    slot_id, photo_key, photo_json, chosen, added_at, updated_at
                ) VALUES (?, ?, ?, 0, ?, ?)
                ON CONFLICT(slot_id, photo_key) DO UPDATE SET
                    photo_json=excluded.photo_json, updated_at=excluded.updated_at
                """,
                (slot_id, key, json.dumps(photo.to_dict(), ensure_ascii=False), timestamp, timestamp),
            )

    def remove_candidate(self, slot_id: int, photo_key: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM production_candidates WHERE slot_id=? AND photo_key=?",
                (slot_id, photo_key),
            )

    def list_candidates(self, slot_id: int) -> list[tuple[Photo, bool]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT photo_json, chosen FROM production_candidates
                WHERE slot_id=? ORDER BY chosen DESC, added_at
                """,
                (slot_id,),
            ).fetchall()
        return [(Photo.from_dict(json.loads(row[0])), bool(row[1])) for row in rows]

    def choose_candidate(self, slot_id: int, photo: Photo) -> None:
        self.add_candidate(slot_id, photo)
        key = stable_photo_key(photo)
        timestamp = _now()
        with self.connect() as connection:
            connection.execute(
                "UPDATE production_candidates SET chosen=0, updated_at=? WHERE slot_id=?",
                (timestamp, slot_id),
            )
            connection.execute(
                """
                UPDATE production_candidates SET chosen=1, updated_at=?
                WHERE slot_id=? AND photo_key=?
                """,
                (timestamp, slot_id, key),
            )

    def save_rights_report(self, photo: Photo, report: dict) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO production_rights(photo_key, report_json, checked_at)
                VALUES (?, ?, ?)
                ON CONFLICT(photo_key) DO UPDATE SET
                    report_json=excluded.report_json, checked_at=excluded.checked_at
                """,
                (stable_photo_key(photo), json.dumps(report, ensure_ascii=False), _now()),
            )

    def get_rights_report(self, photo: Photo) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT report_json FROM production_rights WHERE photo_key=?",
                (stable_photo_key(photo),),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save_download(self, slot_id: int, photo: Photo, local_path: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO production_downloads(slot_id, photo_key, local_path, downloaded_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(slot_id, photo_key) DO UPDATE SET
                    local_path=excluded.local_path, downloaded_at=excluded.downloaded_at
                """,
                (slot_id, stable_photo_key(photo), local_path, _now()),
            )

    def get_download(self, slot_id: int, photo: Photo) -> str:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT local_path FROM production_downloads
                WHERE slot_id=? AND photo_key=?
                """,
                (slot_id, stable_photo_key(photo)),
            ).fetchone()
        return str(row[0]) if row else ""

    def get_rights_mode(self, project_id: int) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT rights_mode FROM production_project_settings WHERE project_id=?",
                (project_id,),
            ).fetchone()
        return str(row[0]) if row else "experimental"

    def set_rights_mode(self, project_id: int, mode: str) -> None:
        if mode not in {"experimental", "strict"}:
            raise ValueError("Modo de derechos no válido.")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO production_project_settings(project_id, rights_mode, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    rights_mode=excluded.rights_mode, updated_at=excluded.updated_at
                """,
                (project_id, mode, _now()),
            )

    def record_decision(
        self,
        slot_id: int,
        photo: Photo,
        decision: str,
        rights_mode: str,
        rights_level: str = "unchecked",
        local_path: str = "",
    ) -> None:
        if decision not in {"use", "discard", "alternative"}:
            raise ValueError("Decisión no válida.")
        if rights_mode not in {"experimental", "strict"}:
            raise ValueError("Modo de derechos no válido.")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO production_decisions(
                    slot_id, photo_key, photo_json, decision, rights_mode,
                    rights_level, source_page_url, local_path, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slot_id, photo_key) DO UPDATE SET
                    photo_json=excluded.photo_json,
                    decision=excluded.decision,
                    rights_mode=excluded.rights_mode,
                    rights_level=excluded.rights_level,
                    source_page_url=excluded.source_page_url,
                    local_path=excluded.local_path,
                    decided_at=excluded.decided_at
                """,
                (
                    slot_id,
                    stable_photo_key(photo),
                    json.dumps(photo.to_dict(), ensure_ascii=False),
                    decision,
                    rights_mode,
                    rights_level,
                    photo.original_page_url,
                    local_path,
                    _now(),
                ),
            )

    def decision_map(self, slot_id: int) -> dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT photo_key, decision FROM production_decisions WHERE slot_id=?",
                (slot_id,),
            ).fetchall()
        return {str(row[0]): str(row[1]) for row in rows}
