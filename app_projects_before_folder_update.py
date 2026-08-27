from __future__ import annotations

import json
import math
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from models.photo import Photo
from services.downloader import download_photo
from services.entity_resolver import EntityResolutionError, EntityResolver
from services.fast_search import (
    ARCHIVE_SOURCES,
    BRAVE_SOURCE,
    GOOGLE_SOURCE,
    PEXELS_SOURCE,
    FastPhotoSearch,
)
from services.photo_state_store import stable_photo_key
from services.production_store import ProductionProject, ProductionSlot, ProductionStore
from services.query_builder import parse_aliases
from services.rights_inspector import RightsInspector
from utils.text_utils import safe_filename


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "fotos_de_ayer.db"
DOWNLOAD_ROOT = ROOT / "proyectos_fotos"
load_dotenv(ROOT / ".env")

st.set_page_config(page_title="Fotos del Ayer · Proyectos", page_icon="🎬", layout="wide")
st.markdown(
    """
    <style>
    .block-container {padding-top: 1.2rem; padding-bottom: 4rem;}
    .project-box {border-left: 4px solid #ef5350; padding: .7rem .9rem; background: rgba(239,83,80,.07);}
    .saved-box {border-left: 4px solid #16794b; padding: .6rem .8rem; background: rgba(22,121,75,.08);}
    div[data-testid="stImage"] img {border-radius: 8px; max-height: 320px; object-fit: contain;}
    </style>
    """,
    unsafe_allow_html=True,
)


