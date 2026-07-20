"""Extrae los elementos <rte> legacy como candidatos GPX independientes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from xml.etree import ElementTree

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_DIR = PROJECT_ROOT / "rutas" / "GPX-Gilles legacy"
DEFAULT_SOURCE = LEGACY_DIR / "TEPOZ-todo-heredar-pueblos-cerros.gpx"
DEFAULT_COMPARISON = LEGACY_DIR / "TEPOZ-heredar-puntos de partida.gpx"
DEFAULT_OUTPUT = PROJECT_ROOT / "rutas" / "_candidatas" / "gilles"
GENERATOR = "scripts/extract_legacy_routes.py"

GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"
EARTH_RADIUS_M = 6_371_008.8
CLASSIFICATION_KEYS = ("criterio_1", "criterio_2", "criterio_3")


class ExtractionError(ValueError):
    """Error de entrada o de integridad apto para mostrar en la terminal."""


@dataclass(frozen=True, slots=True)
class RoutePoint:
    lat: float
    lon: float
    elevation_m: float | None


@dataclass(frozen=True, slots=True)
class LegacyRoute:
    order: int
    name: str
    points: tuple[RoutePoint, ...]
    source_time_count: int

    @property
    def route_id(self) -> str:
        return f"gilles-{self.order:03d}-{slugify(self.name)}"

    @property
    def geometry_hash(self) -> str:
        payload = "\n".join(
            f"{format_coordinate(point.lat)},{format_coordinate(point.lon)}"
            for point in self.points
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def distance_m(self) -> float:
        return sum(
            haversine_m(previous, current)
            for previous, current in zip(self.points, self.points[1:], strict=False)
        )

    @property
    def has_elevation(self) -> bool:
        return all(point.elevation_m is not None for point in self.points)

    @property
    def is_closed(self) -> bool:
        return len(self.points) > 2 and haversine_m(self.points[0], self.points[-1]) <= 50


def normalize_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-") or "sin-nombre"
    return slug[:72].rstrip("-")


def format_coordinate(value: float) -> str:
    return f"{value:.8f}".rstrip("0").rstrip(".")


def format_elevation(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def parse_point(element: ElementTree.Element, source: Path) -> RoutePoint:
    try:
        lat = float(element.attrib["lat"])
        lon = float(element.attrib["lon"])
    except (KeyError, ValueError) as exc:
        raise ExtractionError(f"Punto sin coordenadas válidas en {source}") from exc
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ExtractionError(f"Coordenada fuera de rango en {source}: {lat}, {lon}")

    elevation_text = element.findtext("{*}ele")
    try:
        elevation = float(elevation_text) if elevation_text else None
    except ValueError as exc:
        raise ExtractionError(f"Elevación inválida en {source}: {elevation_text}") from exc
    return RoutePoint(lat=lat, lon=lon, elevation_m=elevation)


def load_routes(source: Path) -> list[LegacyRoute]:
    try:
        root = ElementTree.parse(source).getroot()
    except (OSError, ElementTree.ParseError) as exc:
        raise ExtractionError(f"No se pudo leer el GPX {source}: {exc}") from exc

    routes = []
    for order, route_element in enumerate(root.findall("{*}rte"), start=1):
        name = normalize_name(route_element.findtext("{*}name") or "")
        if not name:
            name = f"Ruta sin nombre {order:03d}"
        point_elements = route_element.findall("{*}rtept")
        points = tuple(parse_point(point, source) for point in point_elements)
        if len(points) < 2:
            raise ExtractionError(f"La ruta #{order} ({name}) tiene menos de dos puntos")
        source_times = {
            value for point in point_elements if (value := point.findtext("{*}time")) is not None
        }
        routes.append(
            LegacyRoute(
                order=order,
                name=name,
                points=points,
                source_time_count=len(source_times),
            )
        )

    if not routes:
        raise ExtractionError(f"{source} no contiene elementos <rte>")
    ids = [route.route_id for route in routes]
    if len(ids) != len(set(ids)):
        raise ExtractionError("La extracción produjo identificadores duplicados")
    return routes


def compare_sources(
    routes: list[LegacyRoute], comparison_source: Path
) -> tuple[dict[int, str | None], int]:
    comparison = load_routes(comparison_source)
    if len(routes) != len(comparison):
        raise ExtractionError(
            f"Las fuentes no contienen el mismo número de rutas: {len(routes)} != {len(comparison)}"
        )

    alternate_names: dict[int, str | None] = {}
    name_differences = 0
    for route, other in zip(routes, comparison, strict=True):
        if route.geometry_hash != other.geometry_hash:
            raise ExtractionError(
                f"La geometría #{route.order} difiere entre {DEFAULT_SOURCE.name} "
                f"y {comparison_source.name}"
            )
        alternate_name = other.name if other.name != route.name else None
        alternate_names[route.order] = alternate_name
        name_differences += alternate_name is not None
    return alternate_names, name_differences


def haversine_m(a: RoutePoint, b: RoutePoint) -> float:
    phi_a = math.radians(a.lat)
    phi_b = math.radians(b.lat)
    delta_phi = math.radians(b.lat - a.lat)
    delta_lambda = math.radians(b.lon - a.lon)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(value))


def automatic_observations(route: LegacyRoute, alternate_name: str | None) -> list[str]:
    observations = []
    if route.distance_m < 500:
        observations.append("tramo-muy-corto")
    elif route.distance_m < 2_000:
        observations.append("tramo-corto")
    if "conex" in route.name.casefold():
        observations.append("posible-conector")
    if re.search(r"\d(?:\s+\d)*$", route.name):
        observations.append("nombre-con-sufijo-numerico")
    if len(route.name) <= 5:
        observations.append("nombre-ambiguo")
    if not route.has_elevation:
        observations.append("sin-elevacion")
    if route.is_closed:
        observations.append("circuito-cerrado")
    if alternate_name:
        observations.append("nombre-difiere-entre-fuentes")
    return observations


def manifest_for(
    route: LegacyRoute,
    source: Path,
    alternate_name: str | None,
) -> dict[str, object]:
    return {
        "version_esquema": 1,
        "id": route.route_id,
        "titulo": route.name,
        "archivo_gpx": "ruta.gpx",
        "zona_horaria": "America/Mexico_City",
        "actividad": "senderismo",
        "region": "Tepoztlán, Morelos",
        "descripcion": "",
        "publicada": False,
        "tipo_registro": "por-definir",
        "clasificacion_editorial": {key: None for key in CLASSIFICATION_KEYS},
        "fotos_estrategicas": [],
        "revision": {
            "estado": "pendiente",
            "nombre": "pendiente",
            "geometria": "pendiente",
            "clasificacion": "pendiente",
            "resena": "pendiente",
            "fotos": "pendiente",
        },
        "procedencia": {
            "coleccion": "gilles-legacy",
            "archivo": display_path(source),
            "elemento": "rte",
            "indice": route.order,
            "nombre_original": route.name,
            "nombre_alternativo": alternate_name,
            "sha256_geometria": route.geometry_hash,
        },
    }


def write_gpx(path: Path, route: LegacyRoute, source: Path) -> None:
    ElementTree.register_namespace("", GPX_NAMESPACE)
    qualified = lambda tag: f"{{{GPX_NAMESPACE}}}{tag}"  # noqa: E731
    root = ElementTree.Element(
        qualified("gpx"),
        {"version": "1.1", "creator": "mapas_BotasPuestas legacy extractor"},
    )
    metadata = ElementTree.SubElement(root, qualified("metadata"))
    ElementTree.SubElement(metadata, qualified("name")).text = route.name
    ElementTree.SubElement(metadata, qualified("desc")).text = (
        f"Candidata extraída de {source.name}, <rte> #{route.order}. "
        "Debe revisarse antes de publicarse."
    )
    ElementTree.SubElement(
        metadata,
        qualified("bounds"),
        {
            "minlat": format_coordinate(min(point.lat for point in route.points)),
            "minlon": format_coordinate(min(point.lon for point in route.points)),
            "maxlat": format_coordinate(max(point.lat for point in route.points)),
            "maxlon": format_coordinate(max(point.lon for point in route.points)),
        },
    )
    track = ElementTree.SubElement(root, qualified("trk"))
    ElementTree.SubElement(track, qualified("name")).text = route.name
    ElementTree.SubElement(track, qualified("type")).text = "senderismo"
    segment = ElementTree.SubElement(track, qualified("trkseg"))
    for point in route.points:
        point_element = ElementTree.SubElement(
            segment,
            qualified("trkpt"),
            {"lat": format_coordinate(point.lat), "lon": format_coordinate(point.lon)},
        )
        if point.elevation_m is not None:
            ElementTree.SubElement(point_element, qualified("ele")).text = format_elevation(
                point.elevation_m
            )

    ElementTree.indent(root, space="  ")
    payload = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    path.write_bytes(payload + b"\n")


def write_yaml(path: Path, value: dict[str, object]) -> None:
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )


def index_row(
    route: LegacyRoute,
    alternate_name: str | None,
) -> dict[str, str | int | float]:
    observations = automatic_observations(route, alternate_name)
    elevations = [point.elevation_m for point in route.points if point.elevation_m is not None]
    return {
        "orden": route.order,
        "id": route.route_id,
        "nombre_original": route.name,
        "nombre_alternativo": alternate_name or "",
        "ruta_relativa": f"{route.route_id}/ruta.gpx",
        "distancia_km": f"{route.distance_m / 1_000:.3f}",
        "puntos": len(route.points),
        "elevacion": "completa" if route.has_elevation else "ausente",
        "elevacion_min_m": f"{min(elevations):.1f}" if elevations else "",
        "elevacion_max_m": f"{max(elevations):.1f}" if elevations else "",
        "es_circuito": "si" if route.is_closed else "no",
        "estado_revision": "pendiente",
        "observaciones_automaticas": "|".join(observations),
        "sha256_geometria": route.geometry_hash,
    }


def extraction_summary(
    routes: list[LegacyRoute],
    source: Path,
    comparison_source: Path,
    name_differences: int,
) -> dict[str, object]:
    distances = sorted(route.distance_m for route in routes)
    geometry_hashes = [route.geometry_hash for route in routes]
    return {
        "version_esquema": 1,
        "generador": GENERATOR,
        "fuente": display_path(source),
        "sha256_fuente": file_sha256(source),
        "fuente_comparada": display_path(comparison_source),
        "sha256_fuente_comparada": file_sha256(comparison_source),
        "geometrias_coinciden_entre_fuentes": True,
        "nombres_distintos_entre_fuentes": name_differences,
        "rutas_extraidas": len(routes),
        "puntos_extraidos": sum(len(route.points) for route in routes),
        "geometrias_duplicadas": len(geometry_hashes) - len(set(geometry_hashes)),
        "rutas_menores_500_m": sum(route.distance_m < 500 for route in routes),
        "rutas_menores_2_km": sum(route.distance_m < 2_000 for route in routes),
        "rutas_con_elevacion_completa": sum(route.has_elevation for route in routes),
        "distancia_km": {
            "minima": round(distances[0] / 1_000, 3),
            "mediana": round(median(distances) / 1_000, 3),
            "maxima": round(distances[-1] / 1_000, 3),
        },
        "transformaciones": [
            "cada <rte> se convirtió en un <trk> con un <trkseg>",
            "se conservaron latitud, longitud y elevación cuando existía",
            "se descartaron nombres de puntos, extensiones Garmin y marcas de tiempo de edición",
            "ninguna candidata se publica automáticamente",
        ],
    }


def generated_readme(summary: dict[str, object]) -> str:
    return f"""# Candidatas extraídas de Gilles

