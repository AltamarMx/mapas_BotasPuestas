"""Importa tracks GPX de una colección como candidatas para el constructor de tramos.

Los GPX originales viven en `<colección>/fuentes/`. Cada `<trk>` se convierte en una carpeta
`<prefijo>-NNN-<nombre>/` con `ruta.gpx` limpio y `ruta.yml` editorial. Los tracks ya importados
se reconocen por `procedencia` y nunca se sobrescriben, así que el comando puede repetirse al
agregar GPX nuevos.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

import yaml

from scripts.build_content import SLUG_PATTERN, TrackPoint, track_geometry_sha256
from scripts.extract_legacy_routes import (
    CLASSIFICATION_KEYS,
    GPX_NAMESPACE,
    display_path,
    file_sha256,
    format_coordinate,
    format_elevation,
    normalize_name,
    slugify,
    write_yaml,
)

GENERATOR = "scripts/import_gpx_tracks.py"
SOURCES_DIRNAME = "fuentes"


class GpxImportError(ValueError):
    """Error de entrada apto para mostrar en la terminal."""


@dataclass(frozen=True, slots=True)
class SourceTrack:
    source: Path
    track_index: int
    name: str
    segments: tuple[tuple[TrackPoint, ...], ...]

    @property
    def points(self) -> list[TrackPoint]:
        return [point for segment in self.segments for point in segment]

    @property
    def geometry_hash(self) -> str:
        return track_geometry_sha256([list(segment) for segment in self.segments])

    @property
    def has_elevation(self) -> bool:
        return all(point.elevation_m is not None for point in self.points)


def load_tracks(source: Path) -> list[SourceTrack]:
    try:
        root = ElementTree.parse(source).getroot()
    except (OSError, ElementTree.ParseError) as exc:
        raise GpxImportError(f"GPX inválido en {source}: {exc}") from exc

    tracks = []
    for track_index, track_element in enumerate(root.findall("{*}trk"), start=1):
        segments = []
        for segment_element in track_element.findall("{*}trkseg"):
            segment = []
            for point_element in segment_element.findall("{*}trkpt"):
                try:
                    lat = float(point_element.attrib["lat"])
                    lon = float(point_element.attrib["lon"])
                except (KeyError, ValueError) as exc:
                    raise GpxImportError(f"Punto sin coordenadas válidas en {source}") from exc
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise GpxImportError(f"Coordenada fuera de rango en {source}: {lat}, {lon}")
                elevation_text = point_element.findtext("{*}ele")
                try:
                    elevation = float(elevation_text) if elevation_text else None
                except ValueError as exc:
                    raise GpxImportError(f"Elevación inválida en {source}") from exc
                segment.append(TrackPoint(lat, lon, elevation, None))
            if len(segment) >= 2:
                segments.append(tuple(segment))
        if not segments:
            continue
        name = normalize_name(track_element.findtext("{*}name") or "") or source.stem
        tracks.append(SourceTrack(source, track_index, name, tuple(segments)))

    if not tracks:
        raise GpxImportError(f"{source} no contiene <trk> con al menos dos puntos.")
    return tracks


def manifest_for(
    track: SourceTrack,
    route_id: str,
    order: int,
    *,
    collection: str,
    region: str,
) -> dict[str, object]:
    return {
        "version_esquema": 1,
        "id": route_id,
        "titulo": track.name,
        "archivo_gpx": "ruta.gpx",
        "zona_horaria": "America/Mexico_City",
        "actividad": "senderismo",
        "region": region,
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
            "coleccion": collection,
            "archivo": display_path(track.source),
            "sha256_archivo": file_sha256(track.source),
            "elemento": "trk",
            "indice_trk": track.track_index,
            "indice": order,
            "nombre_original": track.name,
            "sha256_geometria": track.geometry_hash,
        },
    }


def write_gpx(path: Path, track: SourceTrack) -> None:
    ElementTree.register_namespace("", GPX_NAMESPACE)
    qualified = lambda tag: f"{{{GPX_NAMESPACE}}}{tag}"  # noqa: E731
    points = track.points
    root = ElementTree.Element(
        qualified("gpx"),
        {"version": "1.1", "creator": "mapas_BotasPuestas GPX importer"},
    )
    metadata = ElementTree.SubElement(root, qualified("metadata"))
    ElementTree.SubElement(metadata, qualified("name")).text = track.name
    ElementTree.SubElement(metadata, qualified("desc")).text = (
        f"Candidata importada de {track.source.name}, <trk> #{track.track_index}. "
        "Debe revisarse antes de publicarse."
    )
    ElementTree.SubElement(
        metadata,
        qualified("bounds"),
        {
            "minlat": format_coordinate(min(point.lat for point in points)),
            "minlon": format_coordinate(min(point.lon for point in points)),
            "maxlat": format_coordinate(max(point.lat for point in points)),
            "maxlon": format_coordinate(max(point.lon for point in points)),
        },
    )
    track_element = ElementTree.SubElement(root, qualified("trk"))
    ElementTree.SubElement(track_element, qualified("name")).text = track.name
    ElementTree.SubElement(track_element, qualified("type")).text = "senderismo"
    for segment in track.segments:
        segment_element = ElementTree.SubElement(track_element, qualified("trkseg"))
        for point in segment:
            point_element = ElementTree.SubElement(
                segment_element,
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


def existing_candidates(collection_dir: Path) -> tuple[set[tuple[str, int]], int]:
    """Devuelve los tracks ya importados (hash de archivo, índice) y el mayor orden usado."""
    imported: set[tuple[str, int]] = set()
    last_order = 0
    for manifest_path in collection_dir.glob("*/ruta.yml"):
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        provenance = manifest.get("procedencia") or {}
        if isinstance(provenance.get("indice"), int):
            last_order = max(last_order, provenance["indice"])
        if provenance.get("sha256_archivo") and provenance.get("indice_trk"):
            imported.add((provenance["sha256_archivo"], provenance["indice_trk"]))
    return imported, last_order


def import_collection(
    collection_dir: Path,
    *,
    prefix: str,
    region: str,
    dry_run: bool = False,
) -> list[tuple[str, SourceTrack]]:
    if not SLUG_PATTERN.fullmatch(prefix):
        raise GpxImportError(f"Prefijo inválido: {prefix}; usa minúsculas y guiones.")
    if not collection_dir.is_dir():
        raise GpxImportError(f"No existe la colección {collection_dir}")

    sources_dir = collection_dir / SOURCES_DIRNAME
    loose_sources = sorted(collection_dir.glob("*.gpx"))
    if loose_sources and not dry_run:
        sources_dir.mkdir(exist_ok=True)
        for source in loose_sources:
            target = sources_dir / source.name
            if target.exists():
                raise GpxImportError(f"Ya existe {display_path(target)}; renombra {source.name}.")
            shutil.move(source, target)
    sources = sorted(sources_dir.glob("*.gpx")) if sources_dir.is_dir() else []
    if dry_run:
        sources = sorted([*sources, *loose_sources], key=lambda path: path.name)
    if not sources:
        raise GpxImportError(f"No hay GPX en {display_path(collection_dir)}.")

    imported, order = existing_candidates(collection_dir)
    created: list[tuple[str, SourceTrack]] = []
    for source in sources:
        source_hash = file_sha256(source)
        for track in load_tracks(source):
            if (source_hash, track.track_index) in imported:
                continue
            order += 1
            route_id = f"{prefix}-{order:03d}-{slugify(track.name)}"
            route_dir = collection_dir / route_id
            if route_dir.exists():
                raise GpxImportError(f"Ya existe {display_path(route_dir)}; no se sobrescribe.")
            if not dry_run:
                route_dir.mkdir()
                write_gpx(route_dir / "ruta.gpx", track)
                write_yaml(
                    route_dir / "ruta.yml",
                    manifest_for(
                        track, route_id, order, collection=collection_dir.name, region=region
                    ),
                )
            created.append((route_id, track))
    return created


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coleccion", type=Path, help="Carpeta en rutas/_candidatas/.")
    parser.add_argument("--prefijo", required=True, help="Prefijo de los ids, p. ej. hugo.")
    parser.add_argument("--region", default="Morelos")
    parser.add_argument("--check", action="store_true", help="Mostrar sin escribir.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        created = import_collection(
            args.coleccion, prefix=args.prefijo, region=args.region, dry_run=args.check
        )
    except GpxImportError as exc:
        print(f"Error de importación: {exc}", file=sys.stderr)
        return 2

    for route_id, track in created:
        elevation = "con elevación" if track.has_elevation else "SIN elevación"
        print(f"- {route_id}: {len(track.points)} puntos, {elevation} ({track.source.name})")
    if not created:
        print("No hay tracks nuevos por importar.")
    elif any(not track.has_elevation for _, track in created):
        print(
            "Algunos tramos no tienen <ele>: genera el caché DEM con\n"
            f"  uv run python -m scripts.build_elevation_cache --candidates {args.coleccion} "
            f"--output {args.coleccion}/elevacion-dem.json"
        )
    print("Simulación; no se escribieron archivos." if args.check else f"{len(created)} creadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
