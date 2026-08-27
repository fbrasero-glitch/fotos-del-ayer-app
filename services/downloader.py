from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlparse

import requests

from models.photo import Photo
from utils.text_utils import safe_filename


ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff"}
CONTENT_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/tiff": ".tif",
}


def download_photo(
    photo: Photo,
    directory: str | Path = "data/photos",
    timeout: int = 30,
    max_bytes: int = 40 * 1024 * 1024,
) -> Path:
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    response = requests.get(
        photo.image_url,
        stream=True,
        timeout=timeout,
        headers={"User-Agent": "FotosDeAyer/0.1"},
    )
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    if content_type and not content_type.startswith("image/"):
        raise ValueError(f"El recurso no es una imagen ({content_type}).")

    extension = Path(urlparse(photo.image_url).path).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        extension = CONTENT_EXTENSIONS.get(content_type, ".jpg")
    digest = hashlib.sha1(photo.image_url.encode("utf-8")).hexdigest()[:10]
    destination = target_dir / f"{safe_filename(photo.title)}-{digest}{extension}"
    temporary = destination.with_suffix(destination.suffix + ".part")

    received = 0
    try:
        with temporary.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=128 * 1024):
                if not chunk:
                    continue
                received += len(chunk)
                if received > max_bytes:
                    raise ValueError("La imagen supera el límite local de 40 MB.")
                handle.write(chunk)
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()

    photo.local_path = str(destination.resolve())
    return destination.resolve()

