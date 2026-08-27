from __future__ import annotations

from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from services.gemini_publication import NETWORKS, TONES, PublicationMetadataStore, final_script, generate
from services.gemini_service import GeminiService
from services.production_store import ProductionStore
from services.project_files import project_directory
from services.publication_store import PublicationStore

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "fotos_de_ayer.db"
PROJECTS_ROOT = ROOT / "proyectos_fotos"
load_dotenv(ROOT / ".env")

st.set_page_config(page_title="Fotos del Ayer · Gemini", page_icon=":material/auto_awesome:", layout="wide")
projects_store = ProductionStore(DB_PATH)
queue = PublicationStore(DB_PATH)
versions_store = PublicationMetadataStore(DB_PATH)
service = GeminiService()
projects = projects_store.list_projects()

st.title("Fotos del Ayer · Asistente Gemini")
st.caption("Genera y revisa metadatos; esta pantalla nunca publica en Instagram, Facebook ni TikTok.")
if not projects:
    st.info("Primero crea y edita un proyecto.")
    st.stop()

with st.sidebar:
    project_id = st.selectbox("Proyecto", [project.id for project in projects], format_func=lambda value: next(project.name for project in projects if project.id == value))
    if service.configured:
        st.success("Gemini disponible")
        st.caption("La clave se lee desde .env y nunca se muestra.")
    else:
        st.info("Gemini no configurado")
        st.caption("Añade GEMINI_API_KEY=… al archivo .env local y reinicia la app.")
    tone = st.segmented_control("Tono", list(TONES), default="nostálgico", key=f"gemini_tone_{project_id}") or "nostálgico"

project = projects_store.get_project(int(project_id))
assert project is not None
script = final_script(DB_PATH, project_directory(PROJECTS_ROOT, project), project.id)
st.caption(f"Entrada: guion final guardado · {len(script)} caracteres · sin nuevas búsquedas de imágenes")
job = queue.get(project.id)
versions = versions_store.versions(project.id)

if st.button("Generar metadatos con Gemini", type="primary", icon=":material/auto_awesome:", disabled=not service.configured):
    try:
        with st.spinner("Generando metadatos…"):
            metadata = generate(service, project.name, project.character, script, tone)
            saved = versions_store.save(project.id, metadata, tone, service.model)
            if job:
                queue.save_draft(project.id, title=metadata["youtube_title"], description=metadata["youtube_description"], tags=metadata["tags"], hashtags=metadata["hashtags"], pinned_comment=metadata["pinned_comment"])
            st.session_state[f"gemini_metadata_{project.id}"] = metadata
        st.success("Metadatos generados y guardados como una nueva versión.")
    except Exception as exc:
        st.error(f"No se pudo generar: {exc}")

metadata = st.session_state.get(f"gemini_metadata_{project.id}")
if metadata is None and versions:
    metadata = versions[0]["metadata"]
if metadata:
    st.subheader("Metadatos editables")
    with st.form(f"gemini_edit_{project.id}"):
        edited_title = st.text_input("Título de YouTube", value=metadata["youtube_title"])
        edited_description = st.text_area("Descripción de YouTube", value=metadata["youtube_description"], height=160)
        edited_tags = st.text_input("Etiquetas", value=", ".join(metadata["tags"]))
        edited_hashtags = st.text_input("Hashtags", value=" ".join(metadata["hashtags"]))
        edited_comment = st.text_area("Comentario fijado", value=metadata["pinned_comment"], height=100)
        social = {network: st.text_area(network.capitalize(), value=metadata["social"].get(network, ""), height=120) for network in NETWORKS}
        save = st.form_submit_button("Guardar cambios en la ficha", icon=":material/save:")
    if save:
        clean = {"youtube_title": edited_title, "youtube_description": edited_description, "tags": [item.strip() for item in edited_tags.split(",") if item.strip()], "hashtags": [item.strip() for item in edited_hashtags.split() if item.strip()], "pinned_comment": edited_comment, "social": social}
        from services.gemini_publication import validate_metadata
        try:
            clean = validate_metadata(clean)
            st.session_state[f"gemini_metadata_{project.id}"] = clean
            if not job:
                queue.save_draft(project.id, title=clean["youtube_title"], description=clean["youtube_description"], tags=clean["tags"], hashtags=clean["hashtags"], pinned_comment=clean["pinned_comment"])
            else:
                queue.save_draft(project.id, title=clean["youtube_title"], description=clean["youtube_description"], tags=clean["tags"], hashtags=clean["hashtags"], pinned_comment=clean["pinned_comment"])
            st.success("Cambios guardados. YouTube conserva su flujo de fase 1 y las redes siguen sin publicar.")
        except ValueError as exc:
            st.error(str(exc))

if versions:
    with st.expander(f"Versiones guardadas ({len(versions)})"):
        for version in versions:
            st.write(f"Versión {version['id']} · tono {version['tone']} · {version['generated_at']}")
