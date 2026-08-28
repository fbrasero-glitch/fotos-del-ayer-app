from __future__ import annotations

from io import BytesIO
from pathlib import Path

from services.facebook_batch import FACEBOOK_ORDER, find_batch_files, persist_batch_files


ITEMS = [{"key": key, "file": f"{index:02d}_{key}.mp4"} for index, key in enumerate(FACEBOOK_ORDER, start=1)]


def test_find_batch_files_prefers_the_first_available_directory(tmp_path: Path):
    saved = tmp_path / "saved"
    original = tmp_path / "original"
    filename = ITEMS[2]["file"]
    (original / filename).parent.mkdir(parents=True)
    (original / filename).write_bytes(b"original")
    (saved / filename).parent.mkdir(parents=True)
    (saved / filename).write_bytes(b"saved")

    found = find_batch_files(ITEMS, (saved, original))

    assert found[ITEMS[2]["key"]] == saved / filename


def test_persist_batch_files_writes_all_files(tmp_path: Path):
    uploads = {
        item["file"]: BytesIO(f"{item['key']}-video".encode())
        for item in ITEMS
    }

    found = persist_batch_files(uploads, ITEMS, tmp_path / "library")

    assert tuple(found) == FACEBOOK_ORDER
    assert all(path.is_file() for path in found.values())
    assert found[FACEBOOK_ORDER[-1]].read_bytes() == f"{FACEBOOK_ORDER[-1]}-video".encode()
