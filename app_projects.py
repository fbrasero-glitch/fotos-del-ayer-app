from __future__ import annotations

import json
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from models.photo import Photo
from services.downloader import download_photo
from services.entity_resolver import EntityResolutionError, EntityResolver
from services.fast_search import (
    BRAVE_SOURCE,
    FALLBACK_DISCOVERY_SOURCES,
    GOOGLE_SOURCE,
    MAX_FINALISTS,
    MAX_VISUAL_CANDIDATES,
    OPTIONAL_DISCOVERY_SOURCES,
    PRIMARY_DISCOVERY_SOURCES,
    QUICK_FETCH_LIMIT,
    QUALITY_TARGET,
    FastPhotoSearch,
)
from services.gemini_service import GeminiService
from services.local_vision_service import LocalVisionService
from services.photo_quality_pipeline import PhotoQualityPipeline
from services.photo_state_store import stable_photo_key
from services.production_store import ProductionProject, ProductionSlot, ProductionStore
from services.project_files import (
    ensure_project_structure,
    project_directory,
    scan_project_images,
    slot_directory,
)
from services.query_builder import parse_aliases
from services.rights_inspector import RightsInspector


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


def report_badge(report: dict | None) -> str:
    if not report:
        return "⚪ Sin comprobar"
    return {
        "green": "🟢 Probablemente utilizable",
        "yellow": "🟡 Falta información",
        "red": "🔴 Riesgo alto",
    }.get(report.get("level"), "🟡 Revisar")


def mobile_photo_badge(photo: Photo) -> str:
    """Show the qualities that matter before choosing a photo for a Short."""
    width = max(0, int(photo.width or 0))
    height = max(0, int(photo.height or 0))
    if not width or not height:
        return "📱 Formato no informado · comprobar el original"

    orientation = "vertical" if height >= width else "horizontal"
    minimum_side = min(width, height)
    quality = "alta" if minimum_side >= 900 else "media" if minimum_side >= 500 else "baja"
    fit = FastPhotoSearch.mobile_fit_score(photo)
    return f"📱 {orientation} · calidad {quality} · móvil {fit}/100 · {width}×{height}px"


def vision_photo_badge(photo: Photo) -> str:
    if not photo.final_score:
        return "🤖 Pendiente de selección visual"
    recommendation = "recomendada" if photo.ai_recommended else "revisar"
    mobile = photo.metadata.get("vision_mobile_crop", "?")
    clean = photo.metadata.get("vision_clean_image", "?")
    model = str(photo.metadata.get("vision_model", "visión"))
    model_label = "Gemini" if "gemini" in model.casefold() else "Qwen local" if "qwen" in model.casefold() else model
    return (
        f"🤖 {model_label} {photo.final_score}/100 · {recommendation} · "
        f"recorte {mobile}/100 · limpieza {clean}/100"
    )


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


def slot_label(slots: list[ProductionSlot], slot_id: int) -> str:
    return next(slot.label for slot in slots if slot.id == slot_id)


def use_photo(
    store: ProductionStore,
    inspector: RightsInspector,
    project: ProductionProject,
    destination_slot: ProductionSlot,
    photo: Photo,
    rights_mode: str,
) -> tuple[bool, str]:
    """Descarga, elige y registra una foto; en estricto exige informe verde."""
    report = store.get_rights_report(photo)
    if rights_mode == "strict" and report is None:
        report = inspector.inspect(photo)
        store.save_rights_report(photo, report)
    rights_level = report.get("level", "unchecked") if report else photo.traffic_light
    if rights_mode == "strict" and rights_level != "green":
        return False, "Modo Estricto: la escena solo se cierra con una comprobación verde."

    local_path = store.get_download(destination_slot.id, photo)
    if not local_path or not Path(local_path).exists():
        local_path = str(
            download_photo(
                photo,
                slot_directory(DOWNLOAD_ROOT, project, destination_slot),
            )
        )
        store.save_download(destination_slot.id, photo, local_path)
    store.choose_candidate(destination_slot.id, photo)
    store.record_decision(
        destination_slot.id,
        photo,
        "use",
        rights_mode,
        rights_level,
        local_path,
    )
    return True, f"{destination_slot.label} terminada: foto descargada, elegida y registrada."


