from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import sys
from bisect import bisect_left
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import yaml
from PIL import ExifTags, Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROUTES = REPO_ROOT / "rutas"
DEFAULT_CONFIG = REPO_ROOT / "app" / "config"
DEFAULT_DATA_OUTPUT = REPO_ROOT / "app" / "_generated"
DEFAULT_WEB_OUTPUT = REPO_ROOT / "app" / "www" / "generated"

EARTH_RADIUS_M = 6_371_008.8
PROFILE_STEP_M = 20.0
PROFILE_MEDIAN_WINDOW = 5
MAP_TOLERANCE_M = 5.0
MAX_MAP_POINTS = 3_000
MAX_PROFILE_POINTS = 2_000
PHOTO_TRACK_TOLERANCE_M = 2_000.0
PHOTO_TIME_TOLERANCE = timedelta(minutes=10)
PHOTO_EXTENSIONS = {".heic", ".heif", ".jpeg", ".jpg"}
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ContentError(ValueError):
    """Error de contenido con un mensaje apto para quien mantiene las rutas."""


@dataclass(frozen=True, slots=True)
class TrackPoint:
    lat: float
    lon: float
    elevation_m: float | None
    recorded_at: datetime | None


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ContentError(f"No se pudo leer {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ContentError(f"{path} debe contener un objeto YAML.")
    return data


def confined_path(parent: Path, relative: str, *, field: str) -> Path:
    candidate = (parent / relative).resolve()
    if not candidate.is_relative_to(parent.resolve()):
        raise ContentError(f"{field} no puede salir de {parent}: {relative}")
    return candidate


def parse_timestamp(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ContentError(f"Timestamp inválido: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def parse_gpx(path: Path) -> tuple[list[list[TrackPoint]], dict[str, str | None]]:
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError) as exc:
        raise ContentError(f"GPX inválido en {path}: {exc}") from exc

    track_element = root.find(".//{*}trk")
    if track_element is None:
        raise ContentError(f"{path} no contiene un track GPX.")

    segments: list[list[TrackPoint]] = []
    for segment_element in track_element.findall("{*}trkseg"):
        segment: list[TrackPoint] = []
        for point_element in segment_element.findall("{*}trkpt"):
            try:
                lat = float(point_element.attrib["lat"])
                lon = float(point_element.attrib["lon"])
            except (KeyError, ValueError) as exc:
                raise ContentError(f"Punto sin coordenadas válidas en {path}.") from exc
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ContentError(f"Coordenada fuera de rango en {path}: {lat}, {lon}")

            elevation_element = point_element.find("{*}ele")
            time_element = point_element.find("{*}time")
            try:
                elevation = (
                    float(elevation_element.text)
                    if elevation_element is not None and elevation_element.text
                    else None
                )
            except ValueError as exc:
                raise ContentError(f"Elevación inválida en {path}.") from exc
            recorded_at = (
                parse_timestamp(time_element.text)
                if time_element is not None and time_element.text
                else None
            )
            segment.append(TrackPoint(lat, lon, elevation, recorded_at))
        if segment:
            segments.append(segment)

    if not segments:
        raise ContentError(f"{path} no contiene segmentos con puntos.")

    return segments, {
        "name": track_element.findtext("{*}name"),
        "type": track_element.findtext("{*}type"),
    }


def haversine_m(a: TrackPoint | tuple[float, float], b: TrackPoint | tuple[float, float]) -> float:
    lat_a, lon_a = (a.lat, a.lon) if isinstance(a, TrackPoint) else a
    lat_b, lon_b = (b.lat, b.lon) if isinstance(b, TrackPoint) else b
    phi_a = math.radians(lat_a)
    phi_b = math.radians(lat_b)
    delta_phi = math.radians(lat_b - lat_a)
    delta_lambda = math.radians(lon_b - lon_a)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(value))


def cumulative_distances(segment: list[TrackPoint]) -> list[float]:
    distances = [0.0]
    for previous, current in zip(segment, segment[1:], strict=False):
        distances.append(distances[-1] + haversine_m(previous, current))
    return distances


def median_filter(values: list[float], window: int = PROFILE_MEDIAN_WINDOW) -> list[float]:
    radius = window // 2
    return [
        float(median(values[max(0, index - radius) : index + radius + 1]))
        for index in range(len(values))
    ]


def resample_profile(
    segment: list[TrackPoint], distances: list[float], step_m: float = PROFILE_STEP_M
) -> tuple[list[tuple[float, float]], float, float]:
    valid = [
        (distance, point.elevation_m)
        for point, distance in zip(segment, distances, strict=True)
        if point.elevation_m is not None
    ]
    if len(valid) < 2 or distances[-1] <= 0:
        return [], 0.0, 0.0

    valid_distances = [item[0] for item in valid]
    elevations = [float(item[1]) for item in valid]
    targets: list[float] = []
    target = 0.0
    while target <= distances[-1]:
        targets.append(target)
        target += step_m
    if not math.isclose(targets[-1], distances[-1]):
        targets.append(distances[-1])

    interpolated: list[float] = []
    for target in targets:
        index = bisect_left(valid_distances, target)
        if index == 0:
            interpolated.append(elevations[0])
        elif index >= len(valid_distances):
            interpolated.append(elevations[-1])
        else:
            left_distance = valid_distances[index - 1]
            right_distance = valid_distances[index]
            fraction = (
                0.0
                if math.isclose(left_distance, right_distance)
                else (target - left_distance) / (right_distance - left_distance)
            )
            interpolated.append(
                elevations[index - 1]
                + fraction * (elevations[index] - elevations[index - 1])
            )

    filtered = median_filter(interpolated)
    ascent = sum(
        max(0.0, current - previous)
        for previous, current in zip(filtered, filtered[1:], strict=False)
    )
    descent = sum(
        max(0.0, previous - current)
        for previous, current in zip(filtered, filtered[1:], strict=False)
    )
    return list(zip(targets, filtered, strict=True)), ascent, descent


def _perpendicular_distance(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    if start == end:
        return math.dist(point, start)
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    fraction = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (
        dx * dx + dy * dy
    )
    fraction = min(1.0, max(0.0, fraction))
    projection = (start[0] + fraction * dx, start[1] + fraction * dy)
    return math.dist(point, projection)


def simplify_segment(
    segment: list[TrackPoint], tolerance_m: float = MAP_TOLERANCE_M
) -> list[list[float]]:
    if len(segment) <= 2:
        return [[point.lat, point.lon] for point in segment]

    mean_latitude = math.radians(sum(point.lat for point in segment) / len(segment))
    projected = [
        (
            EARTH_RADIUS_M * math.radians(point.lon) * math.cos(mean_latitude),
            EARTH_RADIUS_M * math.radians(point.lat),
        )
        for point in segment
    ]
    keep = {0, len(segment) - 1}
    stack = [(0, len(segment) - 1)]
    while stack:
        start, end = stack.pop()
        furthest_index = -1
        furthest_distance = 0.0
        for index in range(start + 1, end):
            distance = _perpendicular_distance(projected[index], projected[start], projected[end])
            if distance > furthest_distance:
                furthest_index = index
                furthest_distance = distance
        if furthest_index >= 0 and furthest_distance > tolerance_m:
            keep.add(furthest_index)
            stack.append((start, furthest_index))
            stack.append((furthest_index, end))
    return [[segment[index].lat, segment[index].lon] for index in sorted(keep)]


def evenly_cap(items: list[Any], maximum: int) -> list[Any]:
    if len(items) <= maximum:
        return items
    indexes = sorted({round(index * (len(items) - 1) / (maximum - 1)) for index in range(maximum)})
    return [items[index] for index in indexes]


def analyze_track(segments: list[list[TrackPoint]]) -> dict[str, Any]:
    total_distance_m = 0.0
    ascent_m = 0.0
    descent_m = 0.0
    moving_seconds = 0.0
    profile: list[list[float]] = []
    geometry: list[list[list[float]]] = []
    all_points = [point for segment in segments for point in segment]

    for segment in segments:
        distances = cumulative_distances(segment)
        segment_profile, segment_ascent, segment_descent = resample_profile(segment, distances)
        profile.extend(
            [
                [round((total_distance_m + distance_m) / 1_000, 4), round(elevation_m, 2)]
                for distance_m, elevation_m in segment_profile
            ]
        )
        ascent_m += segment_ascent
        descent_m += segment_descent
        geometry.append(simplify_segment(segment))

        for previous, current in zip(segment, segment[1:], strict=False):
            if previous.recorded_at is None or current.recorded_at is None:
                continue
            elapsed = (current.recorded_at - previous.recorded_at).total_seconds()
            if not 0 < elapsed <= 300:
                continue
            speed_kmh = haversine_m(previous, current) / elapsed * 3.6
            if 1 <= speed_kmh <= 12:
                moving_seconds += elapsed
        total_distance_m += distances[-1]

    geometry_count = sum(len(segment) for segment in geometry)
    if geometry_count > MAX_MAP_POINTS:
        ratio = MAX_MAP_POINTS / geometry_count
        geometry = [
            evenly_cap(segment, max(2, round(len(segment) * ratio))) for segment in geometry
        ]
    profile = evenly_cap(profile, MAX_PROFILE_POINTS)

    timestamps = sorted(point.recorded_at for point in all_points if point.recorded_at is not None)
    elevations = [point.elevation_m for point in all_points if point.elevation_m is not None]
    latitudes = [point.lat for point in all_points]
    longitudes = [point.lon for point in all_points]
    started_at = timestamps[0] if timestamps else None
    finished_at = timestamps[-1] if timestamps else None

    return {
        "distance_m": total_distance_m,
        "ascent_m": ascent_m if profile else None,
        "descent_m": descent_m if profile else None,
        "elevation_min_m": min(elevations) if elevations else None,
        "elevation_max_m": max(elevations) if elevations else None,
        "duration_seconds": (
            (finished_at - started_at).total_seconds() if started_at and finished_at else None
        ),
        "moving_seconds": moving_seconds if timestamps else None,
        "started_at": started_at.isoformat() if started_at else None,
        "finished_at": finished_at.isoformat() if finished_at else None,
        "bounds": [[min(latitudes), min(longitudes)], [max(latitudes), max(longitudes)]],
        "segments": geometry,
        "profile": profile,
        "point_count": len(all_points),
    }


def parse_exif_datetime(exif: Image.Exif, timezone_name: str) -> datetime | None:
    exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
    date_text = exif_ifd.get(ExifTags.Base.DateTimeOriginal) or exif.get(ExifTags.Base.DateTime)
    if not date_text:
        return None
    try:
        captured = datetime.strptime(str(date_text), "%Y:%m:%d %H:%M:%S")
    except ValueError as exc:
        raise ContentError(f"Fecha EXIF inválida: {date_text}") from exc
    offset = exif_ifd.get(ExifTags.Base.OffsetTimeOriginal) or exif_ifd.get(
        ExifTags.Base.OffsetTime
    )
    if offset:
        try:
            offset_zone = datetime.strptime(str(offset), "%z").tzinfo
        except ValueError as exc:
            raise ContentError(f"Zona horaria EXIF inválida: {offset}") from exc
        return captured.replace(tzinfo=offset_zone)
    return captured.replace(tzinfo=ZoneInfo(timezone_name))


def _gps_coordinate(values: Any, reference: str) -> float:
    degrees, minutes, seconds = (float(value) for value in values)
    coordinate = degrees + minutes / 60 + seconds / 3_600
    return -coordinate if reference in {"S", "W"} else coordinate


def parse_exif_gps(exif: Image.Exif) -> tuple[float, float, float | None] | None:
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    latitude = gps.get(ExifTags.GPS.GPSLatitude)
    latitude_ref = gps.get(ExifTags.GPS.GPSLatitudeRef)
    longitude = gps.get(ExifTags.GPS.GPSLongitude)
    longitude_ref = gps.get(ExifTags.GPS.GPSLongitudeRef)
    if not all((latitude, latitude_ref, longitude, longitude_ref)):
        return None
    altitude_value = gps.get(ExifTags.GPS.GPSAltitude)
    altitude = float(altitude_value) if altitude_value is not None else None
    if altitude is not None and gps.get(ExifTags.GPS.GPSAltitudeRef) == 1:
        altitude = -altitude
    return (
        _gps_coordinate(latitude, str(latitude_ref)),
        _gps_coordinate(longitude, str(longitude_ref)),
        altitude,
    )


def interpolate_track_position(
    segments: list[list[TrackPoint]], captured_at: datetime
) -> tuple[float, float] | None:
    timed_points = sorted(
        (point for segment in segments for point in segment if point.recorded_at is not None),
        key=lambda point: point.recorded_at,
    )
    if not timed_points:
        return None
    timestamps = [point.recorded_at for point in timed_points]
    index = bisect_left(timestamps, captured_at)
    if index == 0:
        if timed_points[0].recorded_at - captured_at <= PHOTO_TIME_TOLERANCE:
            return timed_points[0].lat, timed_points[0].lon
        return None
    if index == len(timed_points):
        if captured_at - timed_points[-1].recorded_at <= PHOTO_TIME_TOLERANCE:
            return timed_points[-1].lat, timed_points[-1].lon
        return None

    previous = timed_points[index - 1]
    current = timed_points[index]
    elapsed = (current.recorded_at - previous.recorded_at).total_seconds()
    fraction = (
        0.0
        if elapsed <= 0
        else (captured_at - previous.recorded_at).total_seconds() / elapsed
    )
    return (
        previous.lat + fraction * (current.lat - previous.lat),
        previous.lon + fraction * (current.lon - previous.lon),
    )


def load_photo_overrides(route_dir: Path) -> dict[str, dict[str, str]]:
    csv_path = route_dir / "fotos.csv"
    if not csv_path.exists():
        return {}
    try:
        with csv_path.open(encoding="utf-8", newline="") as file:
            rows = list(csv.DictReader(file))
    except OSError as exc:
        raise ContentError(f"No se pudo leer {csv_path}: {exc}") from exc
    overrides: dict[str, dict[str, str]] = {}
    for row in rows:
        filename = (row.get("archivo") or "").strip()
        if not filename:
            raise ContentError(f"Fila sin archivo en {csv_path}.")
        if filename in overrides:
            raise ContentError(f"Foto duplicada en {csv_path}: {filename}")
        overrides[filename] = {key: (value or "").strip() for key, value in row.items()}
    return overrides


def build_photo(
    photo_path: Path,
    route_dir: Path,
    route_title: str,
    timezone_name: str,
    segments: list[list[TrackPoint]],
    override: dict[str, str],
) -> dict[str, Any]:
    try:
        with Image.open(photo_path) as image:
            exif = image.getexif()
            captured_at = parse_exif_datetime(exif, timezone_name)
            gps = parse_exif_gps(exif)
            original_size = [image.width, image.height]
    except (OSError, SyntaxError) as exc:
        raise ContentError(f"No se pudo leer la foto {photo_path}: {exc}") from exc

    if override.get("fecha_hora"):
        captured_at = parse_timestamp(override["fecha_hora"])
    if captured_at is None:
        raise ContentError(f"{photo_path} no contiene fecha EXIF ni fecha_hora en fotos.csv.")

    latitude_text = override.get("latitud")
    longitude_text = override.get("longitud")
    if bool(latitude_text) != bool(longitude_text):
        raise ContentError(f"{photo_path}: latitud y longitud deben proporcionarse juntas.")
    if latitude_text and longitude_text:
        try:
            latitude = float(latitude_text)
            longitude = float(longitude_text)
        except ValueError as exc:
            raise ContentError(f"Coordenadas manuales inválidas en {photo_path}.") from exc
        altitude = gps[2] if gps else None
        location_source = "manual"
    elif gps:
        latitude, longitude, altitude = gps
        location_source = "EXIF"
    else:
        interpolated = interpolate_track_position(segments, captured_at)
        if interpolated is None:
            raise ContentError(
                f"{photo_path} no tiene GPS y su fecha no permite interpolar una posición."
            )
        latitude, longitude = interpolated
        altitude = None
        location_source = "interpolada"

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ContentError(f"Coordenadas fuera de rango en {photo_path}.")
    nearest_track_m = min(
        haversine_m((latitude, longitude), point) for segment in segments for point in segment
    )
    if nearest_track_m > PHOTO_TRACK_TOLERANCE_M:
        raise ContentError(
            f"{photo_path} está a {nearest_track_m:.0f} m del track; revisa su asociación."
        )

    description = override.get("descripcion", "")
    alt_text = override.get("texto_alt") or (
        f"Foto de {route_title} tomada el {captured_at.date().isoformat()}"
    )
    relative_path = photo_path.relative_to(route_dir).as_posix()
    return {
        "id": re.sub(r"[^a-z0-9]+", "-", photo_path.stem.lower()).strip("-"),
        "source_file": relative_path,
        "captured_at": captured_at.isoformat(),
        "lat": latitude,
        "lon": longitude,
        "altitude_m": altitude,
        "location_source": location_source,
        "nearest_track_m": nearest_track_m,
        "description": description,
        "alt_text": alt_text,
        "original_size": original_size,
        "_source_path": photo_path,
    }


def write_photo_variants(photo: dict[str, Any], route_id: str, web_output: Path) -> None:
    source = Path(photo["_source_path"])
    filename = f"{photo['id']}.jpg"
    thumbnail_dir = web_output / "fotos" / route_id / "miniaturas"
    image_dir = web_output / "fotos" / route_id / "web"
    thumbnail_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    thumbnail_path = thumbnail_dir / filename
    image_path = image_dir / filename

    with Image.open(source) as original:
        oriented = ImageOps.exif_transpose(original).convert("RGB")
        web_image = oriented.copy()
        web_image.thumbnail((1_280, 1_280), Image.Resampling.LANCZOS)
        web_image.save(image_path, "JPEG", quality=82, optimize=True, progressive=True)
        thumbnail = oriented.copy()
        thumbnail.thumbnail((480, 480), Image.Resampling.LANCZOS)
        thumbnail.save(thumbnail_path, "JPEG", quality=78, optimize=True, progressive=True)

    photo["image_url"] = f"generated/fotos/{route_id}/web/{filename}"
    photo["thumbnail_url"] = f"generated/fotos/{route_id}/miniaturas/{filename}"


def classify_effort(
    distance_m: float,
    ascent_m: float | None,
    moving_seconds: float | None,
    categories: list[dict[str, Any]],
) -> tuple[float, dict[str, Any]]:
    effort_km = distance_m / 1_000 + (ascent_m or 0.0) / 100
    effort_level = len(categories) - 1
    time_level = 0
    for index, category in enumerate(categories):
        maximum = category.get("max_km_esfuerzo")
        if maximum is None or effort_km < float(maximum):
            effort_level = index
            break
    if moving_seconds is not None:
        moving_hours = moving_seconds / 3_600
        time_level = len(categories) - 1
        for index, category in enumerate(categories):
            maximum = category.get("max_horas_movimiento")
            if maximum is None or moving_hours < float(maximum):
                time_level = index
                break
    return effort_km, categories[max(effort_level, time_level)]


def build_route(
    route_dir: Path, classification: dict[str, Any]
) -> dict[str, Any] | None:
    manifest_path = route_dir / "ruta.yml"
    manifest = load_yaml(manifest_path)
    if manifest.get("version_esquema") != 1:
        raise ContentError(f"version_esquema no soportada en {manifest_path}.")
    route_id = str(manifest.get("id", ""))
    if not SLUG_PATTERN.fullmatch(route_id):
        raise ContentError(f"id inválido en {manifest_path}: {route_id}")
    if route_id != route_dir.name:
        raise ContentError(f"El id {route_id} debe coincidir con la carpeta {route_dir.name}.")
    if manifest.get("publicada", True) is False:
        return None

    title = str(manifest.get("titulo", "")).strip()
    timezone_name = str(manifest.get("zona_horaria", "")).strip()
    track_filename = str(manifest.get("archivo_gpx", "")).strip()
    if not title or not timezone_name or not track_filename:
        raise ContentError(f"{manifest_path} requiere titulo, zona_horaria y archivo_gpx.")
    try:
        ZoneInfo(timezone_name)
    except (KeyError, ValueError) as exc:
        raise ContentError(f"Zona horaria desconocida en {manifest_path}: {timezone_name}") from exc

    track_path = confined_path(route_dir, track_filename, field="archivo_gpx")
    if not track_path.is_file():
        raise ContentError(f"No existe el GPX declarado: {track_path}")
    segments, gpx_metadata = parse_gpx(track_path)
    analysis = analyze_track(segments)
    categories = classification.get("categorias")
    if not isinstance(categories, list) or not categories:
        raise ContentError("clasificacion.yml debe declarar al menos una categoría.")
    effort_km, effort_category = classify_effort(
        analysis["distance_m"],
        analysis["ascent_m"],
        analysis["moving_seconds"],
        categories,
    )

    overrides = load_photo_overrides(route_dir)
    photos_dir = route_dir / "fotos"
    photo_paths = (
        sorted(
            path
            for path in photos_dir.iterdir()
            if path.is_file() and path.suffix.lower() in PHOTO_EXTENSIONS
        )
        if photos_dir.is_dir()
        else []
    )
    photos: list[dict[str, Any]] = []
    used_overrides: set[str] = set()
    for photo_path in photo_paths:
        relative = photo_path.relative_to(route_dir).as_posix()
        override = overrides.get(relative) or overrides.get(photo_path.name) or {}
        if override:
            used_overrides.add(relative if relative in overrides else photo_path.name)
        photos.append(
            build_photo(
                photo_path,
                route_dir,
                title,
                timezone_name,
                segments,
                override,
            )
        )
    unused_overrides = sorted(set(overrides) - used_overrides)
    if unused_overrides:
        raise ContentError(
            f"fotos.csv en {route_dir} refiere archivos inexistentes: {', '.join(unused_overrides)}"
        )
    photo_ids = [photo["id"] for photo in photos]
    if len(photo_ids) != len(set(photo_ids)):
        raise ContentError(f"Dos fotos generan el mismo id dentro de {route_dir}.")

    started_at = analysis["started_at"]
    local_date = (
        datetime.fromisoformat(started_at).astimezone(ZoneInfo(timezone_name)).date().isoformat()
        if started_at
        else None
    )
    return {
        "schema_version": 1,
        "id": route_id,
        "title": title,
        "description": str(manifest.get("descripcion", "")),
        "region": str(manifest.get("region", "")),
        "activity": str(manifest.get("actividad", gpx_metadata.get("type") or "senderismo")),
        "timezone": timezone_name,
        "date": local_date,
        "gpx_name": gpx_metadata.get("name"),
        "metrics": {
            key: analysis[key]
            for key in (
                "distance_m",
                "ascent_m",
                "descent_m",
                "elevation_min_m",
                "elevation_max_m",
                "duration_seconds",
                "moving_seconds",
                "point_count",
            )
        }
        | {
            "effort_km": effort_km,
            "effort": {
                "id": effort_category["id"],
                "label": effort_category["etiqueta"],
                "color": effort_category["color"],
            },
        },
        "started_at": analysis["started_at"],
        "finished_at": analysis["finished_at"],
        "bounds": analysis["bounds"],
        "segments": analysis["segments"],
        "profile": analysis["profile"],
        "photos": photos,
        "_cover_source": str(manifest.get("portada", "")),
    }


def route_summary(route: dict[str, Any]) -> dict[str, Any]:
    return {
        key: route[key]
        for key in ("id", "title", "description", "region", "activity", "timezone", "date")
    } | {
        "metrics": route["metrics"],
        "photo_count": len(route["photos"]),
        "cover_url": route.get("cover_url"),
    }


def _clean_generated(path: Path) -> None:
    resolved = path.resolve()
    if len(resolved.parts) < 4 or resolved == REPO_ROOT.resolve():
        raise RuntimeError(f"Ruta de salida insegura: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_project(
    routes_root: Path = DEFAULT_ROUTES,
    config_root: Path = DEFAULT_CONFIG,
    data_output: Path | None = None,
    web_output: Path | None = None,
) -> dict[str, Any]:
    classification = load_yaml(config_root / "clasificacion.yml")
    maps = load_yaml(config_root / "mapas.yml")
    route_dirs = sorted(path.parent for path in routes_root.glob("*/ruta.yml"))
    if not route_dirs:
        raise ContentError(f"No se encontraron rutas con ruta.yml en {routes_root}.")

    routes: list[dict[str, Any]] = []
    route_ids: set[str] = set()
    for route_dir in route_dirs:
        route = build_route(route_dir, classification)
        if route is None:
            continue
        if route["id"] in route_ids:
            raise ContentError(f"id de ruta duplicado: {route['id']}")
        route_ids.add(route["id"])
        routes.append(route)

    routes.sort(key=lambda route: (route.get("date") or "", route["id"]), reverse=True)
    if (data_output is None) != (web_output is None):
        raise ValueError("data_output y web_output deben proporcionarse juntos.")

    if data_output is not None and web_output is not None:
        _clean_generated(data_output)
        _clean_generated(web_output)

    for route in routes:
        for photo in route["photos"]:
            if web_output is not None:
                write_photo_variants(photo, route["id"], web_output)
            else:
                filename = f"{photo['id']}.jpg"
                photo["image_url"] = f"generated/fotos/{route['id']}/web/{filename}"
                photo["thumbnail_url"] = (
                    f"generated/fotos/{route['id']}/miniaturas/{filename}"
                )
            del photo["_source_path"]

        cover_source = route.pop("_cover_source")
        cover_photo = next(
            (photo for photo in route["photos"] if photo["source_file"] == cover_source),
            route["photos"][0] if route["photos"] else None,
        )
        route["cover_url"] = cover_photo["image_url"] if cover_photo else None

    catalog = {
        "schema_version": 1,
        "default_map": maps.get("mapa_predeterminado"),
        "maps": maps.get("mapas", []),
        "routes": [route_summary(route) for route in routes],
    }
    if data_output is not None:
        write_json(data_output / "catalogo.json", catalog)
        for route in routes:
            write_json(data_output / "rutas" / f"{route['id']}.json", route)
    return {"catalog": catalog, "routes": routes}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Valida y prepara las rutas para la app.")
    parser.add_argument("--check", action="store_true", help="Validar sin escribir artefactos.")
    parser.add_argument("--routes", type=Path, default=DEFAULT_ROUTES)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--data-output", type=Path, default=DEFAULT_DATA_OUTPUT)
    parser.add_argument("--web-output", type=Path, default=DEFAULT_WEB_OUTPUT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = build_project(
            routes_root=args.routes,
            config_root=args.config,
            data_output=None if args.check else args.data_output,
            web_output=None if args.check else args.web_output,
        )
    except ContentError as exc:
        print(f"Error de contenido: {exc}", file=sys.stderr)
        return 2

    print(f"Rutas válidas: {len(result['routes'])}")
    for route in result["routes"]:
        metrics = route["metrics"]
        print(
            f"- {route['id']}: {metrics['distance_m'] / 1_000:.2f} km, "
            f"+{(metrics['ascent_m'] or 0):.0f}/-{(metrics['descent_m'] or 0):.0f} m, "
            f"{len(route['photos'])} fotos, {metrics['effort']['label']}"
        )
    if args.check:
        print("Validación terminada; no se escribieron artefactos.")
    else:
        print(f"Datos generados en {args.data_output}")
        print(f"Imágenes web generadas en {args.web_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
