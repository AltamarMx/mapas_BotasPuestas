from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.build_content import build_candidate_segment
from scripts.import_gpx_tracks import GpxImportError, import_collection


def write_source_gpx(path: Path, name: str, *, offset: float = 0.0) -> None:
    path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1" creator="GaiaGPS">
  <wpt lat="19.0" lon="-99.2"><name>Mi foto</name></wpt>
  <trk>
    <name>{name}</name>
    <trkseg>
      <trkpt lat="{19.0 + offset}" lon="-99.2"><ele>2200.5</ele>
        <time>2025-07-05T17:41:30Z</time></trkpt>
      <trkpt lat="{19.001 + offset}" lon="-99.201"><ele>2210</ele>
        <time>2025-07-05T17:42:30Z</time></trkpt>
    </trkseg>
  </trk>
</gpx>
""",
        encoding="utf-8",
    )


def test_import_moves_sources_and_builds_valid_candidates(tmp_path: Path) -> None:
    collection = tmp_path / "colaborador"
    collection.mkdir()
    write_source_gpx(collection / "Cueva.gpx", "La cueva del mictlan ")

    created = import_collection(collection, prefix="hugo", region="Morelos")

    assert [route_id for route_id, _ in created] == ["hugo-001-la-cueva-del-mictlan"]
    assert (collection / "fuentes" / "Cueva.gpx").is_file()
    assert not (collection / "Cueva.gpx").exists()

    route_dir = collection / "hugo-001-la-cueva-del-mictlan"
    gpx = (route_dir / "ruta.gpx").read_text(encoding="utf-8")
    assert "<time>" not in gpx
    assert "<wpt" not in gpx
    manifest = yaml.safe_load((route_dir / "ruta.yml").read_text(encoding="utf-8"))
    assert manifest["titulo"] == "La cueva del mictlan"
    assert manifest["publicada"] is False

    segment = build_candidate_segment(route_dir)
    assert segment is not None
    assert segment["elevation_source"] == "gpx"


def test_import_is_incremental(tmp_path: Path) -> None:
    collection = tmp_path / "colaborador"
    collection.mkdir()
    write_source_gpx(collection / "a.gpx", "Arroyo seco")
    import_collection(collection, prefix="hugo", region="Morelos")

    assert import_collection(collection, prefix="hugo", region="Morelos") == []

    write_source_gpx(collection / "b.gpx", "Mojoneras", offset=0.01)
    created = import_collection(collection, prefix="hugo", region="Morelos")
    assert [route_id for route_id, _ in created] == ["hugo-002-mojoneras"]


def test_check_mode_writes_nothing(tmp_path: Path) -> None:
    collection = tmp_path / "colaborador"
    collection.mkdir()
    write_source_gpx(collection / "a.gpx", "Arroyo seco")

    created = import_collection(collection, prefix="hugo", region="Morelos", dry_run=True)

    assert len(created) == 1
    assert sorted(path.name for path in collection.iterdir()) == ["a.gpx"]


def test_rejects_invalid_prefix(tmp_path: Path) -> None:
    with pytest.raises(GpxImportError, match="Prefijo"):
        import_collection(tmp_path, prefix="Hugo_M", region="Morelos")