@st.cache_resource
def shared_searcher(database_path: str) -> FastPhotoSearch:
    return FastPhotoSearch(usage_path=database_path)


@st.cache_resource
def shared_local_vision() -> LocalVisionService:
    return LocalVisionService()


@st.cache_resource
def shared_gemini() -> GeminiService:
    return GeminiService()


@st.cache_resource
def shared_rights_inspector() -> RightsInspector:
    return RightsInspector()


store = ProductionStore(DB_PATH)
searcher = shared_searcher(str(DB_PATH))
local_vision = shared_local_vision()
gemini = shared_gemini()
gemini_pipeline = PhotoQualityPipeline(store, gemini)
local_pipeline = PhotoQualityPipeline(store, local_vision)
inspector = shared_rights_inspector()
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
    '<div class="project-box"><strong>Buscador rápido con memoria.</strong> Las búsquedas se '
    'guardan y cada foto puede enviarse a cualquier parte del Short.</div>',
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
                    created_project = store.get_project(project_id)
                    if created_project:
                        ensure_project_structure(
                            DOWNLOAD_ROOT, created_project, store.list_slots(project_id)
                        )
                    st.session_state.production_project_id = project_id
                    st.session_state.production_slot_id = 0
                    st.session_state.production_notice = "Proyecto y carpetas creados."
                    st.rerun()

if not selected_project_id:
    st.info("Crea un proyecto desde la barra lateral para comenzar.")
    st.stop()

project = store.get_project(int(selected_project_id))
if project is None:
    st.error("El proyecto seleccionado ya no existe.")
    st.stop()

slots = store.list_slots(project.id)
directories = ensure_project_structure(DOWNLOAD_ROOT, project, slots)

with st.sidebar:
    st.divider()
    st.caption(f"Personaje: {project.entity.label} ({project.entity.qid})")
    with st.form("add_scene_form"):
        scene_name = st.text_input("Nueva escena", placeholder="Sola en el trampolín del yate")
        add_scene_clicked = st.form_submit_button("Añadir escena y carpeta", width="stretch")
    if add_scene_clicked:
        scene_id = store.add_scene(project.id, scene_name)
        new_slots = store.list_slots(project.id)
        ensure_project_structure(DOWNLOAD_ROOT, project, new_slots)
        st.session_state.production_slot_id = scene_id
        st.session_state.production_notice = "Escena y carpeta creadas."
        st.rerun()

    st.divider()
    st.subheader("Derechos")
    saved_rights_mode = store.get_rights_mode(project.id)
    rights_mode_label = st.segmented_control(
        "Modo de trabajo",
        ["Experimental", "Estricto"],
        default="Estricto" if saved_rights_mode == "strict" else "Experimental",
        key=f"rights_mode_{project.id}",
        help=(
            "Experimental informa del riesgo sin bloquear. Estricto comprueba los derechos "
            "y solo permite cerrar con un resultado verde."
        ),
    )
    rights_mode = "strict" if rights_mode_label == "Estricto" else "experimental"
    if rights_mode != saved_rights_mode:
        store.set_rights_mode(project.id, rights_mode)

    st.divider()
    st.subheader("Límites locales")
    for name, snapshot in searcher.usage_snapshot().items():
        st.metric(name, f"{snapshot.used}/{snapshot.limit}", f"quedan {snapshot.remaining}")
    st.caption(
        f"DuckDuckGo busca hasta {QUICK_FETCH_LIMIT} resultados; se muestran hasta {MAX_FINALISTS} "
        f"finalistas y Gemini analiza como máximo {MAX_VISUAL_CANDIDATES}. Brave y Google solo "
        f"entran si faltan {QUALITY_TARGET} opciones buenas. Qwen queda como botón manual."
    )

