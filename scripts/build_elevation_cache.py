"""Genera elevación DEM reproducible para los tramos GPX que no incluyen <ele>."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import shutil
import struct
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import BinaryIO

from scripts.build_content import (
    ELEVATION_CACHE_FILENAME,
    TrackPoint,
    load_yaml,
    parse_gpx,
    track_geometry_sha256,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATES = PROJECT_ROOT / "rutas" / "_candidatas" / "gilles"
DEFAULT_OUTPUT = DEFAULT_CANDIDATES / ELEVATION_CACHE_FILENAME
DEFAULT_CACHE = PROJECT_ROOT / ".cache" / "srtm"

SOURCE_ID = "nasa-srtmgl1-v3"
SOURCE_NAME = "NASA Shuttle Radar Topography Mission Global 1 arc-second V003"
SOURCE_PROVIDER = "AWS Open Data Terrain Tiles"
SOURCE_DATASET_URL = (
    "https://data.nasa.gov/dataset/"
    "nasa-shuttle-radar-topography-mission-global-1-arc-second-v003-e47e1"
)
SOURCE_DISTRIBUTION_URL = "https://registry.opendata.aws/terrain-tiles/"
SOURCE_URL_TEMPLATE = (
    "https://s3.amazonaws.com/elevation-tiles-prod/skadi/{latitude_band}/{tile}.hgt.gz"
)

HGT_SIDE = 3_601
HGT_BYTES = HGT_SIDE * HGT_SIDE * 2
HGT_VOID = -32_768


class ElevationError(ValueError):
    """Error de descarga, geometría o muestreo apto para mostrar en la terminal."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def tile_name(lat: float, lon: float) -> str:
    south = math.floor(lat)
    west = math.floor(lon)
    latitude = f"{'N' if south >= 0 else 'S'}{abs(south):02d}"
    longitude = f"{'E' if west >= 0 else 'W'}{abs(west):03d}"
    return f"{latitude}{longitude}"


def tile_origin(name: str) -> tuple[int, int]:
    if len(name) != 7 or name[0] not in "NS" or name[3] not in "EW":
        raise ElevationError(f"Nombre de tesela SRTM inválido: {name}")
    latitude = int(name[1:3]) * (1 if name[0] == "N" else -1)
    longitude = int(name[4:7]) * (1 if name[3] == "E" else -1)
    return latitude, longitude


def tile_url(name: str) -> str:
    return SOURCE_URL_TEMPLATE.format(latitude_band=name[:3], tile=name)


def copy_response(response: BinaryIO, target: Path) -> None:
    with target.open("wb") as output:
        shutil.copyfileobj(response, output)


