# Fotos del Ayer · Búsqueda manual por escenas

Esta entrada añade un flujo específico para construir un Short fotográfico:

1. Busca una fotografía gancho.
2. Añade una escena manual cada vez.
3. Consulta primero Wikimedia, Europeana, Flickr y Pexels.
4. Brave y Google Images mediante SerpAPI se activan con botones separados.
5. El cribado visual local con Qwen2.5-VL se limita a los ocho mejores candidatos.
6. El usuario elige exactamente una fotografía por escena.

## Seguridad de las claves

Las claves se leen exclusivamente desde `.env`, que ya está excluido del control
de versiones. Usa `.env.story.example` como lista de variables, pero no escribas
secretos en archivos de ejemplo, capturas o conversaciones.

## Límites locales

- Pexels: 18.000 consultas al mes.
- Brave: 900 consultas al mes.
- SerpAPI: 200 consultas al mes.

Estos márgenes son deliberadamente inferiores a las cuotas iniciales. La base de
datos local registra únicamente llamadas reales: los aciertos de caché no consumen
el contador. Brave no usa la caché persistente.

## Ejecutar

```powershell
.\.venv\Scripts\python.exe -m streamlit run app_story.py
```

La aplicación anterior permanece disponible en `app.py` como respaldo.

## Visión local

La aplicación usa Ollama en `http://localhost:11434` y el modelo `qwen2.5vl:7b` por
defecto. Si Ollama no está disponible, el botón de cribado puede usar Gemini cuando
`GEMINI_API_KEY` esté configurada. Para forzar Gemini, define `VISION_BACKEND=gemini`
en `.env`. La instalación inicial del modelo es:

```powershell
ollama pull qwen2.5vl:7b
```