def default_aliases(character: str) -> str:
    try:
        mapping = json.loads((ROOT / "config" / "aliases.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    for name, aliases in mapping.items():
        if name.casefold() == character.strip().casefold():
            return "; ".join(aliases)
    return ""


def slot_directory(project: ProductionProject, slot: ProductionSlot) -> Path:
    project_dir = DOWNLOAD_ROOT / safe_filename(project.name, f"proyecto-{project.id}")
    if slot.kind == "hook":
        folder = "01-gancho"
    elif slot.kind == "final":
        folder = "99-foto-final"
    else:
        folder = f"{slot.position + 1:02d}-escena-{slot.position}-{safe_filename(slot.label)}"
    return project_dir / folder


def report_badge(report: dict | None) -> str:
    if not report:
        return "⚪ Sin comprobar"
    return {
        "green": "🟢 Probablemente utilizable",
        "yellow": "🟡 Falta información",
        "red": "🔴 Riesgo alto",
    }.get(report.get("level"), "🟡 Revisar")


def render_report(report: dict) -> None:
    level = report.get("level", "yellow")
    if level == "green":
        st.success(report["decision"])
    elif level == "red":
        st.error(report["decision"])
    else:
        st.warning(report["decision"])
    st.caption(
        f"Fuente: {report.get('domain')} · Autor: {report.get('author')} · "
        f"Licencia: {report.get('license')}"
    )
    for issue in report.get("issues", []):
        st.markdown(f"- Riesgo: {issue}")
    for action in report.get("actions", []):
        st.markdown(f"- Acción: {action}")
    if report.get("evidence"):
        with st.expander("Texto sobre derechos encontrado en la fuente"):
            for evidence in report["evidence"]:
                st.caption(evidence)
    st.caption(report.get("disclaimer", ""))


store = ProductionStore(DB_PATH)
searcher = FastPhotoSearch(usage_path=str(DB_PATH))
inspector = RightsInspector()
configured = searcher.configured_sources()

for key, value in {
    "production_project_id": 0,
    "production_slot_id": 0,
    "production_notice": "",
}.items():
    if key not in st.session_state:
        st.session_state[key] = value

st.title("Fotos del Ayer · Proyectos")
st.markdown(
    '<div class="project-box"><strong>Buscador rápido con memoria.</strong> Cada consulta se '
    'guarda. Si vuelves a pedirla, se recupera sin gastar otra petición.</div>',
    unsafe_allow_html=True,
)

projects = store.list_projects()
with st.sidebar:
    st.header("Proyecto")
    if projects:
        project_ids = [project.id for project in projects]
        current_id = st.session_state.production_project_id
        selected_project_id = st.selectbox(
            "Proyecto activo",
            project_ids,
            index=project_ids.index(current_id) if current_id in project_ids else 0,
            format_func=lambda value: next(p.name for p in projects if p.id == value),
        )
        st.session_state.production_project_id = selected_project_id
    else:
        selected_project_id = 0
        st.info("Crea tu primer proyecto.")

    with st.expander("➕ Crear un proyecto", expanded=not projects):
        with st.form("create_project_form"):
            new_name = st.text_input("Nombre", placeholder="Lady Di, la mujer más fotografiada")
            new_character = st.text_input("Personaje", placeholder="Lady Di")
            new_aliases_text = st.text_input("Alias opcionales", placeholder="Princess Diana; Diana Spencer")
            create_clicked = st.form_submit_button("Crear proyecto", type="primary", width="stretch")
        if create_clicked:
            if not new_name.strip() or not new_character.strip():
                st.error("Escribe el nombre del proyecto y el personaje.")
            else:
                aliases_text = new_aliases_text or default_aliases(new_character)
                aliases = parse_aliases(aliases_text.replace(";", "\n"))
                try:
                    with st.spinner("Confirmando el personaje una sola vez…"):
                        entity = EntityResolver().resolve(new_character.strip(), aliases)
                except EntityResolutionError as exc:
                    st.error(f"No se pudo confirmar el personaje: {exc}")
                else:
                    project_id = store.create_project(new_name, new_character, aliases, entity)
                    st.session_state.production_project_id = project_id
                    st.session_state.production_slot_id = 0
                    st.session_state.production_notice = "Proyecto creado y guardado."
                    st.rerun()

if not selected_project_id:
    st.info("Crea un proyecto desde la barra lateral para comenzar.")
    st.stop()

project = store.get_project(int(selected_project_id))
if project is None:
    st.error("El proyecto seleccionado ya no existe.")
    st.stop()

slots = store.list_slots(project.id)
with st.sidebar:
    st.divider()
    st.caption(f"Personaje confirmado: {project.entity.label} ({project.entity.qid})")
    with st.form("add_scene_form"):
        scene_label = st.text_input("Nueva escena", placeholder="Sola en el trampolín del yate")
        add_scene_clicked = st.form_submit_button("Añadir escena", width="stretch")
    if add_scene_clicked:
        scene_id = store.add_scene(project.id, scene_label)
        st.session_state.production_slot_id = scene_id
        st.session_state.production_notice = "Escena añadida al proyecto."
        st.rerun()

    st.divider()
    st.subheader("Límites locales")
    for name, snapshot in searcher.usage_snapshot().items():
        st.metric(name, f"{snapshot.used}/{snapshot.limit}", f"quedan {snapshot.remaining}")
    result_count = st.slider("Resultados nuevos", 20, 40, 30, 5)
    st.caption("Abrir resultados guardados consume 0 créditos.")

if st.session_state.production_notice:
    st.success(st.session_state.production_notice)
    st.session_state.production_notice = ""

st.header(project.name)
slot_ids = [slot.id for slot in slots]
current_slot_id = st.session_state.production_slot_id
active_slot_id = st.selectbox(
    "Parte del Short",
    slot_ids,
    index=slot_ids.index(current_slot_id) if current_slot_id in slot_ids else 0,
    format_func=lambda value: next(slot.label for slot in slots if slot.id == value),
)
st.session_state.production_slot_id = active_slot_id
slot = next(item for item in slots if item.id == active_slot_id)
final_slot = next(item for item in slots if item.kind == "final")
is_hook = slot.kind == "hook"

st.subheader(slot.label)
with st.form(f"project_search_{slot.id}"):
    keywords = st.text_input(
        "Palabras clave en inglés",
        placeholder=(
            "young portrait 1980s"
            if is_hook
            else "sitting alone yacht diving board 1997"
        ),
    )
    buttons = st.columns(4)
    with buttons[0]:
        brave_clicked = st.form_submit_button("Brave · 1", type="primary", width="stretch")
    with buttons[1]:
        google_clicked = st.form_submit_button("Google · 1", width="stretch")
    with buttons[2]:
        archive_clicked = st.form_submit_button("Archivos históricos", width="stretch")
    with buttons[3]:
        pexels_clicked = st.form_submit_button("Pexels genérico · 1", width="stretch")

clicked = brave_clicked or google_clicked or archive_clicked or pexels_clicked
if clicked:
    if not keywords.strip() and not is_hook:
        st.error("Describe la escena que quieres encontrar.")
    else:
        if brave_clicked:
            requested_sources = [BRAVE_SOURCE]
        elif google_clicked:
            requested_sources = [GOOGLE_SOURCE]
        elif archive_clicked:
            requested_sources = [source for source in ARCHIVE_SOURCES if configured.get(source)]
        else:
            requested_sources = [PEXELS_SOURCE]

        reused = 0
        new_calls = 0
        messages: list[str] = []
        for source in requested_sources:
            query = searcher.query_for(source, project.entity, keywords.strip(), is_hook)
            fingerprint = store.fingerprint(project.entity.qid, source, query)
            saved = store.get_search(fingerprint)
            if saved:
                store.attach_search(slot.id, saved.id)
                reused += 1
                continue
            with st.spinner(f"Buscando una vez en {source}…"):
                result = searcher.search(
                    entity=project.entity,
                    keywords=keywords.strip(),
                    sources=[source],
                    is_hook=is_hook,
                    count=result_count,
                )
            messages.extend(result.warnings)
            if source in result.sources_used:
                saved = store.save_search(
                    fingerprint=fingerprint,
                    entity_qid=project.entity.qid,
                    source=source,
                    query=query,
                    keywords=keywords.strip() or "young portrait",
                    is_hook=is_hook,
                    requested_count=result_count,
                    photos=result.photos,
                )
                store.attach_search(slot.id, saved.id)
                new_calls += 1
        if messages:
            st.session_state.production_notice = " · ".join(messages)
        elif reused and not new_calls:
            st.session_state.production_notice = f"Búsqueda recuperada del historial: {reused} fuente(s), 0 créditos."
        else:
            st.session_state.production_notice = (
                f"Guardada: {new_calls} petición(es) nueva(s)"
                + (f" y {reused} recuperada(s)." if reused else ".")
            )
        st.rerun()

searches = store.slot_searches(slot.id)
photos = store.slot_photos(slot.id)
candidates = store.list_candidates(slot.id)
candidate_keys = {stable_photo_key(photo) for photo, _ in candidates}

if searches:
    with st.expander(f"Historial guardado de {slot.label} · {len(searches)} búsqueda(s)"):
        for saved in searches:
            st.markdown(f"**{saved.source}** · `{saved.query}`")
            st.caption(f"{len(saved.photos)} resultados · guardada {saved.created_at[:16].replace('T', ' ')} UTC")
else:
    st.info("Esta parte del Short todavía no tiene búsquedas guardadas.")

if candidates:
    st.subheader(f"Candidatas · {len(candidates)}")
    candidate_columns = st.columns(min(4, len(candidates)))
    for index, (candidate, chosen) in enumerate(candidates):
        key = stable_photo_key(candidate)
        with candidate_columns[index % len(candidate_columns)]:
            with st.container(border=True):
                st.image(candidate.thumbnail_url or candidate.image_url, width="stretch")
                st.markdown(f"**{'✅ Elegida' if chosen else 'Candidata'}**")
                st.caption(candidate.title)
                if st.button("Elegir para el vídeo", key=f"choose_{slot.id}_{key}", width="stretch"):
                    store.choose_candidate(slot.id, candidate)
                    st.rerun()
                if slot.kind != "final" and st.button(
                    "Usar también como final", key=f"also_final_{slot.id}_{key}", width="stretch"
                ):
                    store.choose_candidate(final_slot.id, candidate)
                    st.session_state.production_notice = "La fotografía también quedó elegida como foto final."
                    st.rerun()

if photos:
    st.subheader(f"Resultados guardados · {len(photos)}")
    controls = st.columns([1.2, 1, 2.8])
    source_options = ["Todas", *dict.fromkeys(photo.source for photo in photos)]
    with controls[0]:
        source_filter = st.selectbox("Mostrar fuente", source_options, key=f"source_{slot.id}")
    visible = photos if source_filter == "Todas" else [p for p in photos if p.source == source_filter]
    with controls[1]:
        page_size = st.selectbox("Por página", [12, 24, 36], key=f"size_{slot.id}")
    total_pages = max(1, math.ceil(len(visible) / page_size))
    with controls[2]:
        page = st.number_input("Página", 1, total_pages, 1, key=f"page_{slot.id}_{total_pages}")
    start = (int(page) - 1) * page_size
    page_photos = visible[start : start + page_size]
    columns = st.columns(4)
    for position, photo in enumerate(page_photos, start=start):
        photo_key = stable_photo_key(photo)
        report = store.get_rights_report(photo)
        downloaded = store.get_download(slot.id, photo)
        with columns[(position - start) % 4]:
            with st.container(border=True):
                st.image(photo.thumbnail_url or photo.image_url, width="stretch")
                st.markdown(f"**{photo.title or 'Sin título'}**")
                st.caption(f"{photo.source} · resultado {position + 1}")
                st.caption(report_badge(report))
                if photo.original_page_url:
                    st.link_button("Abrir fuente", photo.original_page_url, width="stretch")
                if photo_key in candidate_keys:
                    if st.button("Quitar candidata", key=f"remove_{slot.id}_{photo_key}", width="stretch"):
                        store.remove_candidate(slot.id, photo_key)
                        st.rerun()
                elif st.button("Añadir a candidatas", key=f"add_{slot.id}_{photo_key}", width="stretch"):
                    store.add_candidate(slot.id, photo)
                    st.rerun()
                if st.button("Comprobar uso y riesgos", key=f"rights_{slot.id}_{photo_key}", width="stretch"):
                    with st.spinner("Consultando la fuente y preparando el informe…"):
                        report = inspector.inspect(photo)
                        store.save_rights_report(photo, report)
                    st.rerun()
                if report:
                    with st.expander("Ver informe"):
                        render_report(report)
                if downloaded:
                    st.success(f"Descargada: {Path(downloaded).name}")
                elif st.button("Descargar en esta parte", key=f"download_{slot.id}_{photo_key}", width="stretch"):
                    try:
                        with st.spinner("Descargando la imagen original…"):
                            path = download_photo(photo, slot_directory(project, slot))
                            store.save_download(slot.id, photo, str(path))
                    except Exception as exc:
                        st.error(f"No se pudo descargar ({type(exc).__name__}). Abre la fuente original.")
                    else:
                        st.session_state.production_notice = f"Descargada en {path.parent.name}."
                        st.rerun()

st.divider()
st.subheader("Montaje del Short")
summary_columns = st.columns(min(4, len(slots)))
for index, summary_slot in enumerate(slots):
    chosen = next(
        (photo for photo, is_chosen in store.list_candidates(summary_slot.id) if is_chosen),
        None,
    )
    with summary_columns[index % len(summary_columns)]:
        with st.container(border=True):
            st.markdown(f"**{summary_slot.label}**")
            if chosen:
                st.image(chosen.thumbnail_url or chosen.image_url, width="stretch")
                local_path = store.get_download(summary_slot.id, chosen)
                st.caption("Descargada" if local_path else "Elegida · pendiente de descarga")
            else:
                st.info("Pendiente")

st.caption(f"Carpeta del proyecto: {DOWNLOAD_ROOT / safe_filename(project.name)}")
