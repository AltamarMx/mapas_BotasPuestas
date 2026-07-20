"""Genera una comparación PNG de los dos archivos GPX legacy de Gilles."""

from __future__ import annotations

import argparse
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GPX_DIR = PROJECT_ROOT / "rutas" / "GPX-Gilles legacy"
DEFAULT_OUTPUT = DEFAULT_GPX_DIR / "visualizacion-gpx-legacy.png"
DEFAULT_SATELLITE_OUTPUT = DEFAULT_GPX_DIR / "visualizacion-gpx-legacy-satelite.png"
SATELLITE_TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{zoom}/{y}/{x}"
)
SATELLITE_ATTRIBUTION = (
    "Esri World Imagery · Esri, Maxar, Earthstar Geographics y GIS User Community"
)
TILE_SIZE = 256
WEB_MERCATOR_MAX_LAT = 85.05112878

Point = tuple[float, float]
Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class GpxData:
    path: Path
    tracks: list[list[Point]]
    routes: list[list[Point]]
    waypoints: list[Point]

    @property
    def points(self):
        for collection in (self.tracks, self.routes):
            for line in collection:
                yield from line
        yield from self.waypoints

    @property
    def track_point_count(self) -> int:
        return sum(len(line) for line in self.tracks)

    @property
    def route_point_count(self) -> int:
        return sum(len(line) for line in self.routes)


@dataclass(frozen=True)
class Projection:
    min_lon: float
    max_lon: float
    min_lat: float
    max_lat: float
    cos_lat: float
    scale: float
    left: float
    top: float
    right: float
    bottom: float

    def point(self, point: Point) -> tuple[int, int]:
        lon, lat = point
        x = self.left + (lon - self.min_lon) * self.cos_lat * self.scale
        y = self.bottom - (lat - self.min_lat) * self.scale
        return round(x), round(y)