Esta carpeta contiene **{summary["rutas_extraidas"]} candidatas** generadas desde los elementos
`<rte>` legacy. No forman parte del catálogo activo porque están dos niveles debajo de `rutas/`
y todos sus manifiestos declaran `publicada: false`.

Los datos muestran que muchas entradas son tramos de una red: {summary["rutas_menores_500_m"]}
miden menos de 500 m y {summary["rutas_menores_2_km"]} menos de 2 km. Antes de publicar hay que
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
"""


def write_extraction(
    output: Path,
    routes: list[LegacyRoute],
    source: Path,
    comparison_source: Path,
    alternate_names: dict[int, str | None],
    name_differences: int,
) -> None:
    output = output.resolve()
    if output.exists():
        raise ExtractionError(
            f"La salida ya existe: {output}. Usa --check o elige otra carpeta con --output."
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        rows = []
        for route in routes:
            route_dir = staging / route.route_id
            route_dir.mkdir()
            alternate_name = alternate_names[route.order]
            write_gpx(route_dir / "ruta.gpx", route, source)
            write_yaml(
                route_dir / "ruta.yml",
                manifest_for(route, source, alternate_name),
            )
            rows.append(index_row(route, alternate_name))

        with (staging / "indice.csv").open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

        summary = extraction_summary(
            routes,
            source,
            comparison_source,
            name_differences,
        )
        (staging / "extraccion.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (staging / "README.md").write_text(generated_readme(summary), encoding="utf-8")
        staging.rename(output)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def validate_extraction(
    output: Path,
    routes: list[LegacyRoute],
    source: Path,
) -> list[str]:
    errors = []
    if not output.is_dir():
        return [f"No existe la carpeta de extracción: {output}"]

    summary_path = output / "extraccion.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"No se pudo leer {summary_path}: {exc}"]
    if summary.get("generador") != GENERATOR:
        errors.append("extraccion.json no corresponde a este extractor")
    if summary.get("sha256_fuente") != file_sha256(source):
        errors.append("el hash de la fuente cambió")
    if summary.get("rutas_extraidas") != len(routes):
        errors.append("el conteo de rutas de extraccion.json no coincide")

    expected_ids = {route.route_id for route in routes}
    actual_ids = {path.name for path in output.iterdir() if path.is_dir()}
    missing = sorted(expected_ids - actual_ids)
    extra = sorted(actual_ids - expected_ids)
    if missing:
        errors.append(f"faltan carpetas: {', '.join(missing[:5])}")
    if extra:
        errors.append(f"sobran carpetas: {', '.join(extra[:5])}")

    for route in routes:
        route_dir = output / route.route_id
        manifest_path = route_dir / "ruta.yml"
        gpx_path = route_dir / "ruta.gpx"
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            errors.append(f"{route.route_id}: manifiesto inválido ({exc})")
            continue
        if manifest.get("id") != route.route_id:
            errors.append(f"{route.route_id}: id de manifiesto distinto")
        if manifest.get("publicada") is not False:
            errors.append(f"{route.route_id}: una candidata no debe estar publicada")
        classification = manifest.get("clasificacion_editorial", {})
        if set(classification) != set(CLASSIFICATION_KEYS):
            errors.append(f"{route.route_id}: faltan los tres criterios editoriales")

        try:
            root = ElementTree.parse(gpx_path).getroot()
        except (OSError, ElementTree.ParseError) as exc:
            errors.append(f"{route.route_id}: GPX inválido ({exc})")
            continue
        if root.find("{*}rte") is not None:
            errors.append(f"{route.route_id}: aún contiene <rte>")
        segment = root.find("{*}trk/{*}trkseg")
        point_elements = segment.findall("{*}trkpt") if segment is not None else []
        if len(point_elements) != len(route.points):
            errors.append(f"{route.route_id}: cambió el número de puntos")
            continue
        extracted = LegacyRoute(
            order=route.order,
            name=route.name,
            points=tuple(parse_point(point, gpx_path) for point in point_elements),
            source_time_count=0,
        )
        if extracted.geometry_hash != route.geometry_hash:
            errors.append(f"{route.route_id}: la geometría no coincide con la fuente")
        if root.find(".//{*}time") is not None:
            errors.append(f"{route.route_id}: conserva una marca de tiempo legacy")

    try:
        with (output / "indice.csv").open(encoding="utf-8", newline="") as file:
            index_rows = list(csv.DictReader(file))
    except OSError as exc:
        errors.append(f"no se pudo leer indice.csv ({exc})")
    else:
        if len(index_rows) != len(routes):
            errors.append("el conteo de indice.csv no coincide")
        if {row.get("id") for row in index_rows} != expected_ids:
            errors.append("los ids de indice.csv no coinciden")
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--compare", type=Path, default=DEFAULT_COMPARISON)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Valida la extracción existente sin escribir archivos",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        routes = load_routes(args.source)
        alternate_names, name_differences = compare_sources(routes, args.compare)
        if args.check:
            errors = validate_extraction(args.output, routes, args.source)
            if errors:
                for error in errors:
                    print(f"ERROR: {error}")
                return 2
            print(f"Extracción válida: {len(routes)} candidatas en {args.output.resolve()}")
            return 0

        write_extraction(
            args.output,
            routes,
            args.source,
            args.compare,
            alternate_names,
            name_differences,
        )
    except ExtractionError as exc:
        print(f"Error de extracción: {exc}")
        return 2

    print(f"Rutas extraídas: {len(routes)}")
    print(f"Nombres distintos entre fuentes: {name_differences}")
    print(f"Salida: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