if st.session_state.production_notice:
    st.success(st.session_state.production_notice)
    st.session_state.production_notice = ""

local_images = scan_project_images(DOWNLOAD_ROOT, project, slots)
local_by_slot = {
    slot.id: [image for image in local_images if image.slot_id == slot.id]
    for slot in slots
}
ready_slots = sum(bool(local_by_slot[slot.id]) for slot in slots)
pending_slots = [slot for slot in slots if not local_by_slot[slot.id]]

st.header(project.name)
status_columns = st.columns([1, 3])
with status_columns[0]:
    st.metric("Partes con fotografía", f"{ready_slots}/{len(slots)}")
with status_columns[1]:
    if ready_slots == len(slots):
        st.success("Proyecto completo: todas las partes tienen al menos una imagen local. Ya puedes empezar la edición.")
    else:
        st.info(f"Quedan {len(pending_slots)} partes pendientes.")

if pending_slots:
    with st.container(border=True):
        st.subheader("Escenas pendientes")
        st.caption("Abre directamente la siguiente parte que necesita fotografía.")
        with st.container(horizontal=True):
            for pending_slot in pending_slots:
                if st.button(
                    pending_slot.label,
                    key=f"open_pending_{project.id}_{pending_slot.id}",
                    icon=":material/arrow_forward:",
                ):
                    st.session_state.production_project_id = project.id
                    st.session_state.production_slot_id = pending_slot.id
                    st.session_state[f"slot_selector_{project.id}"] = pending_slot.id
                    st.rerun()

slot_ids = [slot.id for slot in slots]
current_slot_id = st.session_state.production_slot_id
selector_key = f"slot_selector_{project.id}"
st.session_state.setdefault(
    selector_key,
    current_slot_id if current_slot_id in slot_ids else slot_ids[0],
)
active_slot_id = st.selectbox(
    "Búsqueda abierta",
    slot_ids,
    index=slot_ids.index(current_slot_id) if current_slot_id in slot_ids else 0,
    format_func=lambda value: slot_label(slots, value),
    key=selector_key,
)
st.session_state.production_slot_id = active_slot_id
slot = next(item for item in slots if item.id == active_slot_id)
is_hook = slot.kind == "hook"

with st.container(border=True):
    st.subheader(f"{slot.label} · objetivo visual")
    st.caption("La frase y el brief se guardan con esta escena y se reutilizan en las siguientes búsquedas.")
    with st.form(f"project_search_{slot.id}"):
        script_phrase = st.text_area(
            "Frase exacta del guion",
            value=slot.script_phrase,
            height=90,
            placeholder="Pega aquí la frase que debe ilustrar esta escena.",
            help="La frase aparece encima de las fotos para comprobar que la imagen cuenta la historia correcta.",
        )
        visual_brief = st.text_input(
            "Qué debe verse en la foto",
            value=slot.visual_brief,
            placeholder="Ej.: retrato joven en blanco y negro, primeros escenarios, micrófono",
        )
        manual_sources = [
            name
            for name in [*PRIMARY_DISCOVERY_SOURCES, *FALLBACK_DISCOVERY_SOURCES, *OPTIONAL_DISCOVERY_SOURCES]
            if configured.get(name)
        ]
        manual_source = st.selectbox(
            "Fuente manual opcional",
            manual_sources or ["Ninguna configurada"],
            help="La búsqueda normal sigue la cascada económica. Esta opción sirve para consultar una fuente concreta.",
        )
        buttons = st.columns(3)
        with buttons[0]:
            cascade_clicked = st.form_submit_button(
                "Buscar según mi método",
                type="primary",
                width="stretch",
                icon=":material/manage_search:",
            )
        with buttons[1]:
            local_clicked = st.form_submit_button(
                "Analizar con Qwen local",
                width="stretch",
                help="Acción manual de emergencia si Gemini no está disponible.",
            )
        with buttons[2]:
            manual_clicked = st.form_submit_button("Buscar solo esta fuente", width="stretch")

