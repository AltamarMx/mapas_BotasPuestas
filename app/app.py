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
    WidgetControl,
)
from ipywidgets import HTML
from shiny import App, reactive, render, ui
from shinywidgets import output_widget, render_widget

APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "_generated"
WWW_DIR = APP_DIR / "www"
ALL_ROUTES_ID = "__all__"
ROUTE_COLORS = ("#c8643b", "#315c45", "#6b5ca5", "#b7791f", "#2b6f8a", "#9b2c2c")


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
ROUTE_CHOICES = {ALL_ROUTES_ID: "Todas las rutas"} | {
    summary["id"]: summary["title"] for summary in CATALOG.get("routes", [])
}
DEFAULT_ROUTE = ALL_ROUTES_ID


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


def elevation_axis_range(minimum_m: float | None, maximum_m: float | None) -> list[float] | None:
    if minimum_m is None or maximum_m is None:
        return None
    margin_m = max((maximum_m - minimum_m) * 0.10, 10.0)
    return [minimum_m - margin_m, maximum_m + margin_m]


def combined_bounds(routes: list[dict[str, Any]]) -> list[list[float]] | None:
    if not routes:
        return None
    return [
        [
            min(route["bounds"][0][0] for route in routes),
            min(route["bounds"][0][1] for route in routes),
        ],
        [
            max(route["bounds"][1][0] for route in routes),
            max(route["bounds"][1][1] for route in routes),
        ],
    ]


def route_color(index: int) -> str:
    return ROUTE_COLORS[index % len(ROUTE_COLORS)]


def metric_card(label: str, value: str, *, accent: str = "#315c45") -> Any:
    return ui.div(
        ui.tags.span(label, class_="metric-label"),
        ui.tags.strong(value, class_="metric-value"),
        class_="metric-card",
        style=f"--metric-accent: {accent}",
    )


