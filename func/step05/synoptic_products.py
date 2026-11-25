"""Step05 synoptic-support maps for the AI-selected context domain.

One image, one spatial contract: the displayed native grid and the reported
quantitative extrema both use the Step04 synoptic request's ``requested_domain``.
This avoids implicit domain changes and visual ROI annotations mistaken for
physical evidence.  Only model forecasts are rendered; no inference is made.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import math

import numpy as np

SYNOPTIC_PRODUCTS: tuple[str, ...] = (
    "QL_MSLP_WIND10",
    "QL_Z500_T500",
    "QL_JET300_Z300",
)
SYNOPTIC_OBSERVABLES: dict[str, tuple[str, ...]] = {
    "QL_MSLP_WIND10": ("mean_sea_level_pressure", "wind_vector_10m"),
    "QL_Z500_T500": ("geopotential_height_500", "temperature_500"),
    "QL_JET300_Z300": ("upper_jet300_ms", "geopotential_height_300", "wind_vector_300"),
}

def _shared_product_style(root: Path, product_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve Step05 synoptic products through the existing Step02 style contract.

    The common YAML owns the contour/halo, shade, colorbar and quiver settings.
    The product remains a Step05 request, even when its Step02 catalog entry is
    disabled (QL_Z500_T500), so this lookup deliberately does not filter enabled.
    """
    from func.shared.broad_map_style import resolve_render_style_config
    from func.shared.runtime_paths import runtime_path
    import yaml

    config_path = runtime_path(root, "daba", "ai_pass1", "discovery_source_panel_products.yaml")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("SYNOPTIC_STYLE_INVALID: product catalog must be a mapping")
    # Keep the original product style ID and plot_type. In particular, the
    # Step02 MSLP contract is pressure contours + 10 m vectors, without shade.
    # Resolve the requested product only; do not make unrelated product-style
    # warnings fatal to Step05 synoptic rendering.
    resolved, _diagnostics = resolve_render_style_config(root, config)
    for entry in resolved.get("products", []):
        if not isinstance(entry, dict) or entry.get("product_id") != product_id:
            continue
        product = dict(entry)
        style = {**resolved.get("default_style", {}), **product.get("style", {})}
        required = (("contour_levels",) if product.get("plot_type") == "raw_contour_plus_raw_vector"
                    else ("levels", "ticks", "colors", "contour_levels"))
        if not all(style.get(key) for key in required):
            raise ValueError(f"SYNOPTIC_STYLE_INVALID: missing shared style fields {required} for {product_id}")
        if product_id == "QL_MSLP_WIND10" and product.get("plot_type") != "raw_contour_plus_raw_vector":
            raise ValueError("SYNOPTIC_STYLE_INVALID: MSLP must use Step02 contour+vector plot_type")
        return product, style
    raise ValueError(f"SYNOPTIC_STYLE_MISSING: {product_id}")


def _finite_summary(values: np.ndarray) -> dict[str, int | float | None]:
    if np.isinf(values).any():
        raise ValueError("SYNOPTIC_FIELD_INFINITE: GRIB field contains infinite values")
    finite = np.isfinite(values)
    count = int(finite.sum())
    return {
        "finite_count": count,
        "nan_count": int(np.isnan(values).sum()),
        "minimum": float(np.min(values[finite])) if count else None,
        "maximum": float(np.max(values[finite])) if count else None,
    }


def _domain_from_bbox(bbox: dict[str, Any]) -> Any:
    from func.shared.map_renderer import MapDomain
    west, east, south, north = [float(bbox[k]) for k in ("west", "east", "south", "north")]
    if not all(map(math.isfinite, (west, east, south, north))) or not (west < east and south < north):
        raise ValueError("SYNOPTIC_DOMAIN_INVALID: invalid bbox")
    return MapDomain(lon_min=west, lon_max=east, lat_min=south, lat_max=north)


def _requested_domain(request: dict[str, Any]) -> Any:
    """Immutable Step04 quantitative region, not the broad display canvas."""
    bbox = request.get("requested_domain")
    if not isinstance(bbox, dict):
        raise ValueError("SYNOPTIC_DOMAIN_MISSING: requested_domain is required")
    return _domain_from_bbox(bbox)


