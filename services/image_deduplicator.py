from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError

from models.photo import Photo


class ImageDeduplicator:
    def __init__(self, cache_path: str | Path = "data/fotos_de_ayer.db") -> None:
        self.path = Path(cache_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS image_fingerprint_cache (
                    url_key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _key(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def _cached(self, url: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT fingerprint FROM image_fingerprint_cache WHERE url_key = ?",
                (self._key(url),),
            ).fetchone()
        return str(row[0]) if row else None

    def _store(self, url: str, fingerprint: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO image_fingerprint_cache(url_key, fingerprint, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(url_key) DO UPDATE SET fingerprint=excluded.fingerprint
                """,
                (self._key(url), fingerprint, datetime.now(timezone.utc).isoformat()),
            )

    @staticmethod
    def _hash_image(content: bytes) -> str:
        with Image.open(BytesIO(content)) as image:
            image = image.convert("L")
            width, height = image.size
            dhash_image = image.resize((9, 8), Image.Resampling.LANCZOS)
            pixels = list(dhash_image.getdata())
            dhash = 0
            for row in range(8):
                offset = row * 9
                for col in range(8):
                    dhash = (dhash << 1) | int(pixels[offset + col] > pixels[offset + col + 1])

            ahash_image = image.resize((8, 8), Image.Resampling.LANCZOS)
            values = list(ahash_image.getdata())
            average = sum(values) / len(values)
            ahash = 0
            for value in values:
                ahash = (ahash << 1) | int(value >= average)

            aspect = round(width / height, 1) if height else 0
            return f"{dhash:016x}:{ahash:016x}:{aspect:.1f}"

    def fingerprint(self, photo: Photo) -> str:
        url = photo.thumbnail_url or photo.image_url
        if not url:
            return ""
        cached = self._cached(url)
        if cached is not None:
            return cached
        try:
            response = requests.get(
                url,
                headers={"User-Agent": "FotosDeAyer/2.0"},
                timeout=(2, 4),
            )
            response.raise_for_status()
            if len(response.content) > 5_000_000:
                return ""
            fingerprint = self._hash_image(response.content)
        except (requests.RequestException, OSError, UnidentifiedImageError):
            return ""
        self._store(url, fingerprint)
        return fingerprint

    @staticmethod
    def _similar(left: str, right: str) -> bool:
        if not left or not right:
            return False
        try:
            ld, la, lr = left.split(":")
            rd, ra, rr = right.split(":")
            if abs(float(lr) - float(rr)) > 0.2:
                return False
            d_distance = (int(ld, 16) ^ int(rd, 16)).bit_count()
            a_distance = (int(la, 16) ^ int(ra, 16)).bit_count()
            return (d_distance <= 7 and a_distance <= 10) or d_distance <= 3
        except (ValueError, AttributeError):
            return False

    @staticmethod
    def _merge(target: Photo, duplicate: Photo) -> None:
        target.matched_searches = list(
            dict.fromkeys([*target.matched_searches, *duplicate.matched_searches])
        )
        if duplicate.search_relevance > target.search_relevance:
            target.search_relevance = duplicate.search_relevance
            target.relevance_reason = duplicate.relevance_reason
        target.metadata.setdefault("duplicate_sources", [])
        target.metadata["duplicate_sources"].append(
            {
                "source": duplicate.source,
                "url": duplicate.original_page_url,
                "title": duplicate.title,
            }
        )

    def deduplicate(self, photos: list[Photo]) -> tuple[list[Photo], int]:
        if not photos:
            return [], 0
        with ThreadPoolExecutor(max_workers=min(24, len(photos))) as executor:
            jobs = {executor.submit(self.fingerprint, photo): photo for photo in photos}
            for future in as_completed(jobs):
                try:
                    jobs[future].perceptual_hash = future.result()
                except Exception:
                    jobs[future].perceptual_hash = ""

        kept: list[Photo] = []
        exact_urls: dict[str, Photo] = {}
        duplicates = 0
        for photo in photos:
            url_key = (photo.image_url or photo.thumbnail_url).split("?", 1)[0].casefold()
            exact = exact_urls.get(url_key)
            if exact:
                self._merge(exact, photo)
                duplicates += 1
                continue
            similar = next(
                (
                    candidate
                    for candidate in kept
                    if self._similar(candidate.perceptual_hash, photo.perceptual_hash)
                ),
                None,
            )
            if similar:
                self._merge(similar, photo)
                duplicates += 1
                continue
            kept.append(photo)
            exact_urls[url_key] = photo
        return kept, duplicates
