from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

APP_FETCH = 'fetch("./app.json")'
SHINYLIVE_IMPORT = 'from "./shinylive/shinylive.js";'


def version_export(site_dir: Path) -> tuple[str, Path]:
    """Give the app bundle a content-derived URL so browsers cannot reuse an old one."""
    app_path = site_dir / "app.json"
    index_path = site_dir / "index.html"
    shinylive_path = site_dir / "shinylive" / "shinylive.js"

    missing = [path for path in (app_path, index_path, shinylive_path) if not path.is_file()]
    if missing:
        formatted = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"Exportación Shinylive incompleta; faltan: {formatted}")

    app_bytes = app_path.read_bytes()
    digest = hashlib.sha256(app_bytes).hexdigest()[:12]
    versioned_app_path = site_dir / f"app-{digest}.json"

    index_html = index_path.read_text(encoding="utf-8")
    shinylive_js = shinylive_path.read_text(encoding="utf-8")
    if index_html.count(SHINYLIVE_IMPORT) != 1:
        raise RuntimeError("No se encontró una única importación de shinylive.js en index.html")
    if shinylive_js.count(APP_FETCH) != 1:
        raise RuntimeError("No se encontró una única carga de app.json en shinylive.js")

    index_html = index_html.replace(
        SHINYLIVE_IMPORT,
        f'from "./shinylive/shinylive.js?v={digest}";',
    )
    shinylive_js = shinylive_js.replace(
        APP_FETCH,
        f'fetch("./{versioned_app_path.name}", {{ cache: "no-store" }})',
    )

    versioned_app_path.write_bytes(app_bytes)
    app_path.unlink()
    index_path.write_text(index_html, encoding="utf-8")
    shinylive_path.write_text(shinylive_js, encoding="utf-8")
    return digest, versioned_app_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Versiona app.json después de exportar una aplicación Shinylive."
    )
    parser.add_argument("site_dir", type=Path, help="Directorio generado por shinylive export")
    args = parser.parse_args()

    digest, app_path = version_export(args.site_dir)
    print(f"Bundle versionado: {app_path.name} ({digest})")


if __name__ == "__main__":
    main()