def _extract(data: Any, domain: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    from func.shared.map_renderer import lat_lon_names
    lat_name, lon_name = lat_lon_names(data)
    lat = np.asarray(data[lat_name].values, dtype="float64")
    lon = np.asarray(data[lon_name].values, dtype="float64")
    values = np.squeeze(np.asarray(data.values, dtype="float64"))
    if values.ndim != 2:
        raise ValueError("SYNOPTIC_DIMENSION_INVALID: field must be 2-D")
    if lat.ndim == lon.ndim == 1:
        lon2, lat2 = np.meshgrid(lon, lat)
    elif lat.shape == lon.shape == values.shape:
        lon2, lat2 = lon, lat
    else:
        raise ValueError("SYNOPTIC_COORDINATE_INVALID: latitude/longitude shape mismatch")
    if lon2.shape != values.shape:
        raise ValueError("SYNOPTIC_COORDINATE_INVALID: data shape mismatch")
    inside = ((lon2 >= domain.lon_min) & (lon2 <= domain.lon_max)
              & (lat2 >= domain.lat_min) & (lat2 <= domain.lat_max))
    if not inside.any():
        raise ValueError("SYNOPTIC_DOMAIN_EMPTY: no native grid cells inside requested domain")
    rows = np.flatnonzero(np.any(inside, axis=1))
    columns = np.flatnonzero(np.any(inside, axis=0))
    sl = np.s_[rows[0]:rows[-1] + 1, columns[0]:columns[-1] + 1]
    return lon2[sl], lat2[sl], np.where(inside[sl], values[sl], np.nan)


def _aligned(data: Any, domain: Any, xy: tuple[np.ndarray, np.ndarray]) -> np.ndarray:
    x, y, values = _extract(data, domain)
    if x.shape != xy[0].shape or y.shape != xy[1].shape:
        raise ValueError("SYNOPTIC_GRID_MISMATCH: grid dimensions differ")
    if not np.allclose(x, xy[0], atol=1e-9, rtol=0) or not np.allclose(y, xy[1], atol=1e-9, rtol=0):
        raise ValueError("SYNOPTIC_GRID_MISMATCH: native field coordinates differ")
    return values


def _geopotential_gpm(raw_z: np.ndarray) -> np.ndarray:
    """AIFS GRIB shortName=z is geopotential in m2 s-2, not geopotential metres."""
    finite = raw_z[np.isfinite(raw_z)]
    if not finite.size or float(np.nanmedian(finite)) <= 10000:
        raise ValueError("SYNOPTIC_Z_UNIT_INVALID: expected raw geopotential [m2 s-2]")
    return raw_z / 9.80665


def _figure_geometry(root: Path, domain: Any) -> tuple[tuple[float, float], list[float], list[float], int]:
    """Match the existing Step05 map lane and domain-dependent figure height."""
    from func.step05.renderer import _load_step05_layout

    layout = _load_step05_layout(root)
    figure_cfg = layout["figure"]
    axes_cfg = layout["axes"]
    colorbar_cfg = layout["colorbar"]
    width = float(figure_cfg["figsize_inches"][0])
    minimum_height = float(figure_cfg.get("minimum_height_inches", 0.0))
    axes = [float(v) for v in axes_cfg["rect"]]
    cbar = [float(v) for v in colorbar_cfg["rect"]]
    domain_width = domain.lon_max - domain.lon_min
    domain_height = domain.lat_max - domain.lat_min
    # The shared map renderer sets equal aspect in native lon/lat degrees.
    # Resize the figure to fill its map lane without stretching the field.
    height = max(minimum_height, (width * axes[2] * domain_height / domain_width) / axes[3])
    return (width, height), axes, cbar, int(figure_cfg["dpi"])


def _time_label(init: str, hour: int) -> str:
    init_utc = datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    kst = timezone(timedelta(hours=9))
    return (
        f"초기시각 {(init_utc.astimezone(kst)):%Y-%m-%d %H:%M} KST"
        f"  |  유효시각 {((init_utc + timedelta(hours=int(hour))).astimezone(kst)):%Y-%m-%d %H:%M} KST"
        f" (F{hour:03d})"
    )


def render_synoptic_product_png(
    *, root: Path, init: str, request: dict[str, Any], hour: int,
    output_path: Path, fields: Any, product_id: str,
) -> dict[str, Any]:
    """Render a Step04-selected synoptic field using the common map engine.

    All map styles come from broad_map_render_styles.yaml via the product
    catalog. Step05's layout YAML controls only the one-image figure geometry.
    The strict requested-domain extraction remains the quantitative reference.
    """
    if product_id not in SYNOPTIC_PRODUCTS:
        raise ValueError(f"SYNOPTIC_PRODUCT_UNSUPPORTED: {product_id}")
    from func.shared.map_renderer import (
        RenderContext, draw_product_panel, import_matplotlib,
        draw_common_quiver, common_quiver_color, _prepare_quiver_components, _style_quiver_key,
    )
    from func.step05.renderer import _load_step05_layout

    domain = _requested_domain(request)
    product, style = _shared_product_style(root, product_id)
    if product_id == "QL_MSLP_WIND10":
        shaded_source = fields.feature("msl_hpa", hour)
        contour_source = shaded_source
        vector_sources = (fields.raw("10u", None, hour), fields.raw("10v", None, hour))
    elif product_id == "QL_Z500_T500":
        shaded_source = fields.feature("t500_c", hour)
        contour_source = fields.feature("z500_gpm", hour)
        vector_sources = None
    else:
        shaded_source = fields.feature("upper_jet300_ms", hour)
        raw_z = fields.raw("z", 300, hour)
        # Preserve the existing strict raw-geopotential unit check before
        # using the shared contour renderer, which expects geopotential metres.
        x_z, y_z, z_values = _extract(raw_z, domain)
        _geopotential_gpm(z_values)
        contour_source = raw_z / 9.80665
        vector_sources = (fields.raw("u", 300, hour), fields.raw("v", 300, hour))

    x, y, shading = _extract(shaded_source, domain)
    xy = (x, y)
    contoured = _aligned(contour_source, domain, xy)
    vectors = (
        tuple(_aligned(field, domain, xy) for field in vector_sources)
        if vector_sources is not None else None
    )
    # Keep native-grid quantitative extrema and the AI-selected Step04 domain.
    stats = _finite_summary(shading)
    if not stats["finite_count"]:
        raise ValueError("SYNOPTIC_FIELD_EMPTY: requested domain contains no valid field")
    contour_stats = _finite_summary(contoured)

    plt, _mcolors, path_effects = import_matplotlib()
    figsize, axes_rect, cbar_rect, dpi = _figure_geometry(root, domain)
    is_contour_vector = product["plot_type"] == "raw_contour_plus_raw_vector"
    if is_contour_vector:
        # No MSLP shade/colorbar in Step02. Use the freed right-hand map lane
        # and retain the original request domain and native-grid aspect ratio.
        axes_rect[2] = min(0.87, 1.0 - axes_rect[0] - 0.04)
        figure_cfg = _load_step05_layout(root)["figure"]
        figsize = (figsize[0], max(float(figure_cfg.get("minimum_height_inches", 0.0)),
                  figsize[0] * axes_rect[2] * (domain.lat_max - domain.lat_min)
                  / ((domain.lon_max - domain.lon_min) * axes_rect[3])))
    fig = plt.figure(figsize=figsize, dpi=dpi, facecolor="white")
    ax = fig.add_axes(axes_rect, facecolor="white")
    cax = None if is_contour_vector else fig.add_axes(cbar_rect, facecolor="white")
    layout = _load_step05_layout(root)
    context = RenderContext(project_root=root, domain=domain, style=style)
    try:
        if is_contour_vector:
            # EXACT same product plot_type, palette-free contour and vector
            # engine as Step02. Do not add a separate 10 m wind quiver.
            panel = draw_product_panel(
                ax, product=product,
                fields={"contour": contour_source,
                        "u": vector_sources[0], "v": vector_sources[1]},
                panel_title="", domain=domain, context=context,
                overview_mode=False,
            )
            if panel.get("mesh") is not None:
                raise ValueError("SYNOPTIC_STYLE_INVALID: Step02 MSLP unexpectedly returned shading")
        else:
            panel_product = {**product, "plot_type": "raw_shaded_plus_raw_contour"}
            panel = draw_product_panel(
                ax, product=panel_product,
                fields={"shaded": shaded_source, "contour": contour_source},
                panel_title="", domain=domain, context=context, overview_mode=False,
            )
        resolved_style = panel["style"]
        mesh = panel.get("mesh")
        if not is_contour_vector:
            if mesh is None:
                raise ValueError(f"SYNOPTIC_SHADE_MISSING: {product_id}")
            mesh.get_cmap().set_bad(str(layout.get("missing", {}).get("bad_color", "#8C8C8C")))

        if vectors is not None and not is_contour_vector:
            u, v = vectors
            # Bounded density is retained for large AI-selected context maps.
            # Rendering scale/width, thresholds, halo and key come from YAML.
            stride = max(int(resolved_style.get("wind_stride", 3)), math.ceil(max(shading.shape) / 24))
            vu, vv, _metadata = _prepare_quiver_components(u, v, product=panel_product, style=resolved_style)
            vx, vy, vu, vv = (a[::stride, ::stride] for a in (x, y, vu, vv))
            mask = np.isfinite(vu) & np.isfinite(vv)
            if mask.any():
                quiver = draw_common_quiver(
                    ax, x=vx[mask], y=vy[mask], u=vu[mask], v=vv[mask],
                    style=resolved_style,
                    scale=float(resolved_style.get("quiver_scale", 250)),
                    width=float(resolved_style.get("quiver_width", 0.0026)),
                    zorder=float(resolved_style.get("quiver_zorder", 16)),
                    pe=path_effects,
                )
                if resolved_style.get("quiver_key_enabled", False):
                    key = ax.quiverkey(
                        quiver,
                        X=float(resolved_style.get("quiver_key_x", 0.8)),
                        Y=float(resolved_style.get("quiver_key_y", 0.04)),
                        U=float(resolved_style.get("quiver_key_length", 10)),
                        label=str(resolved_style.get("quiver_key_label", "m s$^{-1}$")),
                        labelpos=str(resolved_style.get("quiver_key_labelpos", "E")),
                        coordinates=str(resolved_style.get("quiver_key_coordinates", "axes")),
                        color=common_quiver_color(resolved_style),
                    )
                    _style_quiver_key(key, path_effects, resolved_style)

        presentation = layout.get("product_presentation", {}).get(product_id, {})
        unit_label = str(
            presentation.get("colorbar_label")
            or product.get("unit_label") or product.get("contour_label") or ""
        )
        if not is_contour_vector:
            colorbar_cfg = layout["colorbar"]
            bar = fig.colorbar(
                mesh, cax=cax, ticks=resolved_style["ticks"],
                extend=str(resolved_style.get("extend", "neither")),
            )
            bar.set_label(
                unit_label,
                fontsize=float(colorbar_cfg.get("label_fontsize", 10)),
                labelpad=float(colorbar_cfg.get("label_pad", 7)),
            )
            bar.ax.tick_params(labelsize=float(colorbar_cfg.get("tick_fontsize", 9)))
        display_title = str(product.get("display_title") or product_id)
        fig.text(0.085, 0.988, display_title, fontsize=13, ha="left", va="top")
        fig.text(0.085, 0.958, _time_label(init, hour), fontsize=10.4, ha="left", va="top")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, facecolor="white", dpi=dpi)
        from func.shared.map_renderer import _trim_png_background
        _trim_png_background(output_path, pad_px=2)
    finally:
        plt.close(fig)
    from PIL import Image
    with Image.open(output_path) as image:
        image.verify()

    contour_levels = [float(v) for v in resolved_style["contour_levels"]]
    finite_contour = contoured[np.isfinite(contoured)]
    contour_drawn = bool(
        finite_contour.size >= 4 and min(contoured.shape) >= 2
        and any(float(np.min(finite_contour)) <= lev <= float(np.max(finite_contour)) for lev in contour_levels)
    )
    return {
        "output_path": output_path,
        "product_id": product_id,
        "physical_diagnostic": product_id,
        "forecast_hour": int(hour),
        "unit_label": unit_label,
        "field_value_summary": stats,
        "contour_field_value_summary": contour_stats,
        "contour_drawn": contour_drawn,
        "map_background": panel["map_background"],
        "observable_ids": list(SYNOPTIC_OBSERVABLES[product_id]),
        "render_style": {
            "title_ko": display_title,
            "style_profile_id": str(product["style_profile_id"]),
            "style_file": "daba/ai_pass1/broad_map_render_styles.yaml",
            "levels": [float(level) for level in resolved_style.get("levels", [])] if not is_contour_vector else [],
            "colors": list(resolved_style.get("colors", [])) if not is_contour_vector else [],
            "colorbar_ticks": [float(tick) for tick in resolved_style.get("ticks", [])] if not is_contour_vector else [],
            "colorbar_extend": str(resolved_style.get("extend", "neither")) if not is_contour_vector else "neither",
            "contour_levels": contour_levels,
            "contour_halo_color": str(resolved_style.get("contour_halo_color", "#FFFFFF")),
            "contour_halo_linewidth": float(resolved_style.get("contour_halo_linewidth", 2.4)),
            "quiver_reference": (float(resolved_style["quiver_key_length"])
                                 if vectors is not None and not is_contour_vector
                                 and resolved_style.get("quiver_key_enabled") else None),
            "display_domain": {
                "west": domain.lon_min, "east": domain.lon_max,
                "south": domain.lat_min, "north": domain.lat_max,
            },
            "statistics_domain": {
                "west": domain.lon_min, "east": domain.lon_max,
                "south": domain.lat_min, "north": domain.lat_max,
            },
        },
    }


