from __future__ import annotations

from xml.etree import ElementTree

import pytest

from app.app import (
    AVAILABLE_SEGMENT_COLORS,
    SELECTED_SEGMENT_COLOR,
    active_segment_ids,
    append_segment,
    available_segments,
    base_map_layer_options,
    build_segment_spatial_index,
    closest_orientation,
    combined_bounds,
    construction_details,
    construction_elevation,
    construction_gpx,
    construction_profile,
    default_base_map_index,
    format_elevation,
    normalized_search,
    oriented_geometry,
    rank_segments,
    replace_base_map,
    segment_network_color,
    segment_within_bounds,
)


class FakeMap:
    def __init__(self, layers: tuple[object, ...]) -> None:
        self.layers = layers

    def add(self, layer: object) -> None:
        self.layers += (layer,)

    def substitute(self, old: object, new: object) -> None:
        self.layers = tuple(new if layer is old else layer for layer in self.layers)


def test_base_map_layer_options_and_configured_default() -> None:
    configs = [
        {
            "id": "standard",
            "nombre": "Estándar",
            "url": "https://example.com/standard/{z}/{x}/{y}.png",
        },
        {
            "id": "satellite",
            "nombre": "Satélite",
            "url": "https://example.com/satellite/{z}/{x}/{y}.jpg",
            "zoom_nativo_maximo": 14,
        },
    ]

    options = base_map_layer_options(configs)

    assert [item["name"] for item in options] == ["Estándar", "Satélite"]
    assert all(item["base"] for item in options)
    assert options[1]["max_native_zoom"] == 14
    assert default_base_map_index(configs, "satellite") == 1


def test_base_map_layer_options_fall_back_to_the_first_available_map() -> None:
    configs = [
        {"id": "only", "nombre": "Único", "url": "https://example.com/{z}/{x}/{y}.png"}
    ]
    options = base_map_layer_options(configs)

    assert len(options) == 1
    assert options[0]["name"] == "Único"
    assert default_base_map_index(configs, "missing") == 0


def test_replacing_base_map_removes_the_previous_tile_layer() -> None:
    standard = object()
    satellite = object()
    routes = object()
    map_widget = FakeMap((standard, routes))

    replace_base_map(map_widget, (standard, satellite), 1)

    assert map_widget.layers == (satellite, routes)


def segment(
    segment_id: str,
    title: str,
    distance_m: float,
    start: list[float],
    end: list[float],
) -> dict:
    return {
        "id": segment_id,
        "title": title,
        "region": "Tepoztlán",
        "start": start,
        "end": end,
        "geometry": [[start, end]],
        "metrics": {"distance_m": distance_m},
    }


def test_initial_list_contains_every_segment_sorted_by_distance() -> None:
    items = [
        segment("short", "Corto", 100, [19.0, -99.0], [19.0, -99.001]),
        segment("long", "Largo", 500, [19.0, -99.0], [19.0, -99.005]),
        segment("medium", "Medio", 300, [19.0, -99.0], [19.0, -99.003]),
    ]
    ranked = rank_segments(items)
    assert [item[0]["id"] for item in ranked] == ["long", "medium", "short"]


def test_map_keeps_every_unselected_segment_available() -> None:
    items = [
        segment(str(index), f"Tramo {index}", index, [19.0, -99.0], [19.0, -99.001])
        for index in range(12)
    ]
    lookup = {item["id"]: item for item in items}

    assert len(available_segments((), lookup)) == 12
    assert [item["id"] for item in available_segments((("3", False),), lookup)] == [
        item["id"] for item in items if item["id"] != "3"
    ]


def test_segment_within_bounds_requires_full_containment() -> None:
    inside = {"bounds": [[19.0, -99.2], [19.1, -99.1]]}
    partial = {"bounds": [[18.9, -99.2], [19.1, -99.1]]}
    outside = {"bounds": [[20.0, -98.0], [20.1, -97.9]]}
    viewport = ((18.95, -99.3), (19.2, -99.0))

    assert segment_within_bounds(inside, viewport)
    assert not segment_within_bounds(partial, viewport)
    assert not segment_within_bounds(outside, viewport)
    assert segment_within_bounds(partial, ())


def test_available_map_colors_are_stable_and_never_selection_green() -> None:
    colors = [segment_network_color(f"gilles-{index:03d}") for index in range(194)]

    assert set(colors) <= set(AVAILABLE_SEGMENT_COLORS)
    assert SELECTED_SEGMENT_COLOR not in colors
    assert segment_network_color("gilles-001") == segment_network_color("gilles-001")
    assert len(set(colors)) == len(AVAILABLE_SEGMENT_COLORS)


def test_only_nearby_or_crossing_segments_stay_active() -> None:
    first = segment("first", "Primero", 1_000, [19.0, -99.01], [19.0, -99.0])
    endpoint_near = segment(
        "near",
        "Cercano",
        300,
        [19.0001, -99.0],
        [19.001, -99.0],
    )
    crossing = segment(
        "crossing",
        "Cruce",
        300,
        [18.9999, -99.005],
        [19.0001, -99.005],
    )
    far = segment("far", "Lejano", 300, [20.0, -100.0], [20.001, -100.0])
    lookup = {item["id"]: item for item in (first, endpoint_near, crossing, far)}
    spatial_index = build_segment_spatial_index(lookup)

    active = active_segment_ids((("first", False),), lookup, spatial_index)

    assert active == {"near", "crossing"}


