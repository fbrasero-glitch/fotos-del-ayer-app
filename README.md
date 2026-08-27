# Fotos de Ayer · Fase 1

Fotos de Ayer es un agregador local de búsqueda de fotografías para investigación visual.

La aplicación no interpreta un guion ni decide el montaje final. El usuario define búsquedas manuales, revisa toda la batería encontrada y marca las imágenes que le interesan.

## Entrada

- Personaje.
- Alias.
- Una lista de búsquedas manuales, una por línea.

Ejemplo:

```text
foto gancho joven
foto coche enfadada
foto dentro coche
foto corriendo
foto gimnasio
foto sola mar
```

Gemini reformula cada línea de forma literal al inglés, sin inventar escenas. Todas las consultas conservan el nombre del personaje y Wikidata mantiene la identidad resuelta.

## Vistas

- **Todas:** pool general y coincidencias específicas.
- **Por búsqueda:** solo resultados obtenidos para esa línea o sus alternativas.
- **Seleccionadas:** favoritas y candidatas para vídeo.
- **Descartadas:** fotografías apartadas que pueden recuperarse.

Cada tarjeta muestra miniatura, fuente, título, URL original, relevancia, motivo y derechos. Puede marcarse como favorita, descartada o candidata para vídeo.

## Resultados y paginación

No existe un límite de presentación tipo “top”. Si la colección contiene 42 fotografías, las 42 quedan disponibles.

La paginación admite 20, 50 o 100 resultados por página. El control lateral permite solicitar entre 10 y 100 resultados por consulta y fuente; el valor inicial es 20.

## Fuentes

Orden prioritario:

1. Bing Images
2. Google Images
3. Wikimedia Commons
4. Europeana
5. Flickr Commons
6. Pinterest como descubrimiento

Bing, Google y Flickr requieren sus claves. Pinterest utiliza Bing o Google y no puede marcarse como candidata final hasta localizar la fuente original.

## Derechos

La licencia nunca elimina fotografías en esta fase. Se muestra aparte como:

- `Conocida · <licencia>`
- `Revisar derechos`
- `Revisar derechos · solo descubrimiento`

La clasificación es orientativa. Siempre debe abrirse la URL original antes de publicar.

## Velocidad y deduplicación

- Solo se cargan miniaturas al investigar.
- Los proveedores trabajan en paralelo.
- Las respuestas se guardan siete días en SQLite.
- Las reformulaciones Gemini se guardan treinta días.
- Las huellas perceptuales de miniaturas se reutilizan.
- Se agrupan URLs idénticas e imágenes visualmente muy parecidas.
- Las imágenes originales no se descargan automáticamente.

## Configuración

```dotenv
EUROPEANA_API_KEY=
GEMINI_API_KEY=
BING_IMAGE_API_KEY=
GOOGLE_SEARCH_API_KEY=
GOOGLE_SEARCH_ENGINE_ID=
FLICKR_API_KEY=
GEMINI_MODEL=
```

`GEMINI_MODEL` es opcional. El valor predeterminado es `gemini-3.1-flash-lite`.

Las claves solo deben almacenarse en `.env`, que está incluido en `.gitignore`.

## Ejecutar

```powershell
cd "C:\AA CANALES\Canal fotos virales\mecanización"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m streamlit run app_projects.py
```

Después abre [http://localhost:8501](http://localhost:8501).

## Limitaciones actuales

- Sin claves de Bing o Google, las búsquedas periodísticas concretas pueden tener pocos resultados.
- Wikimedia aporta un pool fiable del personaje, pero raramente contiene paparazzi, gimnasio o escenas privadas.
- Europeana se orienta a patrimonio cultural y puede devolver poco material de celebridades recientes.
- La relevancia se basa en identidad, consulta y metadatos. Las fuentes comerciales pueden necesitar verificación visual manual.
- La deduplicación perceptual es conservadora para evitar unir fotografías distintas.
