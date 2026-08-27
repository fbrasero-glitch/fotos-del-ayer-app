"""Validación local reproducible de un Short antes de ponerlo en cola."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageStat


@dataclass(frozen=True, slots=True)
class ShortValidation:
    ok: bool
    video_exists: bool
    vertical_9_16: bool
    duration_ok: bool
    audio_present: bool
    subtitles_present: bool
    first_frame_visible: bool
    width: int = 0
    height: int = 0
    duration_seconds: float = 0.0
    first_frame_path: str = ""
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)


def validate_short(video_path: str | Path, subtitles_path: str | Path = "", preview_dir: str | Path = "") -> ShortValidation:
    video = Path(video_path)
    errors: list[str] = []
    if not video.is_file():
        return ShortValidation(False, False, False, False, False, False, False, errors=("No existe el vídeo renderizado.",))
    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if not ffprobe or not ffmpeg:
        return ShortValidation(False, True, False, False, False, Path(subtitles_path).is_file(), False, errors=("Faltan FFmpeg o FFprobe para validar el render.",))
    probe = _run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)])
    if probe.returncode:
        return ShortValidation(False, True, False, False, False, Path(subtitles_path).is_file(), False, errors=("No se pudieron leer los metadatos del vídeo.",))
    data = json.loads(probe.stdout)
    streams = data.get("streams", [])
    visual = next((item for item in streams if item.get("codec_type") == "video"), {})
    width, height = int(visual.get("width", 0)), int(visual.get("height", 0))
    duration = float(data.get("format", {}).get("duration") or 0)
    vertical = bool(width and height and abs((width / height) - (9 / 16)) < 0.015)
    duration_ok = 0 < duration <= 180
    audio = any(item.get("codec_type") == "audio" for item in streams)
    subtitles = Path(subtitles_path).is_file()
    folder = Path(preview_dir) if preview_dir else video.parent / "publicacion"
    folder.mkdir(parents=True, exist_ok=True)
    first = folder / "primer_fotograma_publicacion.jpg"
    extracted = _run([ffmpeg, "-y", "-v", "error", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(first)]).returncode == 0 and first.is_file()
    visible = False
    if extracted:
        with Image.open(first) as image:
            visible = max(ImageStat.Stat(image.convert("L")).var) > 2 and ImageStat.Stat(image.convert("L")).mean[0] > 3
    checks = ((vertical, "El render debe tener relación 9:16."), (duration_ok, "La duración debe ser mayor de 0 y no superar 180 segundos."), (audio, "El vídeo no contiene una pista de audio."), (subtitles, "No se encontró el archivo SRT de subtítulos."), (visible, "El primer fotograma no pudo comprobarse como visible."))
    errors.extend(message for passed, message in checks if not passed)
    return ShortValidation(not errors, True, vertical, duration_ok, audio, subtitles, visible, width, height, duration, str(first) if extracted else "", tuple(errors))
