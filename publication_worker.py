"""Base de trabajador local futuro: solo refresca trabajos ya subidos, no publica ni sube por sí solo."""
from __future__ import annotations

from pathlib import Path

from services.publication_store import PublicationStore
from services.youtube_client import YouTubeClient


def refresh_processing_jobs(db_path: str | Path) -> int:
    store = PublicationStore(db_path)
    client = YouTubeClient()
    refreshed = 0
    for job in store.list_jobs():
        if job.status not in {"procesando", "programado"} or not job.youtube_video_id:
            continue
        try:
            result = client.video_status(job.youtube_video_id)
            status = "publicado" if result["privacy_status"] == "public" else job.status
            store.update_result(job.project_id, status=status, **result, last_error="")
            refreshed += 1
        except Exception as exc:
            store.update_result(job.project_id, status="error", last_error=str(exc))
    return refreshed


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    print(f"Actualizados: {refresh_processing_jobs(root / 'data' / 'fotos_de_ayer.db')}")
