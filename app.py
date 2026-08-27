from __future__ import annotations

import json
import math
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from models.photo import Photo
from services.entity_resolver import EntityResolutionError, EntityResolver
from services.manual_query_builder import parse_manual_lines
from services.photo_aggregator import AggregatorResults, PhotoAggregator
from services.photo_state_store import PhotoResearchState, PhotoStateStore, stable_photo_key
from services.query_builder import parse_aliases
from services.search_providers import build_provider_registry


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "fotos_de_ayer.db"
load_dotenv(ROOT / ".env")

st.set_page_config(page_title="Fotos de Ayer · Investigación", page_icon="🔎", layout="wide")
st.markdown(
    """
    <style>
    .block-container {padding-top: 1.5rem; padding-bottom: 4rem;}
    .entity-box {border-left: 4px solid #16794b; padding: .7rem .9rem; background: rgba(22,121,75,.08);}
    .research-box {border-left: 4px solid #4263a8; padding: .7rem .9rem; background: rgba(66,99,168,.08);}
    div[data-testid="stImage"] img {border-radius: 8px; max-height: 330px; object-fit: contain;}
    </style>
    """,
    unsafe_allow_html=True,
)


SOURCE_OPTIONS = [
    "Bing Images",
    "Google Images",
    "Wikimedia Commons",
    "Europeana",
    "Flickr Commons",
    "Pinterest (descubrimiento)",
]
DEFAULT_SEARCHES = """foto gancho joven
foto coche enfadada
foto dentro coche
foto corriendo
foto gimnasio
foto sola mar"""


