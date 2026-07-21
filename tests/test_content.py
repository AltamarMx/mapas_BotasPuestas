from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from scripts.build_content import DEFAULT_CONFIG, DEFAULT_ROUTES, build_project


@pytest.fixture(scope="module")
def built_content(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict, Path, Path]:
    root = tmp_path_factory.mktemp("generated-content")
    data_output = root / "data"
    web_output = root / "web"
    result = build_project(
        routes_root=DEFAULT_ROUTES,
        config_root=DEFAULT_CONFIG,
        data_output=data_output,
        web_output=web_output,
    )
    return result, data_output, web_output


def test_current_content_is_a_segment_library(
    built_content: tuple[dict, Path, Path],
) -> None:
    result, _, _ = built_content
    assert result["routes"] == []
    assert len(result["segments"]) == 194

    longest = result["segments"][0]
    assert longest["id"] == "gilles-092-exvia"
    assert longest["title"] == "EXVIA"
    assert longest["metrics"]["distance_m"] == pytest.approx(31_718, abs=2)
    assert longest["start"] == longest["geometry"][0][0]
    assert longest["end"] == longest["geometry"][-1][-1]


def test_segments_are_sorted_and_keep_review_state(
    built_content: tuple[dict, Path, Path],
) -> None:
    result, _, _ = built_content
    segments = result["segments"]
    distances = [segment["metrics"]["distance_m"] for segment in segments]
    assert distances == sorted(distances, reverse=True)
    assert all(segment["record_type"] == "por-definir" for segment in segments)
    assert all(segment["review_status"] == "pendiente" for segment in segments)
    assert all(segment["metrics"]["elevation_min_m"] is not None for segment in segments)
    assert all(segment["profile"] for segment in segments)
    assert Counter(segment["elevation_source"] for segment in segments) == {
        "gpx": 27,
        "nasa-srtmgl1-v3": 167,
    }


def test_generated_json_contains_builder_configuration(
    built_content: tuple[dict, Path, Path],
) -> None:
    _, data_output, web_output = built_content
    catalog = json.loads((data_output / "catalogo.json").read_text(encoding="utf-8"))
    segments = json.loads((data_output / "tramos.json").read_text(encoding="utf-8"))

    assert catalog["default_map"] == "osm"
    assert [item["id"] for item in catalog["maps"]] == ["osm", "satelite", "topografico"]
    assert catalog["routes"] == []
    assert catalog["builder"] == {
        "direct_connection_m": 100.0,
        "elevation_profile_count": 194,
        "segment_count": 194,
        "warning_connection_m": 500.0,
    }
    assert len(segments["segments"]) == 194
    assert set(segments["elevation_sources"]) == {"gpx", "nasa-srtmgl1-v3"}
    assert not list(web_output.rglob("*.jpg"))


def test_elevation_cache_is_complete_and_geometry_bound() -> None:
    cache_path = DEFAULT_ROUTES / "_candidatas" / "gilles" / "elevacion-dem.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8"))

    assert cache["schema_version"] == 1
    assert cache["source"]["id"] == "nasa-srtmgl1-v3"
    assert {tile["id"] for tile in cache["source"]["tiles"]} == {
        "N18W099",
        "N18W100",
        "N19W099",
        "N19W100",
    }
    assert cache["validation"]["dem_route_count"] == 167
    assert cache["validation"]["dem_point_count"] == 8_812
    assert cache["validation"]["preserved_gpx_route_count"] == 27
    assert len(cache["segments"]) == 167
    assert all(
        len(record["elevations_m"]) == record["point_count"]
        and len(record["geometry_sha256"]) == 64
        for record in cache["segments"].values()
    )
