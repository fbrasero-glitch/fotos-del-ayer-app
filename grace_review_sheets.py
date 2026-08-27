"""Genera hojas de contacto pequeñas para revisar candidatos sin abrirlos uno a uno."""

from __future__ import annotations

import io
import json
from pathlib import Path
from textwrap import wrap

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

from services.photo_state_store import stable_photo_key
from services.production_store import ProductionStore

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "fotos_de_ayer.db"
OUT = ROOT / "data" / "grace_review"
OUT.mkdir(parents=True, exist_ok=True)


def fetch(url: str) -> Image.Image | None:
    try:
        response = requests.get(url, timeout=20, headers={"User-Agent": "FotosDelAyer/5.0"})
        response.raise_for_status()
        return Image.open(io.BytesIO(response.content)).convert("RGB")
    except Exception:
        return None


def sheet(slot_key: str, photos: list, suffix: str) -> None:
    photos = photos[:12]
    cell_w, cell_h = 300, 390
    canvas = Image.new("RGB", (cell_w * 4, cell_h * 3), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    manifest = []
    for index, photo in enumerate(photos, start=1):
        row, col = divmod(index - 1, 4)
        x, y = col * cell_w, row * cell_h
        image = fetch(photo.thumbnail_url or photo.image_url)
        if image:
            fitted = ImageOps.contain(image, (280, 285))
            px = x + (cell_w - fitted.width) // 2
            py = y + 8 + (285 - fitted.height) // 2
            canvas.paste(fitted, (px, py))
        draw.rectangle((x, y, x + cell_w - 1, y + cell_h - 1), outline="#777777", width=2)
        label = f"{index}. {photo.source} | {photo.width}x{photo.height}"
        draw.text((x + 8, y + 300), label, fill="black", font=font)
        lines = wrap(photo.title or "Sin título", 42)[:4]
        for line_no, line in enumerate(lines):
            draw.text((x + 8, y + 320 + line_no * 14), line, fill="black", font=font)
        manifest.append(
            {
                "index": index,
                "photo_key": stable_photo_key(photo),
                "title": photo.title,
                "source": photo.source,
                "width": photo.width,
                "height": photo.height,
                "image_url": photo.image_url,
                "page_url": photo.original_page_url,
            }
        )
    canvas.save(OUT / f"{slot_key}_{suffix}.jpg", quality=88)
    (OUT / f"{slot_key}_{suffix}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    store = ProductionStore(DB_PATH)
    project = next(p for p in store.list_projects() if p.character == "Grace Kelly")
    for slot in store.list_slots(project.id):
        photos = store.slot_photos(slot.id)
        free = [p for p in photos if p.source in {"Wikimedia Commons", "Europeana"}]
        if free:
            sheet(slot.slot_key, free, "free")
            print(slot.slot_key, len(free))


if __name__ == "__main__":
    main()
