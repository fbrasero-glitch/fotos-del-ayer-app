from __future__ import annotations

from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from dotenv import load_dotenv

from services.production_store import ProductionStore
from services.project_files import project_directory
from services.publication_store import PublicationStore
from services.gemini_publication import PublicationMetadataStore
from services.short_validation import validate_short
from services.social_clients import SocialPublisher
from services.youtube_client import YouTubeClient


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "fotos_de_ayer.db"
PROJECTS_ROOT = ROOT / "proyectos_fotos"
MADRID = ZoneInfo("Europe/Madrid")
load_dotenv(ROOT / ".env")

st.set_page_config(page_title="Fotos del Ayer · Publicación", page_icon=":material/publish:", layout="wide")

projects_store = ProductionStore(DB_PATH)
queue = PublicationStore(DB_PATH)
metadata_store = PublicationMetadataStore(DB_PATH)
social_client = SocialPublisher()
projects = projects_store.list_projects()

st.title("Fotos del Ayer · Publicación")
st.caption("Validación local y publicación manual mediante las APIs oficiales de YouTube, Facebook, Instagram y TikTok.")

if not projects:
    st.info("Primero crea y edita un proyecto.")
    st.stop()

with st.sidebar:
    st.header("Publicación")
    project_id = st.selectbox(
        "Proyecto",
        [project.id for project in projects],
        format_func=lambda value: next(project.name for project in projects if project.id == value),
    )
    client = YouTubeClient()
    connection = client.connection_status()
    if connection.connected:
        st.success(f"Conectado: {connection.channel_title}")
    else:
        st.info("YouTube sin conectar")
    if st.button("Conectar cuenta de YouTube", icon=":material/account_circle:", width="stretch"):
        try:
            with st.spinner("Abriendo autorización segura de Google…"):
                connection = client.connect()
            st.success(f"Conectado: {connection.channel_title}")
        except Exception as exc:
            st.error(f"No se pudo conectar: {exc}")

project = projects_store.get_project(int(project_id))
if project is None:
    st.error("El proyecto ya no existe.")
    st.stop()

base = project_directory(PROJECTS_ROOT, project)
render_candidates = sorted(base.glob("edicion/**/short_final_con_musica*.mp4"), reverse=True)
render_candidates += sorted(base.glob("edicion/**/short_final_sin_musica*.mp4"), reverse=True)
video_options = [str(item.resolve()) for item in dict.fromkeys(render_candidates)]
default_video = video_options[0] if video_options else ""
hook_folder = base / "01-gancho"
cover_options = [str(path.resolve()) for path in hook_folder.glob("*") if path.suffix.lower() in {".jpg", ".jpeg", ".png"}]
vertical_cover = base / "edicion" / "render" / "portada_9x16.jpg"
if vertical_cover.is_file():
    cover_options.insert(0, str(vertical_cover.resolve()))
cover_options = list(dict.fromkeys(cover_options))

job = queue.get(project.id)
current_video = job.video_path if job and Path(job.video_path).is_file() else default_video
if current_video and current_video not in video_options:
    video_options.insert(0, current_video)

st.header(project.name)
with st.container(border=True):
    st.subheader("Ficha de publicación")
    with st.form(f"publication_form_{project.id}"):
        video_path = st.selectbox("Vídeo renderizado", video_options or [""], index=(video_options.index(current_video) if current_video in video_options else 0), format_func=lambda item: Path(item).name if item else "No hay un render disponible")
        title = st.text_input("Título", value=job.title if job else f"{project.character}: una historia para recordar #Shorts")
        description = st.text_area("Descripción", value=job.description if job else "", height=150)
        tags = st.text_input("Etiquetas", value=", ".join(job.tags) if job else f"{project.character}, Fotos del Ayer")
        hashtags = st.text_input("Hashtags", value=" ".join(job.hashtags) if job else "#FotosDelAyer #Shorts")
        playlist_id = st.text_input("ID de playlist de YouTube (opcional)", value=job.playlist_id if job else "")
        pinned_comment = st.text_area("Comentario para fijar en Studio", value=job.pinned_comment if job else "", help="La API oficial permite crear comentarios, pero no fijarlos; queda guardado para pegarlo y fijarlo en YouTube Studio.")
        thumbnail_path = st.selectbox("Portada (debe proceder de 01-gancho)", cover_options or [""], index=(cover_options.index(job.thumbnail_path) if job and job.thumbnail_path in cover_options else 0), format_func=lambda item: Path(item).name if item else "No hay portada local")
        date_value = st.date_input("Fecha de publicación", value=(datetime.fromisoformat(job.publish_at.replace("Z", "+00:00")).astimezone(MADRID).date() if job and job.publish_at else None))
        time_value = st.time_input("Hora (España peninsular)", value=(datetime.fromisoformat(job.publish_at.replace("Z", "+00:00")).astimezone(MADRID).time().replace(tzinfo=None) if job and job.publish_at else time(19, 0)))
        save = st.form_submit_button("Guardar ficha", type="primary", icon=":material/save:")
    if save:
        publish_at = ""
        if date_value:
            publish_at = datetime.combine(date_value, time_value, tzinfo=MADRID).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        job = queue.save_draft(project.id, video_path=video_path, title=title.strip(), description=description.strip(), tags=[item.strip() for item in tags.split(",") if item.strip()], hashtags=[item.strip() for item in hashtags.split() if item.strip()], playlist_id=playlist_id.strip(), pinned_comment=pinned_comment.strip(), thumbnail_path=thumbnail_path, publish_at=publish_at)
        st.success("Ficha guardada en la cola local.")

