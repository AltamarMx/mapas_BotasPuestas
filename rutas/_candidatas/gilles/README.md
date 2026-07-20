# Candidatas extraídas de Gilles

Esta carpeta contiene **194 candidatas** generadas desde los elementos
`<rte>` legacy. No forman parte del catálogo activo porque están dos niveles debajo de `rutas/`
y todos sus manifiestos declaran `publicada: false`.

Los datos muestran que muchas entradas son tramos de una red: 56
miden menos de 500 m y 144 menos de 2 km. Antes de publicar hay que
decidir si cada candidata es un recorrido completo, un tramo reutilizable o material descartable.

## Archivos

- `indice.csv`: cola de revisión con métricas y alertas automáticas.
- `extraccion.json`: procedencia, hashes y resumen de integridad.
- `<id>/ruta.gpx`: geometría limpia, convertida de `<rte>` a `<trk>/<trkseg>`.
- `<id>/ruta.yml`: metadatos editoriales pendientes de curación.

No ejecutes una extracción encima de esta carpeta: el script se niega a sobrescribirla. Para
comprobar que sigue correspondiendo a las fuentes usa:

```bash
uv run python scripts/extract_legacy_routes.py --check
```

El flujo completo y las decisiones pendientes están en `docs/flujo-de-datos-rutas.md`.
