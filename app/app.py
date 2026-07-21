from __future__ import annotations

import json
import math
import unicodedata
import zlib
from html import escape
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
from ipyleaflet import (
    CircleMarker,
    DivIcon,
    FullScreenControl,
    LayerGroup,
    Map,
    Marker,
    Polyline,
    ScaleControl,
    TileLayer,
    WidgetControl,
)
from ipywidgets import HTML, Dropdown
from shiny import App, reactive, render, ui
from shinywidgets import output_widget, reactive_read, render_widget

APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "_generated"
WWW_DIR = APP_DIR / "www"
EARTH_RADIUS_M = 6_371_008.8
DEFAULT_DIRECT_CONNECTION_M = 100.0
DEFAULT_WARNING_CONNECTION_M = 500.0
AVAILABLE_SEGMENT_COLORS = (
    "#c45536",
    "#2f6db0",
    "#7956a8",
    "#b13f57",
    "#d18a20",
    "#a44b87",
    "#5162a8",
    "#8f5646",
    "#d4618c",
    "#3f7cac",
    "#cf6f2e",
    "#6b5b95",
)
SELECTED_SEGMENT_COLOR = "#16803d"
FALLBACK_MAP_CONFIG = {
    "id": "osm",
    "nombre": "Estándar",
    "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "atribucion": "© OpenStreetMap contributors",
    "zoom_minimo": 1,
    "zoom_maximo": 19,
}

Point = list[float]
Selection = tuple[tuple[str, bool], ...]
SpatialIndex = dict[tuple[int, int], list[tuple[str, Point]]]


