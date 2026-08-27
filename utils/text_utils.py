from __future__ import annotations

import html
import re
import unicodedata


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(value).split())


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.lower())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def words(value: str) -> list[str]:
    return [token for token in normalize_text(value).split() if len(token) > 2]


def first_value(value: object, default: str = "") -> str:
    if isinstance(value, list):
        return str(value[0]) if value else default
    if value is None:
        return default
    return str(value)


def safe_filename(value: str, fallback: str = "foto") -> str:
    normalized = normalize_text(value).replace(" ", "-")[:80].strip("-")
    return normalized or fallback

