"""Reusable quicklook-style map renderer for AIFSHeavyRain.

This module generalizes the rendering pattern used by the previous
render_quicklook_images.py pipeline: shaded field, shaded+contour,
shaded+vector, and wind-speed shaded products.

It is intentionally step-agnostic so it can be reused by:
- step03 AI Pass 1 broad maps
- later Detail Evidence products for AI Pass 2
- final dashboard / portable bundle rendering

Operational map resources are local-first.  The South-Korea first-order
administrative-boundary resource is bootstrapped once from a pinned Natural
Earth archive when it is absent, then reused locally.  Rendering still proceeds
without that optional layer if the bootstrap is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import math
from datetime import datetime, timedelta
from functools import lru_cache
import os
import re
import tempfile
from typing import Any, Iterable
from urllib.request import Request, urlopen
import zipfile

from func.shared.runtime_paths import resolve_logical_ref, runtime_dir


G0 = 9.80665

KOREA_ADMIN1_RESOURCE_DIR = "ne_10m_admin_1_states_provinces_lines"
KOREA_ADMIN1_RESOURCE_STEM = "ne_10m_admin_1_states_provinces_lines"
KOREA_ADMIN1_RESOURCE_URL = (
    "https://naturalearth.s3.amazonaws.com/10m_cultural/"
    "ne_10m_admin_1_states_provinces_lines.zip"
)
KOREA_ADMIN1_RESOURCE_VERSION = "Natural Earth 1:10m admin-1 boundary lines"

_SUPERSCRIPT_TO_ASCII = {
    "⁻": "-", "⁺": "+",
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
}
_SUPERSCRIPT_PATTERN = re.compile(r"[⁻⁺⁰¹²³⁴⁵⁶⁷⁸⁹]+")


def normalize_display_mathtext(value: Any) -> str:
    """Convert Unicode superscripts to Matplotlib mathtext.

    Korean operational fonts often lack U+207B (superscript minus).  Mixed
    font fallback can then render a dummy square in PNG output.  This helper
    preserves the surrounding Korean text while converting only superscript
    runs, e.g. ``m s⁻¹`` -> ``m s$^{-1}$``.  Existing mathtext is unchanged.
    """
    text = str(value)

    def repl(match: re.Match[str]) -> str:
        token = "".join(_SUPERSCRIPT_TO_ASCII[ch] for ch in match.group(0))
        return f"$^{{{token}}}$"

    return _SUPERSCRIPT_PATTERN.sub(repl, text)


def _read_image_size(path: Path) -> tuple[int, int]:
    from PIL import Image  # type: ignore
    with Image.open(path) as image:
        image.load()
        return image.size


def _trim_png_background(path: Path, *, pad_px: int = 4) -> tuple[int, int]:
    from PIL import Image, ImageChops  # type: ignore
    with Image.open(path) as original:
        image = original.convert("RGBA")
        width, height = image.size
        background_rgba = image.getpixel((0, 0))
        background = Image.new("RGBA", image.size, background_rgba)
        diff = ImageChops.difference(image, background)
        bbox = diff.getbbox()
        if bbox is None:
            return width, height
        left = max(0, bbox[0] - pad_px)
        top = max(0, bbox[1] - pad_px)
        right = min(width, bbox[2] + pad_px)
        bottom = min(height, bbox[3] + pad_px)
        cropped = image.crop((left, top, right, bottom))
        cropped.save(path)
        return cropped.size


@dataclass(frozen=True)
class MapDomain:
    lon_min: float
    lon_max: float
    lat_min: float
    lat_max: float


@dataclass(frozen=True)
class DomainOverlay:
    domain_id: str
    name_ko: str
    lon_min: float
    lon_max: float
    lat_min: float
    lat_max: float
    highlight: bool = False


@dataclass(frozen=True)
class RenderContext:
    project_root: Path
    domain: MapDomain
    style: dict[str, Any] = field(default_factory=dict)


def import_matplotlib():
    try:
        import matplotlib
        matplotlib.use('Agg')
        # Windows 운영환경에서 한국어 제목이 tofu(□)로 렌더링되지 않도록
        # 설치 가능성이 높은 한글 글꼴을 우선하고 범용 글꼴을 fallback으로 둔다.
        matplotlib.rcParams['font.family'] = 'sans-serif'
        matplotlib.rcParams['font.sans-serif'] = [
            'Malgun Gothic', 'Gulim', 'NanumGothic', 'Noto Sans CJK KR', 'DejaVu Sans'
        ]
        matplotlib.rcParams['axes.unicode_minus'] = False
        import matplotlib.pyplot as plt  # type: ignore
        import matplotlib.colors as mcolors  # type: ignore
        import matplotlib.patheffects as pe  # type: ignore
    except ModuleNotFoundError as exc:
        missing = exc.name or 'matplotlib'
        raise RuntimeError(f'required package is not installed: {missing}. Install with: pip install matplotlib') from exc
    return plt, mcolors, pe


def lat_lon_names(arr: Any) -> tuple[str, str]:
    lat_candidates = ['latitude', 'lat', 'y']
    lon_candidates = ['longitude', 'lon', 'x']
    lat_name = next((name for name in lat_candidates if name in arr.coords or name in arr.dims), None)
    lon_name = next((name for name in lon_candidates if name in arr.coords or name in arr.dims), None)
    if not lat_name or not lon_name:
        raise RuntimeError(f'could not identify latitude/longitude coordinates in array dims={arr.dims}')
    return lat_name, lon_name


def _to_2d(arr: Any):
    import numpy as np  # type: ignore
    lat_name, lon_name = lat_lon_names(arr)
    lat = arr[lat_name]
    lon = arr[lon_name]
    values = np.asarray(arr.values, dtype='float64')
    if values.ndim != 2:
        values = np.squeeze(values)
    if values.ndim != 2:
        raise RuntimeError(f'expected 2-D data, got shape={values.shape}')
    if getattr(lat, 'ndim', 1) == 1 and getattr(lon, 'ndim', 1) == 1:
        lon2d, lat2d = np.meshgrid(np.asarray(lon), np.asarray(lat))
    else:
        lon2d = np.asarray(lon)
        lat2d = np.asarray(lat)
    return lon2d, lat2d, values


def _crop_to_domain(lon2d: Any, lat2d: Any, values: Any, domain: MapDomain):
    import numpy as np  # type: ignore
    mask = (
        (lon2d >= domain.lon_min) & (lon2d <= domain.lon_max) &
        (lat2d >= domain.lat_min) & (lat2d <= domain.lat_max)
    )
    if not np.any(mask):
        return lon2d, lat2d, values
    row_any = mask.any(axis=1)
    col_any = mask.any(axis=0)
    rows = np.where(row_any)[0]
    cols = np.where(col_any)[0]
    r0, r1 = int(rows.min()), int(rows.max()) + 1
    c0, c1 = int(cols.min()), int(cols.max()) + 1
    return lon2d[r0:r1, c0:c1], lat2d[r0:r1, c0:c1], values[r0:r1, c0:c1]



def _crop_contour_with_native_neighbours(lon2d: Any, lat2d: Any, values: Any, domain: MapDomain):
    """Give Matplotlib contours real neighbouring source-grid support.

    Matplotlib requires >=2x2 cells for contour, while a valid Step04
    candidate bbox may select just one native AIFS grid point.  Extend only
    the *contour computation*, using original, un-interpolated neighbouring
    grid points.  Never change the shaded-field crop, requested map extent,
    quantitative scope or the original request domain.
    """
    import numpy as np

    cropped_lon, cropped_lat, cropped_values = _crop_to_domain(lon2d, lat2d, values, domain)
    original_shape = tuple(int(dimension) for dimension in cropped_values.shape)
    metadata = {
        "mode": "requested_domain_native_grid",
        "requested_grid_shape_yx": list(original_shape),
        "contour_grid_shape_yx": list(original_shape),
        "uses_neighbouring_native_grid": False,
        "requested_domain_unchanged": True,
        "interpolation_or_padding_applied": False,
    }
    mask = (
        (lon2d >= domain.lon_min) & (lon2d <= domain.lon_max)
        & (lat2d >= domain.lat_min) & (lat2d <= domain.lat_max)
    )
    if not bool(np.any(mask)):
        # _crop_to_domain uses the full native field when there is no centre
        # inside the requested bbox.  No synthetic data should be generated.
        raise ValueError("Contour request contains no native grid point in the requested domain")
    if min(original_shape) >= 2:
        return cropped_lon, cropped_lat, cropped_values, metadata
    rows = np.flatnonzero(np.any(mask, axis=1))
    columns = np.flatnonzero(np.any(mask, axis=0))

    def nearby_bounds(indices: Any, maximum: int) -> tuple[int, int]:
        first, stop = int(indices[0]), int(indices[-1]) + 1
        if maximum < 2:
            raise ValueError("Native contour source grid is smaller than 2x2")
        # Three grid centres give a symmetric neighbourhood for a 1-point
        # candidate domain.  At a source boundary use the available 2 centres.
        target = min(3, maximum)
        if stop - first >= target:
            return first, stop
        missing = target - (stop - first)
        first = max(0, first - (missing // 2))
        stop = min(maximum, first + target)
        first = max(0, stop - target)
        return first, stop

    r0, r1 = nearby_bounds(rows, values.shape[0])
    c0, c1 = nearby_bounds(columns, values.shape[1])
    supported = (lon2d[r0:r1, c0:c1], lat2d[r0:r1, c0:c1], values[r0:r1, c0:c1])
    metadata.update({
        "mode": "neighbouring_original_grid_contour_support",
        "contour_grid_shape_yx": [int(r1 - r0), int(c1 - c0)],
        "uses_neighbouring_native_grid": True,
    })
    return *supported, metadata

def subset_to_domain(arr: Any, domain: dict[str, Any] | MapDomain):
    if isinstance(domain, MapDomain):
        domain_dict = {'lon_min': domain.lon_min, 'lon_max': domain.lon_max, 'lat_min': domain.lat_min, 'lat_max': domain.lat_max}
    else:
        domain_dict = domain
    lat_name, lon_name = lat_lon_names(arr)
    lat = arr[lat_name]
    lon = arr[lon_name]
    mask = (
        (lat >= float(domain_dict['lat_min'])) & (lat <= float(domain_dict['lat_max'])) &
        (lon >= float(domain_dict['lon_min'])) & (lon <= float(domain_dict['lon_max']))
    )
    return arr.where(mask, drop=True)


def _env_path_list(name: str) -> list[Path]:
    value = os.environ.get(name, "")
    if not value:
        return []
    return [Path(item).expanduser() for item in value.split(os.pathsep) if item.strip()]


def _cartopy_data_dirs() -> list[Path]:
    candidates: list[Path] = []
    candidates.extend(_env_path_list("CARTOPY_DATA_DIR"))
    try:
        import cartopy  # type: ignore

        data_dir = cartopy.config.get("data_dir")
        pre_existing = cartopy.config.get("pre_existing_data_dir")
        if data_dir:
            candidates.append(Path(str(data_dir)).expanduser())
        if pre_existing:
            candidates.append(Path(str(pre_existing)).expanduser())
    except Exception:
        pass

    home = Path.home()
    candidates.extend(
        [
            home / ".local" / "share" / "cartopy",
            home / ".cartopy",
            home / "AppData" / "Local" / "cartopy",
            home / "AppData" / "Roaming" / "cartopy",
        ]
    )
    return candidates


def _default_shape_dirs(project_root: Path) -> list[Path]:
    daba_root = runtime_dir(project_root, "daba")
    dirs = [
        daba_root / "map",
        daba_root / "map" / "admin",
        daba_root / "map" / "province",
        daba_root / "map" / "boundary",
        daba_root / "map" / "borders",
        daba_root / "map" / "coastline",
        daba_root / "map" / "ne_10m_coastline",
        daba_root / "map" / "ne_50m_coastline",
        daba_root / "map" / "ne_10m_admin_0_countries",
        daba_root / "map" / "ne_10m_admin_1_states_provinces",
        project_root / "resources" / "map",
        project_root / "resources" / "map" / "admin",
        project_root / "resources" / "map" / "province",
        project_root / "resources" / "map" / "boundary",
        project_root / "resources" / "map" / "borders",
        project_root / "resources" / "map" / "coastline",
        project_root / "resources" / "map" / "ne_10m_coastline",
        project_root / "resources" / "map" / "ne_50m_coastline",
        project_root / "resources" / "map" / "ne_10m_admin_0_countries",
        project_root / "resources" / "map" / "ne_10m_admin_1_states_provinces",
        project_root / ".cartopy-cache",
    ]
    dirs.extend(_cartopy_data_dirs())
    dirs.extend(_env_path_list("AIFSHEAVYRAIN_MAP_SHAPE_DIR"))
    return dirs

def _has_required_shapefile_sidecars(path: Path) -> bool:
    """Return True when the main shapefile and required sidecars exist.

    PyShp can sometimes open partial data, but operational rendering is more
    stable when .shp, .shx, and .dbf are present together.
    """
    return (
        path.exists()
        and path.with_suffix(".shx").exists()
        and path.with_suffix(".dbf").exists()
    )


def _shape_kind(path: Path) -> str:
    full = str(path).lower()
    if "coastline" in full:
        return "coastline"
    if "admin_1" in full or "province" in full or "states_provinces" in full:
        return "admin1"
    if "admin_0" in full or "countries" in full or "boundary" in full or "borders" in full:
        return "border"
    return "map"


def _shape_priority(path: Path) -> tuple[int, str]:
    full = str(path).lower()
    kind = _shape_kind(path)
    priority = {"coastline": 0, "border": 1, "admin1": 2, "map": 9}[kind]
    return (priority, full)


def _resolve_extra_shape_dirs(project_root: Path, extra_dirs: Iterable[Path] | None) -> list[Path]:
    resolved: list[Path] = []
    if not extra_dirs:
        return resolved
    for item in extra_dirs:
        p = Path(item)
        if not p.is_absolute():
            p = resolve_logical_ref(project_root, p)
        resolved.append(p.expanduser())
    return resolved


def find_local_shapes(project_root: Path, extra_dirs: Iterable[Path] | None = None) -> list[Path]:
    """Return one coastline and one land-border layer for the map background.

    Polygon land/country layers are intentionally excluded because drawing their
    exteriors together with the coastline produces visually duplicated coastlines.
    Korean province/metropolitan-city boundaries are rendered separately by
    ``_draw_korea_admin1_boundaries``.
    """
    found: dict[str, Path] = {}
    directories = [runtime_dir(project_root, "daba") / "map", *_resolve_extra_shape_dirs(project_root, extra_dirs)]
    for directory in directories:
        if not directory.exists():
            continue
        for shp in sorted(p for p in directory.rglob("*.shp") if p.is_file()):
            if _has_required_shapefile_sidecars(shp):
                found[str(shp.resolve())] = shp

    coastlines = sorted(
        (path for path in found.values() if _shape_kind(path) == "coastline"),
        key=lambda path: (0 if "10m" in path.name.lower() else 1, str(path).lower()),
    )
    borders = sorted(
        (
            path
            for path in found.values()
            if _shape_kind(path) == "border" and "boundary_lines_land" in path.name.lower()
        ),
        key=lambda path: (0 if "10m" in path.name.lower() else 1, str(path).lower()),
    )
    selected: list[Path] = []
    if coastlines:
        selected.append(coastlines[0])
    if borders:
        selected.append(borders[0])
    return selected

def _admin1_resource_path(project_root: Path) -> Path:
    return (
        runtime_dir(project_root, "daba")
        / "map"
        / KOREA_ADMIN1_RESOURCE_DIR
        / f"{KOREA_ADMIN1_RESOURCE_STEM}.shp"
    )


def _truthy_env(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _ensure_korea_admin1_resource(project_root_text: str) -> str | None:
    """Return the local admin-1 boundary-line shapefile, bootstrapping it once if needed."""
    project_root = Path(project_root_text)
    target_shp = _admin1_resource_path(project_root)
    if _has_required_shapefile_sidecars(target_shp):
        return str(target_shp)
    if _truthy_env("AIFSHEAVYRAIN_DISABLE_MAP_RESOURCE_DOWNLOAD"):
        return None

    target_dir = target_shp.parent
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="aifs_admin1_") as tmpdir:
            archive = Path(tmpdir) / "admin1.zip"
            request = Request(
                KOREA_ADMIN1_RESOURCE_URL,
                headers={"User-Agent": "AIFSHeavyRain-map-resource/1.0"},
            )
            with urlopen(request, timeout=45) as response, archive.open("wb") as handle:
                handle.write(response.read())
            with zipfile.ZipFile(archive) as zf:
                wanted_suffixes = {".shp", ".shx", ".dbf", ".prj", ".cpg"}
                members: dict[str, str] = {}
                for name in zf.namelist():
                    path = Path(name)
                    if path.stem == KOREA_ADMIN1_RESOURCE_STEM and path.suffix.lower() in wanted_suffixes:
                        members[path.suffix.lower()] = name
                if not {".shp", ".shx", ".dbf"}.issubset(members):
                    return None
                for suffix, member in members.items():
                    destination = target_dir / f"{KOREA_ADMIN1_RESOURCE_STEM}{suffix}"
                    temporary = destination.with_name(destination.name + f".{os.getpid()}.tmp")
                    with zf.open(member) as source, temporary.open("wb") as output:
                        output.write(source.read())
                    temporary.replace(destination)
    except Exception:
        return None
    return str(target_shp) if _has_required_shapefile_sidecars(target_shp) else None


@lru_cache(maxsize=8)
def _korea_admin1_internal_segments_from_path(shp_text: str) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Return South-Korean first-order administrative boundary line segments."""
    shp = Path(shp_text)
    if not _has_required_shapefile_sidecars(shp):
        return ()
    try:
        import shapefile  # type: ignore
        reader = shapefile.Reader(str(shp))
        fields = [field[0] for field in reader.fields[1:]]
    except Exception:
        return ()

    segments: list[tuple[tuple[float, float], ...]] = []
    for shape_record in reader.iterShapeRecords():
        record = {str(key).lower(): value for key, value in zip(fields, shape_record.record)}
        adm0_a3 = str(record.get("adm0_a3") or "").strip().upper()
        adm0_name = str(record.get("adm0_name") or "").strip().lower()
        if adm0_a3 != "KOR" and adm0_name not in {"south korea", "republic of korea"}:
            continue
        shape = shape_record.shape
        points = shape.points
        if not points:
            continue
        parts = list(shape.parts) + [len(points)]
        for index in range(len(parts) - 1):
            raw = points[parts[index] : parts[index + 1]]
            if len(raw) < 2:
                continue
            segment = tuple((float(point[0]), float(point[1])) for point in raw)
            segments.append(segment)
    return tuple(segments)