def test_construction_profile_accumulates_distance_and_reverses_elevation() -> None:
    first = segment("first", "Primero", 1_000, [19.0, -99.01], [19.0, -99.0])
    first["profile"] = [[0.0, 100.0], [1.0, 200.0]]
    second = segment("second", "Segundo", 500, [19.0, -98.995], [19.0, -99.0])
    second["profile"] = [[0.0, 300.0], [0.5, 400.0]]
    lookup = {item["id"]: item for item in (first, second)}

    pieces = construction_profile((("first", False), ("second", True)), lookup)

    assert pieces[0]["points"] == [[0.0, 100.0], [1.0, 200.0]]
    assert pieces[1]["points"] == [[1.0, 400.0], [1.5, 300.0]]
    assert pieces[-1]["end_km"] == pytest.approx(1.5)


def test_construction_elevation_follows_route_direction() -> None:
    first = segment("first", "Primero", 1_000, [19.0, -99.01], [19.0, -99.0])
    first["profile"] = [[0.0, 100.0], [0.5, 180.0], [1.0, 150.0]]
    second = segment("second", "Segundo", 500, [19.0, -98.995], [19.0, -99.0])
    second["profile"] = [[0.0, 200.0], [0.25, 260.0], [0.5, 220.0]]
    lookup = {item["id"]: item for item in (first, second)}
    details = construction_details((("first", False), ("second", True)), lookup)

    ascent_m, descent_m = construction_elevation(details)

    assert ascent_m == pytest.approx(120)
    assert descent_m == pytest.approx(90)


def test_construction_elevation_uses_only_trimmed_portions() -> None:
    first = segment("first", "Oeste a este", 1_000, [19.0, -99.01], [19.0, -99.0])
    first["profile"] = [[0.0, 100.0], [0.5, 150.0], [1.0, 100.0]]
    crossing = segment(
        "crossing",
        "Sur a norte",
        1_000,
        [18.995, -99.005],
        [19.005, -99.005],
    )
    crossing["profile"] = [[0.0, 300.0], [0.5, 250.0], [1.0, 350.0]]
    lookup = {item["id"]: item for item in (first, crossing)}
    selection = append_segment((), "first", lookup)
    selection = append_segment(
        selection,
        "crossing",
        lookup,
        click_point=[19.004, -99.005],
    )

    ascent_m, descent_m = construction_elevation(construction_details(selection, lookup))

    assert ascent_m == pytest.approx(150, abs=1)
    assert descent_m == pytest.approx(0, abs=1)


def test_construction_elevation_prefers_precise_metrics_for_complete_segments() -> None:
    item = segment("one", "Uno", 1_000, [19.0, -99.01], [19.0, -99.0])
    item["profile"] = [[0.0, 100.0], [1.0, 200.0]]
    item["metrics"].update({"ascent_m": 123.4, "descent_m": 4.5})
    details = construction_details((("one", True),), {"one": item})

    assert construction_elevation(details) == pytest.approx((4.5, 123.4))


def test_format_elevation_rounds_for_presentation_and_handles_missing_data() -> None:
    assert format_elevation(124.9, 75.1) == "+120 / −80 m"
    assert format_elevation(None, 75.1) == "Sin datos"


def test_construction_gpx_exports_ordered_track_with_elevation() -> None:
    first = segment("first", "Primero", 1_000, [19.0, -99.01], [19.0, -99.0])
    first["profile"] = [[0.0, 100.0], [1.0, 200.0]]
    second = segment("second", "Segundo", 500, [19.0, -98.995], [19.0, -99.0])
    second["profile"] = [[0.0, 300.0], [0.5, 400.0]]
    lookup = {item["id"]: item for item in (first, second)}

    xml = construction_gpx((("first", False), ("second", True)), lookup)
    root = ElementTree.fromstring(xml)
    ns = {"gpx": "http://www.topografix.com/GPX/1/1"}

    assert root.find("gpx:trk/gpx:name", ns).text == "Primero + Segundo"
    assert len(root.findall(".//gpx:trkseg", ns)) == 2
    points = root.findall(".//gpx:trkpt", ns)
    assert [point.attrib["lon"] for point in points] == [
        "-99.010000",
        "-99.000000",
        "-99.000000",
        "-98.995000",
    ]
    elevations = [float(point.find("gpx:ele", ns).text) for point in points]
    assert elevations == [100.0, 200.0, 400.0, 300.0]