clicked = cascade_clicked or local_clicked or manual_clicked
if clicked:
    scene_text = " ".join(
        item for item in (visual_brief.strip(), script_phrase.strip()) if item
    ) or ("young portrait" if is_hook else "")
    search_text = visual_brief.strip() or script_phrase.strip() or ("young portrait" if is_hook else "")
    if not scene_text or not search_text:
        st.error("Escribe la frase del guion o describe qué debe verse en esta escena.")
    else:
        store.update_slot_brief(slot.id, script_phrase, visual_brief)
        reused = 0
        new_calls = 0
        messages: list[str] = []
        def attach_sources(sources: list[str]) -> tuple[int, int]:
            local_reused = 0
            local_calls = 0
            missing: list[str] = []
            fingerprints: dict[str, tuple[str, str]] = {}
            for source in sources:
                query = searcher.query_for(source, project.entity, search_text, is_hook)
                fingerprint = store.fingerprint(project.entity.qid, source, query)
                fingerprints[source] = (fingerprint, query)
                saved = store.get_search(fingerprint)
                if saved:
                    store.attach_search(slot.id, saved.id)
                    local_reused += 1
                else:
                    missing.append(source)

            if missing:
                with st.spinner(
                    "Buscando en paralelo: " + ", ".join(missing) + "…"
                ):
                    result = searcher.search(
                        project.entity,
                        search_text,
                        missing,
                        is_hook,
                        QUICK_FETCH_LIMIT,
                    )
                messages.extend(result.warnings)
                for source in result.sources_used:
                    fingerprint, query = fingerprints[source]
                    source_photos = result.by_source.get(source, [])
                    saved = store.save_search(
                        fingerprint,
                        project.entity.qid,
                        source,
                        query,
                        search_text,
                        is_hook,
                        QUICK_FETCH_LIMIT,
                        source_photos,
                    )
                    store.attach_search(slot.id, saved.id)
                    local_calls += 1
            return local_reused, local_calls

        def analyze_current_pool(
            pipeline: PhotoQualityPipeline,
            label: str,
            max_new_analyses: int = MAX_VISUAL_CANDIDATES,
            candidate_limit: int = MAX_VISUAL_CANDIDATES,
        ):
            with st.spinner(
                f"{label} analiza como máximo {MAX_VISUAL_CANDIDATES} candidatas…"
            ):
                return pipeline.rank(
                    slot.id,
                    project.character,
                    project.entity,
                    scene_text,
                    is_hook,
                    store.slot_photos(slot.id),
                    limit=candidate_limit,
                    batch_size=max(1, min(candidate_limit, max_new_analyses or candidate_limit)),
                    max_new_analyses=max_new_analyses,
                )

        if local_clicked:
            st.session_state[f"vision_backend_{project.id}_{slot.id}"] = "local"
            quality = analyze_current_pool(local_pipeline, "Qwen local")
            messages.extend(quality.warnings)
            messages.append("Qwen se ha usado solo porque lo has solicitado manualmente.")
        elif cascade_clicked:
            st.session_state[f"vision_backend_{project.id}_{slot.id}"] = "gemini"
            reused_delta, new_delta = attach_sources(list(PRIMARY_DISCOVERY_SOURCES))
            reused += reused_delta
            new_calls += new_delta
            quality = analyze_current_pool(
                gemini_pipeline,
                "Gemini",
                QUALITY_TARGET,
                QUALITY_TARGET,
            )
            messages.extend(quality.warnings)
            visual_used = quality.cache_hits + quality.analyzed_count
            visual_failed = bool(quality.warnings and not (quality.cache_hits or quality.analyzed_count))
            enough = (
                bool(quality.vision_available) and len(quality.photos) >= QUALITY_TARGET
                if visual_failed
                else quality.good_count >= QUALITY_TARGET
            )
            if not enough and configured.get(BRAVE_SOURCE):
                messages.append(
                    f"DuckDuckGo dejó {quality.good_count} foto(s) buenas; se amplió con Brave."
                )
                reused_delta, new_delta = attach_sources([BRAVE_SOURCE])
                reused += reused_delta
                new_calls += new_delta
                quality = (
                    gemini_pipeline.rank(
                        slot.id,
                        project.character,
                        project.entity,
                        scene_text,
                        is_hook,
                        store.slot_photos(slot.id),
                        analyze_missing=False,
                        limit=MAX_FINALISTS,
                    )
                    if visual_failed
                    else analyze_current_pool(
                        gemini_pipeline,
                        "Gemini",
                        max(0, MAX_VISUAL_CANDIDATES - visual_used),
                    )
                )
                messages.extend(quality.warnings)
                visual_used += quality.analyzed_count
                enough = (
                    bool(quality.vision_available) and len(quality.photos) >= QUALITY_TARGET
                    if visual_failed
                    else quality.good_count >= QUALITY_TARGET
                )
            if not enough and configured.get(GOOGLE_SOURCE):
                messages.append(
                    f"La criba dejó {quality.good_count} foto(s) buenas; se amplió con Google."
                )
                reused_delta, new_delta = attach_sources([GOOGLE_SOURCE])
                reused += reused_delta
                new_calls += new_delta
                quality = (
                    gemini_pipeline.rank(
                        slot.id,
                        project.character,
                        project.entity,
                        scene_text,
                        is_hook,
                        store.slot_photos(slot.id),
                        analyze_missing=False,
                        limit=MAX_FINALISTS,
                    )
                    if visual_failed
                    else analyze_current_pool(
                        gemini_pipeline,
                        "Gemini",
                        max(0, MAX_VISUAL_CANDIDATES - visual_used),
                    )
                )
                messages.extend(quality.warnings)
            messages.append(
                f"La selección final contiene {len(quality.photos)} candidata(s); "
                f"{quality.good_count} recomendada(s) por Gemini."
            )
        else:
            st.session_state[f"vision_backend_{project.id}_{slot.id}"] = "gemini"
            reused_delta, new_delta = attach_sources([manual_source])
            reused += reused_delta
            new_calls += new_delta
            quality = analyze_current_pool(gemini_pipeline, "Gemini")
            messages.extend(quality.warnings)

        if messages:
            st.session_state.production_notice = " · ".join(messages)
        elif reused and not new_calls:
            st.session_state.production_notice = f"Recuperada del historial: {reused} fuente(s), 0 créditos."
        else:
            st.session_state.production_notice = (
                f"Guardada: {new_calls} petición(es) nueva(s)"
                + (f" y {reused} recuperada(s)." if reused else ".")
                + f" Preselección limitada a {MAX_FINALISTS}."
            )
        st.rerun()

