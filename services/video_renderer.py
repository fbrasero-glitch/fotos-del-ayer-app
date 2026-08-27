from __future__ import annotations

import os
import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from models.edit import EditSegment
from services.subtitle_builder import captions_for_segments, write_ass, write_srt


class RenderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RenderSettings:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    crf: int = 18
    transition_seconds: float = 0.32
    zoom_start: float = 1.0
    zoom_end: float = 1.10


@dataclass(frozen=True, slots=True)
class RenderResult:
    final_path: Path
    no_music_path: Path
    srt_path: Path
    ass_path: Path
    duration_seconds: float


class ShortVideoRenderer:
    def __init__(
        self,
        ffmpeg: str | None = None,
        ffprobe: str | None = None,
        settings: RenderSettings | None = None,
    ) -> None:
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg") or ""
        self.ffprobe = ffprobe or shutil.which("ffprobe") or ""
        self.settings = settings or RenderSettings()

    @property
    def configured(self) -> bool:
        return bool(self.ffmpeg and self.ffprobe)

    @staticmethod
    def _startupinfo() -> subprocess.STARTUPINFO | None:
        if os.name != "nt":
            return None
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return info

    def _run(self, command: list[str], cwd: Path | None = None) -> None:
        process = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=self._startupinfo(),
        )
        if process.returncode:
            detail = (process.stderr or process.stdout)[-1800:]
            raise RenderError(f"FFmpeg no pudo completar el montaje.\n{detail}")

    def probe_duration(self, path: str | Path) -> float:
        process = subprocess.run(
            [
                self.ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            startupinfo=self._startupinfo(),
        )
        if process.returncode:
            raise RenderError(f"No se pudo medir el audio {Path(path).name}.")
        try:
            return float(process.stdout.strip())
        except ValueError as exc:
            raise RenderError(f"Duración no válida para {Path(path).name}.") from exc

    def _is_portrait_image(self, path: str | Path) -> bool:
        """Return whether an image is suitable for a full-bleed vertical crop."""
        process = subprocess.run(
            [
                self.ffprobe,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0:s=x",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=self._startupinfo(),
        )
        try:
            width, height = (int(value) for value in process.stdout.strip().split("x", 1))
        except (ValueError, TypeError):
            return False
        return height >= width

    @staticmethod
    def _normalized_image_path(path: str | Path, cache_directory: Path) -> Path:
        """Create a clean RGB JPEG copy for predictable FFmpeg decoding.

        Some downloaded JPEGs contain unusual metadata/scan markers that Pillow can
        read but that FFmpeg may repeatedly reject when the image is looped. The
        source photo remains untouched; the render uses this local working copy.
        """
        source = Path(path)
        stat = source.stat()
        key = hashlib.sha1(
            f"{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
        ).hexdigest()[:16]
        cache_directory.mkdir(parents=True, exist_ok=True)
        destination = cache_directory / f"{key}.jpg"
        if not destination.is_file():
            with Image.open(source) as image:
                normalized = ImageOps.exif_transpose(image).convert("RGB")
                normalized.save(destination, format="JPEG", quality=95, optimize=True)
        return destination

    def _render_clip(
        self,
        segment: EditSegment,
        destination: Path,
        duration: float,
        *,
        fade_in: bool,
    ) -> None:
        settings = self.settings
        pause_seconds = max(0.0, segment.pause_after_ms / 1000)
        image_path = self._normalized_image_path(segment.image_path, destination.parent / "_imagenes_normalizadas")
        frame_count = max(2, round(duration * settings.fps))
        zoom_range = max(0.0, settings.zoom_end - settings.zoom_start)
        zoom_expression = (
            f"min({settings.zoom_start:.4f}+"
            f"{zoom_range:.4f}*on/{frame_count - 1},{settings.zoom_end:.4f})"
        )
        # Todas las fotos ocupan el lienzo completo. En las horizontales se recortan
        # los laterales: el canal prioriza una imagen limpia a pantalla completa y
        # no usa fondos difuminados. El zoom suave añade movimiento sin marear.
        video_filter = (
            f"[0:v]scale={settings.width}:{settings.height}:"
            "force_original_aspect_ratio=increase,"
            f"crop={settings.width}:{settings.height},"
            f"zoompan=z='{zoom_expression}':"
            "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:"
            f"s={settings.width}x{settings.height}:fps={settings.fps},"
            "setsar=1,format=yuv420p[video];"
            f"[1:a]apad=pad_dur={pause_seconds:.3f},"
            f"atrim=duration={duration:.3f},asetpts=N/SR/TB[audio]"
        )
        command = [
            self.ffmpeg, "-y",
            # Explicitly select the image2 demuxer so JPEG and JPG inputs use
            # the same looping behavior across FFmpeg builds.
            "-f", "image2", "-loop", "1", "-framerate", str(settings.fps),
            "-t", f"{duration:.3f}", "-i", str(image_path),
            "-i", segment.audio_path,
            "-filter_complex", video_filter,
            "-map", "[video]", "-map", "[audio]",
            "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", str(settings.crf),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", str(destination),
        ]
        self._run(command)

    def render(
        self,
        segments: list[EditSegment],
        output_directory: str | Path,
        *,
        music_path: str = "",
        music_volume_db: float = -22.0,
    ) -> RenderResult:
        if not self.configured:
            raise RenderError("FFmpeg y FFprobe no están disponibles.")
        if not segments:
            raise RenderError("La edición no contiene escenas.")
        for segment in segments:
            if not segment.image_path or not Path(segment.image_path).is_file():
                raise RenderError(f"Falta la fotografía de {segment.label}.")
            if not segment.audio_is_current or not Path(segment.audio_path).is_file():
                raise RenderError(f"Falta generar la narración actual de {segment.label}.")

        output_directory = Path(output_directory)
        output_directory.mkdir(parents=True, exist_ok=True)
        clip_durations: list[float] = []
        clip_names: list[str] = []
        for index, segment in enumerate(segments, start=1):
            narration_duration = self.probe_duration(segment.audio_path)
            duration = narration_duration + max(0.0, segment.pause_after_ms / 1000)
            clip_name = f"escena_{index:02d}.mp4"
            # El gancho debe ser visible desde el primer fotograma; las transiciones
            # se aplican después al unir los clips para evitar cortes bruscos.
            self._render_clip(
                segment,
                output_directory / clip_name,
                duration,
                fade_in=False,
            )
            clip_names.append(clip_name)
            clip_durations.append(duration)

        joined = output_directory / "montaje_base.mp4"
        transition = min(self.settings.transition_seconds, min(clip_durations) / 3)
        if len(clip_names) == 1:
            self._run([self.ffmpeg, "-y", "-i", clip_names[0], "-c", "copy", joined.name], cwd=output_directory)
        else:
            inputs = [item for name in clip_names for item in ("-i", name)]
            video_label = "[0:v]"
            audio_label = "[0:a]"
            video_parts: list[str] = []
            audio_parts: list[str] = []
            current_duration = clip_durations[0]
            for index in range(1, len(clip_names)):
                video_output = f"[v{index}]"
                audio_output = f"[a{index}]"
                video_offset = current_duration - transition
                video_parts.append(
                    f"{video_label}[{index}:v]xfade=transition=fade:duration={transition:.3f}:"
                    f"offset={video_offset:.3f}{video_output};"
                )
                audio_parts.append(
                    f"{audio_label}[{index}:a]acrossfade=d={transition:.3f}:"
                    f"c1=tri:c2=tri{audio_output};"
                )
                video_label = video_output
                audio_label = audio_output
                current_duration += clip_durations[index] - transition
            filter_complex = (
                "".join(video_parts)
                + f"{video_label}format=yuv420p[vout];"
                + "".join(audio_parts)
                + f"{audio_label}aformat=sample_fmts=fltp[aout]"
            )
            self._run(
                [
                    self.ffmpeg, "-y", *inputs,
                    "-filter_complex", filter_complex,
                    "-map", "[vout]", "-map", "[aout]",
                    "-c:v", "libx264", "-preset", "medium", "-crf", str(self.settings.crf),
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
                    joined.name,
                ],
                cwd=output_directory,
            )

        # Las transiciones solapan unos fotogramas; ajustamos los offsets de subtítulos
        # para que cada escena siga sincronizada con su narración.
        caption_durations = [
            duration - transition if index < len(clip_durations) - 1 else duration
            for index, duration in enumerate(clip_durations)
        ]
        captions = captions_for_segments(segments, caption_durations)
        srt_path = write_srt(captions, output_directory / "subtitulos.srt")
        ass_path = write_ass(
            captions,
            output_directory / "subtitulos.ass",
            width=self.settings.width,
            height=self.settings.height,
        )
        no_music = output_directory / "short_final_sin_musica.mp4"
        self._run(
            [
                self.ffmpeg, "-y", "-i", joined.name,
                "-vf", "ass=subtitulos.ass",
                "-af", "loudnorm=I=-15:TP=-1.5:LRA=11",
                "-c:v", "libx264", "-preset", "medium", "-crf", str(self.settings.crf),
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                no_music.name,
            ],
            cwd=output_directory,
        )

        selected_music = Path(music_path) if music_path else None
        if selected_music and selected_music.is_file():
            final_path = output_directory / "short_final_con_musica.mp4"
            audio_filter = (
                f"[1:a]volume={float(music_volume_db):.1f}dB[music];"
                "[music][0:a]sidechaincompress="
                "threshold=0.02:ratio=8:attack=20:release=500[ducked];"
                "[0:a][ducked]amix=inputs=2:duration=first:dropout_transition=2,"
                "loudnorm=I=-15:TP=-1.5:LRA=11[audio]"
            )
            self._run(
                [
                    self.ffmpeg, "-y", "-i", no_music.name,
                    "-stream_loop", "-1", "-i", str(selected_music.resolve()),
                    "-filter_complex", audio_filter,
                    "-map", "0:v", "-map", "[audio]", "-shortest",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", final_path.name,
                ],
                cwd=output_directory,
            )
        else:
            final_path = no_music

        return RenderResult(
            final_path.resolve(),
            no_music.resolve(),
            srt_path.resolve(),
            ass_path.resolve(),
            sum(clip_durations),
        )
