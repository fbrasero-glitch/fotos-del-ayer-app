"""Publicación del lote inicial de Shorts en la página de Facebook."""
from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from services.facebook_batch import FACEBOOK_ORDER, find_batch_files, persist_batch_files
from services.social_clients import SocialPublisher
from youtube_upload import UPLOAD_DIR, VIDEOS


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

EXPECTED = {item["key"]: item for item in VIDEOS[:4]}
SAVED_BATCH_DIR = ROOT / "data" / "facebook_upload" / "library"
SOURCE_SAVED = "Lote guardado"
SOURCE_UPLOAD = "Seleccionar archivos"


def _save_and_select_saved_batch(uploaded_by_name: dict[str, object]) -> None:
    persist_batch_files(uploaded_by_name, EXPECTED.values(), SAVED_BATCH_DIR)
    st.session_state["facebook_video_source"] = SOURCE_SAVED


st.set_page_config(page_title="Fotos del Ayer · Facebook", page_icon=":material/video_library:", layout="wide")
st.title("Fotos del Ayer · Facebook")
st.caption("Lote inicial de cuatro reels, publicado en orden inverso al de YouTube.")

publisher = SocialPublisher()
connection = publisher.connection("facebook")
if connection.connected:
    st.success("Facebook conectado. Los vídeos se enviarán desde la API oficial.")
else:
    st.error(connection.message)
    st.info("Configura el secreto de la página en Streamlit y vuelve a cargar esta pantalla.")
    st.stop()

st.session_state.setdefault("facebook_batch_results", {})

with st.container(border=True):
    st.subheader("Orden de publicación")
    for position, key in enumerate(FACEBOOK_ORDER, start=1):
        item = EXPECTED[key]
        result = st.session_state["facebook_batch_results"].get(key, {})
        status = result.get("status", "pendiente")
        st.write(f"{position}. **{item['title']}** · `{item['file']}` · {status}")

saved_by_key = find_batch_files(EXPECTED.values(), (SAVED_BATCH_DIR, UPLOAD_DIR))
saved_complete = len(saved_by_key) == len(FACEBOOK_ORDER)
default_source = SOURCE_SAVED if saved_complete else SOURCE_UPLOAD

with st.container(border=True):
    st.subheader("Fuente de los vídeos")
    source = st.segmented_control(
        "Cómo quieres cargar el lote",
        options=[SOURCE_SAVED, SOURCE_UPLOAD],
        default=default_source,
        key="facebook_video_source",
        help="El lote guardado evita volver a abrir el selector de archivos en futuras ejecuciones.",
        width="stretch",
    )

    uploaded_by_name = {}
    active_paths: dict[str, Path] = {}
    using_saved = source == SOURCE_SAVED and saved_complete
    if using_saved:
        active_paths = saved_by_key
        st.success("Hay un lote guardado. La app lo reutilizará automáticamente.")
        st.caption("Si cambias los vídeos, selecciona «Seleccionar archivos» y guarda el nuevo lote.")
    else:
        uploaded_files = st.file_uploader(
            "Selecciona los cuatro vídeos MP4",
            type=["mp4"],
            accept_multiple_files=True,
            max_upload_size=200,
            key="facebook_batch_files",
            help="Selecciona estos archivos: 04_jackie_kennedy.mp4, 03_james_dean.mp4, 02_lady_di.mp4 y 01_marilyn_monroe.mp4.",
        )
        uploaded_by_name = {file.name: file for file in uploaded_files}
        missing = [EXPECTED[key]["file"] for key in FACEBOOK_ORDER if EXPECTED[key]["file"] not in uploaded_by_name]
        if missing:
            st.warning("Faltan por seleccionar: " + ", ".join(missing))
        else:
            if st.button(
                "Guardar este lote para reutilizarlo",
                icon=":material/save:",
                help="Conserva los cuatro MP4 en el almacenamiento de la app para no tener que seleccionarlos otra vez.",
                on_click=_save_and_select_saved_batch,
                args=(uploaded_by_name,),
            ):
                st.success("Lote guardado. En la próxima visita podrás usarlo automáticamente.")

        if not active_paths:
            active_paths = {
                key: Path(f"uploaded:{EXPECTED[key]['file']}")
                for key in FACEBOOK_ORDER
                if EXPECTED[key]["file"] in uploaded_by_name
            }

if using_saved or len(active_paths) == len(FACEBOOK_ORDER):
    st.success("Los cuatro vídeos están listos y se publicarán en el orden indicado.")

missing = [key for key in FACEBOOK_ORDER if key not in active_paths]

publish = st.button(
    "Publicar los 4 reels",
    type="primary",
    icon=":material/publish:",
    disabled=bool(missing),
    help="La publicación es pública y se ejecuta una sola vez por vídeo en esta sesión.",
)

if publish:
    for key in FACEBOOK_ORDER:
        if st.session_state["facebook_batch_results"].get(key, {}).get("remote_id"):
            continue
        item = EXPECTED[key]
        caption = f"{item['title']}\n\n{item['description']}"
        temp_path = None
        try:
            if using_saved or item["file"] not in uploaded_by_name:
                temp_path = active_paths[key]
            else:
                uploaded = uploaded_by_name[item["file"]]
                with tempfile.NamedTemporaryFile(prefix="fotos_del_ayer_", suffix=".mp4", delete=False) as temp:
                    temp.write(uploaded.getvalue())
                    temp_path = Path(temp.name)
            with st.status(f"Publicando {item['title']}…", expanded=True) as status:
                result = publisher.publish("facebook", str(temp_path), caption)
                st.session_state["facebook_batch_results"][key] = result
                status.update(label=f"Publicado: {item['title']}", state="complete", expanded=False)
        except Exception as exc:
            st.session_state["facebook_batch_results"][key] = {"status": "error", "error": str(exc)}
            st.error(f"No se pudo publicar {item['title']}: {exc}")
            break
        finally:
            if temp_path is not None and not using_saved and item["file"] in uploaded_by_name:
                temp_path.unlink(missing_ok=True)

results = st.session_state["facebook_batch_results"]
if results:
    with st.container(border=True):
        st.subheader("Resultado")
        for key in FACEBOOK_ORDER:
            result = results.get(key, {})
            if result.get("remote_url"):
                st.link_button(f"Abrir {EXPECTED[key]['key']}", result["remote_url"], icon=":material/open_in_new:")
            elif result.get("error"):
                st.error(f"{EXPECTED[key]['title']}: {result['error']}")
