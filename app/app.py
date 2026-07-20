from __future__ import annotations

import json
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
from ipyleaflet import (
    FullScreenControl,
    Map,
    Marker,
    MarkerCluster,
    Polyline,
    ScaleControl,
    TileLayer,
)
from ipywidgets import HTML
from shiny import App, reactive, render, ui
from shinywidgets import output_widget, render_widget

APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "_generated"
WWW_DIR = APP_DIR / "www"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_content() -> tuple[dict[str, Any], dict[str, dict[str, Any]], str | None]:
    catalog_path = DATA_DIR / "catalogo.json"
    if not catalog_path.exists():
        return {"routes": [], "maps": []}, {}, (
            "Faltan los datos generados. Ejecuta "
            "`uv run python scripts/build_content.py` desde la raíz del proyecto."
        )
    try:
        catalog = read_json(catalog_path)
        routes = {
            summary["id"]: read_json(DATA_DIR / "rutas" / f"{summary['id']}.json")
            for summary in catalog["routes"]
        }
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        return {"routes": [], "maps": []}, {}, f"No se pudieron cargar los datos: {exc}"
    return catalog, routes, None


CATALOG, ROUTES, CONTENT_ERROR = load_content()
ROUTE_CHOICES = {summary["id"]: summary["title"] for summary in CATALOG.get("routes", [])}
DEFAULT_ROUTE = next(
    (summary["id"] for summary in CATALOG.get("routes", []) if summary["photo_count"]),
    next(iter(ROUTE_CHOICES), ""),
)


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "Sin datos"
    hours, remainder = divmod(round(seconds / 60), 60)
    return f"{hours} h {remainder:02d} min"


def format_date(value: str | None) -> str:
    if not value:
        return "Fecha desconocida"
    months = (
        "ene",
        "feb",
        "mar",
        "abr",
        "may",
        "jun",
        "jul",
        "ago",
        "sep",
        "oct",
        "nov",
        "dic",
    )
    parsed = datetime.fromisoformat(value)
    return f"{parsed.day} {months[parsed.month - 1]} {parsed.year}"