def synoptic_features_from_images(images: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Expose requested-domain grid extrema via the existing Step05 JSON contract.

    These are native grid extrema over Step04's request domain (not the
    broader rendered map); they are not pressure-system centre estimates.
    """
    feature_by_map = {
        "QL_MSLP_WIND10": ("msl_hpa", "hPa"),
        "QL_Z500_T500": ("t500_c", "deg C"),
        "QL_JET300_Z300": ("upper_jet300_ms", "m s-1"),
    }
    products: dict[str, dict[str, Any]] = {}
    for image in images:
        if not isinstance(image, dict):
            continue
        pair = feature_by_map.get(str(image.get("physical_diagnostic") or ""))
        if pair is None:
            continue
        feature, units = pair
        hour = image.get("forecast_hour")
        if not isinstance(hour, int) or isinstance(hour, bool):
            raise ValueError("SYNOPTIC_FEATURE_HOUR_INVALID")
        finite_count = int(image.get("finite_count") or 0)
        nan_count = int(image.get("nan_count") or 0)
        minimum, maximum = image.get("min"), image.get("max")
        if finite_count <= 0 or not isinstance(minimum, (int, float)) or not isinstance(maximum, (int, float)):
            raise ValueError("SYNOPTIC_FEATURE_EMPTY")
        if not (math.isfinite(minimum) and math.isfinite(maximum) and minimum <= maximum):
            raise ValueError("SYNOPTIC_FEATURE_STATS_INVALID")
        row = products.setdefault(feature, {
            "units": units, "finite_count": 0, "nan_count": 0, "per_forecast_hour": [],
        })
        row["finite_count"] += finite_count
        row["nan_count"] += nan_count
        row["per_forecast_hour"].append({
            "forecast_hour": hour,
            "statistics": {"min": float(minimum), "max": float(maximum)},
        })
    for row in products.values():
        row["per_forecast_hour"].sort(key=lambda item: item["forecast_hour"])
        hours = [item["forecast_hour"] for item in row["per_forecast_hour"]]
        if len(hours) != len(set(hours)):
            raise ValueError("SYNOPTIC_FEATURE_DUPLICATE_HOUR")
    return products