job = queue.get(project.id)
if job is None:
    st.info("Guarda la ficha para crear el elemento de cola.")
    st.stop()

# Los textos sociales ya generados se copian una sola vez a una cola independiente.
versions = metadata_store.versions(project.id)
generated_social = versions[0]["metadata"].get("social", {}) if versions else {}
social_posts = {item.network: item for item in queue.ensure_social_publications(project.id, generated_social)}

with st.container(border=True):
    st.subheader("Validación local del render")
    if st.button("Validar Short", icon=":material/fact_check:"):
        subtitle = Path(job.video_path).parent / "subtitulos.srt"
        result = validate_short(job.video_path, subtitle, Path(job.video_path).parent / "publicacion")
        job = queue.update_result(project.id, status="validado" if result.ok else "error", validation_json=result.to_dict(), last_error="" if result.ok else " · ".join(result.errors))
        if result.ok:
            st.success("Render validado: 9:16, duración, audio, subtítulos y primer fotograma correctos.")
        else:
            st.error("La validación local detectó incidencias: " + " ".join(result.errors))
    validation = job.validation_json
    if validation:
        checks = [
            ("Archivo", validation.get("video_exists")), ("9:16", validation.get("vertical_9_16")),
            ("Duración", validation.get("duration_ok")), ("Audio", validation.get("audio_present")),
            ("Subtítulos", validation.get("subtitles_present")), ("Primer fotograma", validation.get("first_frame_visible")),
        ]
        st.write(" · ".join(f"{'✓' if ok else '✕'} {label}" for label, ok in checks))
        st.caption(f"{validation.get('width', 0)} × {validation.get('height', 0)} · {validation.get('duration_seconds', 0):.1f} segundos")
        frame = validation.get("first_frame_path", "")
        if frame and Path(frame).is_file():
            st.image(frame, caption="Primer fotograma extraído para revisión visual", width="content")

with st.container(border=True):
    st.subheader("YouTube y cola")
    st.write(f"Estado local: **{job.status}** · Privacidad: **{job.privacy_status}** · Procesamiento: **{job.processing_status or 'sin consultar'}**")
    if job.youtube_url:
        st.link_button("Abrir vídeo privado", job.youtube_url, icon=":material/open_in_new:")
    actions = st.container(horizontal=True)
    with actions:
        upload = st.button("Subir como privado", type="primary", icon=":material/upload:", disabled=not bool(job.validation_json.get("ok")) or bool(job.youtube_video_id))
        refresh = st.button("Consultar procesamiento", icon=":material/refresh:", disabled=not bool(job.youtube_video_id))
        schedule = st.button("Programar publicación", icon=":material/schedule:", disabled=not (job.youtube_video_id and job.processing_status == "succeeded" and job.publish_at))
    if upload:
        queue.update_result(project.id, status="subiendo", last_error="")
        try:
            with st.spinner("Subiendo como privado a YouTube…"):
                uploaded = client.upload_private(job)
            queue.update_result(project.id, status="procesando", **uploaded)
            st.success("Vídeo subido como privado. Consulta el procesamiento antes de programarlo.")
        except Exception as exc:
            queue.update_result(project.id, status="error", last_error=str(exc))
            st.error(f"La subida no se completó: {exc}")
    if refresh:
        try:
            status = client.video_status(job.youtube_video_id)
            local_status = "publicado" if status["privacy_status"] == "public" else ("procesando" if status["processing_status"] != "succeeded" else job.status)
            queue.update_result(project.id, status=local_status, **status, last_error="")
            st.success("Estado de procesamiento actualizado.")
        except Exception as exc:
            queue.update_result(project.id, status="error", last_error=str(exc))
            st.error(f"No se pudo consultar YouTube: {exc}")
    if schedule:
        try:
            client.schedule(job.youtube_video_id, job.publish_at)
            queue.update_result(project.id, status="programado", privacy_status="private", last_error="")
            st.success("Publicación programada. El vídeo seguirá privado hasta la fecha indicada.")
        except Exception as exc:
            queue.update_result(project.id, status="error", last_error=str(exc))
            st.error(f"No se pudo programar: {exc}")
    if job.last_error:
        st.error(f"Último error: {job.last_error}")