def base_map_layer_options(
    map_configs: list[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Translate the map catalog into tile-layer options."""
    configs = map_configs or [FALLBACK_MAP_CONFIG]
    layer_options: list[dict[str, Any]] = []
    for config in configs:
        options: dict[str, Any] = {
            "url": config.get("url", FALLBACK_MAP_CONFIG["url"]),
            "attribution": config.get("atribucion", FALLBACK_MAP_CONFIG["atribucion"]),
            "min_zoom": config.get("zoom_minimo", FALLBACK_MAP_CONFIG["zoom_minimo"]),
            "max_zoom": config.get("zoom_maximo", FALLBACK_MAP_CONFIG["zoom_maximo"]),
            "name": config.get("nombre", config.get("id", "Mapa base")),
            "base": True,
        }
        if config.get("zoom_nativo_maximo") is not None:
            options["max_native_zoom"] = config["zoom_nativo_maximo"]
        layer_options.append(options)
    return tuple(layer_options)


def base_map_layers(
    map_configs: list[dict[str, Any]],
) -> tuple[TileLayer, ...]:
    """Create tile widgets inside the active Shiny session."""
    return tuple(TileLayer(**options) for options in base_map_layer_options(map_configs))


def default_base_map_index(
    map_configs: list[dict[str, Any]],
    default_map_id: str | None,
) -> int:
    configs = map_configs or [FALLBACK_MAP_CONFIG]
    return next(
        (index for index, config in enumerate(configs) if config.get("id") == default_map_id),
        0,
    )


def replace_base_map(
    map_widget: Map,
    layers: tuple[TileLayer, ...],
    selected_index: int,
) -> None:
    """Replace the active tile layer without leaving transparent bases behind."""
    new_layer = layers[selected_index]
    current_layer = next((layer for layer in layers if layer in map_widget.layers), None)
    if current_layer is new_layer:
        return
    if current_layer is None:
        map_widget.add(new_layer)
    else:
        map_widget.substitute(current_layer, new_layer)


def base_map_picker(
    map_widget: Map,
    layers: tuple[TileLayer, ...],
    selected_index: int,
) -> Dropdown:
    """Create a picker that replaces the active base layer on the map."""
    picker = Dropdown(
        options=[(layer.name, index) for index, layer in enumerate(layers)],
        value=selected_index,
        description="Mapa:",
        layout={"width": "210px"},
        style={"description_width": "45px"},
    )
    picker.add_class("map-layer-picker")

    def switch_base_map(change: dict[str, Any]) -> None:
        replace_base_map(map_widget, layers, change["new"])

    picker.observe(switch_base_map, names="value")
    return picker


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_content() -> tuple[dict[str, Any], dict[str, dict[str, Any]], str | None]:
    catalog_path = DATA_DIR / "catalogo.json"
    segments_path = DATA_DIR / "tramos.json"
    if not catalog_path.exists() or not segments_path.exists():
        return (
            {"maps": [], "builder": {}},
            {},
            (
                "Faltan los datos del constructor. Ejecuta "
                "`uv run python scripts/build_content.py` desde la raíz del proyecto."
            ),
        )
    try:
        catalog = read_json(catalog_path)
        payload = read_json(segments_path)
        segments = {segment["id"]: segment for segment in payload["segments"]}
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return {"maps": [], "builder": {}}, {}, f"No se pudieron cargar los tramos: {exc}"
    return catalog, segments, None


CATALOG, SEGMENTS, CONTENT_ERROR = load_content()
BUILDER_CONFIG = CATALOG.get("builder", {})
DIRECT_CONNECTION_M = float(BUILDER_CONFIG.get("direct_connection_m", DEFAULT_DIRECT_CONNECTION_M))
WARNING_CONNECTION_M = float(
    BUILDER_CONFIG.get("warning_connection_m", DEFAULT_WARNING_CONNECTION_M)
)


def segment_within_bounds(segment: dict[str, Any], bounds: Any) -> bool:
    if not bounds:
        return True
    (south, west), (north, east) = bounds
    (segment_south, segment_west), (segment_north, segment_east) = segment["bounds"]
    return (
        segment_south >= south
        and segment_west >= west
        and segment_north <= north
        and segment_east <= east
    )


def normalized_search(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").casefold()


def segment_network_color(segment_id: str) -> str:
    color_index = zlib.crc32(segment_id.encode("utf-8")) % len(AVAILABLE_SEGMENT_COLORS)
    return AVAILABLE_SEGMENT_COLORS[color_index]


def point_distance_m(a: Point, b: Point) -> float:
    phi_a = math.radians(a[0])
    phi_b = math.radians(b[0])
    delta_phi = math.radians(b[0] - a[0])
    delta_lambda = math.radians(b[1] - a[1])
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(value))


def spatial_cell(point: Point, cell_size_m: float) -> tuple[int, int]:
    latitude_m = point[0] * 111_320
    longitude_m = point[1] * 111_320 * math.cos(math.radians(point[0]))
    return math.floor(latitude_m / cell_size_m), math.floor(longitude_m / cell_size_m)


def sampled_geometry_points(
    segment: dict[str, Any],
    *,
    spacing_m: float,
) -> list[Point]:
    sampled: list[Point] = []
    for geometry in segment["geometry"]:
        if not geometry:
            continue
        sampled.append(geometry[0])
        for start, end in zip(geometry, geometry[1:], strict=False):
            steps = max(1, math.ceil(point_distance_m(start, end) / spacing_m))
            for step in range(1, steps + 1):
                fraction = step / steps
                sampled.append(
                    [
                        start[0] + fraction * (end[0] - start[0]),
                        start[1] + fraction * (end[1] - start[1]),
                    ]
                )
    return sampled


def build_segment_spatial_index(
    segments: dict[str, dict[str, Any]],
    *,
    cell_size_m: float = DEFAULT_DIRECT_CONNECTION_M,
) -> SpatialIndex:
    index: SpatialIndex = {}
    for segment_id, segment in segments.items():
        for point in sampled_geometry_points(segment, spacing_m=cell_size_m / 2):
            index.setdefault(spatial_cell(point, cell_size_m), []).append((segment_id, point))
    return index


def nearby_geometry_ids(
    segment: dict[str, Any],
    spatial_index: SpatialIndex,
    *,
    distance_m: float = DEFAULT_DIRECT_CONNECTION_M,
) -> set[str]:
    nearby_ids: set[str] = set()
    for point in sampled_geometry_points(segment, spacing_m=distance_m / 2):
        row, column = spatial_cell(point, distance_m)
        for row_offset in (-1, 0, 1):
            for column_offset in (-1, 0, 1):
                for candidate_id, candidate_point in spatial_index.get(
                    (row + row_offset, column + column_offset), []
                ):
                    if (
                        candidate_id != segment["id"]
                        and point_distance_m(point, candidate_point) <= distance_m
                    ):
                        nearby_ids.add(candidate_id)
    return nearby_ids


def oriented_geometry(segment: dict[str, Any], is_reversed: bool) -> list[list[Point]]:
    geometry = segment["geometry"]
    if not is_reversed:
        return geometry
    return [list(reversed(part)) for part in reversed(geometry)]


def oriented_endpoints(segment: dict[str, Any], is_reversed: bool) -> tuple[Point, Point]:
    if is_reversed:
        return segment["end"], segment["start"]
    return segment["start"], segment["end"]


def flattened_geometry(segment: dict[str, Any], is_reversed: bool = False) -> list[Point]:
    return [point for geometry in oriented_geometry(segment, is_reversed) for point in geometry]


def polyline_distances(points: list[Point]) -> list[float]:
    distances = [0.0]
    for start, end in zip(points, points[1:], strict=False):
        distances.append(distances[-1] + point_distance_m(start, end))
    return distances


def point_at_polyline_distance(
    points: list[Point],
    distances: list[float],
    distance_m: float,
) -> Point:
    if not points:
        raise ValueError("No se puede interpolar una geometría vacía.")
    target = min(max(distance_m, 0.0), distances[-1])
    for index in range(1, len(points)):
        if distances[index] < target:
            continue
        span_m = distances[index] - distances[index - 1]
        fraction = 0.0 if span_m == 0 else (target - distances[index - 1]) / span_m
        return [
            points[index - 1][0] + fraction * (points[index][0] - points[index - 1][0]),
            points[index - 1][1] + fraction * (points[index][1] - points[index - 1][1]),
        ]
    return points[-1]


def nearest_position_on_polyline(
    point: Point,
    points: list[Point],
    distances: list[float] | None = None,
) -> tuple[Point, float, float]:
    if not points:
        raise ValueError("No se puede buscar sobre una geometría vacía.")
    distances = distances or polyline_distances(points)
    if len(points) == 1:
        return points[0], 0.0, point_distance_m(point, points[0])

    latitude_scale = 111_320.0
    longitude_scale = latitude_scale * math.cos(math.radians(point[0]))
    best_point = points[0]
    best_along_m = 0.0
    best_distance_m = math.inf
    for index, (start, end) in enumerate(zip(points, points[1:], strict=False)):
        start_x = (start[1] - point[1]) * longitude_scale
        start_y = (start[0] - point[0]) * latitude_scale
        end_x = (end[1] - point[1]) * longitude_scale
        end_y = (end[0] - point[0]) * latitude_scale
        delta_x = end_x - start_x
        delta_y = end_y - start_y
        denominator = delta_x * delta_x + delta_y * delta_y
        fraction = (
            0.0
            if denominator == 0
            else min(1.0, max(0.0, -(start_x * delta_x + start_y * delta_y) / denominator))
        )
        projected = [
            start[0] + fraction * (end[0] - start[0]),
            start[1] + fraction * (end[1] - start[1]),
        ]
        projected_distance_m = point_distance_m(point, projected)
        if projected_distance_m < best_distance_m:
            best_point = projected
            best_along_m = distances[index] + fraction * (
                distances[index + 1] - distances[index]
            )
            best_distance_m = projected_distance_m
    return best_point, best_along_m, best_distance_m


def sampled_polyline_positions(
    points: list[Point],
    *,
    spacing_m: float = 25.0,
) -> list[tuple[Point, float]]:
    distances = polyline_distances(points)
    if not distances or distances[-1] == 0:
        return [(points[0], 0.0)] if points else []
    sample_count = max(1, math.ceil(distances[-1] / spacing_m))
    return [
        (
            point_at_polyline_distance(points, distances, distances[-1] * index / sample_count),
            distances[-1] * index / sample_count,
        )
        for index in range(sample_count + 1)
    ]


def nearest_polyline_connection(
    first_points: list[Point],
    second_points: list[Point],
) -> dict[str, Any]:
    first_samples = sampled_polyline_positions(first_points)
    second_samples = sampled_polyline_positions(second_points)
    if not first_samples or not second_samples:
        raise ValueError("No se puede conectar una geometría vacía.")

    reference_latitude = sum(point[0] for point, _ in first_samples + second_samples) / (
        len(first_samples) + len(second_samples)
    )
    longitude_scale = 111_320 * math.cos(math.radians(reference_latitude))
    latitude_scale = 111_320.0
    best_pair = (first_samples[0], second_samples[0])
    best_squared_distance = math.inf
    for first_sample in first_samples:
        first_x = first_sample[0][1] * longitude_scale
        first_y = first_sample[0][0] * latitude_scale
        for second_sample in second_samples:
            delta_x = first_x - second_sample[0][1] * longitude_scale
            delta_y = first_y - second_sample[0][0] * latitude_scale
            squared_distance = delta_x * delta_x + delta_y * delta_y
            if squared_distance < best_squared_distance:
                best_squared_distance = squared_distance
                best_pair = (first_sample, second_sample)

    first_distances = polyline_distances(first_points)
    second_distances = polyline_distances(second_points)
    second_point, second_along_m, _ = nearest_position_on_polyline(
        best_pair[0][0], second_points, second_distances
    )
    first_point, first_along_m, _ = nearest_position_on_polyline(
        second_point, first_points, first_distances
    )
    second_point, second_along_m, gap_m = nearest_position_on_polyline(
        first_point, second_points, second_distances
    )
    return {
        "first_point": first_point,
        "first_along_m": first_along_m,
        "second_point": second_point,
        "second_along_m": second_along_m,
        "gap_m": gap_m,
    }


def slice_polyline(points: list[Point], start_m: float, end_m: float) -> list[Point]:
    distances = polyline_distances(points)
    if not distances:
        return []
    reverse_result = end_m < start_m
    lower_m, upper_m = sorted((start_m, end_m))
    sliced = [point_at_polyline_distance(points, distances, lower_m)]
    sliced.extend(
        point
        for point, distance_m in zip(points, distances, strict=True)
        if lower_m < distance_m < upper_m
    )
    sliced.append(point_at_polyline_distance(points, distances, upper_m))
    return list(reversed(sliced)) if reverse_result else sliced


def closest_orientation(endpoint: Point, segment: dict[str, Any]) -> tuple[float, bool]:
    distance_to_start = point_distance_m(endpoint, segment["start"])
    distance_to_end = point_distance_m(endpoint, segment["end"])
    if distance_to_end < distance_to_start:
        return distance_to_end, True
    return distance_to_start, False


def selection_endpoint(selection: Selection, segments: dict[str, dict[str, Any]]) -> Point | None:
    if not selection:
        return None
    segment_id, is_reversed = selection[-1]
    segment = segments.get(segment_id)
    if segment is None:
        return None
    return oriented_endpoints(segment, is_reversed)[1]


def available_segments(
    selection: Selection,
    segments: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    selected_ids = {segment_id for segment_id, _ in selection}
    return [segment for segment_id, segment in segments.items() if segment_id not in selected_ids]


def active_segment_ids(
    selection: Selection,
    segments: dict[str, dict[str, Any]],
    spatial_index: SpatialIndex,
    *,
    crossing_distance_m: float = DEFAULT_DIRECT_CONNECTION_M,
    connection_distance_m: float = DEFAULT_WARNING_CONNECTION_M,
    last_geometry: list[list[Point]] | None = None,
    endpoint: Point | None = None,
) -> set[str]:
    selected_ids = {segment_id for segment_id, _ in selection}
    if not selection:
        return set(segments) - selected_ids

    last_segment_id = selection[-1][0]
    last_segment = segments.get(last_segment_id)
    endpoint = endpoint or selection_endpoint(selection, segments)
    if last_segment is None or endpoint is None:
        return set()

    crossing_geometry = last_geometry or last_segment["geometry"]
    active_ids = nearby_geometry_ids(
        {"id": last_segment_id, "geometry": crossing_geometry},
        spatial_index,
        distance_m=crossing_distance_m,
    )
    for segment_id, segment in segments.items():
        if segment_id not in selected_ids:
            gap_m, _ = closest_orientation(endpoint, segment)
            if gap_m <= connection_distance_m:
                active_ids.add(segment_id)
    return active_ids - selected_ids


def append_segment(
    selection: Selection,
    segment_id: str,
    segments: dict[str, dict[str, Any]],
    *,
    click_point: Point | None = None,
) -> Selection:
    if segment_id not in segments or any(item[0] == segment_id for item in selection):
        return selection
    endpoint = selection_endpoint(selection, segments)
    is_reversed = False
    if endpoint is not None:
        is_reversed = closest_orientation(endpoint, segments[segment_id])[1]
    if selection and click_point is not None:
        previous_id, previous_reversed = selection[-1]
        previous = segments.get(previous_id)
        candidate = segments[segment_id]
        if previous is not None:
            previous_points = flattened_geometry(previous, previous_reversed)
            candidate_points = flattened_geometry(candidate)
            connection = nearest_polyline_connection(previous_points, candidate_points)
            _, click_along_m, _ = nearest_position_on_polyline(click_point, candidate_points)
            if connection["gap_m"] <= DIRECT_CONNECTION_M and not math.isclose(
                click_along_m,
                connection["second_along_m"],
                abs_tol=1.0,
            ):
                is_reversed = click_along_m < connection["second_along_m"]
    return (*selection, (segment_id, is_reversed))


def rank_segments(
    segments: list[dict[str, Any]],
    *,
    selected_ids: set[str] | None = None,
    query: str = "",
    endpoint: Point | None = None,
) -> list[tuple[dict[str, Any], float | None, bool]]:
    selected_ids = selected_ids or set()
    normalized_query = normalized_search(query.strip())
    candidates = [
        segment
        for segment in segments
        if segment["id"] not in selected_ids
        and (
            not normalized_query
            or normalized_query in normalized_search(f"{segment['title']} {segment['region']}")
        )
    ]
    ranked = []
    for segment in candidates:
        gap_m, reverse_suggested = (
            closest_orientation(endpoint, segment) if endpoint else (None, False)
        )
        ranked.append((segment, gap_m, reverse_suggested))
    if endpoint is None:
        ranked.sort(key=lambda item: (-item[0]["metrics"]["distance_m"], item[0]["id"]))
    else:
        ranked.sort(
            key=lambda item: (
                item[1] if item[1] is not None else math.inf,
                -item[0]["metrics"]["distance_m"],
                item[0]["id"],
            )
        )
    return ranked


def construction_details(
    selection: Selection,
    segments: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for segment_id, is_reversed in selection:
        segment = segments.get(segment_id)
        if segment is None:
            continue
        points = flattened_geometry(segment, is_reversed)
        if not points:
            continue
        distances = polyline_distances(points)
        entries.append(
            {
                "segment": segment,
                "selection_reversed": is_reversed,
                "points": points,
                "geometry_distance_m": distances[-1],
            }
        )

    connections: list[dict[str, Any] | None] = []
    for previous, current in zip(entries, entries[1:], strict=False):
        connection = nearest_polyline_connection(previous["points"], current["points"])
        has_forward_tail = (
            current["geometry_distance_m"] - connection["second_along_m"] > 1.0
        )
        connections.append(
            connection
            if connection["gap_m"] <= DIRECT_CONNECTION_M and has_forward_tail
            else None
        )

    details: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        segment = entry["segment"]
        geometry_distance_m = entry["geometry_distance_m"]
        start_along_m = (
            connections[index - 1]["second_along_m"]
            if index > 0 and connections[index - 1] is not None
            else 0.0
        )
        end_along_m = (
            connections[index]["first_along_m"]
            if index < len(connections) and connections[index] is not None
            else geometry_distance_m
        )
        points = slice_polyline(entry["points"], start_along_m, end_along_m)
        if not points:
            continue

        source_distance_m = float(segment["metrics"]["distance_m"])
        if geometry_distance_m > 0:
            start_fraction = start_along_m / geometry_distance_m
            end_fraction = end_along_m / geometry_distance_m
        else:
            start_fraction = 0.0
            end_fraction = 1.0
        if entry["selection_reversed"]:
            source_start_m = (1 - start_fraction) * source_distance_m
            source_end_m = (1 - end_fraction) * source_distance_m
        else:
            source_start_m = start_fraction * source_distance_m
            source_end_m = end_fraction * source_distance_m
        previous_end = details[-1]["end"] if details else None
        gap_m = point_distance_m(previous_end, points[0]) if previous_end is not None else None
        details.append(
            {
                "segment": segment,
                "reversed": source_end_m < source_start_m,
                "start": points[0],
                "end": points[-1],
                "gap_m": gap_m,
                "geometry": [points],
                "distance_m": abs(source_end_m - source_start_m),
                "source_start_m": source_start_m,
                "source_end_m": source_end_m,
            }
        )
    return details


def profile_elevation_at(profile: list[list[float]], distance_km: float) -> float:
    if distance_km <= profile[0][0]:
        return float(profile[0][1])
    if distance_km >= profile[-1][0]:
        return float(profile[-1][1])
    for previous, current in zip(profile, profile[1:], strict=False):
        if current[0] < distance_km:
            continue
        span_km = current[0] - previous[0]
        fraction = 0.0 if span_km == 0 else (distance_km - previous[0]) / span_km
        return float(previous[1] + fraction * (current[1] - previous[1]))
    return float(profile[-1][1])


def slice_profile(
    profile: list[list[float]],
    start_km: float,
    end_km: float,
) -> list[list[float]]:
    if not profile:
        return []
    lower_km, upper_km = sorted((start_km, end_km))
    lower_km = min(max(lower_km, profile[0][0]), profile[-1][0])
    upper_km = min(max(upper_km, profile[0][0]), profile[-1][0])
    points = [[lower_km, profile_elevation_at(profile, lower_km)]]
    points.extend(
        [float(distance_km), float(elevation_m)]
        for distance_km, elevation_m in profile
        if lower_km < distance_km < upper_km
    )
    if not math.isclose(lower_km, upper_km):
        points.append([upper_km, profile_elevation_at(profile, upper_km)])
    return list(reversed(points)) if end_km < start_km else points


def construction_elevation(
    details: list[dict[str, Any]],
) -> tuple[float | None, float | None]:
    """Return ascent and descent for the portions used by a construction."""
    ascent_m = 0.0
    descent_m = 0.0
    for detail in details:
        segment = detail["segment"]
        metrics = segment["metrics"]
        source_distance_m = float(metrics["distance_m"])
        source_start_m = float(detail["source_start_m"])
        source_end_m = float(detail["source_end_m"])
        lower_m, upper_m = sorted((source_start_m, source_end_m))
        uses_full_segment = math.isclose(lower_m, 0.0, abs_tol=1e-6) and math.isclose(
            upper_m,
            source_distance_m,
            abs_tol=1e-6,
        )
        segment_ascent_m = metrics.get("ascent_m")
        segment_descent_m = metrics.get("descent_m")

        if uses_full_segment and segment_ascent_m is not None and segment_descent_m is not None:
            if detail["reversed"]:
                ascent_m += float(segment_descent_m)
                descent_m += float(segment_ascent_m)
            else:
                ascent_m += float(segment_ascent_m)
                descent_m += float(segment_descent_m)
            continue

        source_points = slice_profile(
            segment.get("profile") or [],
            source_start_m / 1_000,
            source_end_m / 1_000,
        )
        if not source_points:
            return None, None
        for previous, current in zip(source_points, source_points[1:], strict=False):
            change_m = float(current[1]) - float(previous[1])
            ascent_m += max(0.0, change_m)
            descent_m += max(0.0, -change_m)

    return ascent_m, descent_m


def construction_profile(
    selection: Selection,
    segments: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    pieces: list[dict[str, Any]] = []
    accumulated_km = 0.0
    for detail in construction_details(selection, segments):
        if detail["gap_m"] is not None:
            accumulated_km += detail["gap_m"] / 1_000
        segment = detail["segment"]
        segment_id = segment["id"]
        distance_km = detail["distance_m"] / 1_000
        source_profile = segment.get("profile") or []
        source_points = slice_profile(
            source_profile,
            detail["source_start_m"] / 1_000,
            detail["source_end_m"] / 1_000,
        )
        source_start_km = detail["source_start_m"] / 1_000
        points = [
            [accumulated_km + abs(distance_km_local - source_start_km), elevation_m]
            for distance_km_local, elevation_m in source_points
        ]
        pieces.append(
            {
                "id": segment_id,
                "title": segment["title"],
                "start_km": accumulated_km,
                "end_km": accumulated_km + distance_km,
                "points": points,
            }
        )
        accumulated_km += distance_km
    return pieces


def construction_gpx(selection: Selection, segments: dict[str, dict[str, Any]]) -> str:
    details = construction_details(selection, segments)
    titles = [detail["segment"]["title"] for detail in details]
    name = escape(" + ".join(titles)) if titles else "Ruta en construcción"
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="Botas Puestas" '
        'xmlns="http://www.topografix.com/GPX/1/1">',
        "  <trk>",
        f"    <name>{name}</name>",
    ]
    for detail in details:
        profile = detail["segment"].get("profile") or []
        span_m = detail["source_end_m"] - detail["source_start_m"]
        for points in detail["geometry"]:
            distances = polyline_distances(points)
            total_m = distances[-1] if distances else 0.0
            lines.append("    <trkseg>")
            for point, along_m in zip(points, distances, strict=True):
                if profile:
                    fraction = along_m / total_m if total_m > 0 else 0.0
                    source_km = (detail["source_start_m"] + fraction * span_m) / 1_000
                    elevation = profile_elevation_at(profile, source_km)
                    lines.append(
                        f'      <trkpt lat="{point[0]:.6f}" lon="{point[1]:.6f}">'
                        f"<ele>{elevation:.1f}</ele></trkpt>"
                    )
                else:
                    lines.append(f'      <trkpt lat="{point[0]:.6f}" lon="{point[1]:.6f}"/>')
            lines.append("    </trkseg>")
    lines.extend(["  </trk>", "</gpx>", ""])
    return "\n".join(lines)


def combined_bounds(details: list[dict[str, Any]]) -> list[list[float]] | None:
    points = [point for detail in details for segment in detail["geometry"] for point in segment]
    if not points:
        return None
    return [
        [min(point[0] for point in points), min(point[1] for point in points)],
        [max(point[0] for point in points), max(point[1] for point in points)],
    ]


def format_distance(distance_m: float) -> str:
    return f"{distance_m / 1_000:.1f} km" if distance_m >= 1_000 else f"{distance_m:.0f} m"


def format_elevation(ascent_m: float | None, descent_m: float | None) -> str:
    if ascent_m is None or descent_m is None:
        return "Sin datos"
    rounded_ascent_m = round(ascent_m / 10) * 10
    rounded_descent_m = round(descent_m / 10) * 10
    return f"+{rounded_ascent_m:.0f} / −{rounded_descent_m:.0f} m"


def connection_state(gap_m: float | None) -> tuple[str, str]:
    if gap_m is None:
        return "Inicio", "start"
    if gap_m <= DIRECT_CONNECTION_M:
        return f"Conecta a {format_distance(gap_m)}", "connected"
    if gap_m <= WARNING_CONNECTION_M:
        return f"Separado {format_distance(gap_m)}", "warning"
    return f"Hueco de {format_distance(gap_m)}", "disconnected"


def metric_card(label: str, value: str, *, accent: str = "#315c45") -> Any:
    return ui.div(
        ui.tags.span(label, class_="metric-label"),
        ui.tags.strong(value, class_="metric-value"),
        class_="metric-card",
        style=f"--metric-accent: {accent}",
    )


def segment_card(
    segment: dict[str, Any],
    *,
    is_active: bool = False,
) -> Any:
    metrics = segment["metrics"]
    elevation_copy = (
        "Elevación disponible" if metrics["elevation_min_m"] is not None else "Sin elevación"
    )
    return ui.tags.article(
        ui.div(
            ui.tags.span(format_distance(metrics["distance_m"]), class_="segment-distance"),
            ui.tags.span(
                "Por revisar"
                if segment["record_type"] == "por-definir"
                else segment["record_type"],
                class_="segment-status",
            ),
            class_="segment-card-topline",
        ),
        ui.h3(segment["title"]),
        ui.tags.p(
            f"{metrics['point_count']} puntos · {elevation_copy}",
            class_="segment-meta",
        ),
        ui.tags.button(
            "Viendo en el mapa" if is_active else "Ver en el mapa",
            type="button",
            class_="primary-action",
            data_builder_action="explore",
            data_segment_id=segment["id"],
            aria_label=f"Ver {segment['title']} en el mapa",
        ),
        class_=f"segment-card{' segment-card--active' if is_active else ''}",
        style=f"--segment-color: {segment_network_color(segment['id'])}",
    )


app_ui = ui.page_fluid(
    ui.tags.head(
        ui.tags.meta(name="viewport", content="width=device-width, initial-scale=1"),
        ui.tags.meta(
            name="description",
            content="Constructor de rutas de senderismo por tramos de Botas Puestas",
        ),
        ui.tags.link(
            rel="stylesheet",
            href="styles.css?v=descargar-ruta-20260720-1",
        ),
        ui.tags.script(src="route-state.js?v=lista-20260720-1", defer=True),
    ),
    ui.div(
        ui.tags.header(
            ui.div(
                ui.tags.p("CONSTRUCTOR POR TRAMOS", class_="eyebrow"),
                ui.h1("Botas Puestas"),
                ui.tags.p(
                    "Combina caminos existentes y revisa sus conexiones sobre el mapa.",
                    class_="hero-copy",
                ),
            ),
            ui.tags.span("Tepoztlán", class_="place-pill"),
            class_="hero",
        ),
        ui.div(
            CONTENT_ERROR,
            class_="content-error",
            role="alert",
            style=None if CONTENT_ERROR else "display:none",
        ),
        ui.tags.section(
            ui.div(
                ui.tags.span("01", class_="intro-number"),
                ui.div(
                    ui.h2("Empieza por un tramo y construye desde su extremo"),
                    ui.tags.p(
                        "En Construir, pulsa cualquier trazo de color directamente sobre el mapa. "
                        "Si el siguiente tramo cruza el actual, pulsa el lado que quieres "
                        "conservar: "
                        "la ruta se recorta en el cruce. Usa Explorar tramos para inspeccionar "
                        "geometrías antes de agregarlas.",
                    ),
                ),
            ),
            ui.tags.p(
                "Este prototipo conserva la construcción durante la sesión; guardar y publicar "
                "recorridos será el siguiente incremento.",
                class_="session-note",
            ),
            class_="builder-intro",
        ),
        ui.div(
            ui.navset_tab(
                ui.nav_panel(
                    "Construir",
                    ui.div(
                        ui.tags.main(
                            ui.tags.section(
                                ui.div(
                                    ui.tags.p("MAPA", class_="section-kicker"),
                                    ui.h2("Construye directamente sobre la red"),
                                ),
                                output_widget("mapa", height="680px"),
                                class_="map-panel",
                            ),
                            ui.tags.section(
                                ui.div(
                                    ui.tags.p("ALTIMETRÍA", class_="section-kicker"),
                                    ui.h2("Elevación por distancia"),
                                ),
                                output_widget("altimetria", height="300px"),
                                class_="altimetry-panel",
                            ),
                            class_="builder-main",
                        ),
                        ui.tags.aside(
                            ui.output_ui("builder_metrics"),
                            ui.tags.section(
                                ui.div(
                                    ui.div(
                                        ui.tags.p("SECUENCIA", class_="section-kicker"),
                                        ui.h2("Ruta en construcción"),
                                    ),
                                    ui.div(
                                        ui.output_ui("download_route_area"),
                                        ui.tags.button(
                                            "Vaciar",
                                            type="button",
                                            class_="quiet-action",
                                            data_builder_action="clear",
                                        ),
                                        class_="heading-actions",
                                    ),
                                    class_="construction-heading",
                                ),
                                ui.output_ui("construction"),
                                class_="construction-panel",
                            ),
                            class_="builder-side",
                        ),
                        class_="constructor-layout",
                    ),
                    value="construir",
                ),
                ui.nav_panel(
                    "Explorar tramos",
                    ui.div(
                        ui.tags.section(
                            ui.div(
                                ui.tags.p("BIBLIOTECA", class_="section-kicker"),
                                ui.h2("Tramos disponibles"),
                                ui.tags.p(
                                    f"Explora los {len(SEGMENTS)} tramos sin alterar tu ruta. "
                                    "La lista muestra solo los que caben completos en el mapa.",
                                    class_="section-copy",
                                ),
                                ui.input_text(
                                    "buscar_explorar",
                                    "Buscar por nombre",
                                    placeholder="Ej. Meztitla, cumbre, conexión…",
                                ),
                                ui.output_ui("explorer_context"),
                                class_="library-header",
                            ),
                            ui.output_ui("explorer_list"),
                            class_="segment-library explorer-library",
                        ),
                        ui.tags.main(
                            ui.tags.section(
                                ui.output_ui("explorer_summary"),
                                output_widget("mapa_explorar", height="680px"),
                                class_="map-panel explorer-map-panel",
                            ),
                            class_="explorer-workspace",
                        ),
                        class_="explorer-layout",
                    ),
                    value="explorar",
                ),
                id="modo",
                selected="construir",
            ),
            class_="app-tabs",
        ),
        ui.tags.footer(
            "La clasificación editorial se incorporará después. Por ahora el constructor usa "
            "longitud, desnivel, orden, sentido y distancia entre extremos.",
            class_="site-footer",
        ),
        class_="app-container",
    ),
    title="Botas Puestas — Constructor de rutas",
)


def server(input: Any, output: Any, session: Any) -> None:
    selection = reactive.value(())
    explored_segment_id = reactive.value(None)

    def add_to_selection(segment_id: str, *, click_point: Point | None = None) -> None:
        current = selection.get()
        updated = append_segment(current, segment_id, SEGMENTS, click_point=click_point)
        if updated != current:
            selection.set(updated)

    @reactive.effect
    @reactive.event(input.builder_action)
    def handle_builder_action() -> None:
        payload = input.builder_action()
        if not isinstance(payload, dict):
            return
        action = payload.get("action")
        segment_id = str(payload.get("segment_id") or "")
        current = list(selection.get())
        index = next(
            (position for position, item in enumerate(current) if item[0] == segment_id),
            None,
        )

        if action == "explore":
            if segment_id in SEGMENTS and explored_segment_id.get() != segment_id:
                explored_segment_id.set(segment_id)
            return
        if action == "add":
            add_to_selection(segment_id)
            return
        if action == "clear":
            selection.set(())
            return
        if index is None:
            return
        if action == "remove":
            current.pop(index)
        elif action == "reverse":
            current[index] = (current[index][0], not current[index][1])
        elif action == "move-up" and index > 0:
            current[index - 1], current[index] = current[index], current[index - 1]
        elif action == "move-down" and index < len(current) - 1:
            current[index + 1], current[index] = current[index], current[index + 1]
        else:
            return
        selection.set(tuple(current))

    @reactive.calc
    def selected_details() -> list[dict[str, Any]]:
        return construction_details(selection.get(), SEGMENTS)

    @render.ui
    def download_route_area() -> Any:
        if not selected_details():
            return None
        return ui.download_button(
            "descargar_ruta",
            "Descargar",
            class_="quiet-action quiet-action--download",
        )

    @render.download(filename="ruta-botas-puestas.gpx")
    def descargar_ruta() -> Any:
        yield construction_gpx(selection.get(), SEGMENTS)

    @reactive.calc
    def explorer_visible_segments() -> list[dict[str, Any]]:
        viewport = reactive_read(explorer_map, "bounds")
        return [
            segment
            for segment in SEGMENTS.values()
            if segment_within_bounds(segment, viewport)
        ]

    @reactive.calc
    def explorer_results() -> list[tuple[dict[str, Any], float | None, bool]]:
        return rank_segments(
            explorer_visible_segments(),
            query=input.buscar_explorar(),
        )

    @render.ui
    def explorer_context() -> Any:
        query = input.buscar_explorar().strip()
        result_count = len(explorer_results())
        if query:
            title = f"Resultados para “{query}”"
            copy = (
                f"{result_count} de {len(SEGMENTS)} tramos completos en la vista, "
                "ordenados por longitud."
            )
        else:
            title = "Tramos completos en la vista"
            copy = (
                f"{result_count} de {len(SEGMENTS)} tramos. Mueve o acerca el mapa "
                "para actualizar la lista."
            )
        return ui.div(ui.tags.strong(title), ui.tags.span(copy), class_="catalog-context")

    @render.ui
    def explorer_list() -> Any:
        items = explorer_results()
        if not items:
            return ui.tags.p(
                "Ningún tramo cabe completo en la vista actual. Aleja o mueve el "
                "mapa, o ajusta la búsqueda.",
                class_="empty-state",
            )
        return ui.div(
            *(
                segment_card(segment, is_active=segment["id"] == explored_segment_id.get())
                for segment, _, _ in items
            ),
            class_="segment-list",
        )

    @render.ui
    def explorer_summary() -> Any:
        segment = SEGMENTS.get(explored_segment_id.get() or "")
        if segment is None:
            return ui.div(
                ui.tags.p("MAPA DE EXPLORACIÓN", class_="section-kicker"),
                ui.h2("Selecciona un tramo"),
                ui.tags.p(
                    "Pulsa una geometría en el mapa o usa la lista para verla con detalle.",
                    class_="section-copy",
                ),
                class_="explorer-map-header",
            )
        metrics = segment["metrics"]
        elevation_copy = (
            f"{metrics['elevation_min_m']:.0f}–{metrics['elevation_max_m']:.0f} m de elevación"
            if metrics["elevation_min_m"] is not None
            else "sin elevación"
        )
        return ui.div(
            ui.div(
                ui.tags.p("TRAMO SELECCIONADO", class_="section-kicker"),
                ui.h2(segment["title"]),
                ui.tags.p(
                    f"{format_distance(metrics['distance_m'])} · "
                    f"{metrics['point_count']} puntos · {elevation_copy}",
                    class_="section-copy",
                ),
            ),
            ui.tags.button(
                "Agregar a la construcción",
                type="button",
                class_="primary-action explorer-add-action",
                data_builder_action="add",
                data_segment_id=segment["id"],
            ),
            class_="explorer-map-header explorer-map-header--selected",
        )

    @render.ui
    def builder_metrics() -> Any:
        details = selected_details()
        distance_m = sum(item["distance_m"] for item in details)
        gaps = [item["gap_m"] for item in details if item["gap_m"] is not None]
        ascent_m, descent_m = construction_elevation(details)
        return ui.div(
            metric_card("Tramos", str(len(details))),
            metric_card("Distancia", format_distance(distance_m + sum(gaps))),
            metric_card("Huecos", format_distance(sum(gaps)), accent="#b7791f"),
            metric_card(
                "Desnivel (+)/(−)",
                format_elevation(ascent_m, descent_m),
                accent="#2b6f8a",
            ),
            class_="metrics-grid",
        )

    @render.ui
    def construction() -> Any:
        details = selected_details()
        if not details:
            return ui.div(
                ui.tags.strong("Tu ruta está vacía"),
                ui.tags.p("Pulsa una línea en el mapa o agrega un tramo desde Explorar."),
                class_="empty-state",
            )

        cards = []
        for index, detail in enumerate(details):
            segment = detail["segment"]
            connection_copy, connection_class = connection_state(detail["gap_m"])
            cards.append(
                ui.tags.article(
                    ui.tags.span(str(index + 1), class_="construction-number"),
                    ui.div(
                        ui.tags.strong(segment["title"]),
                        ui.tags.span(
                            f"{format_distance(detail['distance_m'])} · "
                            f"sentido {'invertido' if detail['reversed'] else 'original'}",
                            class_="construction-meta",
                        ),
                        ui.tags.span(
                            connection_copy,
                            class_=f"connection-badge connection-badge--{connection_class}",
                        ),
                        class_="construction-copy",
                    ),
                    ui.div(
                        ui.tags.button(
                            "↑",
                            type="button",
                            class_="icon-action",
                            data_builder_action="move-up",
                            data_segment_id=segment["id"],
                            disabled=index == 0,
                            aria_label=f"Subir {segment['title']}",
                        ),
                        ui.tags.button(
                            "↓",
                            type="button",
                            class_="icon-action",
                            data_builder_action="move-down",
                            data_segment_id=segment["id"],
                            disabled=index == len(details) - 1,
                            aria_label=f"Bajar {segment['title']}",
                        ),
                        ui.tags.button(
                            "↺",
                            type="button",
                            class_="icon-action",
                            data_builder_action="reverse",
                            data_segment_id=segment["id"],
                            aria_label=f"Invertir {segment['title']}",
                        ),
                        ui.tags.button(
                            "×",
                            type="button",
                            class_="icon-action icon-action--danger",
                            data_builder_action="remove",
                            data_segment_id=segment["id"],
                            aria_label=f"Quitar {segment['title']}",
                        ),
                        class_="construction-actions",
                    ),
                    class_="construction-card",
                )
            )
        return ui.div(*cards, class_="construction-list")

    @render_widget
    def altimetria() -> go.Figure:
        pieces = construction_profile(selection.get(), SEGMENTS)
        figure = go.Figure()
        missing_count = 0
        profile_count = 0
        for piece in pieces:
            points = piece["points"]
            if not points:
                missing_count += 1
                figure.add_vrect(
                    x0=piece["start_km"],
                    x1=piece["end_km"],
                    fillcolor="rgba(120, 119, 111, 0.11)",
                    line_width=0,
                    layer="below",
                )
                continue
            profile_count += 1
            figure.add_trace(
                go.Scatter(
                    x=[point[0] for point in points],
                    y=[point[1] for point in points],
                    text=[piece["title"]] * len(points),
                    mode="lines",
                    line={"color": SELECTED_SEGMENT_COLOR, "width": 3},
                    hovertemplate=("%{text}<br>%{x:.2f} km · %{y:.0f} m<extra></extra>"),
                    showlegend=False,
                )
            )

        if not pieces:
            empty_copy = "Agrega un tramo para comenzar el perfil."
        elif not profile_count:
            empty_copy = "Los tramos seleccionados no contienen elevación."
        else:
            empty_copy = None
        if empty_copy:
            figure.add_annotation(
                text=empty_copy,
                x=0.5,
                y=0.5,
                xref="paper",
                yref="paper",
                showarrow=False,
                font={"color": "#6f746f", "size": 13},
            )
        elif missing_count:
            figure.add_annotation(
                text=f"{missing_count} tramo(s) sin datos de elevación",
                x=1,
                y=1.08,
                xref="paper",
                yref="paper",
                xanchor="right",
                showarrow=False,
                font={"color": "#6f746f", "size": 11},
            )

        total_distance_km = pieces[-1]["end_km"] if pieces else 0
        figure.update_layout(
            template="plotly_white",
            height=300,
            autosize=True,
            margin={"l": 58, "r": 20, "t": 42, "b": 52},
            hovermode="x unified",
            paper_bgcolor="#fffdf7",
            plot_bgcolor="#fffdf7",
            uirevision="altimetria-construccion",
        )
        figure.update_xaxes(
            title="Distancia acumulada (km)",
            range=[0, max(total_distance_km, 0.1)] if pieces else None,
            showgrid=True,
            gridcolor="#ebe6da",
            zeroline=False,
            visible=bool(pieces),
        )
        figure.update_yaxes(
            title="Elevación (m)",
            showgrid=True,
            gridcolor="#ebe6da",
            zeroline=False,
            visible=bool(profile_count),
        )
        return figure

    network_details = [{"geometry": segment["geometry"]} for segment in SEGMENTS.values()]
    map_bounds = combined_bounds(network_details)
    map_center = (
        (
            (map_bounds[0][0] + map_bounds[1][0]) / 2,
            (map_bounds[0][1] + map_bounds[1][1]) / 2,
        )
        if map_bounds
        else (19.02, -99.09)
    )
    map_configs = CATALOG.get("maps", [])
    selected_base_map_index = default_base_map_index(map_configs, CATALOG.get("default_map"))
    route_base_layers = base_map_layers(map_configs)
    route_map = Map(
        center=map_center,
        zoom=12,
        layers=(route_base_layers[selected_base_map_index],),
        scroll_wheel_zoom=True,
        layout={"height": "100%", "width": "100%"},
    )
    segment_spatial_index = build_segment_spatial_index(
        SEGMENTS,
        cell_size_m=DIRECT_CONNECTION_M,
    )
    network_lines: dict[str, list[Polyline]] = {}
    available_route_layers = LayerGroup(name="Tramos disponibles")
    selected_route_layers = LayerGroup(name="Ruta seleccionada")
    route_map.add(available_route_layers)
    route_map.add(selected_route_layers)

    def add_callback(segment_id: str) -> Any:
        def add_segment_from_map(**event: Any) -> None:
            coordinates = event.get("coordinates")
            click_point = None
            if isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2:
                try:
                    click_point = [float(coordinates[0]), float(coordinates[1])]
                except (TypeError, ValueError):
                    click_point = None
            add_to_selection(segment_id, click_point=click_point)

        return add_segment_from_map

    for segment in SEGMENTS.values():
        color = segment_network_color(segment["id"])
        for geometry in segment["geometry"]:
            available_line = Polyline(
                locations=geometry,
                color=color,
                weight=5,
                opacity=0.75,
                fill=False,
                line_cap="round",
                line_join="round",
            )
            available_line.on_click(add_callback(segment["id"]))
            available_route_layers.add(available_line)
            network_lines.setdefault(segment["id"], []).append(available_line)

    help_widget = HTML(
        value=(
            '<aside class="map-help"><strong>'
            f"{len(SEGMENTS)} disponibles · 0 seleccionados"
            "</strong><span>Pulsa un trazo de color para iniciar la ruta.</span></aside>"
        )
    )
    route_map.add(WidgetControl(widget=help_widget, position="bottomright"))
    route_map.add(ScaleControl(position="bottomleft", metric=True, imperial=False))
    route_map.add(
        WidgetControl(
            widget=base_map_picker(route_map, route_base_layers, selected_base_map_index),
            position="topright",
        )
    )
    route_map.add(FullScreenControl(position="topright"))
    selected_layers: list[Any] = []

    def add_selected_layer(layer: Any) -> None:
        selected_route_layers.add(layer)
        selected_layers.append(layer)

    @reactive.effect
    def update_selected_map_layers() -> None:
        details = selected_details()
        current_selection = selection.get()
        selected_ids = {segment_id for segment_id, _ in current_selection}
        active_ids = active_segment_ids(
            current_selection,
            SEGMENTS,
            segment_spatial_index,
            crossing_distance_m=DIRECT_CONNECTION_M,
            connection_distance_m=WARNING_CONNECTION_M,
            last_geometry=details[-1]["geometry"] if details else None,
            endpoint=details[-1]["end"] if details else None,
        )
        for segment_id, lines in network_lines.items():
            is_active = segment_id in active_ids
            is_selected = segment_id in selected_ids
            for line in lines:
                line.color = segment_network_color(segment_id) if is_active else "#aaa9a3"
                line.opacity = 0.82 if is_active else (0.12 if is_selected else 0.2)
                line.weight = 5 if is_active else 3
                line.pointer_events = "auto" if is_active else "none"

        for layer in selected_layers:
            selected_route_layers.remove(layer)
        selected_layers.clear()

        previous_end = None
        for index, detail in enumerate(details):
            segment = detail["segment"]
            if previous_end is not None and detail["gap_m"] and detail["gap_m"] > 1:
                gap_color = "#b7791f" if detail["gap_m"] <= WARNING_CONNECTION_M else "#9b2c2c"
                add_selected_layer(
                    Polyline(
                        locations=[previous_end, detail["start"]],
                        color=gap_color,
                        weight=4,
                        opacity=0.9,
                        fill=False,
                        dash_array="7, 9",
                    )
                )
            for geometry in detail["geometry"]:
                add_selected_layer(
                    Polyline(
                        locations=geometry,
                        color="#fffdf7",
                        weight=11,
                        opacity=0.9,
                        fill=False,
                        line_cap="round",
                        line_join="round",
                        pointer_events="none",
                    )
                )
                add_selected_layer(
                    Polyline(
                        locations=geometry,
                        color=SELECTED_SEGMENT_COLOR,
                        weight=7,
                        opacity=1,
                        fill=False,
                        line_cap="round",
                        line_join="round",
                        pointer_events="none",
                    )
                )
            marker_point = max(detail["geometry"], key=len)
            marker_position = marker_point[len(marker_point) // 2]
            add_selected_layer(
                Marker(
                    location=marker_position,
                    icon=DivIcon(
                        html=(
                            '<span class="segment-map-number" style="--segment-color:'
                            f'{SELECTED_SEGMENT_COLOR}">{index + 1}</span>'
                        ),
                        icon_size=(34, 34),
                        icon_anchor=(17, 17),
                    ),
                    title=segment["title"],
                    keyboard=True,
                    rise_on_hover=True,
                )
            )
            previous_end = detail["end"]

        if details:
            start_marker = CircleMarker(
                location=details[0]["start"],
                radius=7,
                color="#fffdf7",
                weight=3,
                fill_color=SELECTED_SEGMENT_COLOR,
                fill_opacity=1,
            )
            start_marker.popup = HTML(value="<strong>Inicio</strong>")
            add_selected_layer(start_marker)
            end_marker = CircleMarker(
                location=details[-1]["end"],
                radius=7,
                color=SELECTED_SEGMENT_COLOR,
                weight=3,
                fill_color="#fffdf7",
                fill_opacity=1,
            )
            end_marker.popup = HTML(value="<strong>Final actual</strong>")
            add_selected_layer(end_marker)

        active_count = len(active_ids)
        help_copy = (
            "Pulsa un trazo de color para iniciar la ruta."
            if not details
            else (
                "Solo están activos los tramos cercanos o que cruzan el último. "
                "En un cruce, pulsa el lado que quieres conservar."
            )
        )
        help_widget.value = (
            '<aside class="map-help"><strong>'
            f"{active_count} activos · {len(details)} seleccionados"
            f"</strong><span>{escape(help_copy)}</span></aside>"
        )

    @render_widget
    def mapa() -> Map:
        if map_bounds:
            route_map.fit_bounds(map_bounds)
        return route_map

    explorer_base_layers = base_map_layers(map_configs)
    explorer_map = Map(
        center=map_center,
        zoom=12,
        layers=(explorer_base_layers[selected_base_map_index],),
        scroll_wheel_zoom=True,
        layout={"height": "100%", "width": "100%"},
    )
    available_explorer_layers = LayerGroup(name="Todos los tramos")
    selected_explorer_layers = LayerGroup(name="Tramo seleccionado")
    explorer_map.add(available_explorer_layers)
    explorer_map.add(selected_explorer_layers)

    def explore_callback(segment_id: str) -> Any:
        def explore_segment_from_map(**_: Any) -> None:
            if explored_segment_id.get() != segment_id:
                explored_segment_id.set(segment_id)

        return explore_segment_from_map

    for segment in SEGMENTS.values():
        color = segment_network_color(segment["id"])
        for geometry in segment["geometry"]:
            explore_line = Polyline(
                locations=geometry,
                color=color,
                weight=5,
                opacity=0.72,
                fill=False,
                line_cap="round",
                line_join="round",
            )
            explore_line.on_click(explore_callback(segment["id"]))
            available_explorer_layers.add(explore_line)

    explorer_help_widget = HTML(
        value=(
            '<aside class="map-help"><strong>Mapa de exploración</strong>'
            "<span>Pulsa un tramo para encuadrarlo y consultar sus datos.</span></aside>"
        )
    )
    explorer_map.add(WidgetControl(widget=explorer_help_widget, position="bottomright"))
    explorer_map.add(ScaleControl(position="bottomleft", metric=True, imperial=False))
    explorer_map.add(
        WidgetControl(
            widget=base_map_picker(explorer_map, explorer_base_layers, selected_base_map_index),
            position="topright",
        )
    )
    explorer_map.add(FullScreenControl(position="topright"))
    explored_layers: list[Any] = []

    def add_explored_layer(layer: Any) -> None:
        selected_explorer_layers.add(layer)
        explored_layers.append(layer)

    @reactive.effect
    def update_explored_map_layers() -> None:
        segment = SEGMENTS.get(explored_segment_id.get() or "")
        for layer in explored_layers:
            selected_explorer_layers.remove(layer)
        explored_layers.clear()
        if segment is None:
            return

        color = segment_network_color(segment["id"])
        for geometry in segment["geometry"]:
            add_explored_layer(
                Polyline(
                    locations=geometry,
                    color="#fffdf7",
                    weight=12,
                    opacity=0.95,
                    fill=False,
                    line_cap="round",
                    line_join="round",
                    pointer_events="none",
                )
            )
            add_explored_layer(
                Polyline(
                    locations=geometry,
                    color=color,
                    weight=8,
                    opacity=1,
                    fill=False,
                    line_cap="round",
                    line_join="round",
                    pointer_events="none",
                )
            )
        for label, location in (("Inicio", segment["start"]), ("Final", segment["end"])):
            marker = CircleMarker(
                location=location,
                radius=7,
                color="#fffdf7",
                weight=3,
                fill_color=color,
                fill_opacity=1,
            )
            marker.popup = HTML(value=f"<strong>{label}</strong>")
            add_explored_layer(marker)

        explorer_help_widget.value = (
            '<aside class="map-help"><strong>'
            f"{escape(segment['title'])}</strong>"
            f"<span>{escape(format_distance(segment['metrics']['distance_m']))}</span></aside>"
        )
        explorer_map.fit_bounds(segment["bounds"])

    @render_widget
    def mapa_explorar() -> Map:
        if map_bounds:
            explorer_map.fit_bounds(map_bounds)
        return explorer_map


app = App(app_ui, server, static_assets=WWW_DIR)
