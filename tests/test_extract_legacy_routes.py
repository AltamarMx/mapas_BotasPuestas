from __future__ import annotations

import csv
from pathlib import Path
from xml.etree import ElementTree

import pytest
import yaml

from scripts.extract_legacy_routes import (
    CLASSIFICATION_KEYS,
    ExtractionError,
    compare_sources,
    load_routes,
    validate_extraction,
    write_extraction,
)


def write_legacy_gpx(path: Path, first_name: str) -> None:
    path.write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">
  <rte>
    <name>{first_name}</name>
    <rtept lat="19.000000000" lon="-99.000000000">
      <ele>2100.123</ele>
      <time>2019-01-01T00:00:00Z</time>
      <name>Punto artificial 1</name>
    </rtept>
    <rtept lat="19.001000000" lon="-99.001000000">
      <ele>2110.456</ele>
      <time>2019-01-01T00:00:00Z</time>
      <name>Punto artificial 2</name>
    </rtept>
  </rte>
  <rte>
    <name>Conexión 2</name>
    <rtept lat="19.010000000" lon="-99.010000000" />
    <rtept lat="19.010100000" lon="-99.010100000" />
  </rte>
</gpx>
""",
        encoding="utf-8",
    )


def test_extracts_routes_as_unpublished_track_candidates(tmp_path: Path) -> None:
    source = tmp_path / "source.gpx"
    comparison = tmp_path / "comparison.gpx"
    output = tmp_path / "candidates"
    write_legacy_gpx(source, "Ruta principal")
    write_legacy_gpx(comparison, "Ruta principal 1")

    routes = load_routes(source)
    alternate_names, differences = compare_sources(routes, comparison)
    write_extraction(
        output,
        routes,
        source,
        comparison,
        alternate_names,
        differences,
    )

    assert len(routes) == 2
    assert differences == 1
    assert routes[0].route_id == "gilles-001-ruta-principal"
    assert validate_extraction(output, routes, source) == []

    route_dir = output / routes[0].route_id
    manifest = yaml.safe_load((route_dir / "ruta.yml").read_text(encoding="utf-8"))
    assert manifest["publicada"] is False
    assert set(manifest["clasificacion_editorial"]) == set(CLASSIFICATION_KEYS)
    assert manifest["procedencia"]["nombre_alternativo"] == "Ruta principal 1"

    root = ElementTree.parse(route_dir / "ruta.gpx").getroot()
    assert root.find("{*}rte") is None
    assert len(root.findall("{*}trk/{*}trkseg/{*}trkpt")) == 2
    assert root.find(".//{*}time") is None
    assert root.find(".//{*}ele").text == "2100.12"
    assert root.find(".//{*}trkpt/{*}name") is None

    with (output / "indice.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 2
    assert "nombre-difiere-entre-fuentes" in rows[0]["observaciones_automaticas"]
    assert "posible-conector" in rows[1]["observaciones_automaticas"]


def test_refuses_to_overwrite_an_extraction(tmp_path: Path) -> None:
    source = tmp_path / "source.gpx"
    comparison = tmp_path / "comparison.gpx"
    output = tmp_path / "candidates"
    write_legacy_gpx(source, "Ruta")
    write_legacy_gpx(comparison, "Ruta")
    routes = load_routes(source)
    alternate_names, differences = compare_sources(routes, comparison)
    write_extraction(
        output,
        routes,
        source,
        comparison,
        alternate_names,
        differences,
    )

    with pytest.raises(ExtractionError, match="salida ya existe"):
        write_extraction(
            output,
            routes,
            source,
            comparison,
            alternate_names,
            differences,
        )
