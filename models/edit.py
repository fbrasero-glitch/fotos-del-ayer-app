from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ShortEdit:
    project_id: int
    voice_id: str = ""
    voice_name: str = ""
    voice_model: str = "eleven_multilingual_v2"
    voice_settings: dict = field(default_factory=dict)
    music_path: str = ""
    music_volume_db: float = -22.0
    status: str = "draft"
    output_path: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class EditSegment:
    project_id: int
    slot_id: int
    position: int
    slot_key: str
    kind: str
    label: str
    image_path: str = ""
    narration: str = ""
    tone: str = "Nostalgia cálida"
    pause_after_ms: int = 450
    audio_path: str = ""
    alignment: dict = field(default_factory=dict)
    audio_duration_ms: int = 0
    audio_text_hash: str = ""
    updated_at: str = ""

    @property
    def audio_is_current(self) -> bool:
        from services.short_edit_store import narration_hash

        return bool(
            self.narration.strip()
            and self.audio_path
            and self.audio_text_hash == narration_hash(self.narration)
        )