def popup_html(photo: dict[str, Any], route_title: str) -> str:
    description = photo["description"] or "Foto del recorrido"
    return f"""
    <article class="map-photo-popup">
      <img src="{escape(photo['thumbnail_url'])}" alt="{escape(photo['alt_text'])}">
      <strong>{escape(description)}</strong>
      <span>{escape(route_title)}</span>
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
    def selected_routes() -> list[dict[str, Any]]:
        route_id = input.ruta()
        if route_id == ALL_ROUTES_ID:
            return list(ROUTES.values())
        route = ROUTES.get(route_id)
        return [route] if route else list(ROUTES.values())

    @reactive.calc
    def showing_all_routes() -> bool:
        return input.ruta() == ALL_ROUTES_ID

    @render.ui
    def route_summary() -> Any:
        routes = selected_routes()
        if not routes:
            return ui.tags.p("No hay rutas generadas.", class_="empty-state")
        if showing_all_routes():
            photo_count = sum(len(route["photos"]) for route in routes)
            return ui.div(
                ui.h2("Todas las rutas", class_="route-title"),
                ui.tags.p(
                    f"{len(routes)} recorridos · {photo_count} fotos",
                    class_="route-meta",
                ),
                ui.tags.p(
                    "Vista general del archivo. Selecciona una ruta para consultar su detalle.",
                    class_="route-description",
                ),
                class_="route-summary",
            )
        route = routes[0]
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
        routes = selected_routes()
        if not routes:
            return None
        if showing_all_routes():
            distance_m = sum(route["metrics"]["distance_m"] for route in routes)
            ascent_m = sum(route["metrics"]["ascent_m"] or 0 for route in routes)
            descent_m = sum(route["metrics"]["descent_m"] or 0 for route in routes)
            photo_count = sum(len(route["photos"]) for route in routes)
            return ui.div(
                metric_card("Rutas", str(len(routes))),
                metric_card("Distancia total", f"{distance_m / 1_000:.1f} km"),
                metric_card("Ascenso total", f"+{round(ascent_m / 10) * 10:.0f} m"),
                metric_card("Descenso total", f"−{round(descent_m / 10) * 10:.0f} m"),
                metric_card("Fotografías", str(photo_count), accent="#c8643b"),
                class_="metrics-grid",
            )
        route = routes[0]
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
        routes = selected_routes()
        bounds = combined_bounds(routes)
        if not routes or bounds is None:
            return Map(center=(19.4326, -99.1332), zoom=9)

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
        for index, route in enumerate(routes):
            color = route_color(index)
            for segment in route["segments"]:
                route_map.add(
                    Polyline(
                        locations=segment,
                        color=color,
                        weight=5,
                        opacity=0.92,
                    )
                )

        if not showing_all_routes():
            route = routes[0]
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
        for route in routes:
            for photo in route["photos"]:
                marker = Marker(
                    location=(photo["lat"], photo["lon"]),
                    title=format_photo_date(photo["captured_at"]),
                )
                marker.popup = HTML(value=popup_html(photo, route["title"]))
                photo_markers.append(marker)
        if photo_markers:
            route_map.add(MarkerCluster(markers=photo_markers))

        if showing_all_routes():
            legend_items = "".join(
                "<div><span style=\"background:"
                f"{route_color(index)}\"></span>{escape(route['title'])}</div>"
                for index, route in enumerate(routes)
            )
            route_map.add(
                WidgetControl(
                    widget=HTML(
                        value=(
                            '<aside class="map-legend"><strong>Rutas</strong>'
                            f"{legend_items}</aside>"
                        )
                    ),
                    position="bottomright",
                )
            )

        route_map.add(ScaleControl(position="bottomleft", metric=True, imperial=False))
        route_map.add(FullScreenControl(position="topright"))
        route_map.fit_bounds(bounds)
        return route_map

    @render_widget
    def perfil() -> go.Figure:
        routes = selected_routes()
        figure = go.Figure()
        routes_with_profile = [route for route in routes if route["profile"]]
        for index, route in enumerate(routes_with_profile):
            profile = route["profile"]
            single_route = len(routes_with_profile) == 1
            figure.add_trace(
                go.Scatter(
                    x=[point[0] for point in profile],
                    y=[point[1] for point in profile],
                    name=route["title"],
                    mode="lines",
                    line={"color": route_color(index), "width": 2.5},
                    fill="tozeroy" if single_route else None,
                    fillcolor="rgba(200, 100, 59, 0.14)",
                    hovertemplate="%{x:.1f} km<br>%{y:.0f} m<extra>%{fullData.name}</extra>",
                )
            )
        if not routes_with_profile:
            figure.add_annotation(
                text="No hay elevación disponible para esta vista.",
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
            showlegend=len(routes_with_profile) > 1,
            legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font={"family": "system-ui, sans-serif", "color": "#24372d"},
        )
        minimum_values = [
            route["metrics"]["elevation_min_m"]
            for route in routes_with_profile
            if route["metrics"]["elevation_min_m"] is not None
        ]
        maximum_values = [
            route["metrics"]["elevation_max_m"]
            for route in routes_with_profile
            if route["metrics"]["elevation_max_m"] is not None
        ]
        axis_range = elevation_axis_range(
            min(minimum_values) if minimum_values else None,
            max(maximum_values) if maximum_values else None,
        )
        if axis_range is not None:
            figure.update_yaxes(range=axis_range)
        return figure

    @render.ui
    def gallery() -> Any:
        routes = selected_routes()
        photos = [
            (route, photo)
            for route in routes
            for photo in route["photos"]
        ]
        if not photos:
            return ui.tags.p("Esta vista todavía no tiene fotografías.", class_="empty-state")
        cards = []
        for route, photo in sorted(photos, key=lambda item: item[1]["captured_at"]):
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
                        ui.tags.span(route["title"], class_="photo-route")
                        if showing_all_routes()
                        else None,
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
