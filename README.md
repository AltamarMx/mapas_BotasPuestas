# Botas Puestas

Explorador web de recorridos, altimetría y fotografías geolocalizadas. La aplicación está hecha con Shiny para Python, se ejecuta en el navegador mediante Shinylive/WebAssembly y se publica como sitio estático en GitHub Pages.

## Inicio rápido

El proyecto usa `uv` como único gestor de dependencias.

```bash
uv sync --locked
uv run python scripts/build_content.py
uv run shiny run --reload app/app.py
```

La app local queda disponible en la URL que imprime Shiny. Los archivos de `app/_generated/` y `app/www/generated/` se reconstruyen a partir de `rutas/` y no se guardan en Git.

Comprobaciones completas:

```bash
uv run ruff check .
uv run pytest
uv run shinylive export app site
uv run python scripts/version_shinylive_export.py site
uv run python -m http.server --directory site 8008
```

No abras `site/index.html` directamente con `file://`: Shinylive necesita que los archivos se sirvan por HTTP.

## Primera publicación de este repositorio

El repositorio remoto es `https://github.com/AltamarMx/mapas_BotasPuestas`. Desde esta carpeta, publica todos los archivos fuente con:

```bash
git add .
git commit -m "Implementa explorador de rutas con Shinylive"
git remote add origin https://github.com/AltamarMx/mapas_BotasPuestas.git
git push -u origin main
```

No ejecutes `echo "# mapas_BotasPuestas" >> README.md`: este README ya contiene la documentación del proyecto. Si `origin` ya existiera, compruébalo con `git remote -v` en vez de volver a agregarlo.

En GitHub, abre **Settings → Pages → Build and deployment** y selecciona **GitHub Actions** como fuente. El workflow `Validar y publicar GitHub Pages` aparecerá en la pestaña **Actions** y publicará el mapa en:

<https://altamarmx.github.io/mapas_BotasPuestas/>

No subas `site/` manualmente ni crees una rama `gh-pages`; el workflow genera y despliega ese artefacto.

### Si el navegador muestra una versión anterior

Una ventana privada empieza con caché limpio, por eso puede mostrar el despliegue nuevo antes que una ventana normal. Para corregir la sesión actual:

1. abre las herramientas de desarrollo del navegador;
2. busca **Application/Storage → Clear site data** (o elimina los datos de `altamarmx.github.io` desde Privacidad);
3. recarga la página con `Cmd+Shift+R` en macOS o `Ctrl+Shift+R` en Windows/Linux.

El workflow aplica además una huella del contenido al bundle de Shinylive. Cada publicación genera una URL distinta para los datos de la app, de modo que los siguientes cambios de rutas, fotos, CSS o Python no reutilicen el bundle anterior. Después de incorporar esta mejora hay que limpiar una sola vez la caché que ya estaba guardada.

## Cómo ingerir una ruta nueva

No hay que editar `app.py` ni agregar la ruta a una lista central. Cada recorrido es una carpeta autocontenida dentro de `rutas/`.

### 1. Elegir una clave estable

Crea una carpeta cuyo nombre sea un identificador en minúsculas y `kebab-case`:

```text
rutas/nevado-toluca-2026/
```

La clave formará parte de la URL (`?ruta=nevado-toluca-2026`). No conviene cambiarla una vez publicada.

### 2. Copiar el GPX

Guarda el recorrido como `ruta.gpx`:

```text
rutas/nevado-toluca-2026/ruta.gpx
```

Se aceptan GPX 1.0 y 1.1 con uno o más `trkseg`. Cada punto debe contener latitud y longitud. Para mostrar todas las funciones se recomienda que también incluya:

- `<ele>` para perfil, ascenso y descenso;
- `<time>` para fecha, duración, tiempo en movimiento e interpolación temporal.

Los segmentos separados no se unen con una distancia artificial.

### 3. Crear `ruta.yml`

Añade el manifiesto junto al GPX:

```yaml
version_esquema: 1
id: nevado-toluca-2026
titulo: Nevado de Toluca 2026
archivo_gpx: ruta.gpx
zona_horaria: America/Mexico_City
actividad: senderismo
region: Estado de México
descripcion: Recorrido alrededor del cráter.
portada: fotos/IMG_1001.HEIC
publicada: true
```

Campos obligatorios:

- `version_esquema`: actualmente debe ser `1`;
- `id`: debe coincidir exactamente con el nombre de la carpeta;
- `titulo`;
- `archivo_gpx`;
- `zona_horaria`: nombre IANA, por ejemplo `America/Mexico_City`.

Campos opcionales:

- `actividad`, `region` y `descripcion`;
- `portada`: ruta relativa a una foto;
- `publicada`: usa `false` para validar y conservar una ruta sin mostrarla en el sitio.

### 4. Agregar fotografías con EXIF

Crea `fotos/` dentro de la ruta y copia allí los archivos originales:

```text
rutas/nevado-toluca-2026/
├── ruta.gpx
├── ruta.yml
└── fotos/
    ├── IMG_1001.HEIC
    └── IMG_1002.jpg
```

Formatos admitidos como fuente: JPEG (`.jpg`, `.jpeg`) y HEIF/HEIC (`.heif`, `.heic`). El contenido real se detecta al abrirlo; también se toleran fotografías HEIC que hayan recibido por error una extensión `.jpeg`.