def load_default_aliases(character: str) -> str:
    try:
        mapping = json.loads((ROOT / "config" / "aliases.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    for name, aliases in mapping.items():
        if name.casefold() == character.strip().casefold():
            return "; ".join(aliases)
    return ""


def init_state() -> None:
    defaults = {
        "aggregator_results": None,
        "photo_states": {},
        "last_character": "",
        "last_aliases": [],
        "last_manual_text": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def configured_defaults() -> list[str]:
    registry, _ = build_provider_registry()
    configured = [name for name in SOURCE_OPTIONS if registry.get(name) and registry[name].configured]
    return configured or ["Wikimedia Commons"]


def load_states(photos: list[Photo]) -> dict[str, PhotoResearchState]:
    keys = [stable_photo_key(photo) for photo in photos]
    stored = PhotoStateStore(DB_PATH).get_many(keys)
    return {
        stable_photo_key(photo): stored.get(stable_photo_key(photo), PhotoResearchState())
        for photo in photos
    }


def toggle_photo_state(photo: Photo, field: str) -> None:
    states: dict[str, PhotoResearchState] = st.session_state.photo_states
    key = stable_photo_key(photo)
    state = states.setdefault(key, PhotoResearchState())
    if field == "favorite":
        state.favorite = not state.favorite
        if state.favorite:
            state.discarded = False
    elif field == "video_candidate":
        state.video_candidate = not state.video_candidate
        if state.video_candidate:
            state.discarded = False
    elif field == "discarded":
        state.discarded = not state.discarded
        if state.discarded:
            state.favorite = False
            state.video_candidate = False
    PhotoStateStore(DB_PATH).set(key, state)
    st.rerun()


def photo_card(photo: Photo, view_key: str, position: int) -> None:
    state_key = stable_photo_key(photo)
    state: PhotoResearchState = st.session_state.photo_states.setdefault(
        state_key, PhotoResearchState()
    )
    try:
        st.image(photo.thumbnail_url or photo.image_url, width="stretch")
    except Exception:
        st.caption("Miniatura no disponible")

    st.markdown(f"**{photo.title or 'Sin título'}**")
    st.caption(f"{photo.source} · {photo.author or 'Autor desconocido'}")
    st.write(f"**Relevancia {photo.search_relevance}/100**")
    st.caption(photo.relevance_reason or "Sin explicación adicional.")
    st.caption(f"Derechos: **{photo.rights_status}**")

    labels = []
    if state.favorite:
        labels.append("⭐ Favorita")
    if state.video_candidate:
        labels.append("🎬 Candidata para vídeo")
    if state.discarded:
        labels.append("🗑️ Descartada")
    if labels:
        st.caption(" · ".join(labels))

    if photo.metadata.get("duplicate_sources"):
        with st.expander(f"Duplicados agrupados: {len(photo.metadata['duplicate_sources'])}"):
            for duplicate in photo.metadata["duplicate_sources"]:
                st.caption(f"{duplicate.get('source', '')}: {duplicate.get('title', '')}")

    if photo.original_page_url:
        st.link_button("Abrir URL original", photo.original_page_url, width="stretch")

    if photo.metadata.get("discovery_only"):
        st.warning("Pinterest: solo descubrimiento. Verifica la fuente original.")
    actions = st.columns(3)
    with actions[0]:
        if st.button(
            "★" if state.favorite else "☆",
            key=f"{view_key}_fav_{photo.id}_{position}",
            help="Marcar o quitar favorita",
            width="stretch",
        ):
            toggle_photo_state(photo, "favorite")
    with actions[1]:
        if st.button(
            "🎬✓" if state.video_candidate else "🎬",
            key=f"{view_key}_video_{photo.id}_{position}",
            help="Marcar o quitar candidata para vídeo",
            width="stretch",
            disabled=bool(photo.metadata.get("discovery_only")),
        ):
            toggle_photo_state(photo, "video_candidate")
    with actions[2]:
        if st.button(
            "↩" if state.discarded else "🗑",
            key=f"{view_key}_discard_{photo.id}_{position}",
            help="Descartar o recuperar",
            width="stretch",
        ):
            toggle_photo_state(photo, "discarded")


def render_paginated(photos: list[Photo], view_key: str) -> None:
    if not photos:
        st.info("No hay fotografías en esta vista.")
        return

    controls = st.columns([1, 1, 3])
    with controls[0]:
        page_size = st.selectbox(
            "Resultados por página",
            [20, 50, 100],
            index=0,
            key=f"page_size_{view_key}",
        )
    total_pages = max(1, math.ceil(len(photos) / page_size))
    with controls[1]:
        page = st.number_input(
            "Página",
            min_value=1,
            max_value=total_pages,
            value=1,
            step=1,
            key=f"page_number_{view_key}_{total_pages}",
        )
    start = (int(page) - 1) * page_size
    end = min(len(photos), start + page_size)
    with controls[2]:
        st.caption(f"Mostrando {start + 1}–{end} de {len(photos)} fotografías")

    columns = st.columns(4)
    for position, photo in enumerate(photos[start:end], start=start):
        with columns[(position - start) % 4]:
            with st.container(border=True):
                photo_card(photo, view_key, position)


init_state()
st.title("Fotos de Ayer")
st.subheader("Agregador de búsqueda de fotografías")
st.markdown(
    '<div class="research-box"><strong>Fase 1 · Investigación:</strong> define búsquedas manuales, '
    'revisa todas las fotografías y decide tú cuáles sirven. La app no construye el vídeo final.</div>',
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Fuentes")
    sources = st.multiselect(
        "Prioridad de búsqueda",
        SOURCE_OPTIONS,
        default=configured_defaults(),
        help="Bing y Google requieren sus claves. Pinterest usa uno de ellos solo para descubrimiento.",
    )
    per_query_limit = st.slider(
        "Máximo por consulta y fuente",
        min_value=10,
        max_value=100,
        value=20,
        step=10,
    )
    st.caption("Se usan miniaturas, caché y búsquedas paralelas. La licencia no filtra resultados.")

with st.form("manual_search_form"):
    character = st.text_input("Personaje", placeholder="Ej.: Lady Di")
    aliases_text = st.text_input(
        "Alias del personaje (separados por punto y coma)",
        placeholder="Ej.: Princess Diana; Diana Spencer",
    )
    manual_text = st.text_area(
        "Búsquedas manuales · una por línea",
        value=DEFAULT_SEARCHES,
        height=230,
        help="Describe literalmente las fotos que quieres encontrar. No pegues un guion.",
    )
    submitted = st.form_submit_button("Buscar batería de fotos", type="primary", width="stretch")

if submitted:
    lines = parse_manual_lines(manual_text)
    if not character.strip():
        st.error("Introduce el personaje.")
    elif not lines:
        st.error("Introduce al menos una búsqueda manual.")
    elif not sources:
        st.error("Selecciona al menos una fuente.")
    else:
        default_aliases = aliases_text or load_default_aliases(character)
        aliases = parse_aliases(default_aliases.replace(";", "\n"))
        try:
            with st.spinner(
                "Resolviendo identidad, buscando miniaturas y agrupando imágenes parecidas…"
            ):
                entity = EntityResolver().resolve(character.strip(), aliases)
                results = PhotoAggregator(cache_path=str(DB_PATH)).search(
                    character=character.strip(),
                    aliases=aliases,
                    manual_lines=lines,
                    sources=sources,
                    per_query_limit=per_query_limit,
                    entity=entity,
                )
        except EntityResolutionError as exc:
            st.error(f"No se pudo resolver el personaje: {exc}")
        except Exception as exc:
            st.error(f"La búsqueda no pudo completarse: {type(exc).__name__}")
        else:
            st.session_state.aggregator_results = results
            st.session_state.photo_states = load_states(results.photos)
            st.session_state.last_character = character.strip()
            st.session_state.last_aliases = aliases
            st.session_state.last_manual_text = manual_text

results: AggregatorResults | None = st.session_state.aggregator_results
if results and results.entity:
    st.divider()
    entity = results.entity
    st.markdown(
        f'<div class="entity-box"><strong>Identidad bloqueada:</strong> {entity.label} '
        f'(<a href="{entity.wikidata_url}" target="_blank">{entity.qid}</a>)<br>'
        f'{entity.description}</div>',
        unsafe_allow_html=True,
    )

    states: dict[str, PhotoResearchState] = st.session_state.photo_states
    favorites = [photo for photo in results.photos if states.get(stable_photo_key(photo), PhotoResearchState()).favorite]
    video_candidates = [
        photo for photo in results.photos
        if states.get(stable_photo_key(photo), PhotoResearchState()).video_candidate
    ]
    discarded = [
        photo for photo in results.photos
        if states.get(stable_photo_key(photo), PhotoResearchState()).discarded
    ]
    selected = [
        photo for photo in results.photos
        if (
            states.get(stable_photo_key(photo), PhotoResearchState()).favorite
            or states.get(stable_photo_key(photo), PhotoResearchState()).video_candidate
        )
        and not states.get(stable_photo_key(photo), PhotoResearchState()).discarded
    ]

    metrics = st.columns(6)
    metrics[0].metric("Resultados brutos", results.total_raw)
    metrics[1].metric("Fotos visibles", len(results.photos))
    metrics[2].metric("Duplicados unidos", results.duplicates_removed)
    metrics[3].metric("Favoritas", len(favorites))
    metrics[4].metric("Para vídeo", len(video_candidates))
    metrics[5].metric("Descartadas", len(discarded))
    st.caption(
        f"Únicas antes de similitud: {results.unique_before_dedup} · "
        f"Aciertos de caché: {results.cache_hits} · "
        f"Fuentes usadas: {', '.join(results.providers_used)}"
    )

    for warning in dict.fromkeys(results.warnings):
        st.warning(warning)

    st.subheader("Consultas ejecutadas")
    st.dataframe(
        [
            {
                "Búsqueda manual": search.original,
                "Consulta reformulada": search.translated,
                "Identidad incluida": search.query_variants[0],
                "Resultados visibles": len(results.by_search.get(search.key, [])),
            }
            for search in results.searches
        ],
        hide_index=True,
        width="stretch",
    )

    st.divider()
    view = st.radio(
        "Vista",
        ["Todas", "Por búsqueda", "Seleccionadas", "Descartadas"],
        horizontal=True,
    )

    if view == "Todas":
        visible = results.photos
        view_key = "all"
    elif view == "Por búsqueda":
        labels = {
            search.original: search.key
            for search in results.searches
        }
        selected_label = st.selectbox("Búsqueda", list(labels))
        key = labels[selected_label]
        visible = results.by_search.get(key, [])
        view_key = key
    elif view == "Seleccionadas":
        selected_filter = st.radio(
            "Mostrar",
            ["Todas las seleccionadas", "Favoritas", "Candidatas para vídeo"],
            horizontal=True,
        )
        if selected_filter == "Favoritas":
            visible = favorites
        elif selected_filter == "Candidatas para vídeo":
            visible = video_candidates
        else:
            visible = selected
        view_key = "selected_" + selected_filter
    else:
        visible = discarded
        view_key = "discarded"

    st.subheader(f"{view} · {len(visible)} fotografías")
    render_paginated(visible, view_key)
