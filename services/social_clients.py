"""Envío manual mediante las APIs oficiales de Meta y TikTok.

Las claves y tokens se leen exclusivamente desde el entorno local. Esta primera
fase no programa publicaciones ni usa automatización de navegador.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


class SocialConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SocialConnection:
    network: str
    connected: bool
    message: str


def _value(name: str) -> str:
    return os.environ.get(name, "").strip()


class SocialPublisher:
    """Cliente pequeño y explícito; cada llamada publica solo al pulsar su botón."""

    graph_version = "v23.0"

    def connection(self, network: str) -> SocialConnection:
        required = {
            "facebook": ("META_PAGE_ID", "META_PAGE_ACCESS_TOKEN"),
            "instagram": ("META_IG_USER_ID", "META_PAGE_ACCESS_TOKEN"),
            "tiktok": ("TIKTOK_ACCESS_TOKEN",),
        }
        missing = [name for name in required[network] if not _value(name)]
        if missing:
            return SocialConnection(network, False, "Falta configurar: " + ", ".join(missing))
        return SocialConnection(network, True, "Cuenta lista para publicar")

    def publish(self, network: str, video_path: str, caption: str) -> dict[str, str]:
        video = Path(video_path)
        if not video.is_file():
            raise FileNotFoundError("No se encuentra el vídeo renderizado.")
        connection = self.connection(network)
        if not connection.connected:
            raise SocialConfigurationError(connection.message)
        if network == "facebook":
            return self._facebook(video, caption)
        if network == "instagram":
            return self._instagram(video, caption)
        if network == "tiktok":
            return self._tiktok(video, caption)
        raise ValueError("Red social no válida.")

    def refresh(self, network: str, remote_id: str) -> dict[str, str]:
        """Consulta el procesamiento sin volver a subir el archivo."""
        connection = self.connection(network)
        if not connection.connected:
            raise SocialConfigurationError(connection.message)
        if network == "instagram":
            token, account_id = _value("META_PAGE_ACCESS_TOKEN"), _value("META_IG_USER_ID")
            status = self._request("GET", f"https://graph.facebook.com/{self.graph_version}/{remote_id}", params={"fields": "status_code,status", "access_token": token})
            if status.get("status_code") != "FINISHED":
                return {"remote_id": remote_id, "remote_url": "", "status": "procesando"}
            published = self._request("POST", f"https://graph.facebook.com/{self.graph_version}/{account_id}/media_publish", data={"creation_id": remote_id, "access_token": token})
            media_id = str(published["id"])
            return {"remote_id": media_id, "remote_url": f"https://www.instagram.com/p/{media_id}/", "status": "publicado"}
        if network == "tiktok":
            token = _value("TIKTOK_ACCESS_TOKEN")
            result = self._request("POST", "https://open.tiktokapis.com/v2/post/publish/status/fetch/", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json={"publish_id": remote_id})
            data = result.get("data", {})
            state = data.get("status", "")
            if state == "PUBLISH_COMPLETE":
                return {"remote_id": remote_id, "remote_url": str(data.get("publicaly_available_post_id", "")), "status": "publicado"}
            if state in {"FAILED", "PUBLISH_FAILED"}:
                raise RuntimeError(str(data.get("fail_reason", "TikTok no pudo publicar el vídeo.")))
            return {"remote_id": remote_id, "remote_url": "", "status": "procesando"}
        return {"remote_id": remote_id, "remote_url": "", "status": "publicado"}

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        response = requests.request(method, url, timeout=180, **kwargs)
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}
        error = payload.get("error")
        is_api_error = bool(error) and not (isinstance(error, dict) and error.get("code") == "ok")
        if not response.ok or is_api_error:
            detail = error or payload
            raise RuntimeError(f"La plataforma rechazó la publicación: {detail}")
        return payload

    def _facebook(self, video: Path, caption: str) -> dict[str, str]:
        token, page_id = _value("META_PAGE_ACCESS_TOKEN"), _value("META_PAGE_ID")
        base = f"https://graph.facebook.com/{self.graph_version}/{page_id}/video_reels"
        started = self._request("POST", base, data={"upload_phase": "start", "access_token": token})
        video_id, upload_url = started["video_id"], started["upload_url"]
        with video.open("rb") as handle:
            uploaded = requests.post(upload_url, data=handle, headers={"Authorization": f"OAuth {token}", "Content-Type": "application/octet-stream"}, timeout=600)
        if not uploaded.ok:
            raise RuntimeError("Facebook no aceptó el archivo de vídeo.")
        finished = self._request("POST", base, data={"upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED", "description": caption, "access_token": token})
        remote_id = str(finished.get("id") or video_id)
        return {"remote_id": remote_id, "remote_url": f"https://www.facebook.com/{remote_id}"}

    def _instagram(self, video: Path, caption: str) -> dict[str, str]:
        token, account_id = _value("META_PAGE_ACCESS_TOKEN"), _value("META_IG_USER_ID")
        base = f"https://graph.facebook.com/{self.graph_version}/{account_id}"
        container = self._request("POST", f"{base}/media", data={"media_type": "REELS", "upload_type": "resumable", "caption": caption, "access_token": token})
        container_id = container["id"]
        with video.open("rb") as handle:
            uploaded = requests.post(
                f"https://rupload.facebook.com/ig-api-upload/{container_id}", data=handle,
                headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(video.stat().st_size), "Content-Type": "application/octet-stream"}, timeout=600,
            )
        if not uploaded.ok:
            raise RuntimeError("Instagram no aceptó el archivo de vídeo.")
        # La API termina de procesar de forma asíncrona; la siguiente pulsación reintenta de forma segura.
        status = self._request("GET", f"https://graph.facebook.com/{self.graph_version}/{container_id}", params={"fields": "status_code,status", "access_token": token})
        if status.get("status_code") != "FINISHED":
            return {"remote_id": container_id, "remote_url": "", "status": "procesando"}
        published = self._request("POST", f"{base}/media_publish", data={"creation_id": container_id, "access_token": token})
        remote_id = str(published["id"])
        return {"remote_id": remote_id, "remote_url": f"https://www.instagram.com/p/{remote_id}/"}

    def _tiktok(self, video: Path, caption: str) -> dict[str, str]:
        """Envía el vídeo como borrador a la bandeja de entrada de TikTok.

        El endpoint ``inbox/video/init`` usa ``video.upload`` y deja que el
        creador termine la edición y pulse Publicar dentro de TikTok.
        """
        token = _value("TIKTOK_ACCESS_TOKEN")
        size, chunk_size = video.stat().st_size, 10 * 1024 * 1024
        initialized = self._request(
            "POST", "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size, "total_chunk_count": math.ceil(size / chunk_size)}},
        )
        data = initialized.get("data", {})
        if initialized.get("error", {}).get("code") not in (None, "ok"):
            raise RuntimeError(initialized["error"].get("message", "TikTok rechazó la publicación."))
        publish_id, upload_url = data["publish_id"], data["upload_url"]
        with video.open("rb") as handle:
            start = 0
            while chunk := handle.read(chunk_size):
                end = start + len(chunk) - 1
                response = requests.put(upload_url, data=chunk, headers={"Content-Type": "video/mp4", "Content-Length": str(len(chunk)), "Content-Range": f"bytes {start}-{end}/{size}"}, timeout=600)
                if not response.ok:
                    raise RuntimeError("TikTok no aceptó una parte del vídeo.")
                start = end + 1
        return {"remote_id": publish_id, "remote_url": "", "status": "procesando"}
