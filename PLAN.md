# Plan de implementación: explorador de rutas Botas Puestas

Estado: MVP implementado localmente; pendiente primer despliegue en GitHub Pages

Última revisión: 2026-07-20

## 1. Objetivo

Construir en `app/` una aplicación web pública que permita:

- descubrir y seleccionar recorridos guardados en `rutas/`;
- ver cada recorrido sobre un mapa interactivo;
- consultar distancia, duración, ascenso positivo, descenso y una clasificación de esfuerzo;
- ver el perfil de elevación a lo largo de la distancia;
- colocar fotos sobre el mapa y mostrar su fecha, descripción y vista previa;
- publicar todo en GitHub Pages sin mantener un servidor.

El contenido se agregará mediante archivos en Git. La primera versión no tendrá un editor, carga de archivos ni base de datos dentro de la propia aplicación.

## 2. Auditoría del repositorio actual

El estado inicial condiciona algunas decisiones:

- `app/` contiene el MVP Shiny, estilos, configuración y artefactos generados durante el build.
- El proyecto declara Python 3.13, gestiona dependencias con `uv` y conserva versiones exactas en `uv.lock`.
- Hay dos archivos GPX 1.1 producidos por Strava. Ambos contienen coordenadas, elevación y hora en cada punto.
- Hay nueve originales en `rutas/milpa-alta-santo-domingo-2026/fotos/`, con un peso total aproximado de 31 MB. Siete son JPEG y dos son HEIF/HEIC aunque llegaron con extensión `.jpeg`.
- Las nueve fotos contienen `DateTimeOriginal`, zona horaria `-06:00`, coordenadas GPS y altitud EXIF.
- Todas fueron tomadas el 2026-01-25 entre las 09:38 y 16:51, dentro del intervalo local del GPX de Milpa Alta a Santo Domingo.
- La comparación espacial confirma la asociación: cada foto está a entre 0.4 y 11.1 m del punto más cercano del track; comparada con la posición interpolada a su hora exacta, la desviación está entre 0.9 y 27.4 m.

Diagnóstico preliminar de los GPX, antes de implementar y fijar el algoritmo definitivo:

| Archivo | Puntos | Distancia aprox. | Intervalo UTC | Desnivel filtrado aprox. |
| --- | ---: | ---: | --- | ---: |
| `rutas/2026-07-19-morning-hike/ruta.gpx` | 8,739 | 6.78 km | 2026-07-19 15:04–17:40 | +192 / -192 m |
| `rutas/milpa-alta-santo-domingo-2026/ruta.gpx` | 8,228 | 23.75 km | 2026-01-25 13:58–23:53 | +147 / -1,008 m |

Estas cifras son una comprobación inicial. Los valores publicados serán calculados de forma determinista por el procesador descrito abajo y cubiertos por pruebas.

## 3. Decisiones de arquitectura

### 3.1 Aplicación estática con Python en el navegador

Se usará **Shiny para Python en modo Core**, exportado con **Shinylive**. Shinylive ejecuta Python mediante Pyodide/WebAssembly en el navegador y genera archivos estáticos compatibles con GitHub Pages. No habrá proceso Python, websocket, API ni base de datos en el servidor.

Consecuencias:

- cada visitante ejecuta la lógica reactiva localmente;
- el sitio escala como contenido estático y no tiene costo de servidor;
- la primera carga incluye el entorno WebAssembly y será más lenta que una página HTML pequeña;
- los paquetes disponibles en el navegador están limitados por Pyodide/Shinylive;
- el sitio no podrá guardar cambios permanentes: una ruta nueva requiere commit, build y despliegue;
- rutas, fotos y código enviados al sitio son descargables por cualquier visitante.

La documentación oficial confirma el flujo `shinylive export <app> <site>` y el despliegue en hosts estáticos como GitHub Pages: <https://shiny.posit.co/py/get-started/shinylive.html>.

