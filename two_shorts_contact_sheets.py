from __future__ import annotations

from io import BytesIO
from pathlib import Path
import textwrap

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

from services.fast_search import FastPhotoSearch
from services.production_store import ProductionStore


OUT = Path("data/two_shorts_review")
OUT.mkdir(parents=True, exist_ok=True)
PROJECTS = {6: "elvis", 7: "audrey"}


def fetch(url: str) -> Image.Image:
    response = requests.get(url, timeout=20, headers={"User-Agent": "FotosDelAyer/1.0"})
    response.raise_for_status()
    return Image.open(BytesIO(response.content)).convert("RGB")


def main() -> None:
    store = ProductionStore("data/fotos_de_ayer.db")
    store.initialize()
    for project_id, slug in PROJECTS.items():
        project = store.get_project(project_id)
        slots = store.list_slots(project_id)
        rows = []
        manifest = []
        for slot in slots:
            cards = []
            photos = FastPhotoSearch.shortlist(store.slot_photos(slot.id), 3)
            for rank, photo in enumerate(photos, start=1):
                try:
                    image = fetch(photo.thumbnail_url or photo.image_url)
                    image = ImageOps.fit(image, (300, 330), method=Image.Resampling.LANCZOS)
                except Exception:
                    image = Image.new("RGB", (300, 330), "#222222")
                card = Image.new("RGB", (300, 430), "white")
                card.paste(image, (0, 0))
                draw = ImageDraw.Draw(card)
                draw.text((8, 338), f"{slot.id} · opción {rank} · {photo.source}", fill="black")
                title = "\n".join(textwrap.wrap(photo.title or "Sin título", width=40)[:4])
                draw.multiline_text((8, 358), title, fill="black", spacing=3)
                cards.append(card)
                manifest.append(
                    f"{slot.id}\t{slot.label}\t{rank}\t{photo.source}\t{photo.title}\t"
                    f"{photo.width}x{photo.height}\t{photo.traffic_light}\t{photo.original_page_url}"
                )
            while len(cards) < 3:
                cards.append(Image.new("RGB", (300, 430), "#333333"))
            row = Image.new("RGB", (900, 470), "#dddddd")
            ImageDraw.Draw(row).text((8, 5), slot.label, fill="black")
            for index, card in enumerate(cards):
                row.paste(card, (index * 300, 40))
            rows.append(row)
        sheet = Image.new("RGB", (900, len(rows) * 470), "#dddddd")
        for index, row in enumerate(rows):
            sheet.paste(row, (0, index * 470))
        sheet.save(OUT / f"{slug}_contact_sheet.jpg", quality=88)
        (OUT / f"{slug}_manifest.tsv").write_text("\n".join(manifest), encoding="utf-8")
        print(OUT / f"{slug}_contact_sheet.jpg")


if __name__ == "__main__":
    main()
