from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest
from PIL import Image

from models.edit import EditSegment
from models.entity import ResolvedEntity
from services.narration_script import estimate_duration_seconds, split_script
from services.production_store import ProductionStore
from services.short_edit_store import ShortEditStore, narration_hash
from services.subtitle_builder import alignment_words, captions_for_segments, write_srt
from services.video_renderer import RenderSettings, ShortVideoRenderer


def _alignment(text: str, duration: float = 0.8) -> dict:
    step = duration / max(1, len(text))
    return {
        "characters": list(text),
        "character_start_times_seconds": [index * step for index in range(len(text))],
        "character_end_times_seconds": [(index + 1) * step for index in range(len(text))],
    }


def _write_tone(path: Path, duration: float = 0.8, sample_rate: int = 16_000) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        samples = []
        for index in range(round(duration * sample_rate)):
            value = int(2000 * math.sin(2 * math.pi * 220 * index / sample_rate))
            samples.append(struct.pack("<h", value))
        audio.writeframes(b"".join(samples))


def test_edit_store_persists_and_invalidates_only_changed_narration(tmp_path):
    database = tmp_path / "short.db"
    production = ProductionStore(database)
    project_id = production.create_project(
        "Historia",
        "Personaje",
        [],
        ResolvedEntity("Q1", "Personaje"),
    )
    scene_id = production.add_scene(project_id, "Un recuerdo")
    slots = production.list_slots(project_id)
    store = ShortEditStore(database)
    store.ensure_project(project_id, slots, {scene_id: "foto.jpg"})

    store.update_segment(project_id, scene_id, "La primera frase.", "Nostalgia cálida", 500)
    audio_path = tmp_path / "voz.mp3"
    audio_path.write_bytes(b"audio")
    store.save_audio(
        project_id,
        scene_id,
        "La primera frase.",
        str(audio_path),
        _alignment("La primera frase."),
        800,
    )
    current = next(item for item in store.list_segments(project_id) if item.slot_id == scene_id)
    assert current.audio_is_current
    assert current.image_path == "foto.jpg"

    store.update_segment(project_id, scene_id, "La primera frase.", "Admiración", 700)
    still_current = next(item for item in store.list_segments(project_id) if item.slot_id == scene_id)
    assert still_current.audio_is_current
    assert still_current.pause_after_ms == 700

    store.update_segment(project_id, scene_id, "Una frase distinta.", "Admiración", 700)
    changed = next(item for item in store.list_segments(project_id) if item.slot_id == scene_id)
    assert not changed.audio_is_current
    assert changed.audio_path == ""


def test_script_split_and_duration_are_suitable_for_scene_cards():
    explicit = "Gancho.\n\n---\n\nComienzo.\n\n---\n\nFinal."
    assert split_script(explicit, 3) == ["Gancho.", "Comienzo.", "Final."]

    automatic = split_script(
        "Esta es la primera frase. Después ocurrió algo importante. "
        "El tiempo pasó lentamente. Pero nadie olvidó aquella mirada. "
        "Y así termina la historia.",
        3,
    )
    assert len(automatic) == 3
    assert all(automatic)
    assert 3 < estimate_duration_seconds("Una fotografía puede guardar toda una vida.") < 6


def test_alignment_creates_readable_offset_subtitles(tmp_path):
    text = "Hola mundo, todavía te recordamos."
    alignment = _alignment(text, 2.0)
    words = alignment_words(alignment, offset=3.0)
    assert words[0].text == "Hola"
    assert words[0].start == pytest.approx(3.0)

    segment = EditSegment(
        project_id=1,
        slot_id=1,
        position=0,
        slot_key="hook",
        kind="hook",
        label="Gancho",
        narration=text,
        alignment=alignment,
    )
    captions = captions_for_segments([segment], [2.5])
    destination = write_srt(captions, tmp_path / "subtitulos.srt")
    content = destination.read_text(encoding="utf-8")
    assert "Hola mundo" in content
    assert "00:00:00,000" in content


def test_renderer_builds_vertical_video_without_external_api(tmp_path):
    renderer = ShortVideoRenderer(settings=RenderSettings(width=360, height=640, fps=10, crf=30))
    if not renderer.configured:
        pytest.skip("FFmpeg no está disponible")

    segments: list[EditSegment] = []
    for index, color in enumerate(((110, 80, 70), (70, 90, 120)), start=1):
        image_path = tmp_path / f"foto_{index}.jpg"
        Image.new("RGB", (500, 350), color).save(image_path)
        audio_path = tmp_path / f"voz_{index}.wav"
        _write_tone(audio_path)
        narration = f"Escena número {index}."
        segments.append(
            EditSegment(
                project_id=1,
                slot_id=index,
                position=index - 1,
                slot_key=f"scene-{index}",
                kind="scene",
                label=f"Escena {index}",
                image_path=str(image_path),
                narration=narration,
                pause_after_ms=100,
                audio_path=str(audio_path),
                alignment=_alignment(narration),
                audio_duration_ms=800,
                audio_text_hash=narration_hash(narration),
            )
        )

    result = renderer.render(segments, tmp_path / "render")

    assert result.final_path.is_file()
    assert result.final_path.stat().st_size > 10_000
    assert result.srt_path.is_file()
    assert 1.6 < result.duration_seconds < 2.2