### 3.2 Preprocesamiento durante el build

Los GPX no se analizarán desde cero en cada navegador. Un script Python ejecutado localmente y en GitHub Actions:

1. validará la estructura de `rutas/`;
2. leerá GPX, YAML, CSV y EXIF;
3. calculará métricas y geolocalización de fotos;
4. simplificará geometrías y perfiles;
5. optimizará las imágenes para web;
6. generará JSON determinista consumido por la app.

Esto mantiene ligero el trabajo dentro de WebAssembly y separa datos fuente de datos de presentación.

### 3.3 Tecnologías elegidas

| Necesidad | Elección | Motivo |
| --- | --- | --- |
| UI reactiva | `shiny` para Python, API Core | Estructura explícita y fácil de probar/modularizar |
| Ejecución estática | `shinylive` + Pyodide/WASM | Compatible con GitHub Pages, sin backend |
| Mapa | `ipyleaflet` mediante `shinywidgets` | Integración oficial con Shiny para Python y soporte de GeoJSON/marcadores |
| Altimetría | `plotly` mediante `shinywidgets` | Perfil interactivo con hover, zoom y selección |
| Procesamiento GPX | biblioteca estándar (`xml.etree`, `datetime`, `math`) | Pocas dependencias y control explícito del cálculo |
| Metadatos | YAML para la ruta y CSV opcional para fotos | Legibles y fáciles de editar en Git |
| Imágenes/EXIF | `Pillow` | Orientación, EXIF y creación de versiones web |
| Dependencias | `uv`, `pyproject.toml` y `uv.lock` | Un solo flujo reproducible en local y CI |
| Validación/pruebas | `pytest` y `ruff` | Pruebas unitarias, de contenido y calidad estática |
| Despliegue | GitHub Actions + GitHub Pages | Build verificable y publicación automática |

`ipyleaflet` y Plotly tienen componentes documentados para Shiny mediante `shinywidgets`: <https://shiny.posit.co/py/components/outputs/map-ipyleaflet/> y <https://shiny.posit.co/py/components/outputs/plot-plotly/>.

No se instalarán paquetes con `pip`. Se agregarán con `uv add`/`uv add --dev`, se confirmará `uv.lock` en Git y CI usará `uv sync --locked` y `uv run`. `app/requirements.txt`, si Shinylive lo necesita para descubrir paquetes de Pyodide, será un archivo generado o verificado desde la configuración del proyecto; no será un segundo mecanismo manual de instalación.

### 3.4 Mapa base y claves

La primera versión usará el mapa raster estándar de OpenStreetMap y **no necesitará una clave**. Se mostrará siempre la atribución visible y no se implementará descarga masiva, precarga de áreas ni modo offline. La URL y atribución estarán en configuración, no dispersas en el código, para poder cambiar de proveedor.

La política del servidor de teselas de OpenStreetMap exige atribución, uso interactivo normal y respeto al caché, y aclara que no ofrece SLA: <https://operations.osmfoundation.org/policies/tiles/>.

Si después se agrega un mapa satelital o topográfico de un proveedor comercial:

- se añadirá como entrada en `app/config/mapas.yml` con URL, atribución y límites de zoom;
- se revisarán sus términos antes de publicarlo;
- solo se usará un token público restringido por dominio y cuota;
- un secreto inyectado durante el build **no permanece secreto** en una app estática, porque acaba visible en el navegador.

## 4. Organización canónica del contenido

Se migrará del directorio plano actual a **una carpeta por ruta**. El nombre de la carpeta es también la clave estable de la ruta.

```text
rutas/
├── 2026-07-19-morning-hike/
│   ├── ruta.gpx
│   ├── ruta.yml
│   ├── fotos.csv          # solo si hay datos ausentes o correcciones
│   └── fotos/
│       └── imagen-01.jpg
└── milpa-alta-santo-domingo-2026/
    ├── ruta.gpx
    ├── ruta.yml
    ├── fotos.csv
    └── fotos/
```