def parse_point(element: ElementTree.Element) -> Point | None:
    try:
        lat = float(element.attrib["lat"])
        lon = float(element.attrib["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lon, lat


def parse_line(element: ElementTree.Element, point_tag: str) -> list[Point]:
    return [
        point for child in element.findall(point_tag) if (point := parse_point(child)) is not None
    ]


def parse_gpx(path: Path) -> GpxData:
    root = ElementTree.parse(path).getroot()

    tracks = []
    for track in root.findall("{*}trk"):
        for segment in track.findall("{*}trkseg"):
            points = parse_line(segment, "{*}trkpt")
            if points:
                tracks.append(points)

    routes = []
    for route in root.findall("{*}rte"):
        points = parse_line(route, "{*}rtept")
        if points:
            routes.append(points)

    waypoints = [
        point for element in root.findall("{*}wpt") if (point := parse_point(element)) is not None
    ]

    if not tracks and not routes and not waypoints:
        raise ValueError(f"{path} no contiene coordenadas GPX válidas")
    return GpxData(path=path, tracks=tracks, routes=routes, waypoints=waypoints)


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = ("DejaVuSans-Bold.ttf", "Arial Bold.ttf") if bold else ("DejaVuSans.ttf", "Arial.ttf")
    directories = (
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/System/Library/Fonts/Supplemental"),
        Path("/Library/Fonts"),
    )
    for directory in directories:
        for name in names:
            try:
                return ImageFont.truetype(directory / name, size=size)
            except OSError:
                pass
    return ImageFont.load_default(size=size)


def combined_bounds(items: list[GpxData]) -> Box:
    points = [point for item in items for point in item.points]
    lons, lats = zip(*points, strict=True)
    return min(lons), min(lats), max(lons), max(lats)


def web_mercator_pixel(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    lat = min(max(lat, -WEB_MERCATOR_MAX_LAT), WEB_MERCATOR_MAX_LAT)
    world_size = TILE_SIZE * 2**zoom
    x = (lon + 180) / 360 * world_size
    lat_radians = math.radians(lat)
    y = (1 - math.asinh(math.tan(lat_radians)) / math.pi) / 2 * world_size
    return x, y


def download_tile(zoom: int, x: int, y: int) -> tuple[int, int, Image.Image]:
    url = SATELLITE_TILE_URL.format(zoom=zoom, x=x, y=y)
    request = Request(
        url,
        headers={
            "User-Agent": (
                "mapas-botaspuestas/0.1 (+https://github.com/AltamarMx/mapas_BotasPuestas)"
            )
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"No se pudo descargar la tesela {zoom}/{y}/{x}: {exc}") from exc

    try:
        with Image.open(BytesIO(payload)) as tile:
            return x, y, tile.convert("RGB")
    except OSError as exc:
        raise RuntimeError(f"La tesela {zoom}/{y}/{x} no es una imagen válida") from exc


def download_satellite_image(bounds: Box, zoom: int) -> Image.Image:
    min_lon, min_lat, max_lon, max_lat = bounds
    min_x, min_y = web_mercator_pixel(min_lon, max_lat, zoom)
    max_x, max_y = web_mercator_pixel(max_lon, min_lat, zoom)
    first_tile_x = math.floor(min_x / TILE_SIZE)
    last_tile_x = math.ceil(max_x / TILE_SIZE) - 1
    first_tile_y = math.floor(min_y / TILE_SIZE)
    last_tile_y = math.ceil(max_y / TILE_SIZE) - 1
    coordinates = [
        (x, y)
        for y in range(first_tile_y, last_tile_y + 1)
        for x in range(first_tile_x, last_tile_x + 1)
    ]
    if len(coordinates) > 100:
        raise ValueError(
            f"El encuadre necesita {len(coordinates)} teselas; usa un zoom satelital menor"
        )

    columns = last_tile_x - first_tile_x + 1
    rows = last_tile_y - first_tile_y + 1
    mosaic = Image.new("RGB", (columns * TILE_SIZE, rows * TILE_SIZE))
    with ThreadPoolExecutor(max_workers=min(6, len(coordinates))) as executor:
        futures = {executor.submit(download_tile, zoom, x, y): (x, y) for x, y in coordinates}
        for future in as_completed(futures):
            x, y, tile = future.result()
            mosaic.paste(
                tile,
                ((x - first_tile_x) * TILE_SIZE, (y - first_tile_y) * TILE_SIZE),
            )

    origin_x = first_tile_x * TILE_SIZE
    origin_y = first_tile_y * TILE_SIZE
    crop = mosaic.crop(
        (
            math.floor(min_x - origin_x),
            math.floor(min_y - origin_y),
            math.ceil(max_x - origin_x),
            math.ceil(max_y - origin_y),
        )
    )
    crop = ImageEnhance.Color(crop).enhance(0.82)
    return ImageEnhance.Brightness(crop).enhance(0.78)


def make_projection(bounds: Box, viewport: Box) -> Projection:
    min_lon, min_lat, max_lon, max_lat = bounds
    viewport_left, viewport_top, viewport_right, viewport_bottom = viewport
    mean_lat = (min_lat + max_lat) / 2
    cos_lat = math.cos(math.radians(mean_lat))
    world_width = max((max_lon - min_lon) * cos_lat, 1e-9)
    world_height = max(max_lat - min_lat, 1e-9)
    scale = min(
        (viewport_right - viewport_left) / world_width,
        (viewport_bottom - viewport_top) / world_height,
    )
    map_width = world_width * scale
    map_height = world_height * scale
    left = viewport_left + (viewport_right - viewport_left - map_width) / 2
    top = viewport_top + (viewport_bottom - viewport_top - map_height) / 2
    return Projection(
        min_lon=min_lon,
        max_lon=max_lon,
        min_lat=min_lat,
        max_lat=max_lat,
        cos_lat=cos_lat,
        scale=scale,
        left=left,
        top=top,
        right=left + map_width,
        bottom=top + map_height,
    )


def grid_values(start: float, end: float, step: float = 0.05):
    value = math.ceil(start / step) * step
    while value < end:
        yield round(value, 10)
        value += step


def coordinate_label(value: float, positive: str, negative: str) -> str:
    direction = positive if value >= 0 else negative
    return f"{abs(value):.2f}° {direction}"


def draw_grid(
    draw: ImageDraw.ImageDraw,
    projection: Projection,
    label_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    *,
    satellite: bool = False,
) -> None:
    grid_color = (255, 255, 255, 105) if satellite else (197, 203, 211, 150)
    label_color = (91, 100, 113, 255)
    for lon in grid_values(projection.min_lon, projection.max_lon):
        x, _ = projection.point((lon, projection.min_lat))
        draw.line((x, projection.top, x, projection.bottom), fill=grid_color, width=1)
        label = coordinate_label(lon, "E", "O")
        box = draw.textbbox((0, 0), label, font=label_font)
        text_width = box[2] - box[0]
        draw.text(
            (x - text_width / 2, projection.bottom + 8),
            label,
            font=label_font,
            fill=label_color,
        )

    for lat in grid_values(projection.min_lat, projection.max_lat):
        _, y = projection.point((projection.min_lon, lat))
        draw.line((projection.left, y, projection.right, y), fill=grid_color, width=1)
        label = coordinate_label(lat, "N", "S")
        box = draw.textbbox((0, 0), label, font=label_font)
        text_width = box[2] - box[0]
        draw.text(
            (projection.left - text_width - 10, y - 9),
            label,
            font=label_font,
            fill=label_color,
        )


def draw_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[list[Point]],
    projection: Projection,
    *,
    color: tuple[int, int, int, int],
    width: int,
) -> None:
    for line in lines:
        projected = [projection.point(point) for point in line]
        if len(projected) == 1:
            x, y = projected[0]
            draw.ellipse((x - width, y - width, x + width, y + width), fill=color)
        else:
            draw.line(projected, fill=color, width=width, joint="curve")


def draw_scale_and_north(
    draw: ImageDraw.ImageDraw,
    projection: Projection,
    label_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    *,
    satellite: bool = False,
) -> None:
    ink = (255, 255, 255, 255) if satellite else (35, 42, 52, 255)
    backdrop = (9, 15, 24, 170)
    scale_km = 5
    scale_width = scale_km / 111.32 * projection.scale
    start_x = projection.left + 24
    end_x = start_x + scale_width
    y = projection.bottom - 28
    if satellite:
        draw.rounded_rectangle(
            (start_x - 12, y - 39, end_x + 12, y + 15),
            radius=8,
            fill=backdrop,
        )
    draw.line((start_x, y, end_x, y), fill=ink, width=5)
    draw.line((start_x, y - 7, start_x, y + 7), fill=ink, width=3)
    draw.line((end_x, y - 7, end_x, y + 7), fill=ink, width=3)
    draw.text((start_x, y - 31), f"{scale_km} km", font=label_font, fill=ink)

    north_x = projection.right - 30
    north_y = projection.top + 24
    if satellite:
        draw.rounded_rectangle(
            (north_x - 22, north_y - 10, north_x + 22, north_y + 61),
            radius=8,
            fill=backdrop,
        )
    draw.polygon(
        ((north_x, north_y), (north_x - 9, north_y + 30), (north_x + 9, north_y + 30)),
        fill=ink,
    )
    label = "N"
    box = draw.textbbox((0, 0), label, font=label_font)
    draw.text(
        (north_x - (box[2] - box[0]) / 2, north_y + 34),
        label,
        font=label_font,
        fill=ink,
    )


def draw_legend_item(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    color: tuple[int, int, int, int],
    text: str,
    label_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    *,
    point: bool = False,
) -> float:
    if point:
        draw.ellipse((x, y + 5, x + 12, y + 17), fill=color)
    else:
        draw.line((x, y + 11, x + 30, y + 11), fill=color, width=5)
    text_x = x + (24 if point else 40)
    draw.text((text_x, y), text, font=label_font, fill=(53, 62, 74, 255))
    text_box = draw.textbbox((text_x, y), text, font=label_font)
    return text_box[2] + 30


def draw_panel(
    image: Image.Image,
    panel: Box,
    data: GpxData,
    bounds: Box,
    track_color: tuple[int, int, int, int],
    basemap: Image.Image | None = None,
) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    left, top, right, bottom = panel
    title_font = font(27, bold=True)
    summary_font = font(20)
    axis_font = font(16)
    legend_font = font(18)

    draw.rounded_rectangle(
        panel,
        radius=22,
        fill=(250, 250, 248, 255),
        outline=(210, 214, 220, 255),
        width=2,
    )
    draw.text((left + 30, top + 24), data.path.name, font=title_font, fill=(29, 36, 46, 255))
    summary = (
        f"{len(data.routes):,} rutas · {len(data.tracks):,} tracks · "
        f"{len(data.waypoints):,} waypoints · "
        f"{data.route_point_count + data.track_point_count:,} puntos de línea"
    )
    draw.text((left + 30, top + 66), summary, font=summary_font, fill=(91, 100, 113, 255))

    viewport = (left + 92, top + 126, right - 32, bottom - 112)
    projection = make_projection(bounds, viewport)
    map_box = (projection.left, projection.top, projection.right, projection.bottom)
    if basemap is None:
        draw.rectangle(map_box, fill=(244, 246, 244, 255))
    else:
        map_size = (
            max(1, round(projection.right - projection.left)),
            max(1, round(projection.bottom - projection.top)),
        )
        resized_basemap = basemap.resize(map_size, Image.Resampling.LANCZOS)
        image.paste(resized_basemap, (round(projection.left), round(projection.top)))
    draw.rectangle(map_box, outline=(170, 177, 186, 255), width=2)
    draw_grid(draw, projection, axis_font, satellite=basemap is not None)

    route_color = (45, 212, 191, 225) if basemap is not None else (15, 118, 110, 118)
    waypoint_color = (17, 24, 39, 230)
    if basemap is not None:
        halo_color = (5, 8, 13, 185)
        draw_lines(draw, data.routes, projection, color=halo_color, width=5)
        draw_lines(draw, data.tracks, projection, color=halo_color, width=7)
    draw_lines(draw, data.routes, projection, color=route_color, width=2)
    draw_lines(draw, data.tracks, projection, color=track_color, width=3)
    for lon, lat in data.waypoints:
        x, y = projection.point((lon, lat))
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(255, 255, 255, 235))
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=waypoint_color)

    draw_scale_and_north(draw, projection, axis_font, satellite=basemap is not None)

    legend_y = bottom - 68
    legend_x = left + 36
    legend_x = draw_legend_item(
        draw,
        legend_x,
        legend_y,
        route_color,
        f"<rte> ({len(data.routes):,})",
        legend_font,
    )
    legend_x = draw_legend_item(
        draw,
        legend_x,
        legend_y,
        track_color,
        f"<trkseg> ({len(data.tracks):,})",
        legend_font,
    )
    draw_legend_item(
        draw,
        legend_x,
        legend_y,
        waypoint_color,
        f"<wpt> ({len(data.waypoints):,})",
        legend_font,
        point=True,
    )


def render(
    items: list[GpxData],
    output: Path,
    *,
    width: int = 2400,
    basemap: Image.Image | None = None,
) -> None:
    if len(items) != 2:
        raise ValueError("Esta comparación necesita exactamente dos archivos GPX")

    height = round(width * 0.625)
    scale = width / 2400
    image = Image.new("RGBA", (width, height), (240, 242, 245, 255))
    draw = ImageDraw.Draw(image, "RGBA")
    title_font = font(max(24, round(46 * scale)), bold=True)
    subtitle_font = font(max(16, round(22 * scale)))
    footer_font = font(max(14, round(17 * scale)))

    draw.text(
        (80 * scale, 48 * scale),
        (
            "Archivos GPX legacy de Gilles · vista satelital"
            if basemap is not None
            else "Archivos GPX legacy de Gilles · comparación geográfica"
        ),
        font=title_font,
        fill=(24, 31, 42, 255),
    )
    draw.text(
        (80 * scale, 112 * scale),
        "Misma extensión y escala en ambos paneles · Tepoztlán y alrededores",
        font=subtitle_font,
        fill=(83, 93, 106, 255),
    )

    gap = 54 * scale
    margin = 80 * scale
    panel_top = 180 * scale
    panel_bottom = 1355 * scale
    panel_width = (width - 2 * margin - gap) / 2
    panels = [
        (margin, panel_top, margin + panel_width, panel_bottom),
        (margin + panel_width + gap, panel_top, width - margin, panel_bottom),
    ]
    bounds = combined_bounds(items)
    colors = (
        ((255, 166, 0, 255), (190, 106, 255, 245))
        if basemap is not None
        else ((217, 119, 6, 210), (124, 58, 237, 180))
    )
    for panel, data, color in zip(panels, items, colors, strict=True):
        draw_panel(image, panel, data, bounds, color, basemap)

    draw.text(
        (80 * scale, 1405 * scale),
        (
            f"Base satelital: {SATELLITE_ATTRIBUTION} · las rutas <rte> aparecen en ambos archivos"
            if basemap is not None
            else "Visualización sin mapa base · proyección equirectangular local · "
            "las rutas <rte> aparecen en ambos archivos"
        ),
        font=footer_font,
        fill=(91, 100, 113, 255),
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output, format="PNG", optimize=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "gpx",
        nargs="*",
        type=Path,
        help="Dos archivos GPX; por omisión usa los dos de rutas/GPX-Gilles legacy",
    )
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--width", type=int, default=2400, help="Ancho del PNG en píxeles")
    parser.add_argument(
        "--satellite",
        action="store_true",
        help="Descarga y agrega como fondo teselas de Esri World Imagery",
    )
    parser.add_argument(
        "--satellite-zoom",
        type=int,
        default=13,
        help="Nivel de zoom de las teselas satelitales (por omisión: 13)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    paths = args.gpx or sorted(DEFAULT_GPX_DIR.glob("*.gpx"))
    if len(paths) != 2:
        raise SystemExit(f"Se esperaban exactamente 2 GPX; se encontraron {len(paths)}")
    if args.width < 1200:
        raise SystemExit("--width debe ser de al menos 1200 píxeles")
    if not 1 <= args.satellite_zoom <= 20:
        raise SystemExit("--satellite-zoom debe estar entre 1 y 20")

    items = [parse_gpx(path) for path in paths]
    basemap = None
    if args.satellite:
        print(f"Descargando la vista satelital (zoom {args.satellite_zoom})...")
        basemap = download_satellite_image(combined_bounds(items), args.satellite_zoom)
    output = args.output or (DEFAULT_SATELLITE_OUTPUT if args.satellite else DEFAULT_OUTPUT)
    render(items, output, width=args.width, basemap=basemap)
    print(f"PNG generado: {output.resolve()}")


if __name__ == "__main__":
    main()
