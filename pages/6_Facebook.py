"""Publicación del lote inicial de Shorts en la página de Facebook."""
from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from services.social_clients import SocialPublisher
from youtube_upload import VIDEOS


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

FACEBOOK_ORDER = ["jackie", "james", "diana", "marilyn"]
EXPECTED = {item["key"]: item for item in VIDEOS[:4]}

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
    st.success("Los cuatro vídeos están listos y se publicarán en el orden indicado.")

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
        uploaded = uploaded_by_name[item["file"]]
        caption = f"{item['title']}\n\n{item['description']}"
        temp_path = None
        try:
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
            if temp_path is not None:
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