Ventajas frente a mantener todos los archivos juntos:

- la asociación entre foto y recorrido no es ambigua;
- agregar o quitar una ruta es una operación autocontenida;
- dos cámaras pueden producir nombres de archivo repetidos sin colisión;
- no hay que modificar código Python para publicar contenido nuevo;
- la clave puede usarse en URLs compartibles, por ejemplo `?ruta=milpa-alta-santo-domingo-2026`.

La estructura plana actual no será el formato permanente. El primer paso de implementación migrará los dos GPX; las fotos no se moverán hasta saber a cuál recorrido pertenecen.

### 4.1 `ruta.yml`

Cada carpeta tendrá un manifiesto pequeño y versionado:

```yaml
version_esquema: 1
id: milpa-alta-santo-domingo-2026
titulo: Milpa Alta a Santo Domingo 2026
archivo_gpx: ruta.gpx
zona_horaria: America/Mexico_City
actividad: senderismo
region: Ciudad de México
descripcion: Recorrido de Milpa Alta a Santo Domingo.
portada: fotos/imagen-01.jpg
publicada: true
```

Reglas:

- `id` es obligatorio, único, en minúsculas y formato `kebab-case`;
- `id` debe coincidir con el nombre de la carpeta y no debe cambiar una vez publicado;
- `titulo`, `archivo_gpx` y `zona_horaria` son obligatorios;
- la fecha principal se obtiene del primer timestamp del GPX; se podrá agregar una corrección explícita al manifiesto;
- `descripcion`, `region`, `portada` y otros textos editoriales son opcionales;
- `publicada: false` permite conservar una ruta en el repositorio sin incluirla en el sitio;
- las rutas se ordenan por fecha descendente.

### 4.2 Fotos y `fotos.csv`

Las imágenes dentro de `fotos/` se descubrirán automáticamente. La prioridad de metadatos, campo por campo, será:

1. valor explícito en `fotos.csv`;
2. EXIF de la imagen original;
3. coordenada interpolada en el GPX a partir de la hora de captura.

Formato propuesto:

```csv
archivo,fecha_hora,latitud,longitud,descripcion,texto_alt
fotos/imagen-01.jpg,2026-01-25T09:12:30-06:00,19.1451,-99.0862,Inicio del recorrido,Sendero entre árboles
fotos/imagen-02.jpg,2026-01-25T10:03:14-06:00,,,Vista hacia el valle,Vista panorámica del valle
```

En la segunda fila, la fecha permite interpolar la posición sobre la ruta aunque falten latitud y longitud. La interpolación solo se aceptará si la hora cae dentro del intervalo del GPX, con una tolerancia configurable de diez minutos. Si EXIF trae una hora sin zona, se interpretará en la `zona_horaria` de la ruta.

El build fallará con un mensaje accionable cuando una foto publicada no tenga:

- una fecha de captura obtenible; o
- una posición GPS explícita, ni una hora válida para interpolarla sobre la ruta.

No se usará el nombre del archivo, la fecha de descarga ni la fecha de modificación para inventar estos valores.

**Situación de las fotos actuales:** las nueve imágenes ya tienen EXIF completo y se pueden asociar automáticamente con `milpa-alta-santo-domingo-2026`. Sus fechas están dentro del recorrido y sus posiciones coinciden con el track dentro del margen normal de GPS. No necesitan `fotos.csv` para fecha ni ubicación; ese archivo solo será necesario si se quieren agregar descripciones, texto alternativo o correcciones manuales.

## 5. Procesamiento y modelo de datos generado

### 5.1 Validación de entrada

`scripts/build_content.py` comprobará como mínimo:

