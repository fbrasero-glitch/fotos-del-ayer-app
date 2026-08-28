from __future__ import annotations

from io import BytesIO
from pathlib import Path

from services.facebook_batch import FACEBOOK_ORDER, find_batch_files, persist_batch_files


ITEMS = [{"key": key, "file": f"{index:02d}_{key}.mp4"} for index, key in enumerate(FACEBOOK_ORDER, start=1)]


def test_find_batch_files_prefers_the_first_available_directory(tmp_path: Path):
    saved = tmp_path / "saved"
    original = tmp_path / "original"
    (original / "03_diana.mp4").parent.mkdir(parents=True)
    (original / "03_diana.mp4").write_bytes(b"original")
    (saved / "03_diana.mp4").parent.mkdir(parents=True)
    (saved / "03_diana.mp4").write_bytes(b"saved")

    found = find_batch_files(ITEMS, (saved, original))

    assert found["diana"] == saved / "03_diana.mp4"


def test_persist_batch_files_writes_all_files(tmp_path: Path):
    uploads = {
        item["file"]: BytesIO(f"{item['key']}-video".encode())
        for item in ITEMS
    }

    found = persist_batch_files(uploads, ITEMS, tmp_path / "library")

    assert tuple(found) == FACEBOOK_ORDER
    assert all(path.is_file() for path in found.values())
    assert found["marilyn"].read_bytes() == b"marilyn-video"