def format_photo_date(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    return f"{format_date(value)}, {parsed:%H:%M}"


def metric_card(label: str, value: str, *, accent: str = "#315c45") -> Any:
    return ui.div(
        ui.tags.span(label, class_="metric-label"),
        ui.tags.strong(value, class_="metric-value"),
        class_="metric-card",
        style=f"--metric-accent: {accent}",
    )


def popup_html(photo: dict[str, Any]) -> str:
    description = photo["description"] or "Foto del recorrido"
    return f"""
    <article class="map-photo-popup">
      <img src="{escape(photo['thumbnail_url'])}" alt="{escape(photo['alt_text'])}">
      <strong>{escape(description)}</strong>
      <time>{escape(format_photo_date(photo['captured_at']))}</time>
    </article>
    """


app_ui = ui.page_fluid(
    ui.tags.head(
        ui.tags.meta(name="viewport", content="width=device-width, initial-scale=1"),
        ui.tags.meta(
            name="description",
            content="Explorador de rutas, altimetría y fotografías de Botas Puestas",
        ),
        ui.tags.link(rel="stylesheet", href="styles.css"),
        ui.tags.script(src="route-state.js", defer=True),
    ),
    ui.div(
        ui.tags.header(
            ui.div(
                ui.tags.p("ARCHIVO DE CAMINATAS", class_="eyebrow"),
                ui.h1("Botas Puestas"),
                ui.tags.p(
                    "Rutas, desniveles y fotografías sobre el terreno.",
                    class_="hero-copy",
                ),
            ),
            ui.tags.span("México", class_="place-pill"),
            class_="hero",
        ),
        ui.div(
            CONTENT_ERROR,
            class_="content-error",
            role="alert",
            style=None if CONTENT_ERROR else "display:none",
        ),
        ui.layout_sidebar(
            ui.sidebar(
                ui.tags.p("RECORRIDO", class_="section-kicker"),
                ui.input_select(
                    "ruta",
                    "Selecciona una ruta",
                    choices=ROUTE_CHOICES or {"": "Sin rutas"},
                    selected=DEFAULT_ROUTE,
                ),
                ui.output_ui("route_summary"),
                width=320,
                open="always",
            ),
            ui.output_ui("metrics"),
            ui.layout_columns(
                ui.card(
                    ui.card_header("Mapa del recorrido"),
                    output_widget("mapa", height="560px"),
                    class_="map-card",
                ),
                ui.card(
                    ui.card_header("Perfil de elevación"),
                    output_widget("perfil", height="360px"),
                    ui.tags.p(
                        "El perfil se filtra cada 20 m para evitar sumar ruido del GPS.",
                        class_="method-note",
                    ),
                    class_="profile-card",
                ),
                col_widths=(7, 5),
                class_="visual-grid",
            ),
            ui.card(
                ui.card_header("Fotografías geolocalizadas"),
                ui.output_ui("gallery"),
                class_="gallery-card",
            ),
        ),
        ui.tags.footer(
            "Las categorías describen esfuerzo físico estimado; no sustituyen una "
            "evaluación de seguridad o dificultad técnica.",
            class_="site-footer",
        ),
        class_="app-container",
    ),
    title="Botas Puestas — Explorador de rutas",
)


def server(input: Any, output: Any, session: Any) -> None:
    @reactive.calc
    def selected_route() -> dict[str, Any] | None:
        route_id = input.ruta()
        return ROUTES.get(route_id) or next(iter(ROUTES.values()), None)

    @render.ui
    def route_summary() -> Any:
        route = selected_route()
        if route is None:
            return ui.tags.p("No hay rutas generadas.", class_="empty-state")
        details = [format_date(route["date"])]
        if route["region"]:
            details.append(route["region"])
        details.append(f"{len(route['photos'])} fotos")
        return ui.div(
            ui.h2(route["title"], class_="route-title"),
            ui.tags.p(" · ".join(details), class_="route-meta"),
            ui.tags.p(route["description"], class_="route-description")
            if route["description"]
            else None,
            ui.tags.p(
                f"Clave: {route['id']}",
                class_="route-key",
            ),
            class_="route-summary",
        )

    @render.ui
    def metrics() -> Any:
        route = selected_route()
        if route is None:
            return None
        values = route["metrics"]
        effort = values["effort"]
        ascent = values["ascent_m"]
        descent = values["descent_m"]
        return ui.div(
            metric_card("Distancia", f"{values['distance_m'] / 1_000:.1f} km"),
            metric_card("Ascenso", f"+{round(ascent / 10) * 10:.0f} m" if ascent else "Sin datos"),
            metric_card(
                "Descenso", f"−{round(descent / 10) * 10:.0f} m" if descent else "Sin datos"
            ),
            metric_card("En movimiento", format_duration(values["moving_seconds"])),
            metric_card("Esfuerzo", effort["label"], accent=effort["color"]),
            class_="metrics-grid",
        )

    @render_widget
    def mapa() -> Map:
        route = selected_route()
        if route is None:
            return Map(center=(19.4326, -99.1332), zoom=9)

        bounds = route["bounds"]
        center = (
            (bounds[0][0] + bounds[1][0]) / 2,
            (bounds[0][1] + bounds[1][1]) / 2,
        )
        map_config = next(
            (
                item
                for item in CATALOG.get("maps", [])
                if item["id"] == CATALOG.get("default_map")
            ),
            CATALOG.get("maps", [{}])[0],
        )
        tile_layer = TileLayer(
            url=map_config.get("url", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"),
            attribution=map_config.get("atribucion", "© OpenStreetMap contributors"),
            min_zoom=map_config.get("zoom_minimo", 1),
            max_zoom=map_config.get("zoom_maximo", 19),
        )
        route_map = Map(
            center=center,
            zoom=12,
            layers=(tile_layer,),
            scroll_wheel_zoom=True,
            layout={"height": "100%", "width": "100%"},
        )
        for segment in route["segments"]:
            route_map.add(
                Polyline(
                    locations=segment,
                    color="#c8643b",
                    weight=5,
                    opacity=0.92,
                )
            )

        first_segment = next((segment for segment in route["segments"] if segment), None)
        last_segment = next(
            (segment for segment in reversed(route["segments"]) if segment),
            None,
        )
        if first_segment:
            route_map.add(Marker(location=first_segment[0], title="Inicio"))
        if last_segment:
            route_map.add(Marker(location=last_segment[-1], title="Final"))

        photo_markers: list[Marker] = []
        for photo in route["photos"]:
            marker = Marker(
                location=(photo["lat"], photo["lon"]),
                title=format_photo_date(photo["captured_at"]),
            )
            marker.popup = HTML(value=popup_html(photo))
            photo_markers.append(marker)
        if photo_markers:
            route_map.add(MarkerCluster(markers=photo_markers))

        route_map.add(ScaleControl(position="bottomleft", metric=True, imperial=False))
        route_map.add(FullScreenControl(position="topright"))
        route_map.fit_bounds(bounds)
        return route_map

    @render_widget
    def perfil() -> go.Figure:
        route = selected_route()
        profile = route["profile"] if route else []
        figure = go.Figure()
        if profile:
            figure.add_trace(
                go.Scatter(
                    x=[point[0] for point in profile],
                    y=[point[1] for point in profile],
                    mode="lines",
                    line={"color": "#315c45", "width": 2.5},
                    fill="tozeroy",
                    fillcolor="rgba(49, 92, 69, 0.16)",
                    hovertemplate="%{x:.1f} km<br>%{y:.0f} m<extra></extra>",
                )
            )
        else:
            figure.add_annotation(
                text="Esta ruta no tiene elevación disponible.",
                x=0.5,
                y=0.5,
                xref="paper",
                yref="paper",
                showarrow=False,
            )
        figure.update_layout(
            template="plotly_white",
            margin={"l": 55, "r": 20, "t": 20, "b": 50},
            xaxis_title="Distancia (km)",
            yaxis_title="Elevación (m)",
            hovermode="x unified",
            showlegend=False,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font={"family": "system-ui, sans-serif", "color": "#24372d"},
        )
        return figure

    @render.ui
    def gallery() -> Any:
        route = selected_route()
        if route is None or not route["photos"]:
            return ui.tags.p("Esta ruta todavía no tiene fotografías.", class_="empty-state")
        cards = []
        for photo in sorted(route["photos"], key=lambda item: item["captured_at"]):
            cards.append(
                ui.tags.figure(
                    ui.tags.a(
                        ui.tags.img(
                            src=photo["thumbnail_url"],
                            alt=photo["alt_text"],
                            loading="lazy",
                        ),
                        href=photo["image_url"],
                        target="_blank",
                        rel="noopener",
                    ),
                    ui.tags.figcaption(
                        ui.tags.strong(photo["description"] or "Foto del recorrido"),
                        ui.tags.time(
                            format_photo_date(photo["captured_at"]),
                            datetime=photo["captured_at"],
                        ),
                    ),
                    class_="photo-card",
                )
            )
        return ui.div(*cards, class_="photo-grid")


app = App(app_ui, server, static_assets=WWW_DIR)