- claves únicas y rutas de archivos confinadas a su carpeta;
- XML GPX 1.0/1.1 válido, al menos un segmento y coordenadas dentro de rango;
- timestamps en orden o advertencias explícitas cuando no lo estén;
- valores de elevación numéricos cuando estén disponibles;
- fechas ISO 8601 y zonas horarias válidas;
- latitud entre -90 y 90 y longitud entre -180 y 180;
- existencia y formato permitido de cada foto;
- distancia anómala de una foto respecto al track;
- campos desconocidos, duplicados y referencias rotas;
- ausencia de secretos o URLs de teselas sin atribución en la configuración.

Un error de contenido detendrá el build; una ruta no se publicará parcialmente.

### 5.2 Distancia, tiempo y elevación

El algoritmo será explícito y estable:

1. preservar los límites entre `trkseg`; nunca unir dos segmentos separados con una línea o distancia artificial;
2. descartar puntos idénticos consecutivos y marcar saltos GPS improbables;
3. calcular distancia acumulada con Haversine sobre WGS84 dentro de cada segmento;
4. interpolar la elevación cada 20 m de distancia horizontal;
5. aplicar una mediana móvil centrada de cinco muestras, aproximadamente 100 m;
6. sumar diferencias positivas para ascenso y negativas para descenso sobre esa misma serie filtrada;
7. usar la serie filtrada para la gráfica, de modo que el perfil y los totales sean coherentes;
8. redondear distancia a 0.1 km y desniveles a 10 m solo para presentación; conservar precisión en JSON.

La suma de elevación cruda punto a punto no se mostrará: amplifica el ruido del sensor. Los parámetros de 20 m/5 muestras estarán centralizados y tendrán pruebas de regresión con los GPX actuales.

Se calcularán dos duraciones cuando haya timestamps:

- `duracion_total`: primera a última hora del GPX;
- `tiempo_en_movimiento`: suma de intervalos de hasta cinco minutos con velocidad entre 1 y 12 km/h.

Los umbrales se documentarán en la interfaz. Si faltan timestamps o elevación, la ruta podrá mostrarse, pero la métrica afectada aparecerá como “sin datos”; el validador emitirá una advertencia.

### 5.3 Clasificación de esfuerzo

La etiqueta se llamará **esfuerzo físico estimado**, no “dificultad técnica”. Un GPX no permite inferir exposición, tipo de terreno, navegación, clima, seguridad ni condición personal.

Primero se calculará:

```text
km_esfuerzo = distancia_km + ascenso_positivo_m / 100
```

La categoría final será la más alta entre los `km_esfuerzo` y el tiempo en movimiento:

| Categoría | km de esfuerzo | Tiempo en movimiento |
| --- | ---: | ---: |
| Ligera | < 10 | < 3 h |
| Moderada | 10 a < 20 | 3 a < 5 h |
| Exigente | 20 a < 30 | 5 a < 7 h |
| Muy exigente | >= 30 | >= 7 h |

Ejemplo: una ruta con 15 km de esfuerzo pero 7.5 horas se clasifica como “Muy exigente”. Si no hay tiempo, se usa solo `km_esfuerzo`. Los umbrales vivirán en `app/config/clasificacion.yml`, tendrán pruebas de frontera y podrán ajustarse después de revisar varias rutas reales sin cambiar el procesador.

No se usará el índice IBP ni se presentará la escala como una certificación externa.

### 5.4 Simplificación y salida

El build generará, dentro de un directorio ignorado por Git:

```text
app/_generated/
├── catalogo.json
├── rutas/
│   └── <id>.json
└── fotos/
    └── <id>/
        ├── miniaturas/
        └── web/
```

