from __future__ import annotations

from dataclasses import dataclass

from .text_utils import normalize_text


@dataclass(frozen=True, slots=True)
class LicenseAssessment:
    traffic_light: str
    commercial_use: bool | None
    attribution_required: bool | None
    description: str


GREEN_PATTERNS = (
    "public domain",
    "publicdomain",
    "pdm",
    "cc0",
    "cc by ",
    "cc by-sa",
    "cc-by",
    "cc-by-sa",
    "creative commons attribution",
)
RED_PATTERNS = (
    "cc by-nc",
    "cc-by-nc",
    "creativecommons.org/licenses/by-nc",
    "noncommercial",
    "non commercial",
    "copyrighted",
    "all rights reserved",
    "editorial only",
    "in copyright",
)


def assess_license(name: str = "", url: str = "", rights: str = "") -> LicenseAssessment:
    raw = " ".join((name, url, rights))
    text = normalize_text(raw)

    # Las restricciones prevalecen sobre cualquier mención genérica a CC.
    if any(normalize_text(pattern) in text for pattern in RED_PATTERNS):
        return LicenseAssessment(
            "red",
            False,
            None,
            "No apta para uso comercial o protegida; no usar sin permiso expreso.",
        )

    green_url = any(
        marker in text
        for marker in (
            "creativecommons org licenses by",
            "creativecommons org publicdomain zero",
            "creativecommons org publicdomain mark",
        )
    )
    if green_url or any(normalize_text(pattern) in text for pattern in GREEN_PATTERNS):
        public_domain = any(key in text for key in ("public domain", "publicdomain", "pdm", "cc0"))
        return LicenseAssessment(
            "green",
            True,
            not public_domain,
            "Uso comercial razonablemente permitido; conserva la atribución y condiciones indicadas.",
        )

    if not text or any(key in text for key in ("unknown", "desconocida", "not available")):
        return LicenseAssessment(
            "red",
            None,
            None,
            "Licencia desconocida: no usar hasta verificarla en la página original.",
        )

    return LicenseAssessment(
        "yellow",
        None,
        None,
        "La declaración de derechos necesita revisión manual en la fuente original.",
    )


def traffic_light_label(value: str) -> str:
    return {
        "green": "🟢 Verde",
        "yellow": "🟡 Amarillo",
        "red": "🔴 Rojo",
    }.get(value, "🟡 Amarillo")

