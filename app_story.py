from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from models.photo import Photo
from services.entity_resolver import EntityResolutionError, EntityResolver
from services.photo_state_store import PhotoResearchState, PhotoStateStore, stable_photo_key
from services.query_builder import parse_aliases
from services.story_search import (
    BRAVE_SOURCE,
    FREE_SOURCES,
    GOOGLE_SOURCE,
    StoryPhotoSearch,
    StorySearchResult,
    build_story_registry,
)


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "fotos_de_ayer.db"
load_dotenv(ROOT / ".env")

st.set_page_config(page_title="Fotos del Ayer · Historias", page_icon="📷", layout="wide")
st.markdown(
    """
    <style>
    .block-container {padding-top: 1.3rem; padding-bottom: 4rem;}
    .identity {border-left: 4px solid #16794b; padding: .7rem .9rem; background: rgba(22,121,75,.08);}
    .scene {border-left: 4px solid #4263a8; padding: .7rem .9rem; background: rgba(66,99,168,.08);}
    div[data-testid="stImage"] img {border-radius: 8px; max-height: 330px; object-fit: contain;}
    </style>
    """,
    unsafe_allow_html=True,
)


def init_state() -> None:
    defaults = {
        "story_scenes": {},
        "story_scene_order": [],
        "active_story_scene": "",
        "story_selections": {},
        "story_photo_states": {},
        "story_character": "",
        "story_aliases": [],
        "story_entity": None,
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


def scene_key(character: str, query: str, is_hook: bool) -> str:
    raw = f"{character.casefold()}\u241f{int(is_hook)}\u241f{query.casefold().strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_photo_states(photos: list[Photo]) -> None:
    keys = [stable_photo_key(photo) for photo in photos]
    stored = PhotoStateStore(DB_PATH).get_many(keys)
    states = st.session_state.story_photo_states
    for photo in photos:
        key = stable_photo_key(photo)
        states.setdefault(key, stored.get(key, PhotoResearchState()))


def toggle_state(photo: Photo, field: str) -> None:
    key = stable_photo_key(photo)
    state = st.session_state.story_photo_states.setdefault(key, PhotoResearchState())
    if field == "favorite":
        state.favorite = not state.favorite
        if state.favorite:
            state.discarded = False
    elif field == "discarded":
        state.discarded = not state.discarded
        if state.discarded:
            state.favorite = False
            state.video_candidate = False
    PhotoStateStore(DB_PATH).set(key, state)
    st.rerun()


def choose_for_scene(scene_id: str, photo: Photo) -> None:
    st.session_state.story_selections[scene_id] = stable_photo_key(photo)
    state = st.session_state.story_photo_states.setdefault(
        stable_photo_key(photo), PhotoResearchState()
    )
    state.video_candidate = True
    state.discarded = False
    PhotoStateStore(DB_PATH).set(stable_photo_key(photo), state)
    st.rerun()


def render_photo(photo: Photo, scene_id: str, position: int) -> None:
    key = stable_photo_key(photo)
    state = st.session_state.story_photo_states.setdefault(key, PhotoResearchState())
    chosen = st.session_state.story_selections.get(scene_id) == key
    try:
        st.image(photo.thumbnail_url or photo.image_url, width="stretch")
    except Exception:
        st.caption("Miniatura no disponible")
    st.markdown(f"**{photo.title or 'Sin título'}**")
    st.caption(f"{photo.source} · {photo.author or 'Autor desconocido'}")
    st.write(f"**Coincidencia {photo.search_relevance}/100**")
    st.caption(photo.relevance_reason or "Pendiente de revisión visual.")
    if photo.ai_description:
        st.caption(f"Análisis visual: {photo.ai_description}")
    st.caption(f"Derechos: **{photo.rights_status}**")
    if photo.width and photo.height:
        crop = "bueno" if photo.height >= photo.width else "necesitará recorte"
        st.caption(f"{photo.width}×{photo.height} · Vertical: {crop}")
    if photo.metadata.get("discovery_only"):
        st.warning("Fuente de descubrimiento: verifica permiso y página original.")
    if photo.original_page_url:
        st.link_button("Abrir fuente original", photo.original_page_url, width="stretch")

    if st.button(
        "✅ Elegida para esta escena" if chosen else "Elegir para esta escena",
        key=f"choose_{scene_id}_{photo.id}_{position}",
        type="primary" if chosen else "secondary",
        width="stretch",
    ):
        choose_for_scene(scene_id, photo)
    actions = st.columns(2)
    with actions[0]:
        if st.button(
            "★ Favorita" if state.favorite else "☆ Favorita",
            key=f"fav_{scene_id}_{photo.id}_{position}",
            width="stretch",
        ):
            toggle_state(photo, "favorite")
    with actions[1]:
        if st.button(
            "↩ Recuperar" if state.discarded else "🗑 Descartar",
            key=f"discard_{scene_id}_{photo.id}_{position}",
            width="stretch",
        ):
            toggle_state(photo, "discarded")


def render_results(scene_id: str, scene_data: dict) -> None:
    result: StorySearchResult = scene_data["result"]
    visible = [
        photo
        for photo in result.photos
        if not st.session_state.story_photo_states.get(
            stable_photo_key(photo), PhotoResearchState()
        ).discarded
    ]
    if not visible:
        st.info("Esta escena todavía no tiene candidatos visibles.")
        return
    controls = st.columns([1, 1, 3])
    with controls[0]:
        page_size = st.selectbox(
            "Resultados por página", [12, 24, 48], key=f"size_{scene_id}"
        )
    total_pages = max(1, math.ceil(len(visible) / page_size))
    with controls[1]:
        page = st.number_input(
            "Página", 1, total_pages, 1, key=f"page_{scene_id}_{total_pages}"
        )
    start = (int(page) - 1) * page_size
    page_photos = visible[start : start + page_size]
    with controls[2]:
        st.caption(f"{len(visible)} candidatos · mostrando {start + 1}–{start + len(page_photos)}")
    columns = st.columns(4)
    for position, photo in enumerate(page_photos, start=start):
        with columns[(position - start) % 4]:
            with st.container(border=True):
                render_photo(photo, scene_id, position)


init_state()
searcher = StoryPhotoSearch(cache_path=str(DB_PATH))
registry = build_story_registry()
configured = {name: bool(provider.configured) for name, provider in registry.items()}

st.title("Fotos del Ayer")
st.subheader("Buscador de fotografías para historias de Shorts")
st.markdown(
    '<div class="scene"><strong>Flujo manual:</strong> busca primero la foto gancho y después '
    'una escena cada vez. Las fuentes gratuitas se consultan primero; Brave y Google solo se '
    'activan cuando tú lo decides.</div>',
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Fuentes y límites")
    for name in FREE_SOURCES:
        st.caption(f"{'✅' if configured.get(name) else '⚪'} {name}")
    st.caption(f"{'✅' if configured.get(BRAVE_SOURCE) else '⚪'} {BRAVE_SOURCE}")
    st.caption(f"{'✅' if configured.get(GOOGLE_SOURCE) else '⚪'} {GOOGLE_SOURCE}")
    st.divider()
    for name, snapshot in searcher.usage_snapshot().items():
        st.metric(name, f"{snapshot.used}/{snapshot.limit}", f"quedan {snapshot.remaining}")
    st.caption("Los contadores son límites locales conservadores. Comprueba también cada panel oficial.")
    result_limit = st.slider("Resultados solicitados por fuente", 10, 80, 40, 10)
    if st.button("Nueva historia", width="stretch"):
        for key in (
            "story_scenes",
            "story_scene_order",
            "story_selections",
            "story_photo_states",
        ):
            st.session_state[key] = {} if key != "story_scene_order" else []
        st.session_state.active_story_scene = ""
        st.session_state.story_entity = None
        st.rerun()

with st.form("scene_search"):
    identity_columns = st.columns(2)
    with identity_columns[0]:
        character = st.text_input(
            "Personaje",
            value=st.session_state.story_character,
            placeholder="Ej.: Lady Di",
        )
    with identity_columns[1]:
        aliases_text = st.text_input(
            "Alias, separados por punto y coma",
            value="; ".join(st.session_state.story_aliases),
            placeholder="Princess Diana; Diana Spencer",
        )
    kind = st.radio("Tipo de fotografía", ["Gancho", "Escena de la historia"], horizontal=True)
    query = st.text_input(
        "Palabras clave de la fotografía",
        placeholder="Ej.: enfadada dentro de un coche, Londres, 1985",
        help="Introduce una sola escena. La identidad del personaje se añadirá automáticamente.",
    )
    submitted = st.form_submit_button(
        "Buscar primero en fuentes gratuitas", type="primary", width="stretch"
    )

if submitted:
    if not character.strip():
        st.error("Introduce el personaje.")
    elif not query.strip():
        st.error("Describe la fotografía que quieres encontrar.")
    else:
        is_hook = kind == "Gancho"
        aliases_value = aliases_text or default_aliases(character)
        aliases = parse_aliases(aliases_value.replace(";", "\n"))
        sid = scene_key(character, query, is_hook)
        if sid in st.session_state.story_scenes:
            st.session_state.active_story_scene = sid
            st.info("Esta búsqueda ya estaba hecha; se muestran los resultados guardados en la sesión.")
        else:
            try:
                with st.spinner("Bloqueando identidad y buscando una vez en cada fuente gratuita…"):
                    entity = EntityResolver().resolve(character.strip(), aliases)
                    free_enabled = [name for name in FREE_SOURCES if configured.get(name)]
                    result = searcher.search_scene(
                        character=character.strip(),
                        scene_text=query.strip(),
                        sources=free_enabled,
                        aliases=aliases,
                        entity=entity,
                        is_hook=is_hook,
                        limit=result_limit,
                    )
            except EntityResolutionError as exc:
                st.error(f"No se pudo confirmar la identidad: {exc}")
            else:
                st.session_state.story_character = character.strip()
                st.session_state.story_aliases = aliases
                st.session_state.story_entity = entity
                st.session_state.story_scenes[sid] = {
                    "label": "Gancho" if is_hook else f"Escena {len(st.session_state.story_scene_order)}",
                    "query": query.strip(),
                    "is_hook": is_hook,
                    "result": result,
                    "sources_searched": set(result.sources_used),
                }
                st.session_state.story_scene_order.append(sid)
                st.session_state.active_story_scene = sid
                load_photo_states(result.photos)
                st.rerun()

if st.session_state.story_entity:
    entity = st.session_state.story_entity
    st.markdown(
        f'<div class="identity"><strong>Identidad bloqueada:</strong> {entity.label} '
        f'(<a href="{entity.wikidata_url}" target="_blank">{entity.qid}</a>)</div>',
        unsafe_allow_html=True,
    )

if st.session_state.story_scene_order:
    scene_labels = {
        sid: f"{st.session_state.story_scenes[sid]['label']} · {st.session_state.story_scenes[sid]['query']}"
        for sid in st.session_state.story_scene_order
    }
    active = st.selectbox(
        "Escena que estás revisando",
        st.session_state.story_scene_order,
        format_func=lambda value: scene_labels[value],
        index=max(
            0,
            st.session_state.story_scene_order.index(st.session_state.active_story_scene)
            if st.session_state.active_story_scene in st.session_state.story_scene_order
            else 0,
        ),
    )
    st.session_state.active_story_scene = active
    data = st.session_state.story_scenes[active]
    result: StorySearchResult = data["result"]
    st.subheader(scene_labels[active])
    st.caption(
        f"Fuentes consultadas: {', '.join(result.sources_used) or 'ninguna'} · "
        f"Caché: {result.cache_hits} · Análisis visual: {result.visual_analyzed}"
    )
    for warning in dict.fromkeys(result.warnings):
        st.warning(warning)

    actions = st.columns(3)
    with actions[0]:
        brave_done = BRAVE_SOURCE in data["sources_searched"]
        if st.button(
            "Brave ya consultado" if brave_done else "Añadir Brave · 1 consulta",
            disabled=brave_done or not configured.get(BRAVE_SOURCE),
            width="stretch",
        ):
            with st.spinner("Consultando Brave una sola vez…"):
                extra = searcher.search_scene(
                    st.session_state.story_character,
                    data["query"],
                    [BRAVE_SOURCE],
                    st.session_state.story_aliases,
                    result.entity,
                    data["is_hook"],
                    result_limit,
                )
                data["result"] = searcher.merge(result, extra)
                data["sources_searched"].add(BRAVE_SOURCE)
                load_photo_states(data["result"].photos)
            st.rerun()
    with actions[1]:
        google_done = GOOGLE_SOURCE in data["sources_searched"]
        if st.button(
            "Google ya consultado" if google_done else "Mejorar con Google · 1 SerpAPI",
            disabled=google_done or not configured.get(GOOGLE_SOURCE),
            width="stretch",
        ):
            with st.spinner("Consultando Google Images mediante SerpAPI una sola vez…"):
                extra = searcher.search_scene(
                    st.session_state.story_character,
                    data["query"],
                    [GOOGLE_SOURCE],
                    st.session_state.story_aliases,
                    result.entity,
                    data["is_hook"],
                    result_limit,
                )
                data["result"] = searcher.merge(result, extra)
                data["sources_searched"].add(GOOGLE_SOURCE)
                load_photo_states(data["result"].photos)
            st.rerun()
    with actions[2]:
        vision_label = searcher.vision_label
        if st.button(
            f"Cribar 8 mejores · {vision_label}",
            disabled=result.visual_analyzed > 0 or not searcher.vision_configured,
            width="stretch",
            help=(
                "El modelo local revisa identidad, escena e impacto únicamente en ocho "
                "miniaturas. Puedes seleccionar Gemini con VISION_BACKEND=gemini."
            ),
        ):
            with st.spinner("Analizando únicamente los mejores candidatos…"):
                data["result"] = searcher.analyze_visuals(
                    result, data["query"], data["is_hook"], maximum=8
                )
            st.rerun()

    result = data["result"]
    chosen_key = st.session_state.story_selections.get(active)
    if chosen_key:
        chosen = next(
            (photo for photo in result.photos if stable_photo_key(photo) == chosen_key), None
        )
        if chosen:
            st.success(f"Foto elegida para esta escena: {chosen.title}")
    render_results(active, data)

    st.divider()
    st.subheader("Resumen del Short")
    summary_columns = st.columns(min(4, max(1, len(st.session_state.story_scene_order))))
    for index, sid in enumerate(st.session_state.story_scene_order):
        scene_data = st.session_state.story_scenes[sid]
        selected_key = st.session_state.story_selections.get(sid)
        selected = next(
            (
                photo
                for photo in scene_data["result"].photos
                if stable_photo_key(photo) == selected_key
            ),
            None,
        )
        with summary_columns[index % len(summary_columns)]:
            with st.container(border=True):
                st.markdown(f"**{scene_data['label']}**")
                st.caption(scene_data["query"])
                if selected:
                    st.image(selected.thumbnail_url or selected.image_url, width="stretch")
                    st.caption(f"✅ {selected.title}")
                else:
                    st.info("Pendiente de elegir")