- `catalogo.json` contendrá solo información necesaria para listar y filtrar rutas.
- Cada JSON de ruta contendrá geometría simplificada, perfil, métricas y metadatos de fotos.
- Los cálculos se harán con todos los puntos válidos; la simplificación solo afectará la representación.
- La línea del mapa se reducirá con Ramer–Douglas–Peucker y una tolerancia inicial de 5 m, conservando extremos de segmento.
- El perfil tendrá como máximo 2,000 muestras.
- Las imágenes respetarán orientación EXIF; se crearán miniaturas y una versión web, sin publicar el original por accidente.
- Después de extraer fecha y posición, las copias web se guardarán sin EXIF. La app recibirá únicamente los campos necesarios mediante JSON, evitando publicar metadatos adicionales del dispositivo o la cámara.
- Los JSON tendrán orden estable para que dos builds de la misma entrada produzcan el mismo resultado.

Presupuesto inicial de contenido generado, sin contar la distribución de Shinylive:

- máximo 2,000 puntos de perfil y 3,000 vértices de mapa por ruta;
- miniatura de hasta 480 px y aproximadamente 100 KB;
- imagen de vista hasta 1,280 px y aproximadamente 350 KB;
- carga diferida de imágenes (`loading="lazy"`);
- revisión de arquitectura si se superan 50 rutas o 10 MB de datos procesados iniciales.

## 6. Experiencia de usuario

### 6.1 Vista principal

En escritorio:

- panel lateral con buscador, lista de rutas, fecha, región y clasificación;
- mapa como elemento principal;
- panel de detalle con métricas y altimetría.

En móvil:

- selector compacto de ruta;
- mapa a ancho completo;
- métricas, perfil y galería apilados;
- controles y popups utilizables con toque.

### 6.2 Al seleccionar una ruta

La aplicación deberá:

1. actualizar `?ruta=<id>` sin recargar la página;
2. ajustar el mapa a los límites del recorrido;
3. dibujar cada segmento, inicio y final;
4. mostrar distancia, ascenso, descenso, duración y esfuerzo;
5. mostrar el perfil elevación vs. distancia con hover;
6. colocar marcadores agrupables para las fotos;
7. mostrar en cada popup imagen, fecha local, descripción y fuente de ubicación (`EXIF`, `manual` o `interpolada`);
8. ordenar la galería por fecha y enfocar el marcador al pulsar una foto.

Sincronizar en tiempo real el hover del perfil con un punto sobre el mapa es deseable, pero puede quedar para una segunda iteración si afecta la estabilidad del MVP.

### 6.3 Estados y accesibilidad

- indicador de carga inicial que explique que Python se está preparando en el navegador;
- mensaje claro si falla WebAssembly, una tesela o un archivo de datos;
- navegación por teclado y foco visible;
- texto alternativo en fotos;
- colores de ruta y elevación con contraste suficiente y sin depender solo del color;
- atribución del mapa siempre visible;
- unidades métricas y fechas en español, usando `America/Mexico_City` salvo configuración distinta por ruta.

## 7. Estructura prevista del proyecto

```text
.
├── app/
│   ├── app.py
│   ├── config/
│   │   ├── clasificacion.yml
│   │   └── mapas.yml
│   ├── modules/
│   │   ├── mapa.py
│   │   ├── perfil.py
│   │   └── rutas.py
│   ├── requirements.txt       # compatibilidad de exportación, generado/verificado
│   ├── www/
│   │   └── styles.css
│   └── _generated/            # ignorado; creado por el build
├── rutas/
│   └── <id>/...
├── scripts/
│   └── build_content.py
├── tests/
│   ├── fixtures/
│   ├── test_altimetria.py
│   ├── test_contenido.py
│   ├── test_fotos.py
│   └── test_gpx.py
├── .github/workflows/
│   └── pages.yml
├── pyproject.toml
├── uv.lock
└── PLAN.md
```

`site/`, `build/` y `app/_generated/` se ignorarán. El resultado de Pages será un artefacto de CI, no una rama con archivos compilados.

## 8. Flujo para agregar contenido

### 8.1 Agregar una ruta