searches = store.slot_searches(slot.id)
latest_scene_text = " ".join(
    item for item in (slot.visual_brief.strip(), slot.script_phrase.strip()) if item
) or (
    searches[0].keywords if searches else ("young portrait" if is_hook else "")
)
active_pipeline = (
    local_pipeline
    if st.session_state.get(f"vision_backend_{project.id}_{slot.id}") == "local"
    else gemini_pipeline
)
restored_quality = active_pipeline.rank(
    slot.id,
    project.character,
    project.entity,
    latest_scene_text,
    is_hook,
    store.slot_photos(slot.id),
    analyze_missing=False,
    limit=MAX_FINALISTS,
)
photos = restored_quality.photos
candidates = store.list_candidates(slot.id)

if searches:
    with st.expander(f"Historial guardado · {len(searches)} búsqueda(s)"):
        for saved in searches:
            st.markdown(f"**{saved.source}** · `{saved.query}`")
            st.caption(f"{len(saved.photos)} resultados · {saved.created_at[:16].replace('T', ' ')} UTC")
else:
    st.info("Esta parte todavía no tiene búsquedas guardadas.")

if searches or photos:
    with st.container(border=True):
        st.subheader(f"Lo que debe contar la imagen · {slot.label}")
        phrase = slot.script_phrase or "Frase no guardada: completa el objetivo visual arriba."
        st.markdown(f"**Frase del guion:** {phrase}")
        st.caption(
            f"**Brief visual:** {slot.visual_brief or 'La búsqueda usa directamente la frase del guion.'}"
        )

