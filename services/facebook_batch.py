"""Utilidades para conservar y reutilizar el lote de reels de Facebook."""
from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Iterable, Mapping


# Orden experimental de publicación en Facebook. Se mantiene separado del
# orden histórico de YouTube para poder comparar la respuesta de cada red.
FACEBOOK_ORDER = ("nino", "lina", "sara", "durcal")


def find_batch_files(
    items: Iterable[Mapping[str, object]],
    directories: Iterable[Path],
) -> dict[str, Path]:
    """Find the first usable file for each expected reel.

    Directories are checked in the order supplied, so a saved Facebook copy
    can take precedence over the original YouTube upload directory.
    """
    ordered_items = list(items)
    by_key = {str(item["key"]): item for item in ordered_items}
    found: dict[str, Path] = {}
    for key in (str(item["key"]) for item in ordered_items):
        item = by_key.get(key)
        if not item:
            continue
        filename = str(item["file"])
        for directory in directories:
            candidate = Path(directory) / filename
            if candidate.is_file() and candidate.stat().st_size > 0:
                found[key] = candidate
                break
    return found


def persist_batch_files(
    uploaded_by_name: Mapping[str, BinaryIO],
    items: Iterable[Mapping[str, object]],
    target_dir: Path,
) -> dict[str, Path]:
    """Write a complete uploaded batch atomically and return its paths."""
    ordered_items = list(items)
    by_key = {str(item["key"]): item for item in ordered_items}
    order = [str(item["key"]) for item in ordered_items]
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    try:
        for key in order:
            item = by_key[key]
            filename = str(item["file"])
            uploaded = uploaded_by_name[filename]
            target = target_dir / filename
            partial = target.with_name(f".{filename}.uploading")
            if hasattr(uploaded, "seek"):
                uploaded.seek(0)
            with partial.open("wb") as destination:
                while chunk := uploaded.read(1024 * 1024):
                    destination.write(chunk)
            partial.replace(target)
            written.append(target)
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return {key: target_dir / str(by_key[key]["file"]) for key in order}
