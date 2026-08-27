from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from models.edit import EditSegment
from services.elevenlabs_tts import ElevenLabsError, ElevenLabsTTS
from services.narration_script import estimate_duration_seconds, split_script
from services.production_store import ProductionProject, ProductionSlot, ProductionStore
from services.project_files import LocalProjectImage, project_directory
from services.short_edit_store import DEFAULT_VOICE_SETTINGS, ShortEditStore, narration_hash
from services.v3_narration_pipeline import NarrationPipelineError, generate_v3_narration
from services.video_renderer import RenderError, ShortVideoRenderer


TONES = (
    "Nostalgia cálida",
    "Intriga contenida",
    "Admiración",
    "Tristeza serena",
    "Tensión emocional",
    "Esperanza",
    "Reflexión final",
)

MUSIC_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


def _editing_directories(root: Path, project: ProductionProject) -> dict[str, Path]:
    base = project_directory(root, project) / "edicion"
    directories = {
        "base": base,
        "audio": base / "narracion",
        "render": base / "render",
        "music": base / "musica",
    }
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)
    return directories


def _initial_images(
    project: ProductionProject,
    slots: list[ProductionSlot],
    local_by_slot: dict[int, list[LocalProjectImage]],
    production_store: ProductionStore,
) -> dict[int, str]:
    selected: dict[int, str] = {}
    for slot in slots:
        chosen = next(
            (
                photo
                for photo, is_chosen in production_store.list_candidates(slot.id)
                if is_chosen
            ),
            None,
        )
        if chosen:
            downloaded = production_store.get_download(slot.id, chosen)
            if downloaded and Path(downloaded).is_file():
                selected[slot.id] = str(Path(downloaded).resolve())
                continue
        files = local_by_slot.get(slot.id, [])
        if len(files) == 1:
            selected[slot.id] = str(files[0].path)
    return selected


def _generate_segment_audio(
    client: ElevenLabsTTS,
    edit_store: ShortEditStore,
    project_id: int,
    segment: EditSegment,
    segments: list[EditSegment],
    audio_directory: Path,
) -> None:
    edit = edit_store.get_edit(project_id)
    index = segments.index(segment)
    previous_text = segments[index - 1].narration if index else ""
    next_text = segments[index + 1].narration if index + 1 < len(segments) else ""
    filename = f"{index + 1:02d}_{segment.slot_key}_{narration_hash(segment.narration)[:10]}.mp3"
    context = {}
    if edit.voice_model != "eleven_v3":
        context = {"previous_text": previous_text, "next_text": next_text}
    result = client.generate(
        segment.narration,
        edit.voice_id,
        audio_directory / filename,
        model_id=edit.voice_model,
        voice_settings=edit.voice_settings,
        **context,
    )
    edit_store.save_audio(
        project_id,
        segment.slot_id,
        segment.narration,
        str(result.path),
        result.alignment,
        result.duration_ms,
    )