1. Crear `rutas/<id>/`, con un `id` estable en `kebab-case`.
2. Copiar el track como `ruta.gpx`.
3. Crear `ruta.yml` a partir de una plantilla.
4. Copiar originales de fotos, si los hay, dentro de `fotos/`.
5. Añadir `fotos.csv` solo para completar o corregir metadatos.
6. Ejecutar:

   ```bash
   uv run python scripts/build_content.py --check
   uv run pytest
   ```

7. Revisar localmente:

   ```bash
   uv run python scripts/build_content.py
   uv run shiny run --reload app/app.py
   ```

8. Hacer commit y push. GitHub Actions validará, exportará y desplegará.

No se modifica `app.py`, una lista Python ni un diccionario central para añadir una ruta.

### 8.2 Agregar o cambiar un mapa base

Editar `app/config/mapas.yml` con:

- `id` único;
- nombre visible;
- plantilla HTTPS de teselas;
- atribución obligatoria;
- zoom mínimo y máximo;
- token público restringido, si el proveedor lo requiere.

Esto es distinto de agregar un recorrido. Los recorridos viven en `rutas/`; los mapas base son proveedores visuales configurados en `app/config/mapas.yml`.

## 9. Dependencias y comandos con `uv`

Durante la implementación, las dependencias se incorporarán de esta forma:

```bash
uv add shiny shinywidgets ipyleaflet plotly pillow pyyaml
uv add --dev shinylive pytest ruff
```

Las versiones exactas resultantes quedarán en `uv.lock`. Antes de aceptar la arquitectura se hará un smoke test de exportación, porque una versión disponible en CPython local puede no estar incluida todavía en la distribución Pyodide de Shinylive.

Comandos habituales:

```bash
uv sync --locked
uv run ruff check .
uv run pytest
uv run shinylive export app site
uv run python -m http.server --directory site 8008
```

El sitio exportado debe servirse por HTTP; abrir `site/index.html` directamente como `file://` no funciona por las restricciones del navegador.

## 10. CI/CD y GitHub Pages

Se usará un workflow personalizado, porque hay que validar y transformar contenido antes de publicar. GitHub documenta el patrón `configure-pages` → `upload-pages-artifact` → `deploy-pages`: <https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages>.

### Pull requests

1. checkout;
2. instalar `uv` con `astral-sh/setup-uv`, fijado a una versión o SHA;
3. `uv sync --locked`;
4. `uv run ruff check .`;
5. `uv run pytest`;
6. validar y generar contenido;
7. exportar con Shinylive como smoke test;
8. no desplegar.

### Push a `main` o ejecución manual

1. repetir todas las verificaciones;
2. generar `app/_generated/`;
3. ejecutar `uv run shinylive export app site`;
4. añadir `.nojekyll` al artefacto;
5. comprobar que `site/index.html` esté en la raíz;
6. subir el artefacto de Pages;
7. desplegar al entorno `github-pages` con permisos mínimos `contents: read`, `pages: write` e `id-token: write`.

Todas las URLs propias serán relativas para funcionar tanto en un dominio raíz como en `usuario.github.io/repositorio/`. El workflow tendrá concurrencia para cancelar despliegues anteriores aún en curso.

La guía oficial de `uv` recomienda `astral-sh/setup-uv`, `uv sync` y `uv run` en GitHub Actions: <https://docs.astral.sh/uv/guides/integration/github/>.

## 11. Pruebas y criterios de aceptación

### 11.1 Pruebas automatizadas

- GPX con namespaces, varios segmentos, puntos repetidos y campos ausentes;
- no sumar distancia entre segmentos;
- regresión de distancia y desnivel de los dos GPX actuales con tolerancias explícitas;
- límites exactos de las cuatro categorías de esfuerzo;
- prioridad CSV > EXIF > interpolación;
- conversión de hora UTC/naive a la zona de la ruta;
- foto antes/después del track, sin fecha o sin posición;
- manifiestos duplicados, paths inseguros y coordenadas inválidas;
- salida JSON determinista;
- build y exportación Shinylive completos.

### 11.2 Aceptación funcional del MVP