if candidates:
    st.subheader(f"Candidatas de {slot.label} · {len(candidates)}")
    candidate_columns = st.columns(min(4, len(candidates)))
    for index, (candidate, chosen) in enumerate(candidates):
        with candidate_columns[index % len(candidate_columns)]:
            with st.container(border=True):
                st.image(candidate.thumbnail_url or candidate.image_url, width="stretch")
                st.markdown(f"**{'Elegida' if chosen else 'Alternativa'}**")
                st.caption(candidate.title)
                st.caption(mobile_photo_badge(candidate))

if photos:
    st.subheader(f"Preselección · {len(photos)}/{MAX_FINALISTS}")
    st.caption(
        f"Finalistas ordenadas por {'Qwen local' if active_pipeline is local_pipeline else 'Gemini'}: "
        "identidad, escena, impacto y encuadre para pantalla vertical. Tú eliges la definitiva."
    )
    destination_id = st.selectbox(
        "Destino de la decisión",
        slot_ids,
        index=slot_ids.index(slot.id),
        format_func=lambda value: slot_label(slots, value),
        key=f"destination_{project.id}_{slot.id}",
    )
    destination_slot = next(item for item in slots if item.id == destination_id)
    decisions = store.decision_map(destination_slot.id)
    st.caption(f"Destino: {directories[destination_slot.id]}")

    source_options = ["Todas", *dict.fromkeys(photo.source for photo in photos)]
    source_filter = st.selectbox("Mostrar fuente", source_options, key=f"source_{slot.id}")
    visible = [
        photo
        for photo in photos
        if decisions.get(stable_photo_key(photo)) != "discard"
        and (source_filter == "Todas" or photo.source == source_filter)
    ]
    if len(visible) < len(photos):
        st.caption(f"{len(photos) - len(visible)} fotografía(s) descartada(s) oculta(s).")
    columns = st.columns(3)
    for position, photo in enumerate(visible):
        photo_key = stable_photo_key(photo)
        report = store.get_rights_report(photo)
        downloaded = store.get_download(destination_slot.id, photo)
        decision = decisions.get(photo_key, "")
        with columns[position % 3]:
            with st.container(border=True):
                st.image(photo.thumbnail_url or photo.image_url, width="stretch")
                st.markdown(f"**Foto {position + 1} · {photo.title or 'Sin título'}**")
                st.caption(f"{photo.source} · resultado {position + 1}")
                st.caption(mobile_photo_badge(photo))
                st.caption(vision_photo_badge(photo))
                issues = photo.metadata.get("vision_quality_issues", [])
                if issues:
                    st.caption("⚠️ " + " · ".join(str(item) for item in issues))
                if photo.ai_description:
                    st.caption(photo.ai_description)
                st.caption(report_badge(report))
                if decision == "alternative":
                    st.info("Guardada como alternativa")
                if photo.original_page_url:
                    st.link_button("Abrir fuente", photo.original_page_url, width="stretch")
                actions = st.columns(3)
                with actions[0]:
                    use_clicked = st.button(
                        "Elegir esta foto",
                        key=f"use_{destination_slot.id}_{photo_key}",
                        type="primary",
                        width="stretch",
                    )
                with actions[1]:
                    discard_clicked = st.button(
                        "Descartar",
                        key=f"discard_{destination_slot.id}_{photo_key}",
                        width="stretch",
                    )
                with actions[2]:
                    alternative_clicked = st.button(
                        "Alternativa",
                        key=f"alternative_{destination_slot.id}_{photo_key}",
                        width="stretch",
                    )

                if use_clicked:
                    try:
                        with st.spinner("Descargando, registrando y cerrando la escena…"):
                            used, notice = use_photo(
                                store,
                                inspector,
                                project,
                                destination_slot,
                                photo,
                                rights_mode,
                            )
                    except Exception as exc:
                        st.error(
                            f"No se pudo usar la foto ({type(exc).__name__}). Abre la fuente original."
                        )
                    else:
                        if used:
                            st.session_state.production_notice = notice
                            st.rerun()
                        else:
                            report = store.get_rights_report(photo)
                            st.error(notice)
                if discard_clicked:
                    store.remove_candidate(destination_slot.id, photo_key)
                    store.record_decision(
                        destination_slot.id,
                        photo,
                        "discard",
                        rights_mode,
                        report.get("level", "unchecked") if report else photo.traffic_light,
                    )
                    st.session_state.production_notice = "Fotografía descartada y retirada de la preselección."
                    st.rerun()
                if alternative_clicked:
                    store.add_candidate(destination_slot.id, photo)
                    store.record_decision(
                        destination_slot.id,
                        photo,
                        "alternative",
                        rights_mode,
                        report.get("level", "unchecked") if report else photo.traffic_light,
                    )
                    st.session_state.production_notice = "Fotografía guardada como alternativa."
                    st.rerun()

                if report is None and st.button(
                    "Comprobar derechos",
                    key=f"rights_{destination_slot.id}_{photo_key}",
                    width="stretch",
                ):
                    with st.spinner("Consultando la fuente y preparando el informe…"):
                        report = inspector.inspect(photo)
                        store.save_rights_report(photo, report)

                if report:
                    with st.expander("Ver informe"):
                        render_report(report)
                if downloaded:
                    st.success(f"En {destination_slot.label}: {Path(downloaded).name}")

