from __future__ import annotations

import pytest

from scripts.build_content import TrackPoint, analyze_track, classify_effort

CATEGORIES = [
    {
        "id": "ligera",
        "etiqueta": "Ligera",
        "color": "green",
        "max_km_esfuerzo": 10,
        "max_horas_movimiento": 3,
    },
    {
        "id": "moderada",
        "etiqueta": "Moderada",
        "color": "yellow",
        "max_km_esfuerzo": 20,
        "max_horas_movimiento": 5,
    },
    {
        "id": "exigente",
        "etiqueta": "Exigente",
        "color": "orange",
        "max_km_esfuerzo": 30,
        "max_horas_movimiento": 7,
    },
    {"id": "muy-exigente", "etiqueta": "Muy exigente", "color": "red"},
]


def point(lat: float, lon: float, elevation: float = 100) -> TrackPoint:
    return TrackPoint(lat, lon, elevation, None)


def test_segments_are_not_joined_by_an_artificial_distance() -> None:
    first = [point(19, -99), point(19, -98.999)]
    second = [point(25, -105), point(25, -104.999)]
    analysis = analyze_track([first, second])
    expected = analyze_track([first])["distance_m"] + analyze_track([second])["distance_m"]
    assert analysis["distance_m"] == pytest.approx(expected)
    assert analysis["distance_m"] < 1_000


@pytest.mark.parametrize(
    ("distance_m", "ascent_m", "moving_seconds", "expected"),
    [
        (7_000, 100, 2 * 3_600, "ligera"),
        (10_000, 0, 2 * 3_600, "moderada"),
        (15_000, 0, 5 * 3_600, "exigente"),
        (5_000, 0, 7 * 3_600, "muy-exigente"),
        (21_000, 900, None, "muy-exigente"),
    ],
)
def test_effort_boundaries(
    distance_m: float, ascent_m: float, moving_seconds: float | None, expected: str
) -> None:
    _, category = classify_effort(distance_m, ascent_m, moving_seconds, CATEGORIES)
    assert category["id"] == expected