def render_short_editor(
    *,
    db_path: Path,
    download_root: Path,
    project: ProductionProject,
    slots: list[ProductionSlot],
    local_by_slot: dict[int, list[LocalProjectImage]],
    production_store: ProductionStore,
) -> None:
    directories = _editing_directories(download_root, project)
    edit_store = ShortEditStore(db_path)
    edit_store.ensure_project(
        project.id,
        slots,
        _initial_images(project, slots, local_by_slot, production_store),
    )
    edit = edit_store.get_edit(project.id)
    segments = edit_store.list_segments(project.id)

    st.subheader("Edición del Short")
    st.caption(
        "El guion sigue siendo creativo y supervisado. La app automatiza voz, tiempos, "
        "subtítulos, mezcla y montaje."
    )
    total_words = sum(len(segment.narration.split()) for segment in segments)
    estimated = sum(estimate_duration_seconds(segment.narration) for segment in segments)
    status_columns = st.columns(4)
    status_columns[0].metric("Fotografías", f"{sum(bool(s.image_path) for s in segments)}/{len(segments)}")
    status_columns[1].metric("Texto", f"{sum(bool(s.narration) for s in segments)}/{len(segments)}")
    status_columns[2].metric("Narraciones", f"{sum(s.audio_is_current for s in segments)}/{len(segments)}")
    status_columns[3].metric("Duración estimada", f"{estimated:.0f} s", f"{total_words} palabras")

    economic_mode = st.toggle(
        "Modo económico",
        key=f"economic_mode_{project.id}",
        help=(
            "Para guiones cerrados y una foto definitiva por escena. Sin búsquedas ni "
            "revisiones visuales repetidas: sincroniza las fotos locales y pasa a voz y montaje."
        ),
    )
    if economic_mode:
        unique_local_images = {
            slot_id: images[0]
            for slot_id, images in local_by_slot.items()
            if len(images) == 1
        }
        missing_unique_images = [
            segment.label for segment in segments if segment.slot_id not in unique_local_images
        ]
        if st.button(
            "Usar las fotografías locales definitivas",
            icon=":material/auto_awesome:",
            disabled=bool(missing_unique_images),
            width="stretch",
        ):
            for slot_id, image in unique_local_images.items():
                edit_store.set_image(project.id, slot_id, str(image.path.resolve()))
            st.success("Fotografías sincronizadas. Puedes importar el guion y generar la voz.")
            st.rerun()
        if missing_unique_images:
            st.info(
                "Para el modo económico debe haber exactamente una foto local en: "
                + ", ".join(missing_unique_images)
                + "."
            )
        else:
            st.caption(
                "Una foto por escena detectada. El botón las fija sin abrir resultados ni "
                "comparar candidatos."
            )

    st.markdown("### 1. Fotografías definitivas")
    st.caption("Si una carpeta contiene varias imágenes, elige exactamente una para el montaje.")
    photo_columns = st.columns(min(4, max(1, len(segments))))
    for index, segment in enumerate(segments):
        available = [str(item.path) for item in local_by_slot.get(segment.slot_id, [])]
        if segment.image_path and Path(segment.image_path).is_file() and segment.image_path not in available:
            available.insert(0, segment.image_path)
        with photo_columns[index % len(photo_columns)]:
            with st.container(border=True):
                st.markdown(f"**{segment.label}**")
                if available:
                    current_index = available.index(segment.image_path) if segment.image_path in available else 0
                    selected_path = st.selectbox(
                        "Fotografía",
                        available,
                        index=current_index,
                        format_func=lambda value: Path(value).name,
                        key=f"edit_image_{project.id}_{segment.slot_id}",
                        label_visibility="collapsed",
                    )
                    if selected_path != segment.image_path:
                        edit_store.set_image(project.id, segment.slot_id, selected_path)
                    st.image(selected_path, width="stretch")
                else:
                    st.error("No hay ninguna imagen local en esta parte.")

    st.markdown("### 2. Guion por fotografías")
    with st.expander("Importar un guion completo"):
        st.caption(
            "Si separas cada escena con una línea en blanco y --- se respetará exactamente. "
            "En otro caso se dividirá procurando equilibrar la duración."
        )
        with st.form(f"import_script_{project.id}"):
            full_script = st.text_area("Guion completo", height=180)
            import_clicked = st.form_submit_button("Dividir entre las fotografías")
        if import_clicked:
            pieces = split_script(full_script, len(segments))
            for segment, piece in zip(segments, pieces):
                edit_store.update_segment(
                    project.id, segment.slot_id, piece, segment.tone, segment.pause_after_ms
                )
            st.success("Guion dividido. Revisa cada escena antes de generar la voz.")
            st.rerun()

    with st.form(f"edit_script_{project.id}"):
        edited_values: dict[int, tuple[str, str, int]] = {}
        for segment in segments:
            with st.container(border=True):
                image_column, text_column = st.columns([1, 2.4])
                with image_column:
                    st.markdown(f"**{segment.position + 1}. {segment.label}**")
                    if segment.image_path and Path(segment.image_path).is_file():
                        st.image(segment.image_path, width="stretch")
                with text_column:
                    narration_key = f"narration_{project.id}_{segment.slot_id}"
                    narration_db_hash_key = f"{narration_key}_db_hash"
                    current_db_hash = narration_hash(segment.narration)
                    if st.session_state.get(narration_db_hash_key) != current_db_hash:
                        st.session_state[narration_key] = segment.narration
                        st.session_state[narration_db_hash_key] = current_db_hash
                    narration = st.text_area(
                        "Texto narrado",
                        key=narration_key,
                        height=105,
                        placeholder="Escribe aquí la parte de la historia que acompaña esta foto…",
                    )
                    controls = st.columns([1.6, 1, 1])
                    tone_options = list(TONES)
                    if segment.tone not in tone_options:
                        tone_options.append(segment.tone)
                    tone = controls[0].selectbox(
                        "Intención",
                        tone_options,
                        index=tone_options.index(segment.tone),
                        key=f"tone_{project.id}_{segment.slot_id}",
                    )
                    pause = int(
                        controls[1].number_input(
                            "Pausa final (ms)",
                            0,
                            2000,
                            segment.pause_after_ms,
                            50,
                            key=f"pause_{project.id}_{segment.slot_id}",
                        )
                    )
                    duration = estimate_duration_seconds(narration)
                    controls[2].metric("Estimación", f"{duration:.1f} s", f"{len(narration.split())} palabras")
                    edited_values[segment.slot_id] = (narration, tone, pause)
        save_script = st.form_submit_button("Guardar guion", type="primary", width="stretch")
    if save_script:
        for slot_id, (narration, tone, pause) in edited_values.items():
            edit_store.update_segment(project.id, slot_id, narration, tone, pause)
        st.success("Guion guardado.")
        st.rerun()

    st.markdown("### 3. Narrador")
    client = ElevenLabsTTS()
    settings = {**DEFAULT_VOICE_SETTINGS, **edit.voice_settings}
    with st.form(f"voice_settings_{project.id}"):
        voice_columns = st.columns([1.4, 1, 1.2])
        voice_id = voice_columns[0].text_input(
            "ID de voz de ElevenLabs",
            value=edit.voice_id or os.getenv("ELEVENLABS_VOICE_ID", ""),
            type="password",
            help="El ID no es una clave secreta, pero se oculta para mantener limpia la pantalla.",
        )
        voice_name = voice_columns[1].text_input(
            "Nombre para recordarla", value=edit.voice_name, placeholder="Narrador cálido"
        )
        model_options = ["eleven_v3", "eleven_multilingual_v2"]
        current_model = edit.voice_model if edit.voice_model in model_options else "eleven_v3"
        model = voice_columns[2].selectbox(
            "Modelo",
            model_options,
            index=model_options.index(current_model),
            format_func=lambda value: (
                "Eleven v3 · más expresivo" if value == "eleven_v3"
                else "Multilingual v2 · más estable"
            ),
            help="V3 interpreta el guion completo en una toma para mantener emoción y continuidad.",
        )
        setting_columns = st.columns(4)
        stability = setting_columns[0].slider("Estabilidad", 0.0, 1.0, float(settings["stability"]), 0.01)
        similarity = setting_columns[1].slider(
            "Similaridad", 0.0, 1.0, float(settings["similarity_boost"]), 0.01
        )
        speed = setting_columns[2].slider("Velocidad", 0.7, 1.2, float(settings["speed"]), 0.01)
        boost = setting_columns[3].checkbox(
            "Reforzar voz", value=bool(settings["use_speaker_boost"])
        )
        save_voice = st.form_submit_button("Guardar narrador")
    if save_voice:
        edit_store.set_voice(
            project.id,
            voice_id,
            voice_name,
            model,
            {
                "stability": stability,
                "similarity_boost": similarity,
                "style": 0.0,
                "use_speaker_boost": boost,
                "speed": speed,
            },
        )
        st.success("Narrador guardado.")
        st.rerun()

    if not client.configured:
        st.info(
            "La edición ya funciona sin conexión, pero para generar la voz falta añadir "
            "ELEVENLABS_API_KEY al archivo privado .env."
        )
    else:
        refreshed_edit = edit_store.get_edit(project.id)
        action_columns = st.columns(2)
        with action_columns[0]:
            if st.button(
                "Generar prueba del narrador",
                disabled=not refreshed_edit.voice_id,
                width="stretch",
            ):
                try:
                    with st.spinner("Interpretando una muestra breve…"):
                        sample = client.generate(
                            "Hubo un tiempo en que una sola fotografía podía contar toda una vida. "
                            "Y algunas miradas todavía permanecen con nosotros.",
                            refreshed_edit.voice_id,
                            directories["audio"] / "prueba_narrador.mp3",
                            model_id=refreshed_edit.voice_model,
                            voice_settings=refreshed_edit.voice_settings,
                        )
                except ElevenLabsError as exc:
                    st.error(str(exc))
                else:
                    st.audio(str(sample.path))
        with action_columns[1]:
            characters = sum(len(segment.narration) for segment in segments)
            if st.button(
                f"Generar toda la narración · {characters} caracteres",
                disabled=not refreshed_edit.voice_id or any(not s.narration for s in segments),
                type="primary",
                width="stretch",
            ):
                progress = st.progress(0, text="Preparando narración…")
                try:
                    if refreshed_edit.voice_model == "eleven_v3":
                        progress.progress(
                            0.15, text="Arconte está interpretando el guion completo…"
                        )
                        generate_v3_narration(
                            client=client,
                            edit_store=edit_store,
                            project_id=project.id,
                            segments=segments,
                            output_directory=directories["audio"],
                        )
                    else:
                        for index, segment in enumerate(segments):
                            progress.progress(
                                index / len(segments), text=f"Narrando {segment.label}…"
                            )
                            _generate_segment_audio(
                                client,
                                edit_store,
                                project.id,
                                segment,
                                segments,
                                directories["audio"],
                            )
                except (ElevenLabsError, NarrationPipelineError) as exc:
                    st.error(str(exc))
                else:
                    progress.progress(1.0, text="Narración terminada.")
                    st.rerun()

    segments = edit_store.list_segments(project.id)
    if any(segment.audio_path for segment in segments):
        st.markdown("#### Escuchar y regenerar escenas")
        for segment in segments:
            if not segment.audio_is_current or not Path(segment.audio_path).is_file():
                continue
            audio_column, action_column = st.columns([4, 1])
            with audio_column:
                st.caption(segment.label)
                st.audio(segment.audio_path)
            with action_column:
                if st.button(
                    "Regenerar",
                    key=f"regenerate_{project.id}_{segment.slot_id}",
                    disabled=not client.configured or not edit.voice_id,
                    width="stretch",
                ):
                    try:
                        with st.spinner(f"Regenerando {segment.label}…"):
                            _generate_segment_audio(
                                client,
                                edit_store,
                                project.id,
                                segment,
                                segments,
                                directories["audio"],
                            )
                    except ElevenLabsError as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()

    st.markdown("### 4. Música y montaje")
    music_files = sorted(
        path.resolve()
        for path in directories["music"].iterdir()
        if path.is_file() and path.suffix.casefold() in MUSIC_EXTENSIONS
    )
    music_options = ["", *[str(path) for path in music_files]]
    current_music_index = music_options.index(edit.music_path) if edit.music_path in music_options else 0
    music_columns = st.columns([2, 1])
    music_key = f"music_{project.id}"
    music_volume_key = f"music_volume_{project.id}"
    if st.session_state.get(music_key) not in music_options:
        st.session_state[music_key] = music_options[current_music_index]
    if music_volume_key not in st.session_state:
        st.session_state[music_volume_key] = float(edit.music_volume_db)
    selected_music = music_columns[0].selectbox(
        "Música",
        music_options,
        format_func=lambda value: "Sin música" if not value else Path(value).name,
        key=music_key,
    )
    music_volume = music_columns[1].slider(
        "Volumen bajo la voz (dB)", -35.0, -12.0, key=music_volume_key
    )
    if selected_music != edit.music_path or music_volume != edit.music_volume_db:
        edit_store.set_music(project.id, selected_music, music_volume)
    st.caption(f"Para añadir pistas, cópialas en: {directories['music']}")

    segments = edit_store.list_segments(project.id)
    ready = bool(segments) and all(
        segment.image_path
        and Path(segment.image_path).is_file()
        and segment.audio_is_current
        and Path(segment.audio_path).is_file()
        for segment in segments
    )
    renderer = ShortVideoRenderer()
    if st.button(
        "Crear Short final",
        type="primary",
        disabled=not ready or not renderer.configured,
        width="stretch",
    ):
        try:
            with st.spinner("Montando imágenes, narración, subtítulos y música…"):
                result = renderer.render(
                    segments,
                    directories["render"],
                    music_path=selected_music,
                    music_volume_db=music_volume,
                )
                edit_store.save_output(project.id, str(result.final_path))
        except RenderError as exc:
            st.error(str(exc))
        else:
            st.success(f"Short terminado · {result.duration_seconds:.1f} segundos")
            st.rerun()

    current_output = edit_store.get_edit(project.id).output_path
    if current_output and Path(current_output).is_file():
        st.markdown("#### Resultado")
        st.video(current_output)
        st.success(f"Vídeo final: {current_output}")
        no_music = directories["render"] / "short_final_sin_musica.mp4"
        if no_music.is_file() and no_music.resolve() != Path(current_output).resolve():
            st.caption(f"Versión sin música: {no_music}")

    if not ready:
        missing = []
        for segment in segments:
            if not segment.image_path or not Path(segment.image_path).is_file():
                missing.append(f"foto de {segment.label}")
            if not segment.narration:
                missing.append(f"texto de {segment.label}")
            elif not segment.audio_is_current or not Path(segment.audio_path).is_file():
                missing.append(f"voz de {segment.label}")
        if missing:
            st.info("Antes de montar falta: " + ", ".join(missing) + ".")

    st.caption(f"Archivos de edición: {directories['base']}")
