from __future__ import annotations

from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from services.production_store import ProductionStore
from services.project_files import ensure_project_structure, scan_project_images
from short_editor_ui import render_short_editor


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "fotos_de_ayer.db"
DOWNLOAD_ROOT = ROOT / "proyectos_fotos"
load_dotenv(ROOT / ".env")

st.set_page_config(page_title="Fotos del Ayer · Editar Short", page_icon="🎞️", layout="wide")
st.markdown(
    """
    <style>
    .block-container {padding-top: 1.2rem; padding-bottom: 4rem;}
    div[data-testid="stImage"] img {border-radius: 8px; max-height: 320px; object-fit: contain;}
    </style>
    """,
    unsafe_allow_html=True,
)

store = ProductionStore(DB_PATH)
projects = store.list_projects()

st.title("Fotos del Ayer · Editar Short")
st.caption("Del guion al vídeo final, conservando el control creativo de cada historia.")

if not projects:
    st.info("Primero crea un proyecto y elige sus fotografías en la pantalla de búsqueda.")
    st.stop()

with st.sidebar:
    st.header("Edición")
    project_id = st.selectbox(
        "Proyecto",
        [project.id for project in projects],
        format_func=lambda value: next(project.name for project in projects if project.id == value),
    )

project = store.get_project(int(project_id))
if project is None:
    st.error("El proyecto seleccionado ya no existe.")
    st.stop()

slots = store.list_slots(project.id)
ensure_project_structure(DOWNLOAD_ROOT, project, slots)
local_images = scan_project_images(DOWNLOAD_ROOT, project, slots)
local_by_slot = {
    slot.id: [image for image in local_images if image.slot_id == slot.id]
    for slot in slots
}

st.header(project.name)
ready_slots = sum(bool(local_by_slot[slot.id]) for slot in slots)
if ready_slots < len(slots):
    missing = [slot.label for slot in slots if not local_by_slot[slot.id]]
    st.warning("Todavía faltan fotografías locales: " + ", ".join(missing))

render_short_editor(
    db_path=DB_PATH,
    download_root=DOWNLOAD_ROOT,
    project=project,
    slots=slots,
    local_by_slot=local_by_slot,
    production_store=store,
)
