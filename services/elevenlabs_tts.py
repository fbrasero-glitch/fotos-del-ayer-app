from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

import requests


class ElevenLabsError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SpeechResult:
    path: Path
    alignment: dict
    duration_ms: int


class ElevenLabsTTS:
    base_url = "https://api.elevenlabs.io"

    def __init__(self, api_key: str | None = None, timeout: int = 90) -> None:
        self.api_key = (api_key or os.getenv("ELEVENLABS_API_KEY", "")).strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def generate(
        self,
        text: str,
        voice_id: str,
        destination: str | Path,
        *,
        model_id: str = "eleven_multilingual_v2",
        voice_settings: dict | None = None,
        previous_text: str = "",
        next_text: str = "",
    ) -> SpeechResult:
        if not self.configured:
            raise ElevenLabsError("Falta ELEVENLABS_API_KEY en el archivo .env.")
        if not voice_id.strip():
            raise ElevenLabsError("Selecciona un narrador antes de generar la voz.")
        clean_text = " ".join(text.split())
        if not clean_text:
            raise ElevenLabsError("La escena no tiene texto para narrar.")

        payload: dict = {
            "text": clean_text,
            "model_id": model_id,
            "voice_settings": voice_settings or {},
        }
        if previous_text.strip():
            payload["previous_text"] = " ".join(previous_text.split())[-1000:]
        if next_text.strip():
            payload["next_text"] = " ".join(next_text.split())[:1000]

        try:
            response = requests.post(
                f"{self.base_url}/v1/text-to-speech/{voice_id.strip()}/with-timestamps",
                params={"output_format": "mp3_44100_128"},
                headers={
                    "xi-api-key": self.api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
                timeout=(10, self.timeout),
            )
        except requests.RequestException as exc:
            raise ElevenLabsError("No se pudo conectar con ElevenLabs.") from exc
        if response.status_code >= 400:
            detail = ""
            try:
                body = response.json()
                detail = str(body.get("detail", ""))[:300]
            except ValueError:
                detail = response.text[:300]
            raise ElevenLabsError(
                f"ElevenLabs devolvió HTTP {response.status_code}"
                + (f": {detail}" if detail else ".")
            )
        try:
            data = response.json()
            audio = base64.b64decode(data["audio_base64"])
            alignment = data.get("normalized_alignment") or data.get("alignment") or {}
        except (ValueError, KeyError, TypeError) as exc:
            raise ElevenLabsError("ElevenLabs devolvió una respuesta de audio no válida.") from exc

        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(audio)
        end_times = alignment.get("character_end_times_seconds") or []
        duration_ms = int(round(float(end_times[-1]) * 1000)) if end_times else 0
        return SpeechResult(destination.resolve(), alignment, duration_ms)