st.subheader("Cola local")
rows = [{"Proyecto": next((item.name for item in projects if item.id == item_job.project_id), str(item_job.project_id)), "Estado": item_job.status, "Procesamiento": item_job.processing_status, "Programado (UTC)": item_job.publish_at or "—", "Actualizado": item_job.updated_at} for item_job in queue.list_jobs()]
st.dataframe(rows, width="stretch", hide_index=True)
st.caption("La base para el trabajador futuro está en publication_worker.py; no hay ninguna tarea programada creada ni activada.")

st.subheader("Redes sociales")
st.caption("Publicación manual: Facebook e Instagram publican directamente; TikTok recibe el vídeo como borrador para que lo revises y pulses Publicar en la app de TikTok.")
if not job.validation_json.get("ok"):
    st.info("Valida primero el Short para habilitar las publicaciones sociales.")

for network in ("facebook", "instagram", "tiktok"):
    post = social_posts[network]
    connection = social_client.connection(network)
    with st.container(border=True):
        st.markdown(f"#### {network.capitalize()}")
        st.caption(connection.message)
        with st.form(f"social_caption_{project.id}_{network}"):
            caption = st.text_area("Texto de la publicación", value=post.caption, height=110, key=f"caption_{project.id}_{network}")
            save_caption = st.form_submit_button("Guardar texto", icon=":material/save:")
        if save_caption:
            post = queue.update_social_publication(project.id, network, caption=caption, last_error="")
            social_posts[network] = post
            st.success("Texto guardado.")
        st.write(f"Estado: **{post.status}**")
        if post.remote_url:
            st.link_button("Abrir publicación", post.remote_url, icon=":material/open_in_new:")
        with st.container(horizontal=True):
            publish = st.button("Publicar ahora", key=f"publish_{project.id}_{network}", type="primary", icon=":material/publish:", disabled=not (job.validation_json.get("ok") and connection.connected) or post.status in {"procesando", "publicado"})
            refresh_social = st.button("Consultar estado", key=f"refresh_social_{project.id}_{network}", icon=":material/refresh:", disabled=not (connection.connected and post.remote_id and post.status == "procesando"))
        if publish:
            queue.update_social_publication(project.id, network, status="subiendo", last_error="")
            try:
                action = "Preparando borrador en TikTok" if network == "tiktok" else f"Publicando en {network.capitalize()}"
                with st.spinner(action + "…"):
                    result = social_client.publish(network, job.video_path, post.caption)
                queue.update_social_publication(project.id, network, status=result.get("status", "publicado"), remote_id=result["remote_id"], remote_url=result.get("remote_url", ""), last_error="")
                message = "Borrador enviado a TikTok. Abre la bandeja de entrada de TikTok para editarlo y pulsar Publicar." if network == "tiktok" else "Envío completado. Consulta el estado si la plataforma sigue procesando."
                st.success(message)
            except Exception as exc:
                queue.update_social_publication(project.id, network, status="error", last_error=str(exc))
                st.error(f"No se pudo publicar: {exc}")
        if refresh_social:
            try:
                result = social_client.refresh(network, post.remote_id)
                queue.update_social_publication(project.id, network, status=result["status"], remote_id=result["remote_id"], remote_url=result.get("remote_url", ""), last_error="")
                st.success("Estado actualizado.")
            except Exception as exc:
                queue.update_social_publication(project.id, network, status="error", last_error=str(exc))
                st.error(f"No se pudo consultar: {exc}")
        if post.last_error:
            st.error(f"Último error: {post.last_error}")
