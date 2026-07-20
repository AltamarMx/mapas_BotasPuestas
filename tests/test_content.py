from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import ExifTags, Image
from pillow_heif import register_heif_opener

from scripts.build_content import DEFAULT_CONFIG, DEFAULT_ROUTES, build_project

register_heif_opener()


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


def test_current_routes_and_metrics(built_content: tuple[dict, Path, Path]) -> None:
    result, _, _ = built_content
    routes = {route["id"]: route for route in result["routes"]}
    assert set(routes) == {
        "2026-07-19-morning-hike",
        "milpa-alta-santo-domingo-2026",
    }

    short = routes["2026-07-19-morning-hike"]
    assert short["metrics"]["distance_m"] == pytest.approx(6_781.3, abs=1)
    assert short["metrics"]["ascent_m"] == pytest.approx(192, abs=2)
    assert short["metrics"]["descent_m"] == pytest.approx(192, abs=2)
    assert short["metrics"]["effort"]["id"] == "ligera"

    milpa = routes["milpa-alta-santo-domingo-2026"]
    assert milpa["metrics"]["distance_m"] == pytest.approx(23_748.7, abs=1)
    assert milpa["metrics"]["ascent_m"] == pytest.approx(147, abs=2)
    assert milpa["metrics"]["descent_m"] == pytest.approx(1_008, abs=2)
    assert milpa["metrics"]["effort"]["id"] == "muy-exigente"
    assert len(milpa["photos"]) == 9


def test_photo_exif_is_read_and_positions_match_track(
    built_content: tuple[dict, Path, Path],
) -> None:
    result, _, _ = built_content
    milpa = next(route for route in result["routes"] if route["id"].startswith("milpa"))
    assert all(photo["location_source"] == "EXIF" for photo in milpa["photos"])
    assert all(photo["captured_at"].endswith("-06:00") for photo in milpa["photos"])
    assert max(photo["nearest_track_m"] for photo in milpa["photos"]) < 12


def test_sources_keep_exif_and_web_copies_strip_it(
    built_content: tuple[dict, Path, Path],
) -> None:
    result, _, web_output = built_content
    milpa = next(route for route in result["routes"] if route["id"].startswith("milpa"))
    source_root = DEFAULT_ROUTES / milpa["id"]

    for photo in milpa["photos"]:
        with Image.open(source_root / photo["source_file"]) as source:
            exif = source.getexif()
            exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
            gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
            assert exif_ifd.get(ExifTags.Base.DateTimeOriginal)
            assert gps_ifd.get(ExifTags.GPS.GPSLatitude)

        relative_web_path = photo["image_url"].removeprefix("generated/")
        with Image.open(web_output / relative_web_path) as public_image:
            assert not public_image.getexif()


def test_generated_json_is_complete(built_content: tuple[dict, Path, Path]) -> None:
    _, data_output, web_output = built_content
    catalog = json.loads((data_output / "catalogo.json").read_text(encoding="utf-8"))
    assert catalog["default_map"] == "osm"
    assert len(catalog["routes"]) == 2
    assert (data_output / "rutas" / "milpa-alta-santo-domingo-2026.json").is_file()
    assert len(list(web_output.glob("fotos/*/web/*.jpg"))) == 9
    assert len(list(web_output.glob("fotos/*/miniaturas/*.jpg"))) == 9