def _draw_korea_admin1_boundaries(
    ax: Any,
    project_root: Path,
    domain: MapDomain,
    pe: Any | None = None,
    style: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Draw South-Korean metropolitan-city/province borders without text labels."""
    style = style or {}
    source = _ensure_korea_admin1_resource(str(project_root.resolve()))
    segments = _korea_admin1_internal_segments_from_path(source) if source else ()
    visible_segments: list[list[tuple[float, float]]] = []
    for segment in segments:
        xs = [point[0] for point in segment]
        ys = [point[1] for point in segment]
        if not xs or not ys:
            continue
        if max(xs) < domain.lon_min or min(xs) > domain.lon_max:
            continue
        if max(ys) < domain.lat_min or min(ys) > domain.lat_max:
            continue
        visible_segments.append(list(segment))

    if not visible_segments:
        return {
            "admin1_drawn": False,
            "admin1_source_path": source,
            "admin1_source_type": "natural_earth_admin1_boundary_lines" if source else "not_available",
            "admin1_boundary_segment_count": 0,
        }

    try:
        from matplotlib.collections import LineCollection  # type: ignore
        line_style = _shape_line_style("admin1", style)
        collection = LineCollection(
            visible_segments,
            colors=line_style["color"],
            linewidths=line_style["linewidth"],
            alpha=line_style["alpha"],
            zorder=line_style["zorder"],
        )
        ax.add_collection(collection)
        if pe is not None and bool(style.get("map_halo_enabled", False)):
            _apply_path_effects(
                collection,
                pe,
                foreground=line_style["halo_color"],
                linewidth=line_style["halo_linewidth"],
            )
    except Exception:
        return {
            "admin1_drawn": False,
            "admin1_source_path": source,
            "admin1_source_type": "render_failed" if source else "not_available",
            "admin1_boundary_segment_count": 0,
        }
    return {
        "admin1_drawn": True,
        "admin1_source_path": source,
        "admin1_source_type": "natural_earth_admin1_boundary_lines",
        "admin1_boundary_segment_count": len(visible_segments),
    }


def _read_shape_segments_uncached(shp: Path):
    try:
        import shapefile  # type: ignore

        reader = shapefile.Reader(str(shp))
        for shape in reader.shapes():
            points = shape.points
            if not points:
                continue
            parts = list(shape.parts) + [len(points)]
            for i in range(len(parts) - 1):
                seg = points[parts[i] : parts[i + 1]]
                if seg:
                    yield seg
        return
    except ModuleNotFoundError:
        pass

    try:
        from cartopy.io import shapereader  # type: ignore

        reader = shapereader.Reader(str(shp))
        for geom in reader.geometries():
            if geom.geom_type == "LineString":
                yield list(geom.coords)
            elif geom.geom_type == "MultiLineString":
                for line in geom.geoms:
                    yield list(line.coords)
            elif geom.geom_type == "Polygon":
                yield list(geom.exterior.coords)
                for ring in geom.interiors:
                    yield list(ring.coords)
            elif geom.geom_type == "MultiPolygon":
                for poly in geom.geoms:
                    yield list(poly.exterior.coords)
                    for ring in poly.interiors:
                        yield list(ring.coords)
    except Exception:
        return


@lru_cache(maxsize=32)
def _cached_shape_segments(path_text: str) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Read immutable local shape geometry once per rendering process."""
    return tuple(
        tuple((float(point[0]), float(point[1])) for point in segment)
        for segment in _read_shape_segments_uncached(Path(path_text))
    )


def _read_shape_segments(shp: Path):
    """Yield cached shape segments without changing map content or order."""
    yield from _cached_shape_segments(str(shp.resolve()))


def _shape_line_style(kind: str, style: dict[str, Any]) -> dict[str, Any]:
    prefixes = {
        "coastline": "coastline",
        "border": "border",
        "admin1": "admin1",
        "map": "map",
    }
    prefix = prefixes.get(kind, "map")
    fallback = {
        "color": style.get("map_line_color", "#8F8F8F"),
        "linewidth": style.get("map_linewidth", 0.45),
        "alpha": style.get("map_line_alpha", 0.65),
        "zorder": style.get("map_line_zorder", 10),
        "halo_color": style.get("map_halo_color", "white"),
        "halo_linewidth": style.get("map_halo_linewidth", 1.0),
    }
    return {
        "color": str(style.get(f"{prefix}_color", fallback["color"])),
        "linewidth": float(style.get(f"{prefix}_linewidth", fallback["linewidth"])),
        "alpha": float(style.get(f"{prefix}_alpha", fallback["alpha"])),
        "zorder": float(style.get(f"{prefix}_zorder", fallback["zorder"])),
        "halo_color": str(style.get(f"{prefix}_halo_color", fallback["halo_color"])),
        "halo_linewidth": float(style.get(f"{prefix}_halo_linewidth", fallback["halo_linewidth"])),
    }


def _draw_shapes(ax: Any, shapes: list[Path], domain: MapDomain, pe: Any | None = None, style: dict[str, Any] | None = None) -> dict[str, Any]:
    style = style or {}
    map_halo_enabled = bool(style.get("map_halo_enabled", False))

    if not shapes:
        return {
            "map_background_status": "shape_not_available_grid_only",
            "coastline_drawn": False,
            "coastline_source_path": None,
            "coastline_source_type": "not_available",
            "borders_drawn": False,
            "borders_source_path": None,
            "borders_source_type": "not_available",
        }

    drawn_by_kind: dict[str, list[str]] = {"coastline": [], "border": [], "admin1": [], "map": []}
    segment_count = 0
    for shp in shapes[:32]:
        kind = _shape_kind(shp)
        line_style = _shape_line_style(kind, style)
        shape_segment_count = 0
        try:
            from matplotlib.collections import LineCollection  # type: ignore

            visible_segments: list[list[tuple[float, float]]] = []
            for seg in _read_shape_segments(shp):
                xs = [float(pt[0]) for pt in seg]
                ys = [float(pt[1]) for pt in seg]
                if not xs or not ys:
                    continue

                if max(xs) > 180 and domain.lon_min < 180:
                    xs = [((x + 180) % 360) - 180 for x in xs]

                if max(xs) < domain.lon_min or min(xs) > domain.lon_max:
                    continue
                if max(ys) < domain.lat_min or min(ys) > domain.lat_max:
                    continue
                visible_segments.append(list(zip(xs, ys, strict=True)))
                shape_segment_count += 1
                segment_count += 1
            if visible_segments:
                collection = LineCollection(
                    visible_segments,
                    colors=line_style["color"], linewidths=line_style["linewidth"],
                    alpha=line_style["alpha"], zorder=line_style["zorder"],
                )
                ax.add_collection(collection)
                if pe is not None and map_halo_enabled:
                    _apply_path_effects(
                        collection, pe,
                        foreground=line_style["halo_color"], linewidth=line_style["halo_linewidth"],
                    )
        except Exception:
            continue

        if shape_segment_count > 0:
            drawn_by_kind[kind].append(str(shp))

    all_drawn = [path for paths in drawn_by_kind.values() for path in paths]
    if not all_drawn:
        return {
            "map_background_status": "shape_error_or_out_of_domain_grid_only",
            "coastline_drawn": False,
            "coastline_source_path": None,
            "coastline_source_type": "not_available",
            "borders_drawn": False,
            "borders_source_path": None,
            "borders_source_type": "not_available",
            "shape_file_count": len(shapes),
            "shape_segment_count": 0,
        }

    coast_paths = drawn_by_kind["coastline"]
    border_paths = [*drawn_by_kind["border"], *drawn_by_kind["admin1"]]
    return {
        "map_background_status": "local_shape",
        "coastline_drawn": bool(coast_paths),
        "coastline_source_path": coast_paths[0] if coast_paths else None,
        "coastline_source_type": "local_coastline_shapefile" if coast_paths else "not_available",
        "borders_drawn": bool(border_paths),
        "borders_source_path": border_paths[0] if border_paths else None,
        "borders_source_type": "local_boundary_shapefile" if border_paths else "not_available",
        "shape_source_paths": all_drawn,
        "coastline_source_paths": coast_paths,
        "border_source_paths": border_paths,
        "shape_file_count": len(shapes),
        "shape_segment_count": segment_count,
    }


def _format_lon_tick(value: float, *, degree_symbol: bool = True) -> str:
    number = f"{int(round(value))}" if abs(value - round(value)) < 1e-6 else f"{value:g}"
    return f"{number}°E" if degree_symbol else f"{number}E"


def _format_lat_tick(value: float, *, degree_symbol: bool = True) -> str:
    number = f"{int(round(value))}" if abs(value - round(value)) < 1e-6 else f"{value:g}"
    return f"{number}°N" if degree_symbol else f"{number}N"


def _setup_ax(
    ax: Any,
    domain: MapDomain,
    style: dict[str, Any] | None = None,
    *,
    show_xlabel: bool = True,
    show_ylabel: bool = True,
) -> dict[str, Any]:
    style = style or {}
    ax.set_xlim(domain.lon_min, domain.lon_max)
    ax.set_ylim(domain.lat_min, domain.lat_max)
    ax.set_aspect('equal', adjustable='box')
    ax.set_xlabel(str(style.get('longitude_axis_label', '경도 (°E)')) if show_xlabel else '')
    ax.set_ylabel(str(style.get('latitude_axis_label', '위도 (°N)')) if show_ylabel else '')
    ax.grid(
        True,
        color=str(style.get("grid_color", "#DDDDDD")),
        linestyle=str(style.get("grid_linestyle", "--")),
        linewidth=float(style.get("grid_linewidth", 0.45)),
        alpha=float(style.get("grid_alpha", 0.45)),
    )
    include_boundaries = bool(style.get('include_domain_boundary_ticks', True))
    x_interval = float(style.get('longitude_tick_interval', 2.5))
    y_interval = float(style.get('latitude_tick_interval', 2.5))
    x_ticks = _nice_ticks(
        domain.lon_min, domain.lon_max, x_interval, include_boundaries=include_boundaries
    )
    minimum_tick_gap_px = float(style.get("minimum_tick_label_gap_px", 0.0))
    if show_xlabel and minimum_tick_gap_px > 0.0 and len(x_ticks) > 1:
        # Boundary labels are optional. Keep the regular grid label when its
        # rendered screen position would collide with a boundary label.
        retained = list(x_ticks)
        for boundary in (round(domain.lon_min, 6), round(domain.lon_max, 6)):
            if boundary not in retained:
                continue
            boundary_x = float(ax.transData.transform((boundary, domain.lat_min))[0])
            nearest = min((value for value in retained if value != boundary), key=lambda value: abs(float(ax.transData.transform((value, domain.lat_min))[0]) - boundary_x), default=None)
            if nearest is not None:
                nearest_x = float(ax.transData.transform((nearest, domain.lat_min))[0])
                if abs(nearest_x - boundary_x) < minimum_tick_gap_px:
                    retained.remove(boundary)
        x_ticks = retained
    y_ticks = _nice_ticks(
        domain.lat_min, domain.lat_max, y_interval, include_boundaries=include_boundaries
    )
    degree_symbol = bool(style.get('tick_degree_symbol', True))
    ax.set_xticks(x_ticks)
    ax.set_yticks(y_ticks)
    ax.set_xticklabels([_format_lon_tick(x, degree_symbol=degree_symbol) for x in x_ticks])
    ax.set_yticklabels([_format_lat_tick(y, degree_symbol=degree_symbol) for y in y_ticks])
    ax.tick_params(axis='x', labelbottom=show_xlabel, pad=1)
    ax.tick_params(axis='y', labelleft=show_ylabel, pad=1)
    ax.xaxis.labelpad = float(style.get('x_label_pad', 1.0))
    ax.yaxis.labelpad = float(style.get('y_label_pad', 1.0))
    return {
        'longitude_ticks': x_ticks,
        'latitude_ticks': y_ticks,
        'longitude_tick_interval': x_interval,
        'latitude_tick_interval': y_interval,
        'include_domain_boundary_ticks': include_boundaries,
        'minimum_tick_label_gap_px': minimum_tick_gap_px,
        'longitude_axis_label_visible': show_xlabel,
        'latitude_axis_label_visible': show_ylabel,
    }


def _nice_ticks(
    start: float,
    end: float,
    interval: float,
    *,
    include_boundaries: bool = False,
) -> list[float]:
    import math

    if interval <= 0:
        raise ValueError(f'tick interval must be positive: {interval}')
    first = math.ceil(start / interval) * interval
    ticks: list[float] = []
    x = first
    while x <= end + 1e-9:
        ticks.append(round(x, 6))
        x += interval
    if include_boundaries:
        # Keep a boundary tick only when it cannot visually collide with the
        # nearest regular tick on narrow multi-panel maps.
        minimum_distance = interval * 0.45
        for boundary in (round(float(start), 6), round(float(end), 6)):
            if not ticks or min(abs(boundary - item) for item in ticks) >= minimum_distance:
                ticks.append(boundary)
    return sorted(dict.fromkeys(ticks))


def _required_color_count(levels: list[float], extend: str) -> int:
    base = max(1, len(levels) - 1)
    if extend in {"min", "both"}:
        base += 1
    if extend in {"max", "both"}:
        base += 1
    return base


def _normalize_discrete_colors(colors: list[str], levels: list[float], extend: str) -> list[str]:
    if not colors:
        return colors
    required = _required_color_count(levels, extend)
    normalized = list(colors)
    if extend in {"min", "both"} and len(normalized) < required:
        normalized = [normalized[0]] + normalized
    if extend in {"max", "both"} and len(normalized) < required:
        normalized = normalized + [normalized[-1]]
    while len(normalized) < required:
        normalized.append(normalized[-1])
    if len(normalized) > required:
        normalized = normalized[:required]
    return normalized


def _color_norm(style: dict[str, Any], mcolors: Any):
    levels = style.get("levels")
    colors = style.get("colors")
    extend = str(style.get("extend", "neither"))
    if levels and colors:
        numeric_levels = [float(v) for v in levels]
        normalized_colors = _normalize_discrete_colors([str(v) for v in colors], numeric_levels, extend)
        style["colors"] = normalized_colors
        cmap = mcolors.ListedColormap(normalized_colors)
        norm = mcolors.BoundaryNorm(numeric_levels, cmap.N, extend=extend)
        return cmap, norm
    return style.get("cmap", "YlGnBu"), None

def _prepare_values(values: Any, style: dict[str, Any]):
    import numpy as np  # type: ignore
    values = np.asarray(values, dtype='float64')
    if 'mask_below' in style:
        values = np.ma.masked_where(values < float(style['mask_below']), values)
    return values


def _auto_vmin_vmax(values: Any, style: dict[str, Any]) -> tuple[float | None, float | None]:
    import numpy as np  # type: ignore
    if 'levels' in style:
        return None, None
    if 'vmin' in style or 'vmax' in style:
        return style.get('vmin'), style.get('vmax')
    flat = np.asarray(values, dtype='float64')
    flat = flat[np.isfinite(flat)]
    if flat.size == 0:
        return None, None
    pmin = float(style.get('percentile_min', 2))
    pmax = float(style.get('percentile_max', 98))
    return float(np.nanpercentile(flat, pmin)), float(np.nanpercentile(flat, pmax))


def _base_figure(style: dict[str, Any]):
    plt, mcolors, pe = import_matplotlib()
    figsize = tuple(style.get('figsize', [10.5, 7.6]))
    fig, ax = plt.subplots(figsize=figsize)
    return plt, mcolors, pe, fig, ax


def _halo_enabled(style: dict[str, Any]) -> bool:
    return bool(style.get('halo_enabled', True))


def _halo_foreground(style: dict[str, Any]) -> str:
    return str(style.get('halo_foreground', 'white'))


def _halo_linewidth_text(style: dict[str, Any]) -> float:
    return float(style.get('halo_linewidth_text', 2.6))


def _halo_linewidth_line(style: dict[str, Any]) -> float:
    return float(style.get('halo_linewidth_line', 2.0))


def _apply_path_effects(artist: Any, pe: Any, *, foreground: str, linewidth: float) -> None:
    if artist is None:
        return
    try:
        artist.set_path_effects([
            pe.Stroke(linewidth=linewidth, foreground=foreground),
            pe.Normal(),
        ])
    except Exception:
        return


def _apply_text_halo(text_artist: Any, pe: Any, style: dict[str, Any]) -> None:
    if not _halo_enabled(style):
        return
    _apply_path_effects(
        text_artist,
        pe,
        foreground=_halo_foreground(style),
        linewidth=_halo_linewidth_text(style),
    )


def _apply_line_halo(line_artist: Any, pe: Any, style: dict[str, Any]) -> None:
    if not _halo_enabled(style):
        return
    _apply_path_effects(
        line_artist,
        pe,
        foreground=_halo_foreground(style),
        linewidth=_halo_linewidth_line(style),
    )


def _style_axes_text(ax: Any, cbar: Any, pe: Any, style: dict[str, Any]) -> None:
    if not _halo_enabled(style):
        return
    text_items = [ax.title, ax.xaxis.label, ax.yaxis.label]
    text_items.extend(ax.get_xticklabels())
    text_items.extend(ax.get_yticklabels())
    if cbar is not None:
        try:
            text_items.append(cbar.ax.yaxis.label)
            text_items.extend(cbar.ax.get_yticklabels())
        except Exception:
            pass
        try:
            text_items.append(cbar.ax.xaxis.label)
            text_items.extend(cbar.ax.get_xticklabels())
        except Exception:
            pass
    for item in text_items:
        _apply_text_halo(item, pe, style)


def _style_line_list(items: Any, pe: Any, style: dict[str, Any]) -> None:
    if not _halo_enabled(style):
        return
    if items is None:
        return
    for item in items:
        _apply_line_halo(item, pe, style)


def _style_quiver_key(qk: Any, pe: Any, style: dict[str, Any]) -> None:
    if not _halo_enabled(style) or qk is None:
        return
    try:
        _apply_text_halo(qk.text, pe, style)
    except Exception:
        pass
    # Matplotlib version dependent: arrow artist may be exposed differently.
    for attr in ('vector', 'arrow', 'poly'):
        try:
            artist = getattr(qk, attr, None)
            if artist is not None:
                _apply_line_halo(artist, pe, style)
        except Exception:
            pass




def _format_title_info_lines(style: dict[str, Any], title_context: dict[str, Any]) -> list[str]:
    """Format title information lines using context supplied by broad_map_builder.

    map_renderer intentionally does not parse time from the title string.
    Time placeholders such as {init_time_utc}, {valid_time_utc}, and {lead_text}
    must be provided in title_context by the caller.
    """
    raw_lines = style.get("title_info_lines", [])
    if isinstance(raw_lines, str):
        raw_lines = [raw_lines]
    if not isinstance(raw_lines, list):
        return []

    formatted: list[str] = []
    for raw in raw_lines:
        text = str(raw).strip()
        if not text:
            continue
        try:
            text = text.format(**title_context)
        except Exception:
            # Keep original text to make missing placeholders visible.
            pass
        formatted.append(normalize_display_mathtext(text))
    return formatted

def _single_panel_header(main_title: str, panel_title: str) -> str:
    """Build one non-duplicated header for a single diagnostic map panel."""
    main_segments = [segment.strip() for segment in str(main_title or '').split('|') if segment.strip()]
    panel_segments = [segment.strip() for segment in str(panel_title or '').split('|') if segment.strip()]
    candidate_segment = next((segment for segment in main_segments if segment.startswith('후보 ')), '')

    ordered: list[str] = []
    for segment in [*panel_segments[:1], candidate_segment, *panel_segments[1:]]:
        if segment and segment not in ordered:
            ordered.append(segment)
    if not ordered:
        ordered = main_segments
    return normalize_display_mathtext(' | '.join(ordered))


def _margin_mm_to_pixels(margin_mm: float, *, dpi: int) -> int:
    return max(0, int(round((max(0.0, float(margin_mm)) / 25.4) * int(dpi))))


def _render_title_block(
    ax: Any,
    *,
    title_main: str,
    title_info_lines: list[str],
    style: dict[str, Any],
) -> dict[str, Any]:
    title_fontsize = float(style.get('title_fontsize', 13))
    title_info_fontsize = float(style.get('title_info_fontsize', max(title_fontsize - 1.0, 10.0)))
    title_loc = str(style.get('title_loc', 'left')).lower()
    if title_loc not in {'left', 'center', 'right'}:
        title_loc = 'left'
    x = {'left': 0.0, 'center': 0.5, 'right': 1.0}[title_loc]
    ha = {'left': 'left', 'center': 'center', 'right': 'right'}[title_loc]
    title_y = float(style.get('title_main_y', 1.055))
    info_y = float(style.get('title_info_y', 1.012))
    title_linespacing = float(style.get('title_linespacing', 1.18))
    info_linespacing = float(style.get('title_info_linespacing', 1.10))

    title_artist = ax.text(
        x,
        title_y,
        title_main,
        transform=ax.transAxes,
        ha=ha,
        va='bottom',
        fontsize=title_fontsize,
        linespacing=title_linespacing,
        clip_on=False,
    )
    title_artist.set_in_layout(True)

    info_artist = None
    if title_info_lines:
        info_artist = ax.text(
            x,
            info_y,
            "\n".join(title_info_lines),
            transform=ax.transAxes,
            ha=ha,
            va='bottom',
            fontsize=title_info_fontsize,
            linespacing=info_linespacing,
            clip_on=False,
        )
        info_artist.set_in_layout(True)

    return {
        'title_main': title_artist,
        'title_info': info_artist,
        'title_loc': title_loc,
        'title_fontsize': title_fontsize,
        'title_info_fontsize': title_info_fontsize,
        'title_main_y': title_y,
        'title_info_y': info_y,
    }



def _finish(
    *,
    fig: Any,
    ax: Any,
    mesh: Any,
    output_path: Path,
    title: str,
    unit_label: str,
    domain: MapDomain,
    context: RenderContext,
    style: dict[str, Any],
    title_context: dict[str, Any] | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    title_context = dict(title_context or {})
    title_main_template = str(style.get('title_main_template', '')).strip()
    if title_main_template:
        try:
            title_main = title_main_template.format(**title_context)
        except Exception:
            title_main = title
    else:
        title_main = title
    title_main = normalize_display_mathtext(title_main)
    title_info_lines = _format_title_info_lines(style, title_context)
    unit_label = normalize_display_mathtext(unit_label)
    axis_metadata = _setup_ax(ax, domain, style)
    title_block = _render_title_block(ax, title_main=title_main, title_info_lines=title_info_lines, style=style)
    plt, _mcolors, pe = import_matplotlib()
    extra_shape_dirs = [Path(item) for item in style.get('shape_search_dirs', [])]
    map_background = _draw_shapes(ax, find_local_shapes(context.project_root, extra_shape_dirs), domain, pe=pe, style=style)
    map_background.update(_draw_korea_admin1_boundaries(ax, context.project_root, domain, pe=pe, style=style))
    orientation = str(style.get('colorbar_orientation', 'vertical'))
    cbar = None
    if mesh is not None:
        if orientation == 'horizontal':
            cbar = fig.colorbar(mesh, ax=ax, orientation='horizontal', pad=0.06, fraction=0.05, extend=style.get('extend', 'neither'))
        else:
            cbar = fig.colorbar(mesh, ax=ax, orientation='vertical', pad=0.025, shrink=0.93, extend=style.get('extend', 'neither'))
        cbar.set_label(unit_label, fontsize=12)
        ticks = style.get('ticks')
        if ticks:
            cbar.set_ticks(ticks)
    _style_axes_text(ax, cbar, pe, style)
    _apply_text_halo(title_block.get('title_main'), pe, style)
    if title_block.get('title_info') is not None:
        _apply_text_halo(title_block.get('title_info'), pe, style)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    dpi = int(style.get('dpi', 150))
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight", pad_inches=0.0)
    import matplotlib.pyplot as plt  # type: ignore
    plt.close(fig)
    image_width_px, image_height_px = _trim_png_background(output_path, pad_px=1)
    return {
        'render_method': 'matplotlib_quicklook_style',
        'rendering_policy': rendering_policy(style),
        'projection_type': str(style.get('projection_type', 'equirectangular_lonlat')),
        'coordinate_reference': str(style.get('coordinate_reference', 'longitude_latitude_degrees')),
        'map_background': map_background,
        'colorbar_levels': style.get('levels'),
        'colorbar_ticks': style.get('ticks'),
        'colorbar_colors': style.get('colors'),
        'colorbar_extend': style.get('extend'),
        'colorbar_orientation': orientation,
        'colorbar_style': style,
        'figure_width_in': figure_width_in,
        'figure_height_in': figure_height_in,
        'dpi': dpi,
        'image_width_px': image_width_px,
        'image_height_px': image_height_px,
        'title_info_lines': title_info_lines,
        'full_title': full_title,
        'style_notes': style.get('style_notes', []),
        'display_text_mathtext_normalized': True,
        **axis_metadata,
        **(extra_metadata or {}),
    }


def rendering_policy(style: dict[str, Any] | None = None) -> dict[str, Any]:
    style = style or {}
    return {
        'render_method': 'matplotlib_quicklook_style',
        'projection': str(style.get('projection_type', 'equirectangular_lonlat')),
        'coordinate_reference': str(style.get('coordinate_reference', 'longitude_latitude_degrees')),
        'map_data': 'local_shapefile_if_available_no_download',
        'domain_selection': 'fixed_by_product_configuration',
    }


def render_shaded_field(field: Any, title: str, unit_label: str, domain: MapDomain, context: RenderContext, output_path: Path, product: dict[str, Any], title_context: dict[str, Any] | None = None) -> dict[str, Any]:
    style = {**context.style, **product.get('style', {})}
    lon2d, lat2d, values = _to_2d(field)
    lon2d, lat2d, values = _crop_to_domain(lon2d, lat2d, values, domain)
    values = _prepare_values(values, style)
    plt, mcolors, pe, fig, ax = _base_figure(style)
    cmap, norm = _color_norm(style, mcolors)
    vmin, vmax = _auto_vmin_vmax(values, style)
    mesh = ax.pcolormesh(lon2d, lat2d, values, shading='auto', cmap=cmap, norm=norm, vmin=vmin, vmax=vmax)
    auto_range_used = 'levels' not in style and 'vmin' not in style and 'vmax' not in style
    return _finish(fig=fig, ax=ax, mesh=mesh, output_path=output_path, title=title, unit_label=unit_label, domain=domain, context=context, style=style, title_context=title_context, extra_metadata={'auto_range_used': auto_range_used})


def render_shaded_plus_contour(shaded: Any, contour: Any, title: str, unit_label: str, contour_label: str, domain: MapDomain, context: RenderContext, output_path: Path, product: dict[str, Any], title_context: dict[str, Any] | None = None) -> dict[str, Any]:
    style = {**context.style, **product.get('style', {})}
    lon2d, lat2d, values = _to_2d(shaded)
    lon2d, lat2d, values = _crop_to_domain(lon2d, lat2d, values, domain)
    values = _prepare_values(values, style)
    c_lon, c_lat, c_values = _to_2d(contour)
    c_lon, c_lat, c_values = _crop_to_domain(c_lon, c_lat, c_values, domain)
    plt, mcolors, pe, fig, ax = _base_figure(style)
    cmap, norm = _color_norm(style, mcolors)
    vmin, vmax = _auto_vmin_vmax(values, style)
    mesh = ax.pcolormesh(lon2d, lat2d, values, shading='auto', cmap=cmap, norm=norm, vmin=vmin, vmax=vmax)
    contour_levels = product.get('contour_levels') or style.get('contour_levels')
    contour_linewidth = float(style.get('contour_linewidth', product.get('contour_linewidth', 1.05)))
    contour_halo_linewidth = float(style.get('contour_halo_linewidth', product.get('contour_halo_linewidth', contour_linewidth + 2.2)))
    contour_color = str(style.get('contour_color', product.get('contour_color', '#111111')))
    contour_halo_color = str(style.get('contour_halo_color', product.get('contour_halo_color', _halo_foreground(style))))
    contour_label_fontsize = float(style.get('contour_label_fontsize', product.get('contour_label_fontsize', 8)))
    contour_label_halo_linewidth = float(style.get('contour_label_halo_linewidth', product.get('contour_label_halo_linewidth', max(_halo_linewidth_text(style), 3.2))))

    # Draw contour halo as a real line layer first. This is more robust than
    # relying only on path_effects on a ContourSet, which varies by Matplotlib
    # version and backend.
    if _halo_enabled(style):
        if contour_levels:
            ax.contour(
                c_lon, c_lat, c_values,
                levels=contour_levels,
                colors=contour_halo_color,
                linewidths=contour_halo_linewidth,
                zorder=14,
            )
        else:
            ax.contour(
                c_lon, c_lat, c_values,
                colors=contour_halo_color,
                linewidths=contour_halo_linewidth,
                zorder=14,
            )

    if contour_levels:
        cs = ax.contour(
            c_lon, c_lat, c_values,
            levels=contour_levels,
            colors=contour_color,
            linewidths=contour_linewidth,
            zorder=15,
        )
    else:
        cs = ax.contour(
            c_lon, c_lat, c_values,
            colors=contour_color,
            linewidths=contour_linewidth,
            zorder=15,
        )

    clabels = ax.clabel(cs, inline=True, fontsize=contour_label_fontsize, fmt='%g')
    for label in clabels:
        if _halo_enabled(style):
            _apply_path_effects(
                label,
                pe,
                foreground=contour_halo_color,
                linewidth=contour_label_halo_linewidth,
            )
            # The bbox is intentionally transparent-edged and lightly padded.
            # It protects labels over high-contrast shaded fields when text
            # path effects are not fully honored by a backend.
            try:
                label.set_bbox({
                    'facecolor': contour_halo_color,
                    'edgecolor': 'none',
                    'alpha': float(style.get('contour_label_bbox_alpha', 0.80)),
                    'boxstyle': 'round,pad=0.08',
                })
            except Exception:
                pass
    return _finish(
        fig=fig, ax=ax, mesh=mesh, output_path=output_path, title=title, unit_label=unit_label,
        domain=domain, context=context, style=style, title_context=title_context,
        extra_metadata={
            'contour_levels': contour_levels,
            'contour_label': normalize_display_mathtext(contour_label),
            'auto_range_used': 'levels' not in style and 'vmin' not in style and 'vmax' not in style,
        },
    )


def _prepare_quiver_components(
    u_values: Any,
    v_values: Any,
    *,
    product: dict[str, Any],
    style: dict[str, Any],
) -> tuple[Any, Any, dict[str, Any]]:
    """Apply display-only vector magnitude limits while preserving direction.

    The source wind fields are never modified. Calm vectors below the configured
    threshold are hidden, while remaining vector lengths are clipped to a
    product-specific display range. This prevents near-zero arrows from becoming
    unreadable and strong-wind arrows from overlapping adjacent vectors.
    """
    import numpy as np  # type: ignore

    u_arr = np.asarray(u_values, dtype='float64')
    v_arr = np.asarray(v_values, dtype='float64')
    speed = np.sqrt(u_arr ** 2 + v_arr ** 2)
    calm_threshold = float(product.get(
        'quiver_calm_threshold_ms', style.get('quiver_calm_threshold_ms', 0.0)
    ))
    min_display_raw = product.get(
        'quiver_min_display_speed_ms', style.get('quiver_min_display_speed_ms')
    )
    max_display_raw = product.get(
        'quiver_max_display_speed_ms', style.get('quiver_max_display_speed_ms')
    )
    min_display = float(min_display_raw) if min_display_raw is not None else None
    max_display = float(max_display_raw) if max_display_raw is not None else None
    percentile_raw = product.get('quiver_magnitude_percentile', style.get('quiver_magnitude_percentile'))
    percentile = float(percentile_raw) if percentile_raw is not None else None
    if min_display is not None and min_display < 0:
        raise ValueError('quiver_min_display_speed_ms must be non-negative')
    if max_display is not None and max_display <= 0:
        raise ValueError('quiver_max_display_speed_ms must be positive')
    if min_display is not None and max_display is not None and min_display > max_display:
        raise ValueError('quiver_min_display_speed_ms cannot exceed quiver_max_display_speed_ms')

    finite = np.isfinite(u_arr) & np.isfinite(v_arr) & np.isfinite(speed)
    active = finite & (speed > 0.0) & (speed >= calm_threshold)
    if percentile is not None and active.any():
        if not 0.0 < percentile <= 100.0:
            raise ValueError('quiver_magnitude_percentile must be in (0, 100]')
        percentile_limit = float(np.percentile(speed[active], percentile))
        max_display = min(max_display, percentile_limit) if max_display is not None else percentile_limit
    display_speed = speed.copy()
    if min_display is not None:
        display_speed = np.where(active, np.maximum(display_speed, min_display), display_speed)
    if max_display is not None:
        display_speed = np.where(active, np.minimum(display_speed, max_display), display_speed)

    factor = np.full(speed.shape, np.nan, dtype='float64')
    factor[active] = display_speed[active] / speed[active]
    u_display = u_arr * factor
    v_display = v_arr * factor
    metadata = {
        'vector_rendering_mode': 'direction_preserving_magnitude_clipped',
        'quiver_calm_threshold_ms': calm_threshold,
        'quiver_min_display_speed_ms': min_display,
        'quiver_max_display_speed_ms': max_display,
        'source_vector_count': int(np.count_nonzero(finite)),
        'active_vector_count_before_stride': int(np.count_nonzero(active)),
        'vector_magnitude_percentile': percentile,
    }
    return u_display, v_display, metadata


def _bounded_vector_subsample(u: Any, v: Any, *, initial_stride: int, max_arrow_count: int) -> tuple[int, Any, Any, Any]:
    """Increase stride until the actual finite nonzero vector count meets the configured limit."""
    import numpy as np  # type: ignore

    stride = max(1, int(initial_stride))
    limit = max(1, int(max_arrow_count))
    while True:
        sampled_u = u[::stride, ::stride]
        sampled_v = v[::stride, ::stride]
        mask = np.isfinite(sampled_u) & np.isfinite(sampled_v) & ((sampled_u != 0.0) | (sampled_v != 0.0))
        if int(np.count_nonzero(mask)) <= limit:
            return stride, sampled_u, sampled_v, mask
        stride += 1


def resolve_quiver_zorder(product: dict[str, Any], style: dict[str, Any]) -> float:
    """Resolve an optional vector draw order while preserving the legacy default."""
    import math

    raw = product.get('quiver_zorder', style.get('quiver_zorder', 8))
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'quiver_zorder must be finite numeric, got {raw!r}') from exc
    if not math.isfinite(value):
        raise ValueError(f'quiver_zorder must be finite numeric, got {raw!r}')
    return value


def render_shaded_plus_vector(shaded: Any, u: Any, v: Any, title: str, unit_label: str, vector_unit_label: str, domain: MapDomain, context: RenderContext, output_path: Path, product: dict[str, Any], title_context: dict[str, Any] | None = None) -> dict[str, Any]:
    import numpy as np  # type: ignore
    style = {**context.style, **product.get('style', {})}
    lon2d, lat2d, values = _to_2d(shaded)
    lon2d, lat2d, values = _crop_to_domain(lon2d, lat2d, values, domain)
    values = _prepare_values(values, style)
    u_lon, u_lat, u_val = _to_2d(u)
    v_lon, v_lat, v_val = _to_2d(v)
    u_lon, u_lat, u_val = _crop_to_domain(u_lon, u_lat, u_val, domain)
    _v_lon, _v_lat, v_val = _crop_to_domain(v_lon, v_lat, v_val, domain)
    plt, mcolors, pe, fig, ax = _base_figure(style)
    cmap, norm = _color_norm(style, mcolors)
    vmin, vmax = _auto_vmin_vmax(values, style)
    mesh = ax.pcolormesh(lon2d, lat2d, values, shading='auto', cmap=cmap, norm=norm, vmin=vmin, vmax=vmax)
    stride = int(product.get('wind_stride', style.get('wind_stride', 3)))
    quiver_zorder = resolve_quiver_zorder(product, style)
    u_display, v_display, vector_metadata = _prepare_quiver_components(
        u_val, v_val, product=product, style=style
    )
    q = ax.quiver(
        u_lon[::stride, ::stride], u_lat[::stride, ::stride],
        u_display[::stride, ::stride], v_display[::stride, ::stride],
        color='black', scale=float(product.get('quiver_scale', style.get('quiver_scale', 250))),
        width=float(product.get('quiver_width', style.get('quiver_width', 0.0026))),
        zorder=quiver_zorder,
    )
    _apply_line_halo(q, pe, style)
    quiver_key_enabled = bool(product.get('quiver_key_enabled', style.get('quiver_key_enabled', True)))
    quiver_key_position = str(product.get('quiver_key_position', style.get('quiver_key_position', 'axes'))).lower()
    if quiver_key_enabled and quiver_key_position not in {'none', 'off', 'false', 'disabled'}:
        try:
            quiver_key_x = float(product.get('quiver_key_x', style.get('quiver_key_x', 0.84)))
            quiver_key_y = float(product.get('quiver_key_y', style.get('quiver_key_y', -0.10)))
            quiver_key_length = float(product.get('quiver_key_length', style.get('quiver_key_length', 10)))
            quiver_key_labelpos = str(product.get('quiver_key_labelpos', style.get('quiver_key_labelpos', 'E')))
            quiver_key_coordinates = str(product.get('quiver_key_coordinates', style.get('quiver_key_coordinates', 'axes')))
            quiver_key_label = normalize_display_mathtext(product.get('quiver_key_label', style.get('quiver_key_label', vector_unit_label)))
            qk = ax.quiverkey(
                q,
                quiver_key_x,
                quiver_key_y,
                quiver_key_length,
                quiver_key_label,
                labelpos=quiver_key_labelpos,
                coordinates=quiver_key_coordinates,
            )
            _style_quiver_key(qk, pe, style)
        except Exception:
            pass
    return _finish(
        fig=fig, ax=ax, mesh=mesh, output_path=output_path, title=title, unit_label=unit_label,
        domain=domain, context=context, style=style, title_context=title_context,
        extra_metadata={
            'wind_stride': stride,
            'resolved_wind_stride': stride,
            'quiver_scale': product.get('quiver_scale', style.get('quiver_scale', 250)),
            'quiver_zorder': quiver_zorder,
            'vector_unit_label': normalize_display_mathtext(vector_unit_label),
            **vector_metadata,
            'auto_range_used': 'levels' not in style and 'vmin' not in style and 'vmax' not in style,
        },
    )


def render_contour_plus_vector(
    contour: Any,
    u: Any,
    v: Any,
    title: str,
    contour_label: str,
    vector_unit_label: str,
    domain: MapDomain,
    context: RenderContext,
    output_path: Path,
    product: dict[str, Any],
    title_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Colorbar 없이 pressure contour와 wind vector를 고정 style로 렌더링한다."""

    import numpy as np  # type: ignore

    style = {**context.style, **product.get('style', {})}
    c_lon, c_lat, c_values = _to_2d(contour)
    c_lon, c_lat, c_values = _crop_to_domain(c_lon, c_lat, c_values, domain)
    u_lon, u_lat, u_values = _to_2d(u)
    v_lon, v_lat, v_values = _to_2d(v)
    u_lon, u_lat, u_values = _crop_to_domain(u_lon, u_lat, u_values, domain)
    _v_lon, _v_lat, v_values = _crop_to_domain(v_lon, v_lat, v_values, domain)
    _plt, _mcolors, pe, fig, ax = _base_figure(style)

    contour_levels = product.get('contour_levels') or style.get('contour_levels')
    contour_linewidth = float(style.get('contour_linewidth', 1.05))
    contour_halo_linewidth = float(style.get('contour_halo_linewidth', contour_linewidth + 2.2))
    contour_color = str(style.get('contour_color', '#111111'))
    contour_halo_color = str(style.get('contour_halo_color', _halo_foreground(style)))
    contour_label_fontsize = float(style.get('contour_label_fontsize', 8))
    contour_label_halo_linewidth = float(style.get('contour_label_halo_linewidth', 3.2))
    if _halo_enabled(style):
        ax.contour(
            c_lon, c_lat, c_values, levels=contour_levels,
            colors=contour_halo_color, linewidths=contour_halo_linewidth, zorder=14,
        )
    contours = ax.contour(
        c_lon, c_lat, c_values, levels=contour_levels,
        colors=contour_color, linewidths=contour_linewidth, zorder=15,
    )
    labels = ax.clabel(contours, inline=True, fontsize=contour_label_fontsize, fmt='%g')
    for label in labels:
        if _halo_enabled(style):
            _apply_path_effects(
                label, pe, foreground=contour_halo_color,
                linewidth=contour_label_halo_linewidth,
            )

    stride = int(product.get('wind_stride', style.get('wind_stride', 3)))
    quiver_scale = float(product.get('quiver_scale', style.get('quiver_scale', 250)))
    quiver_zorder = resolve_quiver_zorder(product, style)
    u_display, v_display, vector_metadata = _prepare_quiver_components(
        u_values, v_values, product=product, style=style
    )
    vectors = ax.quiver(
        u_lon[::stride, ::stride], u_lat[::stride, ::stride],
        u_display[::stride, ::stride], v_display[::stride, ::stride],
        color='black', scale=quiver_scale,
        width=float(product.get('quiver_width', style.get('quiver_width', 0.0026))),
        zorder=quiver_zorder,
    )
    _apply_line_halo(vectors, pe, style)
    if bool(style.get('quiver_key_enabled', True)):
        key = ax.quiverkey(
            vectors,
            float(style.get('quiver_key_x', 0.84)),
            float(style.get('quiver_key_y', -0.10)),
            float(style.get('quiver_key_length', 10)),
            normalize_display_mathtext(style.get('quiver_key_label', vector_unit_label)),
            labelpos=str(style.get('quiver_key_labelpos', 'E')),
            coordinates=str(style.get('quiver_key_coordinates', 'axes')),
        )
        _style_quiver_key(key, pe, style)

    return _finish(
        fig=fig, ax=ax, mesh=None, output_path=output_path, title=title,
        unit_label='', domain=domain, context=context, style=style,
        title_context=title_context,
        extra_metadata={
            'contour_levels': contour_levels,
            'contour_label': normalize_display_mathtext(contour_label),
            'wind_stride': stride,
            'resolved_wind_stride': stride,
            'quiver_scale': quiver_scale,
            'quiver_zorder': quiver_zorder,
            'vector_unit_label': normalize_display_mathtext(vector_unit_label),
            **vector_metadata,
            'auto_range_used': False,
        },
    )


def render_wind_speed_field(u: Any, v: Any, title: str, unit_label: str, domain: MapDomain, context: RenderContext, output_path: Path, product: dict[str, Any], title_context: dict[str, Any] | None = None) -> dict[str, Any]:
    import numpy as np  # type: ignore
    lon2d, lat2d, u_val = _to_2d(u)
    _vlon, _vlat, v_val = _to_2d(v)
    speed = np.sqrt(u_val ** 2 + v_val ** 2)
    # Reuse u's coordinates by creating a lightweight xarray object when possible.
    try:
        speed_arr = u.copy(data=speed)
    except Exception:
        speed_arr = u
        speed_arr.values = speed
    return render_shaded_field(speed_arr, title, unit_label, domain, context, output_path, product, title_context=title_context)



def _panel_style(style: dict[str, Any], *, overview_mode: bool) -> dict[str, Any]:
    panel_style = dict(style)
    if overview_mode:
        panel_style.setdefault('title_fontsize', min(float(style.get('title_fontsize', 13)), 10))
        panel_style.setdefault('contour_label_fontsize', min(float(style.get('contour_label_fontsize', 8)), 7))
    return panel_style


def draw_product_panel(
    ax: Any,
    *,
    product: dict[str, Any],
    fields: dict[str, Any],
    panel_title: str,
    domain: MapDomain,
    context: RenderContext,
    overview_mode: bool = True,
) -> dict[str, Any]:
    """Draw one product panel on a provided axis without saving a separate figure.

    This is used by step03 overview rendering so that overview panels are drawn
    directly from model fields with the same plotting standards as the single
    quicklook renderer.
    """
    import numpy as np  # type: ignore

    base_style = {**context.style, **product.get('style', {})}
    style = _panel_style(base_style, overview_mode=overview_mode)
    plot_type = str(product.get('plot_type'))
    _plt, mcolors, pe = import_matplotlib()

    axis_metadata = _setup_ax(ax, domain, style)
    extra_shape_dirs = [Path(item) for item in style.get('shape_search_dirs', [])]
    map_background = _draw_shapes(ax, find_local_shapes(context.project_root, extra_shape_dirs), domain, pe=pe, style=style)
    map_background.update(_draw_korea_admin1_boundaries(ax, context.project_root, domain, pe=pe, style=style))

    mesh = None
    extra_metadata: dict[str, Any] = {}

    if plot_type == 'shaded_feature':
        shaded = fields['shaded']
        lon2d, lat2d, values = _to_2d(shaded)
        lon2d, lat2d, values = _crop_to_domain(lon2d, lat2d, values, domain)
        values = _prepare_values(values, style)
        cmap, norm = _color_norm(style, mcolors)
        vmin, vmax = _auto_vmin_vmax(values, style)
        mesh = ax.pcolormesh(lon2d, lat2d, values, shading='auto', cmap=cmap, norm=norm, vmin=vmin, vmax=vmax)
        extra_metadata['auto_range_used'] = 'levels' not in style and 'vmin' not in style and 'vmax' not in style

    elif plot_type == 'shaded_feature_plus_raw_vector':
        shaded = fields['shaded']
        u = fields['u']
        v = fields['v']
        lon2d, lat2d, values = _to_2d(shaded)
        lon2d, lat2d, values = _crop_to_domain(lon2d, lat2d, values, domain)
        values = _prepare_values(values, style)
        u_lon, u_lat, u_val = _to_2d(u)
        v_lon, v_lat, v_val = _to_2d(v)
        u_lon, u_lat, u_val = _crop_to_domain(u_lon, u_lat, u_val, domain)
        _v_lon, _v_lat, v_val = _crop_to_domain(v_lon, v_lat, v_val, domain)
        cmap, norm = _color_norm(style, mcolors)
        vmin, vmax = _auto_vmin_vmax(values, style)
        mesh = ax.pcolormesh(lon2d, lat2d, values, shading='auto', cmap=cmap, norm=norm, vmin=vmin, vmax=vmax)
        stride = int(product.get('wind_stride', style.get('wind_stride', 3)))
        quiver_zorder = resolve_quiver_zorder(product, style)
        u_display, v_display, vector_metadata = _prepare_quiver_components(u_val, v_val, product=product, style=style)
        q = ax.quiver(
            u_lon[::stride, ::stride], u_lat[::stride, ::stride],
            u_display[::stride, ::stride], v_display[::stride, ::stride],
            color='black', scale=float(product.get('quiver_scale', style.get('quiver_scale', 250))),
            width=float(product.get('quiver_width', style.get('quiver_width', 0.0026))), zorder=quiver_zorder,
        )
        _apply_line_halo(q, pe, style)
        extra_metadata.update({
            'wind_stride': stride,
            'resolved_wind_stride': stride,
            'quiver_zorder': quiver_zorder,
            'quiver_scale': product.get('quiver_scale', style.get('quiver_scale', 250)),
            'vector_unit_label': normalize_display_mathtext(product.get('vector_unit_label', 'm s$^{-1}$')),
            **vector_metadata,
            'auto_range_used': 'levels' not in style and 'vmin' not in style and 'vmax' not in style,
        })

    elif plot_type == 'raw_wind_speed_plus_raw_contour':
        u = fields['u']
        v = fields['v']
        contour = fields['contour']
        u_lon, u_lat, u_values = _to_2d(u)
        _v_lon, _v_lat, v_values = _to_2d(v)
        u_lon, u_lat, u_values = _crop_to_domain(u_lon, u_lat, u_values, domain)
        _v_lon, _v_lat, v_values = _crop_to_domain(_v_lon, _v_lat, v_values, domain)
        values = _prepare_values(np.sqrt(u_values ** 2 + v_values ** 2), style)
        c_lon, c_lat, c_values = _to_2d(contour)
        c_lon, c_lat, c_values, contour_support = _crop_contour_with_native_neighbours(
            c_lon, c_lat, c_values, domain
        )
        extra_metadata["contour_support"] = contour_support
        cmap, norm = _color_norm(style, mcolors)
        vmin, vmax = _auto_vmin_vmax(values, style)
        mesh = ax.pcolormesh(u_lon, u_lat, values, shading='auto', cmap=cmap, norm=norm, vmin=vmin, vmax=vmax)
        contour_levels = product.get('contour_levels') or style.get('contour_levels')
        contour_linewidth = float(style.get('contour_linewidth', product.get('contour_linewidth', 1.05)))
        contour_halo_linewidth = float(style.get('contour_halo_linewidth', product.get('contour_halo_linewidth', contour_linewidth + 2.2)))
        contour_color = str(style.get('contour_color', product.get('contour_color', '#111111')))
        contour_halo_color = str(style.get('contour_halo_color', product.get('contour_halo_color', _halo_foreground(style))))
        contour_label_fontsize = float(style.get('contour_label_fontsize', product.get('contour_label_fontsize', 8)))
        contour_label_halo_linewidth = float(style.get('contour_label_halo_linewidth', product.get('contour_label_halo_linewidth', max(_halo_linewidth_text(style), 3.2))))
        if _halo_enabled(style):
            ax.contour(c_lon, c_lat, c_values, levels=contour_levels, colors=contour_halo_color, linewidths=contour_halo_linewidth, zorder=14)
        cs = ax.contour(c_lon, c_lat, c_values, levels=contour_levels, colors=contour_color, linewidths=contour_linewidth, zorder=15)
        clabels = ax.clabel(cs, inline=True, fontsize=contour_label_fontsize, fmt='%g')
        for label in clabels:
            if _halo_enabled(style):
                _apply_path_effects(label, pe, foreground=contour_halo_color, linewidth=contour_label_halo_linewidth)
        extra_metadata.update({
            'contour_levels': contour_levels,
            'contour_label': normalize_display_mathtext(product.get('contour_label', '')),
            'auto_range_used': 'levels' not in style and 'vmin' not in style and 'vmax' not in style,
        })

    elif plot_type == 'raw_wind_speed_shaded':
        u = fields['u']
        v = fields['v']
        u_lon, u_lat, u_values = _to_2d(u)
        _v_lon, _v_lat, v_values = _to_2d(v)
        u_lon, u_lat, u_values = _crop_to_domain(u_lon, u_lat, u_values, domain)
        _v_lon, _v_lat, v_values = _crop_to_domain(_v_lon, _v_lat, v_values, domain)
        values = _prepare_values(np.sqrt(u_values ** 2 + v_values ** 2), style)
        cmap, norm = _color_norm(style, mcolors)
        vmin, vmax = _auto_vmin_vmax(values, style)
        mesh = ax.pcolormesh(u_lon, u_lat, values, shading='auto', cmap=cmap, norm=norm, vmin=vmin, vmax=vmax)
        extra_metadata['auto_range_used'] = 'levels' not in style and 'vmin' not in style and 'vmax' not in style

    elif plot_type == 'raw_shaded_plus_raw_contour':
        shaded = fields['shaded']
        contour = fields['contour']
        lon2d, lat2d, values = _to_2d(shaded)
        lon2d, lat2d, values = _crop_to_domain(lon2d, lat2d, values, domain)
        values = _prepare_values(values, style)
        c_lon, c_lat, c_values = _to_2d(contour)
        c_lon, c_lat, c_values, contour_support = _crop_contour_with_native_neighbours(
            c_lon, c_lat, c_values, domain
        )
        extra_metadata["contour_support"] = contour_support
        cmap, norm = _color_norm(style, mcolors)
        vmin, vmax = _auto_vmin_vmax(values, style)
        mesh = ax.pcolormesh(lon2d, lat2d, values, shading='auto', cmap=cmap, norm=norm, vmin=vmin, vmax=vmax)
        contour_levels = product.get('contour_levels') or style.get('contour_levels')
        contour_linewidth = float(style.get('contour_linewidth', product.get('contour_linewidth', 1.05)))
        contour_halo_linewidth = float(style.get('contour_halo_linewidth', product.get('contour_halo_linewidth', contour_linewidth + 2.2)))
        contour_color = str(style.get('contour_color', product.get('contour_color', '#111111')))
        contour_halo_color = str(style.get('contour_halo_color', product.get('contour_halo_color', _halo_foreground(style))))
        contour_label_fontsize = float(style.get('contour_label_fontsize', product.get('contour_label_fontsize', 8)))
        contour_label_halo_linewidth = float(style.get('contour_label_halo_linewidth', product.get('contour_label_halo_linewidth', max(_halo_linewidth_text(style), 3.2))))
        if _halo_enabled(style):
            if contour_levels:
                ax.contour(c_lon, c_lat, c_values, levels=contour_levels, colors=contour_halo_color, linewidths=contour_halo_linewidth, zorder=14)
            else:
                ax.contour(c_lon, c_lat, c_values, colors=contour_halo_color, linewidths=contour_halo_linewidth, zorder=14)
        if contour_levels:
            cs = ax.contour(c_lon, c_lat, c_values, levels=contour_levels, colors=contour_color, linewidths=contour_linewidth, zorder=15)
        else:
            cs = ax.contour(c_lon, c_lat, c_values, colors=contour_color, linewidths=contour_linewidth, zorder=15)
        clabels = ax.clabel(cs, inline=True, fontsize=contour_label_fontsize, fmt='%g')
        for label in clabels:
            if _halo_enabled(style):
                _apply_path_effects(label, pe, foreground=contour_halo_color, linewidth=contour_label_halo_linewidth)
        extra_metadata.update({
            'contour_levels': contour_levels,
            'contour_label': normalize_display_mathtext(product.get('contour_label', '')),
            'auto_range_used': 'levels' not in style and 'vmin' not in style and 'vmax' not in style,
        })

    elif plot_type == 'raw_contour_plus_raw_vector':
        contour = fields['contour']
        u = fields['u']
        v = fields['v']
        c_lon, c_lat, c_values = _to_2d(contour)
        c_lon, c_lat, c_values, contour_support = _crop_contour_with_native_neighbours(
            c_lon, c_lat, c_values, domain
        )
        extra_metadata["contour_support"] = contour_support
        u_lon, u_lat, u_values = _to_2d(u)
        v_lon, v_lat, v_values = _to_2d(v)
        u_lon, u_lat, u_values = _crop_to_domain(u_lon, u_lat, u_values, domain)
        _v_lon, _v_lat, v_values = _crop_to_domain(v_lon, v_lat, v_values, domain)
        contour_levels = product.get('contour_levels') or style.get('contour_levels')
        contour_linewidth = float(style.get('contour_linewidth', 1.05))
        contour_halo_linewidth = float(style.get('contour_halo_linewidth', contour_linewidth + 2.2))
        contour_color = str(style.get('contour_color', '#111111'))
        contour_halo_color = str(style.get('contour_halo_color', _halo_foreground(style)))
        contour_label_fontsize = float(style.get('contour_label_fontsize', 8))
        contour_label_halo_linewidth = float(style.get('contour_label_halo_linewidth', 3.2))
        if _halo_enabled(style):
            ax.contour(c_lon, c_lat, c_values, levels=contour_levels, colors=contour_halo_color, linewidths=contour_halo_linewidth, zorder=14)
        cs = ax.contour(c_lon, c_lat, c_values, levels=contour_levels, colors=contour_color, linewidths=contour_linewidth, zorder=15)
        labels = ax.clabel(cs, inline=True, fontsize=contour_label_fontsize, fmt='%g')
        for label in labels:
            if _halo_enabled(style):
                _apply_path_effects(label, pe, foreground=contour_halo_color, linewidth=contour_label_halo_linewidth)
        stride = int(product.get('wind_stride', style.get('wind_stride', 3)))
        quiver_zorder = resolve_quiver_zorder(product, style)
        u_display, v_display, vector_metadata = _prepare_quiver_components(u_values, v_values, product=product, style=style)
        vectors = ax.quiver(
            u_lon[::stride, ::stride], u_lat[::stride, ::stride],
            u_display[::stride, ::stride], v_display[::stride, ::stride],
            color='black', scale=float(product.get('quiver_scale', style.get('quiver_scale', 250))),
            width=float(product.get('quiver_width', style.get('quiver_width', 0.0026))), zorder=quiver_zorder,
        )
        _apply_line_halo(vectors, pe, style)
        extra_metadata.update({
            'contour_levels': contour_levels,
            'contour_label': normalize_display_mathtext(product.get('contour_label', '')),
            'wind_stride': stride,
            'resolved_wind_stride': stride,
            'quiver_zorder': quiver_zorder,
            'quiver_scale': float(product.get('quiver_scale', style.get('quiver_scale', 250))),
            'vector_unit_label': normalize_display_mathtext(product.get('vector_unit_label', 'm s$^{-1}$')),
            **vector_metadata,
            'auto_range_used': False,
        })

    else:
        raise ValueError(f'unsupported plot_type for panel drawing: {plot_type}')

    title_artist = ax.set_title(panel_title, fontsize=float(style.get('title_fontsize', 10)), loc='left', pad=6)
    _apply_text_halo(title_artist, pe, style)
    _style_axes_text(ax, None, pe, style)

    return {
        'mesh': mesh,
        'style': style,
        'map_background': map_background,
        **axis_metadata,
        **extra_metadata,
    }


def convert_geopotential_height(arr: Any):
    try:
        return arr / G0
    except Exception:
        return arr


def domain_from_config(config: dict[str, Any]) -> MapDomain:
    return MapDomain(
        lon_min=float(config['lon_min']),
        lon_max=float(config['lon_max']),
        lat_min=float(config['lat_min']),
        lat_max=float(config['lat_max']),
    )
