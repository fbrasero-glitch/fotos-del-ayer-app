from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from models.photo import Photo
from utils.licenses import assess_license
from utils.text_utils import strip_html


HIGH_RISK_DOMAINS = (
    "gettyimages.",
    "alamy.",
    "shutterstock.",
    "apimages.",
    "reuters.",
    "imago-images.",
    "depositphotos.",
    "dreamstime.",
    "pinterest.",
    "instagram.",
    "facebook.",
)

RIGHTS_MARKERS = re.compile(
    r"creative commons|public domain|all rights reserved|editorial (?:use )?only|"
    r"rights managed|royalty[- ]free|copyright|licen[cs]e|atribuci[oó]n",
    re.IGNORECASE,
)


class RightsInspector:
    """Informe orientativo basado en metadatos y texto visible de la fuente."""

    def __init__(self, timeout: int = 12) -> None:
        self.timeout = timeout

    def _page_evidence(self, url: str) -> tuple[bool, list[str], str]:
        if not url:
            return False, [], "No hay página original enlazada."
        try:
            response = requests.get(
                url,
                timeout=self.timeout,
                headers={"User-Agent": "Mozilla/5.0 FotosDelAyer/4.0"},
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            return False, [], f"No se pudo consultar la página ({type(exc).__name__})."
        content_type = response.headers.get("Content-Type", "").lower()
        if content_type and "html" not in content_type:
            return True, [], f"La fuente respondió con {content_type.split(';', 1)[0]}."
        text = strip_html(response.text[:1_500_000])
        snippets: list[str] = []
        for match in RIGHTS_MARKERS.finditer(text):
            start = max(0, match.start() - 120)
            end = min(len(text), match.end() + 180)
            snippet = " ".join(text[start:end].split())
            if snippet and snippet not in snippets:
                snippets.append(snippet)
            if len(snippets) == 4:
                break
        return True, snippets, "Página consultada correctamente."

    def inspect(self, photo: Photo) -> dict:
        domain = urlparse(photo.original_page_url).netloc.casefold().removeprefix("www.")
        reachable, evidence, page_note = self._page_evidence(photo.original_page_url)
        combined = " ".join(
            [
                photo.license,
                photo.license_url,
                photo.license_description,
                photo.rights_status,
                *evidence,
            ]
        )
        assessment = assess_license(photo.license, photo.license_url, combined)
        stock_or_social = any(marker in domain for marker in HIGH_RISK_DOMAINS)
        discovery_only = bool(photo.metadata.get("discovery_only"))

        issues: list[str] = []
        actions: list[str] = []
        if stock_or_social:
            level = "red"
            decision = "Riesgo alto: no publicar sin adquirir o confirmar una licencia válida."
            issues.append("La fuente es una agencia comercial o una red social.")
            actions.append("Obtener una licencia expresa para YouTube y uso comercial.")
        elif assessment.traffic_light == "green" or (
            photo.traffic_light == "green" and photo.commercial_use is not False
        ):
            level = "green"
            decision = "Probablemente utilizable si cumples exactamente la licencia indicada."
            if photo.attribution_required is not False:
                actions.append("Conservar autor, fuente y texto de atribución en los créditos.")
            actions.append("Guardar una captura o copia de la licencia vigente.")
        elif assessment.traffic_light == "red":
            level = "red"
            decision = "No utilizar para publicar sin permiso o licencia adicional."
            issues.append(assessment.description)
            actions.append("Solicitar permiso o elegir otra fotografía con licencia clara.")
        else:
            level = "yellow"
            decision = "Información insuficiente: no publicar hasta verificar los derechos."
            issues.append("No aparece una licencia comercial inequívoca.")
            actions.append("Confirmar autor, titular, licencia comercial y condiciones de edición.")

        if discovery_only:
            issues.append("El buscador solo descubrió la imagen; Brave/Google no conceden derechos.")
        if not reachable:
            issues.append(page_note)
        if photo.attribution_required:
            actions.append("Incluir la atribución requerida por la fuente.")
        if photo.commercial_use is False:
            level = "red"
            decision = "La licencia indicada no permite este uso comercial."
            issues.append("La fuente marca el uso comercial como no permitido.")
        if not photo.author or photo.author == "Autor desconocido":
            issues.append("Autor o titular no identificado.")
        actions.append("Revisar también derechos de imagen, privacidad y contexto editorial.")

        return {
            "level": level,
            "decision": decision,
            "source": photo.source,
            "domain": domain or "desconocido",
            "author": photo.author,
            "license": photo.license,
            "license_url": photo.license_url,
            "commercial_use": photo.commercial_use,
            "attribution_required": photo.attribution_required,
            "page_reachable": reachable,
            "page_note": page_note,
            "evidence": evidence,
            "issues": list(dict.fromkeys(issues)),
            "actions": list(dict.fromkeys(actions)),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "disclaimer": (
                "Informe orientativo, no asesoramiento jurídico ni garantía de autorización. "
                "La licencia y el permiso del titular prevalecen."
            ),
        }
