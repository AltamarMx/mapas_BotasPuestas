# Flujo de datos para rutas

## Objetivo

La geometría, la curaduría editorial y los artefactos de la app deben ser capas separadas. Una
ruta nueva se incorpora agregando una carpeta autocontenida; la interfaz nunca mantiene una lista
manual de rutas.

```text
fuente original → candidata + elevación reproducible → catálogo generado → app
```

## Hallazgos de la fuente Gilles

Los dos GPX legacy contienen las mismas 194 geometrías `<rte>` en el mismo orden. Seis nombres
difieren solamente por sufijos. No hay geometrías exactamente duplicadas, ni siquiera al comparar
el sentido inverso.

Estas entradas no equivalen necesariamente a 194 excursiones completas:

- 56 miden menos de 500 m;
- 144 miden menos de 2 km;
- la mediana es 1.115 km;
- muchos nombres contienen `conexión`;
- solo 27 tienen elevación completa en el GPX; los otros 167 se complementan con SRTM sin
  modificar la fuente;
- sus marcas de tiempo son principalmente horas de edición repetidas, no tiempos de recorrido.

Por ello se extraen como **candidatas**. La curaduría debe decidir si cada una es un recorrido
completo, un tramo reutilizable para construir recorridos o material descartable.

## Capas y ubicaciones

```text
rutas/
├── GPX-Gilles legacy/          # fuentes originales, inmutables
├── _candidatas/
│   └── gilles/                   # salida reproducible, fuera del catálogo activo
│       ├── indice.csv
│       ├── extraccion.json
│       ├── elevacion-dem.json     # SRTM por punto, ligado al hash de la geometría
│       └── gilles-001-.../
│           ├── ruta.gpx
│           └── ruta.yml
└── <id-publicado>/            # una ruta curada, descubierta por la app
    ├── ruta.gpx
    ├── ruta.yml
    └── fotos/
```

El importador actual solo descubre `rutas/*/ruta.yml`. Las candidatas están dos niveles abajo y,
además, declaran `publicada: false`, así que extraerlas no modifica la app publicada.

## Contrato de una candidata

`ruta.gpx` contiene un solo `<trk>` con un `<trkseg>`. El extractor conserva coordenadas y
elevación disponible, pero elimina extensiones Garmin, nombres artificiales de puntos y marcas de
tiempo de edición.

`ruta.yml` es la parte editorial y se puede editar después de extraer:

```yaml
version_esquema: 1
id: gilles-001-aire-a-meztitla
titulo: Aire-a Meztitla
archivo_gpx: ruta.gpx
zona_horaria: America/Mexico_City
actividad: senderismo
region: Tepoztlán, Morelos
descripcion: ""
publicada: false
tipo_registro: por-definir  # recorrido | tramo | descartar

clasificacion_editorial:
  criterio_1: null
  criterio_2: null
  criterio_3: null

fotos_estrategicas: []

revision:
  estado: pendiente
  nombre: pendiente
  geometria: pendiente
  clasificacion: pendiente
  resena: pendiente
  fotos: pendiente
```

`indice.csv` es una cola de trabajo generada, no la fuente editorial. Incluye distancia, puntos,
cobertura de elevación, hash de geometría y alertas como `tramo-muy-corto`, `posible-conector` o
`nombre-con-sufijo-numerico`.

## Elevación reproducible

`elevacion-dem.json` contiene únicamente los 167 tramos cuyo GPX no incluía elevación. Cada
registro guarda una elevación por punto y el `sha256` de su geometría. `build_content.py` conserva
los 27 perfiles GPX originales, aplica SRTM a los restantes y detiene el build si una geometría ya
no coincide con el caché.

El generador usa [NASA SRTMGL1 v3](https://data.nasa.gov/dataset/nasa-shuttle-radar-topography-mission-global-1-arc-second-v003-e47e1),
distribuido como teselas `skadi` por [AWS Open Data Terrain Tiles](https://registry.opendata.aws/terrain-tiles/),
con resolución de un arco-segundo y muestreo bilineal. Las teselas binarias se guardan localmente
en `.cache/srtm/` y no se versionan; el JSON sí registra la URL y el hash SHA-256 de cada una.

## Clasificación de tres criterios

Los tres campos se dejan sin semántica definitiva para no fijar una decisión prematura. Una
hipótesis útil para discutir es:

1. **Exigencia física:** distancia, desnivel y duración esperada. Puede apoyarse en cálculos.
2. **Dificultad técnica:** tipo de piso, pendientes, uso de manos y obstáculos. Requiere revisión.
3. **Navegación y compromiso:** claridad del camino, exposición y facilidad de retirada. Requiere
   criterio editorial.

Antes de rehacer la interfaz conviene definir para cada eje una escala corta, mutuamente
comprensible y estable. En ese momento `criterio_1..3` deben migrarse una sola vez a identificadores
semánticos; la app mostrará etiquetas configurables, no esos identificadores internos.

## Reseña y fotografías estratégicas

La reseña vive en `descripcion`. Debe explicar qué hace distintiva a la ruta, condiciones,
orientación y advertencias; no debe repetir las métricas calculadas.

Las fotos siguen dentro de la carpeta de la ruta. Para la futura app, la asociación explícita puede
usar una lista ordenada como esta:

```yaml
fotos_estrategicas:
  - archivo: fotos/inicio.jpg
    rol: inicio
    orden: 10
    descripcion: Acceso junto al camino principal.
    texto_alt: Inicio del sendero junto al camino principal
  - archivo: fotos/desvio-norte.jpg
    rol: desvio
    orden: 20
    descripcion: Tomar el ramal de la izquierda.
    texto_alt: Bifurcación donde la ruta continúa a la izquierda
```

Roles iniciales posibles: `portada`, `inicio`, `desvio`, `terreno`, `hito`, `panorama` y `llegada`.
La selección debe ser breve y funcional; los originales conservan EXIF y el proceso genera copias
web sin metadatos adicionales.

## Flujo de curaduría

1. Ejecutar la extracción una sola vez.
2. Revisar `indice.csv`, empezando por alertas y tramos largos.
3. Marcar `tipo_registro` como `recorrido`, `tramo` o `descartar`.
4. Corregir el nombre y verificar la geometría en el mapa.
5. Completar los tres criterios y una reseña.
6. Añadir pocas fotos con función explícita y texto alternativo.
7. Cambiar cada estado de `revision` a `aprobada`.
8. Para publicar un recorrido, mover su carpeta directamente bajo `rutas/` y cambiar
   `publicada: true`.
9. Ejecutar validación, pruebas y generación. El catálogo resultante alimentará el selector futuro.

Un `tramo` no debería aparecer como tarjeta independiente. En una fase posterior se puede definir
un recorrido como una secuencia de ids de tramo, sin duplicar sus geometrías.

## Comandos reproducibles

Primera extracción:

```bash
uv run python scripts/extract_legacy_routes.py
```

Generación de elevación después de una extracción nueva o de cambiar geometrías:

```bash
uv run python -m scripts.build_elevation_cache
```

Validación y generación de los 194 perfiles usados por la app:

```bash
uv run python scripts/build_content.py --check
uv run python scripts/build_content.py
```

Comprobación posterior, sin modificar archivos:

```bash
uv run python scripts/extract_legacy_routes.py --check
```

El extractor se niega a sobrescribir la salida existente para proteger cualquier curaduría manual.
Para demostrar reproducibilidad se puede extraer a otra carpeta con `--output` y comparar hashes.