def ensure_tile(name: str, cache_dir: Path, *, offline: bool) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{name}.hgt.gz"
    if path.is_file():
        return path
    if offline:
        raise ElevationError(f"Falta {path}; vuelve a ejecutar sin --offline para descargarla.")

    temporary = path.with_suffix(".gz.part")
    request = urllib.request.Request(
        tile_url(name),
        headers={"User-Agent": "mapas-BotasPuestas elevation builder"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            copy_response(response, temporary)
        temporary.replace(path)
    except (OSError, urllib.error.URLError) as exc:
        temporary.unlink(missing_ok=True)
        raise ElevationError(f"No se pudo descargar {tile_url(name)}: {exc}") from exc
    return path


@dataclass(slots=True)
class HgtTile:
    name: str
    archive_path: Path
    data: bytes

    @classmethod
    def load(cls, name: str, archive_path: Path) -> HgtTile:
        try:
            with gzip.open(archive_path, "rb") as file:
                data = file.read()
        except (OSError, gzip.BadGzipFile) as exc:
            raise ElevationError(f"Tesela SRTM inválida: {archive_path}: {exc}") from exc
        if len(data) != HGT_BYTES:
            raise ElevationError(
                f"{archive_path} tiene {len(data)} bytes; se esperaban {HGT_BYTES}."
            )
        return cls(name=name, archive_path=archive_path, data=data)

    def value(self, row: int, column: int) -> int:
        offset = 2 * (row * HGT_SIDE + column)
        return struct.unpack_from(">h", self.data, offset)[0]

    def sample(self, lat: float, lon: float) -> float:
        south, west = tile_origin(self.name)
        row = min(HGT_SIDE - 1, max(0.0, (south + 1 - lat) * (HGT_SIDE - 1)))
        column = min(HGT_SIDE - 1, max(0.0, (lon - west) * (HGT_SIDE - 1)))
        row_0 = math.floor(row)
        column_0 = math.floor(column)
        row_1 = min(HGT_SIDE - 1, row_0 + 1)
        column_1 = min(HGT_SIDE - 1, column_0 + 1)
        row_fraction = row - row_0
        column_fraction = column - column_0
        weighted = (
            (self.value(row_0, column_0), (1 - row_fraction) * (1 - column_fraction)),
            (self.value(row_0, column_1), (1 - row_fraction) * column_fraction),
            (self.value(row_1, column_0), row_fraction * (1 - column_fraction)),
            (self.value(row_1, column_1), row_fraction * column_fraction),
        )
        valid = [(value, weight) for value, weight in weighted if value != HGT_VOID]
        weight_total = sum(weight for _, weight in valid)
        if not valid or math.isclose(weight_total, 0.0):
            raise ElevationError(f"SRTM no tiene elevación válida en {lat}, {lon}.")
        return sum(value * weight for value, weight in valid) / weight_total


class SrtmSampler:
    def __init__(self, cache_dir: Path, *, offline: bool) -> None:
        self.cache_dir = cache_dir
        self.offline = offline
        self.tiles: dict[str, HgtTile] = {}

    def sample(self, point: TrackPoint) -> float:
        name = tile_name(point.lat, point.lon)
        if name not in self.tiles:
            path = ensure_tile(name, self.cache_dir, offline=self.offline)
            self.tiles[name] = HgtTile.load(name, path)
        return self.tiles[name].sample(point.lat, point.lon)


def rounded_metrics(values: list[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    return {
        "point_count": len(values),
        "median_difference_m": round(median(values), 2),
        "median_absolute_difference_m": round(median(abs(value) for value in values), 2),
        "root_mean_square_difference_m": round(
            math.sqrt(sum(value * value for value in values) / len(values)), 2
        ),
    }


def build_cache(
    candidates_root: Path,
    output_path: Path,
    tile_cache_dir: Path,
    *,
    offline: bool = False,
) -> dict[str, object]:
    route_dirs = sorted(path.parent for path in candidates_root.glob("*/ruta.yml"))
    if not route_dirs:
        raise ElevationError(f"No se encontraron candidatas en {candidates_root}.")

    sampler = SrtmSampler(tile_cache_dir, offline=offline)
    records: dict[str, dict[str, object]] = {}
    comparison_differences: list[float] = []
    original_route_count = 0
    dem_point_count = 0

    for route_dir in route_dirs:
        manifest = load_yaml(route_dir / "ruta.yml")
        route_id = str(manifest.get("id", ""))
        track_filename = str(manifest.get("archivo_gpx", "ruta.gpx"))
        segments, _ = parse_gpx(route_dir / track_filename)
        points = [point for segment in segments for point in segment]
        dem_elevations = [sampler.sample(point) for point in points]
        original_count = sum(point.elevation_m is not None for point in points)

        if original_count == len(points):
            original_route_count += 1
            comparison_differences.extend(
                dem - float(point.elevation_m)
                for point, dem in zip(points, dem_elevations, strict=True)
                if point.elevation_m is not None
            )
            continue

        records[route_id] = {
            "geometry_sha256": track_geometry_sha256(segments),
            "point_count": len(points),
            "elevations_m": [round(value, 1) for value in dem_elevations],
        }
        dem_point_count += len(points) - original_count

    tile_records = [
        {
            "id": name,
            "archive_sha256": file_sha256(tile.archive_path),
            "url": tile_url(name),
        }
        for name, tile in sorted(sampler.tiles.items())
    ]
    payload: dict[str, object] = {
        "schema_version": 1,
        "generated_by": "scripts/build_elevation_cache.py",
        "source": {
            "id": SOURCE_ID,
            "name": SOURCE_NAME,
            "provider": SOURCE_PROVIDER,
            "dataset_url": SOURCE_DATASET_URL,
            "distribution_url": SOURCE_DISTRIBUTION_URL,
            "resolution_arc_seconds": 1,
            "sampling": "bilinear",
            "tiles": tile_records,
        },
        "validation": {
            "dem_route_count": len(records),
            "dem_point_count": dem_point_count,
            "preserved_gpx_route_count": original_route_count,
            "dem_minus_gpx": rounded_metrics(comparison_differences),
        },
        "segments": records,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Muestrea SRTM y genera el caché de elevación de las candidatas."
    )
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="No descargar teselas; fallar si no están en --cache-dir.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = build_cache(
            args.candidates,
            args.output,
            args.cache_dir,
            offline=args.offline,
        )
    except (ElevationError, OSError, ValueError) as exc:
        print(f"Error de elevación: {exc}", file=sys.stderr)
        return 2

    validation = payload["validation"]
    assert isinstance(validation, dict)
    print(f"Tramos completados con SRTM: {validation['dem_route_count']}")
    print(f"Puntos completados con SRTM: {validation['dem_point_count']}")
    print(f"Tramos que conservan elevación GPX: {validation['preserved_gpx_route_count']}")
    print(f"Caché generado en {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