- las dos rutas actuales aparecen y pueden seleccionarse;
- el mapa se ajusta a la ruta y conserva atribución visible;
- las métricas no cambian entre builds idénticos;
- el perfil muestra distancia en km y elevación en m;
- ascenso y descenso coinciden con la serie filtrada;
- cada foto publicada aparece en mapa y galería con fecha;
- una foto sin metadatos suficientes detiene el build con su ruta y nombre de archivo;
- `?ruta=<id>` abre directamente el recorrido correcto;
- la app funciona en la versión actual de Chrome, Firefox y Safari, en escritorio y móvil;
- una ruta nueva válida se publica sin editar código;
- no hay tokens secretos, GPX crudos ni originales de foto dentro del artefacto de Pages;
- el workflow de una PR valida sin desplegar y el de `main` publica correctamente.

## 12. Fases de implementación

### Fase 0 — Resolver contenido y privacidad

- asignar las nueve fotos verificadas a `milpa-alta-santo-domingo-2026`;
- decidir si las coordenadas y tiempos pueden ser públicos;
- migrar los dos GPX al formato de una carpeta por ruta.

Salida: contenido normalizado y publicable, sin geolocalizaciones inventadas.

### Fase 1 — Base reproducible

- agregar dependencias exclusivamente con `uv`;
- confirmar `uv.lock`;
- crear estructura de `app/`, configuración y plantillas;
- hacer un spike mínimo Shiny + Shinylive + ipyleaflet + Plotly;
- verificar la exportación y la ruta base de GitHub Pages.

Salida: app mínima exportable en local.

### Fase 2 — Procesador de contenido

- implementar manifiestos, GPX, EXIF/CSV e interpolación;
- implementar distancia, altimetría, tiempo y clasificación;
- generar JSON e imágenes web;
- añadir validaciones y pruebas de regresión.

Salida: artefactos deterministas para las rutas actuales.

### Fase 3 — Interfaz

- lista y selector de recorridos;
- mapa, línea, inicio/final y fotos;
- tarjetas de métricas y perfil Plotly;
- galería, URLs compartibles y diseño responsive;
- estados de carga, errores y accesibilidad.

Salida: MVP funcional en local.

### Fase 4 — Publicación

- workflow de validación y Pages;
- configuración de Pages con GitHub Actions como fuente;
- prueba en URL real, incluyendo rutas relativas, teselas y caché;
- documentar el alta de rutas en `README.md`.

Salida: sitio público y proceso de actualización documentado.

### Fase 5 — Mejoras posteriores

- sincronización perfil ↔ mapa;
- filtros por año, región, distancia y esfuerzo;
- clustering avanzado y carrusel de fotos;
- comparación de rutas;
- proveedor topográfico/satelital;
- PWA u opciones offline solo con un proveedor de teselas que lo permita;
- clasificación técnica manual separada del esfuerzo físico.

## 13. Decisiones aún necesarias

La asociación y geolocalización de las fotos ya está resuelta: las nueve imágenes tienen fecha, zona horaria y GPS válidos, y corresponden al recorrido `milpa-alta-santo-domingo-2026`.

Solo una decisión bloquea la publicación de los datos actuales:

1. **Privacidad:** confirmar que se pueden hacer públicos recorridos, horas y fotos. GitHub Pages es un sitio público; si el repositorio también es público, los GPX fuente seguirán accesibles aunque el artefacto del sitio solo incluya datos procesados.

Decisiones que no bloquean el inicio y tienen un valor por defecto:

- mapa base: OpenStreetMap sin clave;
- idioma/unidades: español y sistema métrico;
- zona horaria por defecto: `America/Mexico_City`;
- clasificación: heurística transparente de esfuerzo descrita arriba;
- nombre y estilo visual: “Botas Puestas” como nombre provisional;
- sincronización altimetría–mapa: posterior al MVP si requiere trabajo adicional.