def test_construction_profile_includes_gaps_in_accumulated_distance() -> None:
    first = segment("first", "Primero", 1_000, [19.0, -99.01], [19.0, -99.0])
    first["profile"] = [[0.0, 100.0], [1.0, 200.0]]
    second = segment("second", "Segundo", 500, [19.0, -98.995], [19.0, -98.99])
    second["profile"] = [[0.0, 300.0], [0.5, 400.0]]
    lookup = {item["id"]: item for item in (first, second)}

    pieces = construction_profile((("first", False), ("second", False)), lookup)
    details = construction_details((("first", False), ("second", False)), lookup)
    gap_km = details[1]["gap_m"] / 1_000

    assert gap_km > 0.4
    assert pieces[1]["start_km"] == pytest.approx(1.0 + gap_km)
    assert pieces[1]["points"][0][0] == pytest.approx(1.0 + gap_km)
    assert pieces[-1]["end_km"] == pytest.approx(1.5 + gap_km)


def test_suggestions_choose_the_nearest_endpoint_and_direction() -> None:
    endpoint = [19.0, -99.0]
    near_reversed = segment(
        "near",
        "Cercano",
        200,
        [19.0, -99.02],
        [19.0, -99.0001],
    )
    far = segment("far", "Lejano", 900, [19.0, -99.1], [19.0, -99.2])

    ranked = rank_segments([far, near_reversed], endpoint=endpoint)

    assert ranked[0][0]["id"] == "near"
    assert ranked[0][2] is True
    assert ranked[0][1] < ranked[1][1]
    assert closest_orientation(endpoint, near_reversed)[1] is True


def test_append_segment_automatically_orients_the_next_step() -> None:
    first = segment("first", "Primero", 100, [19.0, -99.01], [19.0, -99.0])
    second = segment("second", "Segundo", 100, [19.0, -99.02], [19.0, -99.0001])
    lookup = {item["id"]: item for item in (first, second)}

    selection = append_segment((), "first", lookup)
    selection = append_segment(selection, "second", lookup)

    assert selection == (("first", False), ("second", True))
    details = construction_details(selection, lookup)
    assert details[1]["gap_m"] < 20


def test_clicking_a_crossing_segment_keeps_clicked_side_and_trims_tails() -> None:
    first = segment("first", "Oeste a este", 1_000, [19.0, -99.01], [19.0, -99.0])
    first["profile"] = [[0.0, 100.0], [1.0, 200.0]]
    crossing = segment(
        "crossing",
        "Sur a norte",
        1_000,
        [18.995, -99.005],
        [19.005, -99.005],
    )
    crossing["profile"] = [[0.0, 200.0], [1.0, 300.0]]
    lookup = {item["id"]: item for item in (first, crossing)}

    selection = append_segment((), "first", lookup)
    selection = append_segment(
        selection,
        "crossing",
        lookup,
        click_point=[19.004, -99.005],
    )
    details = construction_details(selection, lookup)

    assert selection[-1] == ("crossing", False)
    assert details[0]["end"] == pytest.approx([19.0, -99.005], abs=1e-5)
    assert details[1]["start"] == pytest.approx([19.0, -99.005], abs=1e-5)
    assert details[1]["end"] == crossing["end"]
    assert details[0]["distance_m"] == pytest.approx(500, abs=2)
    assert details[1]["distance_m"] == pytest.approx(500, abs=2)

    pieces = construction_profile(selection, lookup)
    assert pieces[0]["points"][-1][1] == pytest.approx(150, abs=1)
    assert pieces[1]["points"][0][1] == pytest.approx(250, abs=1)
    assert pieces[-1]["end_km"] == pytest.approx(1.0, abs=0.01)


def test_clicking_other_side_of_crossing_reverses_new_segment() -> None:
    first = segment("first", "Oeste a este", 1_000, [19.0, -99.01], [19.0, -99.0])
    crossing = segment(
        "crossing",
        "Sur a norte",
        1_000,
        [18.995, -99.005],
        [19.005, -99.005],
    )
    lookup = {item["id"]: item for item in (first, crossing)}

    selection = append_segment((), "first", lookup)
    selection = append_segment(
        selection,
        "crossing",
        lookup,
        click_point=[18.996, -99.005],
    )
    details = construction_details(selection, lookup)

    assert selection[-1] == ("crossing", True)
    assert details[1]["start"] == pytest.approx([19.0, -99.005], abs=1e-5)
    assert details[1]["end"] == crossing["start"]


def test_reversed_geometry_reverses_parts_and_points() -> None:
    item = {
        "geometry": [
            [[1.0, 1.0], [2.0, 2.0]],
            [[3.0, 3.0], [4.0, 4.0]],
        ]
    }
    assert oriented_geometry(item, True) == [
        [[4.0, 4.0], [3.0, 3.0]],
        [[2.0, 2.0], [1.0, 1.0]],
    ]


def test_combined_bounds_contains_the_construction() -> None:
    details = [
        {"geometry": [[[18.0, -100.0], [19.0, -99.0]]]},
        {"geometry": [[[20.0, -101.0], [21.0, -98.0]]]},
    ]
    assert combined_bounds(details) == [[18.0, -101.0], [21.0, -98.0]]


@pytest.mark.parametrize(
    ("query", "expected"),
    [("pirámide", "piramide"), ("CONEXIÓN", "conexion")],
)
def test_search_is_case_and_accent_insensitive(query: str, expected: str) -> None:
    assert normalized_search(query) == expected
