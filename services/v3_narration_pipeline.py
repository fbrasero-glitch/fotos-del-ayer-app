from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from models.edit import EditSegment
from services.elevenlabs_tts import ElevenLabsTTS, SpeechResult
from services.short_edit_store import ShortEditStore


class NarrationPipelineError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FullNarrationResult:
    full_audio_path: Path
    segment_audio_paths: list[Path]
    duration_ms: int


def _clean(text: str) -> str:
    return " ".join(text.split())


def _startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return info


def _alignment_ranges(alignment: dict, segments: list[EditSegment]) -> list[tuple[int, int]]:
    characters = alignment.get("characters") or []
    aligned_text = "".join(str(character) for character in characters)
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for segment in segments:
        needle = _clean(segment.narration)
        start = aligned_text.find(needle, cursor)
        if start < 0:
            start = aligned_text.casefold().find(needle.casefold(), cursor)
        if start < 0:
            raise NarrationPipelineError(
                f"No se pudo alinear la narración de {segment.label} con el audio completo."
            )
        end = start + len(needle)
        ranges.append((start, end))
        cursor = end
    return ranges


def _slice_alignment(alignment: dict, start: int, end: int, offset: float) -> dict:
    characters = list(alignment.get("characters") or [])[start:end]
    starts = list(alignment.get("character_start_times_seconds") or [])[start:end]
    ends = list(alignment.get("character_end_times_seconds") or [])[start:end]
    return {
        "characters": characters,
        "character_start_times_seconds": [max(0.0, float(value) - offset) for value in starts],
        "character_end_times_seconds": [max(0.0, float(value) - offset) for value in ends],
    }


def _split_audio(
    ffmpeg: str,
    source: Path,
    destination: Path,
    start_seconds: float,
    duration_seconds: float,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-ss",
            f"{start_seconds:.4f}",
            "-t",
            f"{duration_seconds:.4f}",
            "-vn",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(destination),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        startupinfo=_startupinfo(),
    )
    if process.returncode:
        raise NarrationPipelineError(
            "No se pudo dividir la narración completa.\n" + process.stderr[-1200:]
        )


def generate_v3_narration(
    *,
    client: ElevenLabsTTS,
    edit_store: ShortEditStore,
    project_id: int,
    segments: list[EditSegment],
    output_directory: str | Path,
    ffmpeg: str | None = None,
) -> FullNarrationResult:
    """Genera v3 en una toma larga y conserva edición/regeneración por escenas."""
    if not segments or any(not segment.narration.strip() for segment in segments):
        raise NarrationPipelineError("Todas las escenas necesitan texto antes de narrar.")
    edit = edit_store.get_edit(project_id)
    if edit.voice_model != "eleven_v3":
        raise NarrationPipelineError("Este flujo está reservado para Eleven v3.")
    if not edit.voice_id:
        raise NarrationPipelineError("Falta seleccionar el narrador.")
    ffmpeg = ffmpeg or shutil.which("ffmpeg") or ""
    if not ffmpeg:
        raise NarrationPipelineError("FFmpeg no está disponible.")

    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    full_text = " ".join(_clean(segment.narration) for segment in segments)
    generated: SpeechResult = client.generate(
        full_text,
        edit.voice_id,
        output_directory / "narracion_completa_arconte_v3.mp3",
        model_id=edit.voice_model,
        voice_settings=edit.voice_settings,
    )
    alignment = generated.alignment
    characters = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    if not characters or not (len(characters) == len(starts) == len(ends)):
        raise NarrationPipelineError("ElevenLabs no devolvió tiempos utilizables.")

    ranges = _alignment_ranges(alignment, segments)
    segment_paths: list[Path] = []
    for index, (segment, (start_index, end_index)) in enumerate(zip(segments, ranges), start=1):
        start_seconds = float(starts[start_index])
        end_seconds = float(ends[end_index - 1])
        duration_seconds = max(0.1, end_seconds - start_seconds)
        destination = output_directory / f"{index:02d}_{segment.slot_key}_arconte_v3.mp3"
        _split_audio(
            ffmpeg,
            generated.path,
            destination,
            start_seconds,
            duration_seconds,
        )
        local_alignment = _slice_alignment(alignment, start_index, end_index, start_seconds)
        edit_store.save_audio(
            project_id,
            segment.slot_id,
            segment.narration,
            str(destination.resolve()),
            local_alignment,
            int(round(duration_seconds * 1000)),
        )
        segment_paths.append(destination.resolve())

    return FullNarrationResult(
        generated.path.resolve(),
        segment_paths,
        generated.duration_ms,
    )