Cada foto nueva debe entregarse con estos metadatos EXIF:

- `DateTimeOriginal`: fecha y hora de captura;
- `OffsetTimeOriginal`: desfase horario, por ejemplo `-06:00`; si falta, se usa `zona_horaria` de `ruta.yml`;
- `GPSLatitude` y `GPSLatitudeRef`;
- `GPSLongitude` y `GPSLongitudeRef`;
- `GPSAltitude` es recomendable, pero no obligatorio.

Usa los originales de la cámara o teléfono. Aplicaciones de mensajería, redes sociales y algunos editores suelen eliminar EXIF.

El procesador:

1. lee fecha, zona horaria, GPS y orientación;
2. comprueba que la foto esté razonablemente cerca del track;
3. conserva el archivo fuente intacto, con todo su EXIF;
4. crea una miniatura y una copia web en JPEG;
5. elimina EXIF de las copias públicas y guarda únicamente fecha/coordenadas necesarias en el JSON de la app.

Así, el repositorio conserva los originales completos, pero GitHub Pages no publica metadatos adicionales del dispositivo o la cámara.

#### Correcciones o textos opcionales

Con EXIF completo no necesitas `fotos.csv`. Úsalo solamente para corregir datos o añadir descripción y texto alternativo:

```csv
archivo,fecha_hora,latitud,longitud,descripcion,texto_alt
fotos/IMG_1001.HEIC,,,,Vista desde el cráter,Vista panorámica desde el Nevado de Toluca
```

Los valores no vacíos de CSV tienen prioridad sobre EXIF. Excepcionalmente, si una imagen tiene hora pero no GPS, el procesador puede interpolar la posición sobre el GPX; el flujo normal del proyecto sigue siendo entregar las fotos con EXIF completo.

### 5. Validar antes de generar

Desde la raíz del repositorio:

```bash
uv run python scripts/build_content.py --check
```

La validación no escribe archivos. Comprueba manifiestos, GPX, fechas, coordenadas, EXIF, asociación espacial y claves duplicadas. Una salida correcta se parece a:

```text
Rutas válidas: 3
- nevado-toluca-2026: 14.20 km, +740/-740 m, 12 fotos, Exigente
Validación terminada; no se escribieron artefactos.
```

Después ejecuta las pruebas:

```bash
uv run pytest
```

### 6. Generar y revisar localmente

```bash
uv run python scripts/build_content.py
uv run shiny run --reload app/app.py
```

Revisa:

- título, fecha y región;
- ajuste del mapa y forma del track;
- distancia, ascenso y descenso;
- orden y posición de las fotos;
- orientación de las imágenes y sus fechas;
- diseño en escritorio y móvil.

### 7. Publicar

Haz commit de la carpeta fuente, `ruta.yml`, GPX y fotos originales. No agregues `app/_generated/`, `app/www/generated/` ni `site/`; están ignorados porque GitHub Actions los vuelve a crear.

Al hacer push a `main`, el workflow:

1. sincroniza dependencias con `uv sync --locked`;
2. ejecuta lint y pruebas;
3. valida y procesa las rutas;
4. exporta la app con Shinylive;
5. asigna al bundle una URL basada en su contenido para invalidar cachés anteriores;
6. despliega el artefacto en GitHub Pages.

## Errores frecuentes de ingestión

| Mensaje o síntoma | Solución |
| --- | --- |
| `id ... debe coincidir con la carpeta` | Iguala `id` y el nombre del directorio. |
| Foto sin fecha | Entrega el original con `DateTimeOriginal` o corrige `fecha_hora` en CSV. |
| Foto sin GPS | Entrega el original con GPS; como excepción, una hora dentro del GPX permite interpolar. |
| Foto demasiado lejos del track | Comprueba que pertenece a la ruta o corrige coordenadas. |
| Perfil “sin datos” | El GPX no contiene `<ele>`. |
| Duración “sin datos” | El GPX no contiene `<time>`. |
| HEIC no reconocido | Conserva `.heic`/`.heif`; `pillow-heif` se instala mediante `uv sync`. |
| La app no refleja cambios | Vuelve a ejecutar `scripts/build_content.py` antes de iniciar Shiny. |

## Cómo se calculan las métricas

- Distancia: Haversine dentro de cada segmento GPX.
- Perfil: elevación interpolada cada 20 m y mediana móvil de cinco muestras.
- Ascenso/descenso: suma sobre el mismo perfil filtrado mostrado en pantalla.
- Tiempo en movimiento: intervalos de hasta cinco minutos con velocidad entre 1 y 12 km/h.
- Esfuerzo: `distancia_km + ascenso_m / 100`, combinado con el tiempo en movimiento.

La clasificación es una estimación física, no una evaluación de dificultad técnica, clima o seguridad.

## Privacidad

GitHub Pages es público. El artefacto publicado contiene las líneas de ruta, fechas, posiciones y copias web de las fotos. Si el repositorio también es público, cualquier persona podrá descargar además los GPX y originales con EXIF. Revisa el punto de inicio/final y el contenido de las fotografías antes de hacer push.

Para decisiones técnicas y fases posteriores, consulta [PLAN.md](PLAN.md).
