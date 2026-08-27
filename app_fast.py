from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from models.photo import Photo
from services.entity_resolver import EntityResolutionError, EntityResolver
from services.fast_search import (
    ARCHIVE_SOURCES,
    BRAVE_SOURCE,
    GOOGLE_SOURCE,
    PEXELS_SOURCE,
    FastPhotoSearch,
    FastSearchResult,
)
from services.photo_state_store import stable_photo_key
from services.query_builder import parse_aliases


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "fotos_de_ayer.db"
load_dotenv(ROOT / ".env")

st.set_page_config(page_title="Fotos del Ayer · Rápido", page_icon="⚡", layout="wide")
st.markdown(
    """
    <style>
    .block-container {padding-top: 1.2rem; padding-bottom: 4rem;}
    .fast-box {border-left: 4px solid #ef5350; padding: .7rem .9rem; background: rgba(239,83,80,.07);}
    .identity {border-left: 4px solid #16794b; padding: .6rem .8rem; background: rgba(22,121,75,.08);}
    div[data-testid="stImage"] img {border-radius: 8px; max-height: 330px; object-fit: contain;}
    </style>
    """,
    unsafe_allow_html=True,
)


def init_state() -> None:
    defaults = {
        "fast_searches": {},
        "fast_order": [],
        "fast_active": "",
        "fast_selections": {},
        "fast_character": "",
        "fast_aliases": [],
        "fast_entity": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def default_aliases(character: str) -> str:
    try:
        mapping = json.loads((ROOT / "config" / "aliases.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    for name, aliases in mapping.items():
        if name.casefold() == character.strip().casefold():
            return "; ".join(aliases)
    return ""


def search_id(character: str, keywords: str, is_hook: bool) -> str:
    raw = f"{character.casefold().strip()}\u241f{int(is_hook)}\u241f{keywords.casefold().strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def merge_result(current: FastSearchResult | None, incoming: FastSearchResult) -> FastSearchResult:
    if current is None:
        return incoming
    merged = FastSearchResult(
        photos=[*current.photos, *incoming.photos],
        warnings=list(dict.fromkeys([*current.warnings, *incoming.warnings])),
        sources_used=list(dict.fromkeys([*current.sources_used, *incoming.sources_used])),
        api_calls=dict(current.api_calls),
        usage=incoming.usage or current.usage,
    )
    for source, calls in incoming.api_calls.items():
        merged.api_calls[source] = merged.api_calls.get(source, 0) + calls
    exact: dict[str, Photo] = {}
    for photo in merged.photos:
        key = (photo.image_url or photo.thumbnail_url).split("?", 1)[0].casefold()
        if key and key not in exact:
            exact[key] = photo
    merged.photos = list(exact.values())
    return merged


def choose_photo(sid: str, photo: Photo) -> None:
    st.session_state.fast_selections[sid] = stable_photo_key(photo)
    st.rerun()


def render_card(photo: Photo, sid: str, position: int) -> None:
    selected = st.session_state.fast_selections.get(sid) == stable_photo_key(photo)
    try:
        st.image(photo.thumbnail_url or photo.image_url, width="stretch")
    except Exception:
        st.caption("Miniatura no disponible")
    st.markdown(f"**{photo.title or 'Sin título'}**")
    st.caption(f"{photo.source} · resultado {photo.metadata.get('search_position', position + 1)}")
    if photo.width and photo.height:
        st.caption(f"{photo.width}×{photo.height}")
    st.caption(f"Derechos: **{photo.rights_status}**")
    if photo.original_page_url:
        st.link_button("Abrir fuente original", photo.original_page_url, width="stretch")
    if st.button(
        "✅ Elegida" if selected else "Elegir esta foto",
        key=f"fast_choose_{sid}_{photo.id}_{position}",
        type="primary" if selected else "secondary",
        width="stretch",
    ):
        choose_photo(sid, photo)


def render_grid(photos: list[Photo], sid: str) -> None:
    if not photos:
        st.info("Esta fuente no devolvió fotografías.")
        return
    filter_options = ["Todas", *dict.fromkeys(photo.source for photo in photos)]
    controls = st.columns([1.2, 1, 2.8])
    with controls[0]:
        source_filter = st.selectbox("Mostrar fuente", filter_options, key=f"filter_{sid}")
    visible = photos if source_filter == "Todas" else [p for p in photos if p.source == source_filter]
    with controls[1]:
        page_size = st.selectbox("Por página", [12, 24, 36], key=f"fast_size_{sid}")
    total_pages = max(1, math.ceil(len(visible) / page_size))
    with controls[2]:
        page = st.number_input("Página", 1, total_pages, 1, key=f"fast_page_{sid}_{total_pages}")
    start = (int(page) - 1) * page_size
    page_photos = visible[start : start + page_size]
    st.caption(f"{len(visible)} fotografías · mostrando {start + 1}–{start + len(page_photos)}")
    columns = st.columns(4)
    for position, photo in enumerate(page_photos, start=start):
        with columns[(position - start) % 4]:
            with st.container(border=True):
                render_card(photo, sid, position)


init_state()
searcher = FastPhotoSearch(usage_path=str(DB_PATH))
configured = searcher.configured_sources()

st.title("Fotos del Ayer · Modo rápido")
st.markdown(
    '<div class="fast-box"><strong>Una pulsación = una búsqueda.</strong> Los resultados se '
    'muestran sin puntuarlos, sin analizar todas las imágenes y sin descargar miniaturas en el servidor.</div>',
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Fuentes")
    st.caption(f"{'✅' if configured.get(BRAVE_SOURCE) else '⚪'} Brave · principal")
    st.caption(f"{'✅' if configured.get(GOOGLE_SOURCE) else '⚪'} Google · refuerzo")
    st.caption("✅ Wikimedia y Europeana · archivos")
    st.caption(f"{'✅' if configured.get(PEXELS_SOURCE) else '⚪'} Pexels · recursos genéricos")
    st.divider()
    for name, snapshot in searcher.usage_snapshot().items():
        st.metric(name, f"{snapshot.used}/{snapshot.limit}", f"quedan {snapshot.remaining}")
    result_count = st.slider("Resultados por búsqueda", 20, 40, 30, 5)
    st.caption("Los alias no generan consultas adicionales.")
    if st.button("Nueva historia", width="stretch"):
        st.session_state.fast_searches = {}
        st.session_state.fast_order = []
        st.session_state.fast_active = ""
        st.session_state.fast_selections = {}
        st.session_state.fast_entity = None
        st.rerun()

with st.form("fast_form"):
    identity_columns = st.columns(2)
    with identity_columns[0]:
        character = st.text_input(
            "Personaje", value=st.session_state.fast_character, placeholder="Ej.: Lady Di"
        )
    with identity_columns[1]:
        aliases_text = st.text_input(
            "Alias opcionales, separados por punto y coma",
            value="; ".join(st.session_state.fast_aliases),
        )
    kind = st.radio("Tipo", ["Gancho", "Escena"], horizontal=True)
    keywords = st.text_input(
        "Palabras clave en inglés",
        placeholder="Gancho: young portrait 1980s · Escena: angry inside car 1985",
    )
    buttons = st.columns(4)
    with buttons[0]:
        brave_clicked = st.form_submit_button(
            "Buscar en Brave · 1", type="primary", width="stretch"
        )
    with buttons[1]:
        google_clicked = st.form_submit_button(
            "Buscar en Google · 1", width="stretch"
        )
    with buttons[2]:
        archive_clicked = st.form_submit_button(
            "Archivos históricos", width="stretch"
        )
    with buttons[3]:
        pexels_clicked = st.form_submit_button(
            "Recurso de Pexels · 1", width="stretch"
        )

clicked = brave_clicked or google_clicked or archive_clicked or pexels_clicked
if clicked:
    is_hook = kind == "Gancho"
    if not character.strip():
        st.error("Introduce el personaje.")
    elif not keywords.strip() and not is_hook:
        st.error("Describe la escena que quieres encontrar.")
    else:
        aliases_value = aliases_text or default_aliases(character)
        aliases = parse_aliases(aliases_value.replace(";", "\n"))
        sid = search_id(character, keywords, is_hook)
        data = st.session_state.fast_searches.get(sid)
        if brave_clicked:
            requested_sources = [BRAVE_SOURCE]
        elif google_clicked:
            requested_sources = [GOOGLE_SOURCE]
        elif archive_clicked:
            requested_sources = [source for source in ARCHIVE_SOURCES if configured.get(source)]
        else:
            requested_sources = [PEXELS_SOURCE]

        already = set(data["sources_done"]) if data else set()
        pending = [source for source in requested_sources if source not in already]
        if not pending:
            st.session_state.fast_active = sid
            st.info("Esa fuente ya se consultó para esta búsqueda; no se gastó otra petición.")
        else:
            try:
                with st.spinner("Buscando y mostrando resultados sin análisis pesado…"):
                    same_character = (
                        st.session_state.fast_entity is not None
                        and st.session_state.fast_character.casefold() == character.strip().casefold()
                    )
                    entity = (
                        st.session_state.fast_entity
                        if same_character
                        else EntityResolver().resolve(character.strip(), aliases)
                    )
                    incoming = searcher.search(
                        entity=entity,
                        keywords=keywords.strip(),
                        sources=pending,
                        is_hook=is_hook,
                        count=result_count,
                    )
            except EntityResolutionError as exc:
                st.error(f"No se pudo confirmar el personaje: {exc}")
            else:
                merged = merge_result(data["result"] if data else None, incoming)
                if data is None:
                    non_hook_count = sum(
                        not st.session_state.fast_searches[item]["is_hook"]
                        for item in st.session_state.fast_order
                    )
                    data = {
                        "label": "Gancho" if is_hook else f"Escena {non_hook_count + 1}",
                        "keywords": keywords.strip() or "young portrait",
                        "is_hook": is_hook,
                        "result": merged,
                        "sources_done": set(),
                    }
                    st.session_state.fast_searches[sid] = data
                    st.session_state.fast_order.append(sid)
                else:
                    data["result"] = merged
                data["sources_done"].update(incoming.sources_used)
                st.session_state.fast_character = character.strip()
                st.session_state.fast_aliases = aliases
                st.session_state.fast_entity = entity
                st.session_state.fast_active = sid
                st.rerun()

if st.session_state.fast_entity:
    entity = st.session_state.fast_entity
    st.markdown(
        f'<div class="identity"><strong>Personaje confirmado:</strong> {entity.label} '
        f'(<a href="{entity.wikidata_url}" target="_blank">{entity.qid}</a>)</div>',
        unsafe_allow_html=True,
    )

if st.session_state.fast_order:
    labels = {
        sid: f"{st.session_state.fast_searches[sid]['label']} · "
        f"{st.session_state.fast_searches[sid]['keywords']}"
        for sid in st.session_state.fast_order
    }
    active = st.selectbox(
        "Búsqueda abierta",
        st.session_state.fast_order,
        format_func=lambda value: labels[value],
        index=(
            st.session_state.fast_order.index(st.session_state.fast_active)
            if st.session_state.fast_active in st.session_state.fast_order
            else 0
        ),
    )
    st.session_state.fast_active = active
    data = st.session_state.fast_searches[active]
    result: FastSearchResult = data["result"]
    st.subheader(labels[active])
    st.caption(
        f"Fuentes consultadas: {', '.join(result.sources_used) or 'ninguna'} · "
        f"Peticiones: {sum(result.api_calls.values())}"
    )
    for warning in result.warnings:
        st.warning(warning)
    render_grid(result.photos, active)

    st.divider()
    st.subheader("Fotos elegidas para el Short")
    summary_columns = st.columns(min(4, len(st.session_state.fast_order)))
    for index, sid in enumerate(st.session_state.fast_order):
        scene = st.session_state.fast_searches[sid]
        chosen_key = st.session_state.fast_selections.get(sid)
        chosen = next(
            (
                photo
                for photo in scene["result"].photos
                if stable_photo_key(photo) == chosen_key
            ),
            None,
        )
        with summary_columns[index % len(summary_columns)]:
            with st.container(border=True):
                st.markdown(f"**{scene['label']}**")
                st.caption(scene["keywords"])
                if chosen:
                    st.image(chosen.thumbnail_url or chosen.image_url, width="stretch")
                    st.caption(chosen.title)
                else:
                    st.info("Pendiente")