st.divider()
st.subheader("Fotografías detectadas en las carpetas")
st.caption("También aparecen las imágenes que copies manualmente desde el Explorador de archivos.")
unassigned = [image for image in local_images if image.slot_id is None]
local_columns = st.columns(min(4, len(slots)))
for index, local_slot in enumerate(slots):
    files = local_by_slot[local_slot.id]
    with local_columns[index % len(local_columns)]:
        with st.container(border=True):
            st.markdown(f"**{local_slot.label} · {len(files)}**")
            if files:
                st.image(str(files[0].path), width="stretch")
                for image in files[:4]:
                    st.caption(image.path.name)
                if len(files) > 4:
                    st.caption(f"y {len(files) - 4} más")
            else:
                st.info("Carpeta vacía")

if unassigned:
    st.warning(
        f"Hay {len(unassigned)} imagen(es) en carpetas cuyo nombre no coincide con Gancho, "
        "Foto final o una Escena numerada."
    )
    with st.expander("Ver imágenes sin asignar"):
        for image in unassigned:
            st.caption(str(image.relative_path))

st.subheader("Montaje del Short")
summary_columns = st.columns(min(4, len(slots)))
for index, summary_slot in enumerate(slots):
    chosen = next(
        (photo for photo, is_chosen in store.list_candidates(summary_slot.id) if is_chosen),
        None,
    )
    local_files = local_by_slot[summary_slot.id]
    with summary_columns[index % len(summary_columns)]:
        with st.container(border=True):
            st.markdown(f"**{summary_slot.label}**")
            if chosen:
                st.image(chosen.thumbnail_url or chosen.image_url, width="stretch")
                local_path = store.get_download(summary_slot.id, chosen)
                st.caption("Descargada" if local_path else "Elegida · pendiente de descarga")
            elif local_files:
                st.image(str(local_files[0].path), width="stretch")
                st.caption(f"Archivo local detectado · {len(local_files)} disponible(s)")
            else:
                st.info("Pendiente")

st.caption(f"Carpeta del proyecto: {project_directory(DOWNLOAD_ROOT, project)}")
