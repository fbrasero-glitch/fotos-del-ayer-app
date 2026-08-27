# Fotos del Ayer · Editor de Shorts

Para producir y publicar nuevos vídeos con el mismo criterio del canal, consulta
primero `FLUJO_SHORTS_CANAL.md`. Resume el montaje de referencia, los controles
de calidad, la publicación y la regla especial de portadas de Shorts.

Inicia la aplicación habitual:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app_projects.py
```

Streamlit mostrará dos páginas en la navegación: el buscador de proyectos y
`Editar Short`. Ambas comparten la misma base de datos.

## Flujo

1. Confirma una fotografía local por cada parte de la historia.
2. Importa un guion completo o edita el texto de cada fotografía.
3. Guarda un ID de narrador de ElevenLabs y ajusta su interpretación.
4. Genera una prueba o toda la narración únicamente cuando quieras consumir créditos.
5. Copia música con licencia a `edicion/musica` si quieres una versión musical.
6. Crea el vídeo vertical con subtítulos y mezcla normalizada.

Cada proyecto guarda su edición en `proyectos_fotos/<proyecto>/edicion/`:

- `narracion`: prueba del narrador y voz por escenas.
- `musica`: pistas que añadas manualmente.
- `render`: SRT, ASS y vídeos finales con y sin música.

## Clave de voz

Añade `ELEVENLABS_API_KEY` al archivo privado `.env`. Puedes añadir también
`ELEVENLABS_VOICE_ID` como valor inicial. La aplicación nunca genera audio al
abrir una pantalla: cada consumo requiere pulsar un botón explícito.
