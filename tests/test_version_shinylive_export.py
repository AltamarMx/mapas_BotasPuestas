from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.version_shinylive_export import version_export


def make_export(site_dir: Path, app_bytes: bytes = b'[{"name":"app.py"}]') -> None:
    (site_dir / "shinylive").mkdir(parents=True)
    (site_dir / "app.json").write_bytes(app_bytes)
    (site_dir / "index.html").write_text(
        '<script type="module">\n'
        'import { runExportedApp } from "./shinylive/shinylive.js";\n'
        "</script>\n",
        encoding="utf-8",
    )
    (site_dir / "shinylive" / "shinylive.js").write_text(
        'const response = await fetch("./app.json");\n',
        encoding="utf-8",
    )


def test_version_export_fingerprints_app_bundle(tmp_path: Path) -> None:
    app_bytes = b'[{"name":"app.py","content":"updated"}]'
    make_export(tmp_path, app_bytes)

    digest, versioned_path = version_export(tmp_path)

    expected_digest = hashlib.sha256(app_bytes).hexdigest()[:12]
    assert digest == expected_digest
    assert versioned_path == tmp_path / f"app-{expected_digest}.json"
    assert versioned_path.read_bytes() == app_bytes
    assert not (tmp_path / "app.json").exists()
    assert f"shinylive.js?v={expected_digest}" in (tmp_path / "index.html").read_text()
    javascript = (tmp_path / "shinylive" / "shinylive.js").read_text()
    assert f'app-{expected_digest}.json' in javascript
    assert 'cache: "no-store"' in javascript


def test_version_export_fails_before_mutating_an_unknown_export(tmp_path: Path) -> None:
    make_export(tmp_path)
    (tmp_path / "shinylive" / "shinylive.js").write_text(
        "const response = await loadSomethingElse();\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="app.json"):
        version_export(tmp_path)

    assert (tmp_path / "app.json").is_file()
