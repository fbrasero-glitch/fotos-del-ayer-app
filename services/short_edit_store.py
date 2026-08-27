from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from models.edit import EditSegment, ShortEdit
from services.production_store import ProductionSlot


DEFAULT_VOICE_SETTINGS = {
    "stability": 0.52,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": True,
    "speed": 0.95,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def narration_hash(text: str) -> str:
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class ShortEditStore:
    """Persistencia de una edición activa por proyecto.

    Los audios nunca se borran al editar el texto. Simplemente se desacoplan de la
    escena para que una versión anterior siga disponible en la carpeta del proyecto.
    """

    def __init__(self, path: str | Path) -> None:
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
                CREATE TABLE IF NOT EXISTS short_edits (
                    project_id INTEGER PRIMARY KEY
                        REFERENCES production_projects(id) ON DELETE CASCADE,
                    voice_id TEXT NOT NULL DEFAULT '',
                    voice_name TEXT NOT NULL DEFAULT '',
                    voice_model TEXT NOT NULL DEFAULT 'eleven_multilingual_v2',
                    voice_settings_json TEXT NOT NULL DEFAULT '{}',
                    music_path TEXT NOT NULL DEFAULT '',
                    music_volume_db REAL NOT NULL DEFAULT -22,
                    status TEXT NOT NULL DEFAULT 'draft',
                    output_path TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS short_edit_segments (
                    project_id INTEGER NOT NULL
                        REFERENCES short_edits(project_id) ON DELETE CASCADE,
                    slot_id INTEGER NOT NULL
                        REFERENCES production_slots(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    slot_key TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    image_path TEXT NOT NULL DEFAULT '',
                    narration TEXT NOT NULL DEFAULT '',
                    tone TEXT NOT NULL DEFAULT 'Nostalgia cálida',
                    pause_after_ms INTEGER NOT NULL DEFAULT 450,
                    audio_path TEXT NOT NULL DEFAULT '',
                    alignment_json TEXT NOT NULL DEFAULT '{}',
                    audio_duration_ms INTEGER NOT NULL DEFAULT 0,
                    audio_text_hash TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, slot_id)
                );
                """
            )

    def ensure_project(
        self,
        project_id: int,
        slots: list[ProductionSlot],
        initial_images: dict[int, str] | None = None,
    ) -> None:
        timestamp = _now()
        initial_images = initial_images or {}
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO short_edits(
                    project_id, voice_settings_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    project_id,
                    json.dumps(DEFAULT_VOICE_SETTINGS),
                    timestamp,
                    timestamp,
                ),
            )
            current_voice = connection.execute(
                "SELECT voice_id FROM short_edits WHERE project_id=?", (project_id,)
            ).fetchone()
            if current_voice and not str(current_voice["voice_id"]).strip():
                previous_voice = connection.execute(
                    """
                    SELECT voice_id, voice_name, voice_model, voice_settings_json
                    FROM short_edits
                    WHERE project_id<>? AND voice_id<>''
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (project_id,),
                ).fetchone()
                if previous_voice:
                    connection.execute(
                        """
                        UPDATE short_edits
                        SET voice_id=?, voice_name=?, voice_model=?,
                            voice_settings_json=?, updated_at=?
                        WHERE project_id=?
                        """,
                        (
                            previous_voice["voice_id"],
                            previous_voice["voice_name"],
                            previous_voice["voice_model"],
                            previous_voice["voice_settings_json"],
                            timestamp,
                            project_id,
                        ),
                    )
            for order, slot in enumerate(slots):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO short_edit_segments(
                        project_id, slot_id, position, slot_key, kind, label,
                        image_path, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        slot.id,
                        order,
                        slot.slot_key,
                        slot.kind,
                        slot.label,
                        initial_images.get(slot.id, ""),
                        timestamp,
                    ),
                )
                connection.execute(
                    """
                    UPDATE short_edit_segments
                    SET position=?, slot_key=?, kind=?, label=?, updated_at=?
                    WHERE project_id=? AND slot_id=?
                    """,
                    (order, slot.slot_key, slot.kind, slot.label, timestamp, project_id, slot.id),
                )

    @staticmethod
    def _edit(row: sqlite3.Row) -> ShortEdit:
        settings = json.loads(row["voice_settings_json"] or "{}")
        return ShortEdit(
            project_id=int(row["project_id"]),
            voice_id=str(row["voice_id"]),
            voice_name=str(row["voice_name"]),
            voice_model=str(row["voice_model"]),
            voice_settings=settings or dict(DEFAULT_VOICE_SETTINGS),
            music_path=str(row["music_path"]),
            music_volume_db=float(row["music_volume_db"]),
            status=str(row["status"]),
            output_path=str(row["output_path"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def get_edit(self, project_id: int) -> ShortEdit:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM short_edits WHERE project_id=?", (project_id,)
            ).fetchone()
        if not row:
            raise LookupError(f"La edición del proyecto {project_id} no existe.")
        return self._edit(row)

    @staticmethod
    def _segment(row: sqlite3.Row) -> EditSegment:
        return EditSegment(
            project_id=int(row["project_id"]),
            slot_id=int(row["slot_id"]),
            position=int(row["position"]),
            slot_key=str(row["slot_key"]),
            kind=str(row["kind"]),
            label=str(row["label"]),
            image_path=str(row["image_path"]),
            narration=str(row["narration"]),
            tone=str(row["tone"]),
            pause_after_ms=int(row["pause_after_ms"]),
            audio_path=str(row["audio_path"]),
            alignment=json.loads(row["alignment_json"] or "{}"),
            audio_duration_ms=int(row["audio_duration_ms"]),
            audio_text_hash=str(row["audio_text_hash"]),
            updated_at=str(row["updated_at"]),
        )

    def list_segments(self, project_id: int) -> list[EditSegment]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM short_edit_segments
                WHERE project_id=? ORDER BY position, slot_id
                """,
                (project_id,),
            ).fetchall()
        return [self._segment(row) for row in rows]

    def set_image(self, project_id: int, slot_id: int, image_path: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE short_edit_segments SET image_path=?, updated_at=?
                WHERE project_id=? AND slot_id=?
                """,
                (image_path, _now(), project_id, slot_id),
            )

    def update_segment(
        self,
        project_id: int,
        slot_id: int,
        narration: str,
        tone: str,
        pause_after_ms: int,
    ) -> None:
        timestamp = _now()
        clean_text = " ".join(narration.split())
        with self.connect() as connection:
            current = connection.execute(
                """
                SELECT narration FROM short_edit_segments
                WHERE project_id=? AND slot_id=?
                """,
                (project_id, slot_id),
            ).fetchone()
            changed = not current or " ".join(str(current[0]).split()) != clean_text
            if changed:
                connection.execute(
                    """
                    UPDATE short_edit_segments
                    SET narration=?, tone=?, pause_after_ms=?, audio_path='',
                        alignment_json='{}', audio_duration_ms=0,
                        audio_text_hash='', updated_at=?
                    WHERE project_id=? AND slot_id=?
                    """,
                    (clean_text, tone, pause_after_ms, timestamp, project_id, slot_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE short_edit_segments
                    SET tone=?, pause_after_ms=?, updated_at=?
                    WHERE project_id=? AND slot_id=?
                    """,
                    (tone, pause_after_ms, timestamp, project_id, slot_id),
                )

    def set_voice(
        self,
        project_id: int,
        voice_id: str,
        voice_name: str,
        voice_model: str,
        settings: dict,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE short_edits
                SET voice_id=?, voice_name=?, voice_model=?,
                    voice_settings_json=?, updated_at=?
                WHERE project_id=?
                """,
                (
                    voice_id.strip(),
                    voice_name.strip(),
                    voice_model,
                    json.dumps(settings),
                    _now(),
                    project_id,
                ),
            )

    def save_audio(
        self,
        project_id: int,
        slot_id: int,
        narration: str,
        audio_path: str,
        alignment: dict,
        duration_ms: int,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE short_edit_segments
                SET audio_path=?, alignment_json=?, audio_duration_ms=?,
                    audio_text_hash=?, updated_at=?
                WHERE project_id=? AND slot_id=?
                """,
                (
                    audio_path,
                    json.dumps(alignment, ensure_ascii=False),
                    duration_ms,
                    narration_hash(narration),
                    _now(),
                    project_id,
                    slot_id,
                ),
            )

    def set_music(self, project_id: int, music_path: str, volume_db: float) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE short_edits SET music_path=?, music_volume_db=?, updated_at=?
                WHERE project_id=?
                """,
                (music_path, volume_db, _now(), project_id),
            )

    def save_output(self, project_id: int, output_path: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE short_edits SET output_path=?, status='rendered', updated_at=?
                WHERE project_id=?
                """,
                (output_path, _now(), project_id),
            )
