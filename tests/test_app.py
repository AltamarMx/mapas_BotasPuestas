from app.app import (
    ALL_ROUTES_ID,
    DEFAULT_ROUTE,
    combined_bounds,
    elevation_axis_range,
    route_marker_position,
)


def test_elevation_axis_range_adds_ten_percent_on_each_side() -> None:
    assert elevation_axis_range(1_000, 1_500) == [950, 1_550]


def test_elevation_axis_range_uses_safe_margin_for_flat_track() -> None:
    assert elevation_axis_range(2_500, 2_500) == [2_490, 2_510]


def test_elevation_axis_range_handles_missing_data() -> None:
    assert elevation_axis_range(None, 2_500) is None


def test_all_routes_is_the_default_view() -> None:
    assert DEFAULT_ROUTE == ALL_ROUTES_ID


def test_combined_bounds_contains_every_route() -> None:
    routes = [
        {"bounds": [[18.0, -100.0], [19.0, -99.0]]},
        {"bounds": [[20.0, -101.0], [21.0, -98.0]]},
    ]
    assert combined_bounds(routes) == [[18.0, -101.0], [21.0, -98.0]]


def test_route_marker_uses_middle_of_longest_segment() -> None:
    route = {
        "segments": [
            [[18.0, -99.0]],
            [[19.0, -99.2], [19.1, -99.1], [19.2, -99.0]],
        ]
    }

    assert route_marker_position(route) == (19.1, -99.1)


def test_route_marker_handles_empty_track() -> None:
    assert route_marker_position({"segments": [[], []]}) is None
