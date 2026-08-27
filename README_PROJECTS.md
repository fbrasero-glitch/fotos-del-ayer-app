# Fotos del Ayer · Proyectos

La referencia visual permanente para todos los proyectos está en [`PREFERENCIAS_FOTOS_MOVIL.md`](PREFERENCIAS_FOTOS_MOVIL.md). La guía de producción completa está en [`FLUJO_SHORTS_CANAL.md`](FLUJO_SHORTS_CANAL.md).

## Iniciar

```powershell
.\.venv\Scripts\python.exe -m streamlit run app_projects.py
```

## Flujo

1. Crea o selecciona un proyecto.
2. Trabaja primero en **Gancho**.
3. Añade las escenas necesarias desde la barra lateral.
4. Busca en una fuente. La consulta y todos sus resultados quedan guardados.
5. Si repites exactamente la consulta, la aplicación abre el historial y consume 0 créditos.
6. Añade varias fotos a **Candidatas** y elige una para esa parte del vídeo.
7. Pulsa **Comprobar uso y riesgos** únicamente en las fotografías que te interesan.
8. Descarga la elegida en la carpeta correspondiente.
9. Elige una fotografía final nueva o reutiliza una candidata como **Foto final**.

## Carpetas

Las imágenes se descargan dentro de `proyectos_fotos/<nombre-del-proyecto>/`:

- `01-gancho`
- `02-escena-1-...`
- `03-escena-2-...`
- `99-foto-final`

## Derechos

El informe consulta los metadatos disponibles y el texto accesible de la página original. Es una evaluación orientativa, no una garantía jurídica. Un resultado de Brave o Google descubre una imagen, pero no concede permiso para publicarla.
