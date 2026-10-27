"""Single-image Step05 diagnostic renderer.

This module intentionally leaves the Step01--04 pipeline untouched.  It
reads the existing discovery product and style contracts, reuses their
``tp_6h_increment_mm`` calculation and panel drawing code, and owns only the
Step05 figure layout and PNG output.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
from typing import Any

import yaml

from func.shared import aifs_data
from func.step02.aifs_feature_engine import load_yaml_mapping
from func.shared.broad_map_style import resolve_render_style_config
from func.shared.map_renderer import MapDomain, RenderContext, convert_geopotential_height, draw_product_panel, import_matplotlib, lat_lon_names, subset_to_domain
from func.shared.operational_regions import (
    load_operational_regions,
    operational_stats_for,
    operational_threshold_footprint_for,
    operational_region_mask_for_candidate,
    pass1_operational_forecast_region_scope,
    selectable_pass1_regions,
)
from func.shared.runtime_paths import logical_ref, runtime_path


SUPPORTED_PRODUCT_IDS = {
    "QL_PRECIP_6H",
    "QL_TCW_WIND850",
    "QL_CONVERGENCE925_WIND925",
    "QL_OMEGA700_Z500",
    "QL_MOISTURE_FLUX850_WIND850",
}


ADAPTIVE_VECTOR_SAMPLING_MODE = "adaptive_pixel_spacing"
CHECKERBOARD_SAMPLING_GEOMETRY = "regular_checkerboard_original_grid_subset"
CHECKERBOARD_OPERATIONAL_PRODUCT_IDS = frozenset({
    "QL_TCW_WIND850",
    "QL_CONVERGENCE925_WIND925",
    "QL_MOISTURE_FLUX850_WIND850",
})


class Step05RenderError(RuntimeError):
    """An actionable failure while loading or rendering one Step05 image."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _load_step05_layout(root: Path) -> dict[str, Any]:
    path = runtime_path(root, "daba", "analysis", "step05_render_styles.yaml")
    if not path.is_file():
        raise Step05RenderError(
            "STYLE_LOAD_FAILED",
            f"Step05 layout style is missing: {path}. Check daba/analysis/step05_render_styles.yaml.",
        )
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise Step05RenderError("STYLE_LOAD_FAILED", f"Unable to read Step05 layout style {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise Step05RenderError("STYLE_LOAD_FAILED", f"Step05 layout style root must be a mapping: {path}")
    return value


def _resolve_product(root: Path, product_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    config_path = runtime_path(root, "daba", "ai_pass1", "discovery_source_panel_products.yaml")
    try:
        source_config = load_yaml_mapping(config_path)
        resolved_config, _style_diagnostics = resolve_render_style_config(root, source_config)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise Step05RenderError(
            "STYLE_LOAD_FAILED",
            f"Unable to load existing product/style contract from {config_path}: {exc}",
        ) from exc

    for product in resolved_config.get("products", []):
        if isinstance(product, dict) and str(product.get("product_id")) == product_id:
            return dict(product), dict(resolved_config.get("default_style", {}))
    raise Step05RenderError(
        "PRODUCT_ID_MISSING",
        f"product_id={product_id!r} is not declared in {config_path}. Check the Step04 request.",
    )


def _domain_from_request(requested_domain: Any) -> MapDomain:
    if not isinstance(requested_domain, dict):
        raise Step05RenderError(
            "REQUESTED_DOMAIN_MISSING",
            "Step04 request has no requested_domain mapping. Check analysis_data_requests.",
        )
    try:
        domain = MapDomain(
            lon_min=float(requested_domain["west"]),
            lon_max=float(requested_domain["east"]),
            lat_min=float(requested_domain["south"]),
            lat_max=float(requested_domain["north"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise Step05RenderError(
            "REQUESTED_DOMAIN_MISSING",
            "requested_domain must contain numeric west, east, south, and north values.",
        ) from exc
    if domain.lon_min >= domain.lon_max or domain.lat_min >= domain.lat_max:
        raise Step05RenderError("REQUESTED_DOMAIN_MISSING", "requested_domain bounds are not ordered west<east and south<north.")
    return domain




def _operational_precipitation_region_summaries(
    *,
    field: Any,
    candidate_bbox: dict[str, Any],
    regions: list[dict[str, Any]],
    root: Path,
) -> list[dict[str, Any]]:
    """Return deterministic per-region precipitation maxima for the SYSTEM anchor.

    These values are decision metadata only.  They never mask or otherwise
    modify the field rendered for the forecaster/VLM.
    """
    try:
        import numpy as np

        selected = selectable_pass1_regions(regions=regions, candidate_bbox=candidate_bbox)
        summaries: list[dict[str, Any]] = []
        for region in selected:
            stats = operational_stats_for(
                arr=field,
                region=region,
                np=np,
                root=root,
                clip_bbox=candidate_bbox,
            )
            maximum = stats.get("max")
            if not isinstance(maximum, (int, float)) or isinstance(maximum, bool):
                continue
            footprint = operational_threshold_footprint_for(
                arr=field,
                region=region,
                np=np,
                root=root,
                threshold=1.0,
                clip_bbox=candidate_bbox,
            )
            summaries.append({
                "region_id": str(region.get("region_id") or ""),
                "region_name": str(region.get("name_ko") or region.get("region_id") or ""),
                "region_role": str(region.get("region_role") or ""),
                "region_bbox": {
                    "west": float(region.get("bbox", {}).get("lon_min")),
                    "east": float(region.get("bbox", {}).get("lon_max")),
                    "south": float(region.get("bbox", {}).get("lat_min")),
                    "north": float(region.get("bbox", {}).get("lat_max")),
                },
                "maximum_6h_mm": float(maximum),
                "mean_6h_mm": stats.get("mean"),
                "p95_6h_mm": stats.get("p95"),
                "max_location": stats.get("max_location"),
                "valid_grid_count": footprint.get("valid_grid_count"),
                "full_region_grid_count": footprint.get("full_region_grid_count"),
                "precip_ge_1mm_grid_count": footprint.get("threshold_grid_count"),
                "candidate_precip_coverage_fraction": footprint.get("candidate_precip_coverage_fraction"),
                "region_precip_coverage_fraction": footprint.get("region_precip_coverage_fraction"),
                "precip_ge_1mm_centroid": footprint.get("threshold_centroid"),
                "precip_ge_1mm_bbox": footprint.get("threshold_bbox"),
            })
        return summaries
    except Exception as exc:  # noqa: BLE001
        raise Step05RenderError(
            "OPERATIONAL_FORECAST_MASK_FAILED",
            f"Unable to calculate SYSTEM direct-precipitation regional maxima: {exc}",
        ) from exc



def _precipitation_objects(
    *,
    field: Any,
    operational_mask: Any,
    candidate_bbox: dict[str, Any],
    regions: list[dict[str, Any]],
    region_summaries: list[dict[str, Any]],
    root: Path,
    threshold_mm: float = 1.0,
) -> list[dict[str, Any]]:
    """Extract deterministic 8-neighbour precipitation objects from the candidate field."""
    import numpy as np

    raw = np.asarray(field.values, dtype="float64")
    finite = np.isfinite(raw)
    active = finite & (raw >= float(threshold_mm))
    if not active.any():
        return []
    lat_name, lon_name = lat_lon_names(field)
    lat_grid = np.asarray(field[lat_name].broadcast_like(field).values, dtype="float64")
    lon_grid = np.asarray(field[lon_name].broadcast_like(field).values, dtype="float64")
    lon_grid = np.where(lon_grid > 180.0, lon_grid - 360.0, lon_grid)
    operational_mask = np.asarray(operational_mask, dtype=bool)
    if operational_mask.shape != raw.shape:
        raise Step05RenderError(
            "OPERATIONAL_FORECAST_MASK_FAILED",
            f"precipitation-object mask shape={operational_mask.shape} does not match field shape={raw.shape}",
        )

    region_summary_by_id = {
        str(item.get("region_id") or ""): item
        for item in region_summaries
        if isinstance(item, dict) and str(item.get("region_id") or "")
    }
    region_masks: dict[str, Any] = {}
    region_meta: dict[str, dict[str, Any]] = {}
    for region in selectable_pass1_regions(regions=regions, candidate_bbox=candidate_bbox):
        region_id = str(region.get("region_id") or "")
        if not region_id:
            continue
        mask, meta = operational_region_mask_for_candidate(
            arr=field,
            region=region,
            candidate_bbox=candidate_bbox,
            root=root,
            np=np,
        )
        if mask.shape == raw.shape:
            region_masks[region_id] = np.asarray(mask, dtype=bool)
            region_meta[region_id] = {**meta, "region": region}

    visited = np.zeros(active.shape, dtype=bool)
    components: list[dict[str, Any]] = []
    neighbours = [
        (-1, -1), (-1, 0), (-1, 1),
        (0, -1),           (0, 1),
        (1, -1),  (1, 0),  (1, 1),
    ]
    shape = active.shape
    for seed in zip(*np.where(active)):
        if visited[seed]:
            continue
        stack = [seed]
        visited[seed] = True
        cells: list[tuple[int, ...]] = []
        while stack:
            current = stack.pop()
            cells.append(current)
            i, j = current[-2], current[-1]
            prefix = current[:-2]
            for di, dj in neighbours:
                ni, nj = i + di, j + dj
                if ni < 0 or nj < 0 or ni >= shape[-2] or nj >= shape[-1]:
                    continue
                neighbour = (*prefix, ni, nj)
                if active[neighbour] and not visited[neighbour]:
                    visited[neighbour] = True
                    stack.append(neighbour)
        component_mask = np.zeros(active.shape, dtype=bool)
        for cell in cells:
            component_mask[cell] = True
        values = raw[component_mask]
        lats = lat_grid[component_mask]
        lons = lon_grid[component_mask]
        max_flat = int(np.nanargmax(values))
        max_value = float(values[max_flat])
        op_overlap = component_mask & operational_mask
        op_count = int(np.count_nonzero(op_overlap))
        op_values = raw[op_overlap]
        overlaps: list[dict[str, Any]] = []
        for region_id, region_mask in region_masks.items():
            overlap_mask = component_mask & region_mask
            count = int(np.count_nonzero(overlap_mask))
            if count <= 0:
                continue
            overlap_values = raw[overlap_mask]
            overlap_lats = lat_grid[overlap_mask]
            overlap_lons = lon_grid[overlap_mask]
            summary = region_summary_by_id.get(region_id, {})
            meta = region_meta[region_id]
            region = meta["region"]
            region_precip_count = int(summary.get("precip_ge_1mm_grid_count") or 0)
            overlaps.append({
                "region_id": region_id,
                "region_name": str(region.get("name_ko") or region_id),
                "region_role": str(region.get("region_role") or ""),
                "region_bbox": {
                    "west": float(region.get("bbox", {}).get("lon_min")),
                    "east": float(region.get("bbox", {}).get("lon_max")),
                    "south": float(region.get("bbox", {}).get("lat_min")),
                    "north": float(region.get("bbox", {}).get("lat_max")),
                },
                "overlap_grid_count": count,
                "object_overlap_fraction": float(count / len(cells)),
                "object_share_of_region_precip": float(count / region_precip_count) if region_precip_count else None,
                "region_precip_coverage_fraction": summary.get("region_precip_coverage_fraction"),
                "region_precip_grid_count": region_precip_count,
                "full_region_grid_count": int(summary.get("full_region_grid_count") or 0),
                "object_region_coverage_fraction": (
                    float(count / int(summary.get("full_region_grid_count") or 0))
                    if int(summary.get("full_region_grid_count") or 0) > 0 else None
                ),
                "overlap_precipitation_sum_mm": float(np.nansum(overlap_values)),
                "overlap_maximum_6h_mm": float(np.nanmax(overlap_values)),
                "overlap_centroid": {
                    "latitude": float(np.nanmean(overlap_lats)),
                    "longitude": float(np.nanmean(overlap_lons)),
                },
            })
        overlaps.sort(
            key=lambda item: (
                -float(item.get("overlap_precipitation_sum_mm") or 0.0),
                -int(item.get("overlap_grid_count") or 0),
                str(item.get("region_id") or ""),
            )
        )
        components.append({
            "grid_count": int(len(cells)),
            "maximum_6h_mm": max_value,
            "mean_6h_mm": float(np.nanmean(values)),
            "p95_6h_mm": float(np.nanpercentile(values, 95)),
            "precipitation_sum_mm": float(np.nansum(values)),
            "centroid": {
                "latitude": float(np.nanmean(lats)),
                "longitude": float(np.nanmean(lons)),
            },
            "bbox": {
                "west": float(np.nanmin(lons)),
                "east": float(np.nanmax(lons)),
                "south": float(np.nanmin(lats)),
                "north": float(np.nanmax(lats)),
            },
            "operational_overlap_grid_count": op_count,
            "operational_overlap_fraction": float(op_count / len(cells)),
            "operational_precipitation_sum_mm": float(np.nansum(op_values)) if op_count else 0.0,
            "operational_maximum_6h_mm": float(np.nanmax(op_values)) if op_count else None,
            "region_overlaps": overlaps,
        })
    components.sort(
        key=lambda item: (
            -float(item.get("operational_precipitation_sum_mm") or 0.0),
            -int(item.get("operational_overlap_grid_count") or 0),
            -float(item.get("maximum_6h_mm") or 0.0),
            float((item.get("centroid") or {}).get("latitude") or 0.0),
            float((item.get("centroid") or {}).get("longitude") or 0.0),
        )
    )
    for index, item in enumerate(components, start=1):
        item["object_id"] = f"P{index:03d}"
    return components


def _draw_operational_scope_boundary(
    ax: Any,
    *,
    field: Any,
    operational_mask: Any,
    domain: MapDomain,
) -> dict[str, Any]:
    """Draw the direct-precipitation eligibility boundary without masking data.

    The shaded precipitation field remains the full candidate-domain field.
    This outline only communicates which grid cells are eligible to satisfy
    the SYSTEM direct-precipitation presence anchor.
    """
    try:
        import numpy as np

        subset = subset_to_domain(field, domain)
        lat_name, lon_name = lat_lon_names(subset)
        mask = np.asarray(operational_mask, dtype=bool)
        if mask.shape != tuple(int(size) for size in subset.shape):
            raise ValueError(
                f"operational mask shape={mask.shape} does not match field shape={tuple(subset.shape)}"
            )
        if not mask.any() or mask.all():
            return {
                "shown": False,
                "label": "직접강수 판정 대상 운영권역 경계",
                "reason": "uniform_mask",
            }
        ax.contour(
            subset[lon_name].values,
            subset[lat_name].values,
            mask.astype("float64"),
            levels=[0.5],
            colors="#555555",
            linewidths=0.9,
            linestyles="--",
            zorder=12,
        )
        return {
            "shown": True,
            "label": "직접강수 판정 대상 운영권역 경계",
            "line_color": "#555555",
            "line_width": 0.9,
            "line_style": "--",
        }
    except Exception as exc:  # noqa: BLE001
        raise Step05RenderError(
            "OPERATIONAL_FORECAST_BOUNDARY_FAILED",
            f"Unable to draw direct-precipitation operational boundary: {exc}",
        ) from exc


def _validate_2d_field(field: Any) -> None:
    """Fail before Matplotlib with a clear message for malformed GRIB output."""
    try:
        import numpy as np

        lat_name, lon_name = lat_lon_names(field)
        values = np.asarray(field.values)
        lon = np.asarray(field[lon_name].values)
        lat = np.asarray(field[lat_name].values)
    except Exception as exc:  # noqa: BLE001
        raise Step05RenderError("FIELD_LOAD_FAILED", f"Unable to inspect the loaded feature field: {exc}") from exc

    valid_shape = values.ndim == 2 and (
        (lon.ndim == 1 and lat.ndim == 1 and values.shape == (lat.size, lon.size))
        or (lon.ndim == 2 and lat.ndim == 2 and lon.shape == lat.shape == values.shape)
    )
    if not valid_shape:
        raise Step05RenderError(
            "ARRAY_SHAPE_MISMATCH",
            f"Feature field and latitude/longitude shapes are incompatible: values={values.shape}, lon={lon.shape}, lat={lat.shape}.",
        )


def _valid_time(init: str, forecast_hour: int) -> datetime:
    try:
        base = datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise Step05RenderError("INIT_INVALID", f"init must have YYYYMMDDHH format, got {init!r}.") from exc
    return base + timedelta(hours=int(forecast_hour))


def _format_kst_datetime(value: datetime) -> str:
    return value.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")


def _field_value_summary(field: Any, domain: MapDomain, *, product_id: str, style: dict[str, Any]) -> dict[str, Any]:
    """Summarize the values actually included in the requested map domain."""
    try:
        import numpy as np

        subset = subset_to_domain(field, domain)
        values = np.asarray(subset.values, dtype="float64")
    except Exception as exc:  # noqa: BLE001
        raise Step05RenderError("FIELD_LOAD_FAILED", f"Unable to summarize field values in requested_domain: {exc}") from exc

    finite = np.isfinite(values)
    summary: dict[str, Any] = {
        "shape": tuple(int(value) for value in values.shape),
        "finite_count": int(finite.sum()),
        "nan_count": int(np.isnan(values).sum()),
        "exact_zero_count": int((values == 0.0).sum()),
    }
    if finite.any():
        summary["minimum"] = float(np.nanmin(values))
        summary["maximum"] = float(np.nanmax(values))
    if product_id == "QL_PRECIP_6H":
        levels = style.get("levels")
        if isinstance(levels, list) and len(levels) >= 2:
            lower, upper = float(levels[0]), float(levels[1])
            summary["first_color_interval_mm"] = [lower, upper]
            summary["first_color_interval_count"] = int(((values >= lower) & (values < upper) & finite).sum())
            positive = values[(values > 0.0) & finite]
            summary["minimum_positive_mm"] = float(positive.min()) if positive.size else None
    return summary


def _subset_vector_values(u_field: Any, v_field: Any, domain: MapDomain) -> tuple[Any, Any]:
    """Return the unmodified requested-domain U/V values used for vector sampling."""
    try:
        import numpy as np

        u_values = np.asarray(subset_to_domain(u_field, domain).values, dtype="float64")
        v_values = np.asarray(subset_to_domain(v_field, domain).values, dtype="float64")
    except Exception as exc:  # noqa: BLE001
        raise Step05RenderError("FIELD_LOAD_FAILED", f"Unable to summarize 850 hPa vector values in requested_domain: {exc}") from exc
    if u_values.shape != v_values.shape:
        raise Step05RenderError("ARRAY_SHAPE_MISMATCH", f"U/V shapes differ in requested_domain: U={u_values.shape}, V={v_values.shape}.")
    if u_values.ndim != 2:
        raise Step05RenderError(
            "ARRAY_SHAPE_MISMATCH",
            f"U/V values in requested_domain must be two-dimensional, got shape={u_values.shape}.",
        )
    return u_values, v_values


def _vector_value_summary(u_field: Any, v_field: Any, domain: MapDomain) -> dict[str, Any]:
    """Summarize raw U/V and wind speed without changing their values."""
    try:
        import numpy as np

        u_values, v_values = _subset_vector_values(u_field, v_field, domain)
    except Step05RenderError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise Step05RenderError("FIELD_LOAD_FAILED", f"Unable to summarize vector values in requested_domain: {exc}") from exc
    ny, nx = (int(value) for value in u_values.shape)
    finite_u = np.isfinite(u_values)
    finite_v = np.isfinite(v_values)
    finite_speed = finite_u & finite_v
    speed = np.hypot(u_values, v_values)
    summary: dict[str, Any] = {
        "u_finite_count": int(finite_u.sum()),
        "u_nan_count": int(np.isnan(u_values).sum()),
        "v_finite_count": int(finite_v.sum()),
        "v_nan_count": int(np.isnan(v_values).sum()),
        "speed_finite_count": int(finite_speed.sum()),
        "subset_shape": [ny, nx],
        "grid_nx": nx,
        "grid_ny": ny,
    }
    if finite_speed.any():
        summary["wind_speed_min_ms"] = float(speed[finite_speed].min())
        summary["wind_speed_max_ms"] = float(speed[finite_speed].max())
        summary["wind_speed_p75_ms"] = float(np.percentile(speed[finite_speed], 75))
        summary["wind_speed_p90_ms"] = float(np.percentile(speed[finite_speed], 90))
    return summary


def _rendered_vector_count(u_values: Any, v_values: Any, stride: int) -> int:
    """Count exactly the finite non-zero U/V anchors drawn by crop-local sampling."""
    try:
        import numpy as np

        u_array = np.asarray(u_values, dtype="float64")
        v_array = np.asarray(v_values, dtype="float64")
    except Exception as exc:  # noqa: BLE001
        raise Step05RenderError("VECTOR_SAMPLING_INVALID", f"Unable to read U/V values for vector sampling: {exc}") from exc
    if u_array.shape != v_array.shape or u_array.ndim != 2:
        raise Step05RenderError(
            "ARRAY_SHAPE_MISMATCH",
            f"U/V values for vector sampling must be matching two-dimensional arrays, got U={u_array.shape}, V={v_array.shape}.",
        )
    if isinstance(stride, bool) or not isinstance(stride, int) or stride < 1:
        raise Step05RenderError("VECTOR_SAMPLING_INVALID", f"Vector stride must be an integer >= 1, got {stride!r}.")
    speed = np.hypot(u_array, v_array)
    active = np.isfinite(u_array) & np.isfinite(v_array) & np.isfinite(speed) & (speed > 0.0)
    return int(active[::stride, ::stride].sum())


def checkerboard_original_grid_mask(ny: int, nx: int, parity: int) -> Any:
    """Return a periodic original-grid mask where i=y(row) and j=x(column)."""
    try:
        import numpy as np
    except Exception as exc:  # noqa: BLE001
        raise Step05RenderError("VECTOR_SAMPLING_INVALID", f"Unable to create checkerboard sampling mask: {exc}") from exc
    if isinstance(ny, bool) or isinstance(nx, bool) or ny < 1 or nx < 1:
        raise Step05RenderError("VECTOR_SAMPLING_INVALID", f"Checkerboard grid must have positive ny/nx, got ny={ny!r}, nx={nx!r}.")
    if parity not in (0, 1):
        raise Step05RenderError("VECTOR_SAMPLING_INVALID", f"Checkerboard parity must be 0 or 1, got {parity!r}.")
    i_y, j_x = np.indices((int(ny), int(nx)))
    return ((i_y + j_x) % 2) == int(parity)


def select_checkerboard_parity(ny: int, nx: int) -> int:
    """Choose one deterministic parity, prioritizing corners, edges, then point count."""
    candidates: list[tuple[tuple[int, int, int, int], int]] = []
    for parity in (0, 1):
        mask = checkerboard_original_grid_mask(ny, nx, parity)
        corner_count = int(mask[0, 0]) + int(mask[0, -1]) + int(mask[-1, 0]) + int(mask[-1, -1])
        edge_counts = (int(mask[:, 0].sum()), int(mask[:, -1].sum()), int(mask[0, :].sum()), int(mask[-1, :].sum()))
        # The final -parity gives parity 0 a stable tie break without tuning to a field value.
        candidates.append(((corner_count, min(edge_counts), int(mask.sum()), -parity), parity))
    return max(candidates, key=lambda candidate: candidate[0])[1]


def resolve_operational_vector_sampling(
    *,
    product_id: str,
    adaptive_sampling: dict[str, Any],
    u_values: Any,
    v_values: Any,
) -> tuple[dict[str, Any], Any | None]:
    """Apply the approved MF/TCW/CONV full-grid checkerboard policy after adaptive/cap resolution.

    The pre-existing adaptive resolver remains the source of the base stride and
    cap decision.  A checkerboard is used only when that completed decision is
    still full-grid (base and resolved stride are both one); otherwise the
    original rectangular stride sampling is retained unchanged.
    """
    try:
        import numpy as np

        u_array = np.asarray(u_values, dtype="float64")
        v_array = np.asarray(v_values, dtype="float64")
    except Exception as exc:  # noqa: BLE001
        raise Step05RenderError("VECTOR_SAMPLING_INVALID", f"Unable to inspect U/V for operational vector sampling: {exc}") from exc
    if u_array.shape != v_array.shape or u_array.ndim != 2:
        raise Step05RenderError(
            "ARRAY_SHAPE_MISMATCH",
            f"Operational U/V sampling requires matching two-dimensional arrays, got U={u_array.shape}, V={v_array.shape}.",
        )
    try:
        base_stride = int(adaptive_sampling["base_wind_stride"])
        resolved_stride = int(adaptive_sampling["resolved_wind_stride"])
    except (KeyError, TypeError, ValueError) as exc:
        raise Step05RenderError("VECTOR_SAMPLING_INVALID", "Adaptive sampling has no valid base/resolved stride.") from exc

    ny, nx = (int(value) for value in u_array.shape)
    sampling = dict(adaptive_sampling)
    sampling.update({
        "source_grid_shape_yx": [ny, nx],
        "source_grid_points": int(ny * nx),
        "checkerboard_applied": False,
        "checkerboard_parity": None,
        "sampling_geometry": "regular_rectangular_stride_original_grid_subset",
    })
    if (
        product_id not in CHECKERBOARD_OPERATIONAL_PRODUCT_IDS
        or base_stride != 1
        or resolved_stride != 1
    ):
        return sampling, None

    parity = select_checkerboard_parity(ny, nx)
    mask = checkerboard_original_grid_mask(ny, nx, parity)
    speed = np.hypot(u_array, v_array)
    active = np.isfinite(u_array) & np.isfinite(v_array) & np.isfinite(speed) & (speed > 0.0)
    sampling.update({
        "checkerboard_applied": True,
        "checkerboard_parity": parity,
        "sampling_geometry": CHECKERBOARD_SAMPLING_GEOMETRY,
        "i_axis": "y_row",
        "j_axis": "x_column",
        "selected_grid_point_count": int(mask.sum()),
        "rendered_vector_count": int(active[mask].sum()),
        "opposite_parity_grid_point_count": int((~mask).sum()),
    })
    return sampling, mask


def _checkerboard_masked_vector_fields(
    *,
    fields: dict[str, Any],
    domain: MapDomain,
    mask: Any,
) -> dict[str, Any]:
    """Return crop-local U/V copies with only selected original-grid anchors drawable."""
    try:
        import numpy as np

        u_subset = subset_to_domain(fields["u"], domain)
        v_subset = subset_to_domain(fields["v"], domain)
        u_values = np.asarray(u_subset.values)
        v_values = np.asarray(v_subset.values)
    except Exception as exc:  # noqa: BLE001
        raise Step05RenderError("FIELD_LOAD_FAILED", f"Unable to apply checkerboard vector sampling: {exc}") from exc
    if u_values.shape != v_values.shape or tuple(mask.shape) != u_values.shape:
        raise Step05RenderError(
            "ARRAY_SHAPE_MISMATCH",
            f"Checkerboard mask/U/V shape mismatch: mask={getattr(mask, 'shape', None)}, U={u_values.shape}, V={v_values.shape}.",
        )
    # where() retains the exact selected U/V values and coordinates; only
    # unselected display anchors become NaN in this renderer-local copy.
    return {**fields, "u": u_subset.where(mask), "v": v_subset.where(mask)}


def resolve_adaptive_wind_stride(
    *,
    nx: int,
    ny: int,
    axes_width_px: float,
    axes_height_px: float,
    target_min_spacing_px: float,
    u_values: Any | None = None,
    v_values: Any | None = None,
    max_rendered_vectors: int | float | None = None,
) -> dict[str, float | int | str | None]:
    """Resolve one crop-local vector stride from Step05 data-axes geometry."""
    if isinstance(nx, bool) or isinstance(ny, bool) or nx < 1 or ny < 1:
        raise Step05RenderError("VECTOR_SAMPLING_INVALID", f"Vector grid must have nx/ny >= 1, got nx={nx}, ny={ny}.")
    values = {
        "axes_width_px": axes_width_px,
        "axes_height_px": axes_height_px,
        "target_min_spacing_px": target_min_spacing_px,
    }
    try:
        numeric = {key: float(value) for key, value in values.items()}
    except (TypeError, ValueError) as exc:
        raise Step05RenderError("VECTOR_SAMPLING_INVALID", "Vector sampling geometry must be numeric.") from exc
    if any(not math.isfinite(value) or value <= 0.0 for value in numeric.values()):
        raise Step05RenderError(
            "VECTOR_SAMPLING_INVALID",
            "Vector sampling axes geometry and target spacing must be finite positive values.",
        )
    native_x = numeric["axes_width_px"] / max(int(nx) - 1, 1)
    native_y = numeric["axes_height_px"] / max(int(ny) - 1, 1)
    native_spacing = min(native_x, native_y)
    base_stride = max(1, int(math.ceil(numeric["target_min_spacing_px"] / native_spacing)))
    cap: int | None = None
    if max_rendered_vectors is not None:
        try:
            cap_value = float(max_rendered_vectors)
        except (TypeError, ValueError) as exc:
            raise Step05RenderError("VECTOR_SAMPLING_INVALID", "max_rendered_vectors must be a positive integer when declared.") from exc
        if not math.isfinite(cap_value) or cap_value < 1.0 or not cap_value.is_integer():
            raise Step05RenderError("VECTOR_SAMPLING_INVALID", "max_rendered_vectors must be a positive integer when declared.")
        cap = int(cap_value)
        if u_values is None or v_values is None:
            raise Step05RenderError("VECTOR_SAMPLING_INVALID", "U/V values are required when max_rendered_vectors is declared.")
    resolved_stride = base_stride
    rendered_count: int | None = None
    if u_values is not None or v_values is not None:
        if u_values is None or v_values is None:
            raise Step05RenderError("VECTOR_SAMPLING_INVALID", "Both U and V values are required for vector-count sampling.")
        expected_shape = (int(ny), int(nx))
        if tuple(getattr(u_values, "shape", ())) != expected_shape or tuple(getattr(v_values, "shape", ())) != expected_shape:
            raise Step05RenderError(
                "ARRAY_SHAPE_MISMATCH",
                f"Vector sampling grid shape must be {expected_shape}, got U={getattr(u_values, 'shape', None)}, V={getattr(v_values, 'shape', None)}.",
            )
        rendered_count = _rendered_vector_count(u_values, v_values, resolved_stride)
        while cap is not None and rendered_count > cap:
            resolved_stride += 1
            rendered_count = _rendered_vector_count(u_values, v_values, resolved_stride)
    return {
        "sampling_mode": ADAPTIVE_VECTOR_SAMPLING_MODE,
        "target_min_spacing_px": numeric["target_min_spacing_px"],
        "native_x_spacing_px": native_x,
        "native_y_spacing_px": native_y,
        "base_wind_stride": base_stride,
        "resolved_wind_stride": resolved_stride,
        "rendered_vector_count": rendered_count,
        "max_rendered_vectors": cap,
    }


def _adaptive_vector_sampling_config(layout: dict[str, Any], product_id: str) -> dict[str, Any] | None:
    """Return the Step05-only adaptive sampling config for an opted-in product."""
    config = layout.get("vector_sampling")
    if config is None:
        return None
    if not isinstance(config, dict):
        raise Step05RenderError("STYLE_LOAD_FAILED", "vector_sampling must be a mapping when declared.")
    mode = str(config.get("mode") or "").strip()
    product_ids = config.get("product_ids")
    if mode != ADAPTIVE_VECTOR_SAMPLING_MODE:
        raise Step05RenderError("STYLE_LOAD_FAILED", f"Unsupported Step05 vector_sampling.mode={mode!r}.")
    if not isinstance(product_ids, list) or not all(isinstance(value, str) and value for value in product_ids):
        raise Step05RenderError("STYLE_LOAD_FAILED", "vector_sampling.product_ids must be a list of non-empty product IDs.")
    if product_id not in product_ids:
        return None
    if "target_min_spacing_px" not in config:
        raise Step05RenderError("STYLE_LOAD_FAILED", "vector_sampling.target_min_spacing_px is required for adaptive products.")
    return config


def _load_vector_fields(
    *,
    product: dict[str, Any],
    index: dict[tuple[str, int | None, int], Path],
    forecast_hour: int,
    np: Any,
    xr: Any,
) -> tuple[dict[str, Any], list[str]]:
    """Load a product's declared raw U/V vector inputs without altering Step02 loaders."""
    vector = product.get("vector_field")
    if not isinstance(vector, dict):
        raise Step05RenderError("FEATURE_MISSING", "Vector product has no vector_field declaration in the existing product contract.")
    fields: dict[str, Any] = {}
    source_files: list[str] = []
    for key, short_name_key in (("u", "u_short_name"), ("v", "v_short_name")):
        short_name = str(vector.get(short_name_key) or "").strip()
        level_raw = vector.get("level")
        level = int(level_raw) if level_raw is not None else None
        source_path = index.get((short_name, level, int(forecast_hour)))
        if not short_name or source_path is None:
            raise Step05RenderError(
                "FEATURE_MISSING",
                f"Required vector input {short_name or key} level={level} at F{forecast_hour:03d} is absent from local GRIB input.",
            )
        try:
            component = aifs_data.open_field(source_path, short_name, level, np, xr)
        except Exception as exc:  # noqa: BLE001
            raise Step05RenderError("FIELD_LOAD_FAILED", f"Unable to load {short_name} level={level} at F{forecast_hour:03d}: {exc}") from exc
        _validate_2d_field(component)
        fields[key] = component
        source_files.append(str(source_path))
    return fields, source_files


def _load_raw_field(
    *,
    field_spec: dict[str, Any],
    index: dict[tuple[str, int | None, int], Path],
    forecast_hour: int,
    np: Any,
    xr: Any,
) -> tuple[Any, Path]:
    """Load one declared raw field without changing the existing Step02 loader."""
    short_name = str(field_spec.get("short_name") or "").strip()
    level_raw = field_spec.get("level")
    level = int(level_raw) if level_raw is not None else None
    source_path = index.get((short_name, level, int(forecast_hour)))
    if not short_name or source_path is None:
        raise Step05RenderError("FEATURE_MISSING", f"Required raw field {short_name!r} level={level} is absent at F{forecast_hour:03d}.")
    try:
        field = aifs_data.open_field(source_path, short_name, level, np, xr)
    except Exception as exc:  # noqa: BLE001
        raise Step05RenderError("FIELD_LOAD_FAILED", f"Unable to load raw field {short_name} level={level}: {exc}") from exc
    if str(field_spec.get("conversion") or "") == "geopotential_height_gpm":
        field = convert_geopotential_height(field)
    _validate_2d_field(field)
    return field, source_path


def _rendered_extent(ax: Any, domain: MapDomain) -> dict[str, float]:
    west, east = (float(value) for value in ax.get_xlim())
    south, north = (float(value) for value in ax.get_ylim())
    rendered = {"west": west, "east": east, "south": south, "north": north}
    requested = {"west": domain.lon_min, "east": domain.lon_max, "south": domain.lat_min, "north": domain.lat_max}
    if any(not math.isclose(rendered[key], requested[key], rel_tol=0.0, abs_tol=1e-9) for key in requested):
        raise Step05RenderError(
            "EXTENT_MISMATCH",
            f"Rendered axes extent {rendered} differs from Step04 requested_domain {requested}.",
        )
    return rendered


def _add_vector_reference(ax: Any, *, product_id: str, layout: dict[str, Any]) -> dict[str, Any] | None:
    """Add a quiver key tied to the already-rendered Matplotlib quiver artist."""
    references = layout.get("vector_reference", {})
    config = references.get(product_id) if isinstance(references, dict) else None
    if not isinstance(config, dict) or not bool(config.get("enabled", False)):
        return None
    try:
        from matplotlib.quiver import Quiver

        quivers = [artist for artist in ax.get_children() if isinstance(artist, Quiver)]
    except Exception as exc:  # noqa: BLE001
        raise Step05RenderError("VECTOR_REFERENCE_FAILED", f"Unable to identify the rendered quiver artist: {exc}") from exc
    if len(quivers) != 1:
        raise Step05RenderError("VECTOR_REFERENCE_FAILED", f"Expected one rendered quiver for {product_id}, found {len(quivers)}.")
    try:
        value_ms = float(config["value_ms"])
        label = str(config["label"])
        key = ax.quiverkey(
            quivers[0],
            X=float(config.get("x", 0.08)),
            Y=float(config.get("y", 0.04)),
            U=value_ms,
            label=label,
            labelpos=str(config.get("label_position", "E")),
            coordinates=str(config.get("coordinates", "axes")),
            color="black",
            fontproperties={"size": float(config.get("fontsize", 8.5))},
        )
        key.text.set_bbox({"facecolor": "white", "edgecolor": "none", "alpha": 0.82, "boxstyle": "round,pad=0.12"})
    except Exception as exc:  # noqa: BLE001
        raise Step05RenderError("VECTOR_REFERENCE_FAILED", f"Unable to add vector reference for {product_id}: {exc}") from exc
    return {
        "value_ms": value_ms,
        "label": label,
        "coordinates": str(config.get("coordinates", "axes")),
        "x": float(config.get("x", 0.08)),
        "y": float(config.get("y", 0.04)),
    }


def _normalized_window_bbox(fig: Any, bbox: Any) -> dict[str, float]:
    """Return a display-coordinate bounding box in deterministic figure coordinates."""
    figure_bbox = fig.bbox
    return {
        "x0": float((bbox.x0 - figure_bbox.x0) / figure_bbox.width),
        "y0": float((bbox.y0 - figure_bbox.y0) / figure_bbox.height),
        "width": float(bbox.width / figure_bbox.width),
        "height": float(bbox.height / figure_bbox.height),
    }


def _normalized_bbox(fig: Any, artist: Any) -> dict[str, float]:
    """Return an artist bounding box in deterministic figure coordinates."""
    return _normalized_window_bbox(fig, artist.get_window_extent(renderer=fig.canvas.get_renderer()))


def _reference_vector_bbox(fig: Any, data_axes: Any) -> dict[str, float] | None:
    """Measure the rendered quiver key arrow and its label as one reference block."""
    try:
        from matplotlib.quiver import QuiverKey
        from matplotlib.transforms import Bbox

        references = [artist for artist in data_axes.get_children() if isinstance(artist, QuiverKey)]
        if len(references) != 1:
            return None
        key = references[0]
        renderer = fig.canvas.get_renderer()
        bboxes = [key.text.get_window_extent(renderer=renderer)]
        vector = key.vector
        paths = vector.get_paths()
        offsets = vector.get_offsets()
        if paths and len(offsets):
            offset_pixels = vector.get_offset_transform().transform(offsets)
            for offset in offset_pixels:
                for path in paths:
                    arrow_bbox = path.get_extents().transformed(vector.get_transform()).translated(*offset)
                    bboxes.append(arrow_bbox)
        return _normalized_window_bbox(fig, Bbox.union(bboxes))
    except Exception:  # noqa: BLE001 - layout metadata must not alter rendering.
        return None


def _layout_validation(
    fig: Any,
    *,
    data_axes: Any,
    colorbar_axes: Any,
    title_artists: dict[str, Any],
) -> dict[str, Any]:
    """Capture the fixed layout geometry used to render the PNG."""
    fig.canvas.draw()
    data_bbox = _normalized_bbox(fig, data_axes)
    colorbar_bbox = _normalized_bbox(fig, colorbar_axes)
    title_bboxes = {
        name: _normalized_bbox(fig, artist)
        for name, artist in title_artists.items()
    }
    reference_bbox = _reference_vector_bbox(fig, data_axes)
    return {
        "figure_size_inches": [float(value) for value in fig.get_size_inches()],
        "data_axes_bbox_normalized": data_bbox,
        "data_axes_area_ratio": float(data_bbox["width"] * data_bbox["height"]),
        "colorbar_axes_bbox_normalized": colorbar_bbox,
        "title_bboxes_normalized": title_bboxes,
        "title_bbox_normalized": title_bboxes.get("title"),
        "metadata_bbox_normalized": title_bboxes.get("metadata"),
        "reference_vector_bbox_normalized": reference_bbox,
    }


def _palette_summary(
    *,
    product_id: str,
    layout: dict[str, Any],
    resolved_style: dict[str, Any],
    mesh: Any,
    mcolors: Any,
    precipitation_rendering: dict[str, Any] | None,
    missing_color: str,
) -> dict[str, Any] | None:
    """Record the actual Step05 palette while preserving product scale contracts."""
    definitions = layout.get("palette_metadata", {})
    definition = definitions.get(product_id) if isinstance(definitions, dict) else None
    if not isinstance(definition, dict):
        return None
    colors = list(getattr(mesh.cmap, "colors", []))
    if not colors:
        raise Step05RenderError(
            "STYLE_LOAD_FAILED",
            f"Step05 palette metadata requires a discrete colormap for {product_id}.",
        )
    expected_color_count = max(len(list(resolved_style.get("levels", []))) - 1, 0)
    if expected_color_count and len(colors) < expected_color_count:
        raise Step05RenderError(
            "STYLE_LOAD_FAILED",
            f"Step05 palette for {product_id} has {len(colors)} colors but requires {expected_color_count} level bins.",
        )
    color_sequence = [mcolors.to_hex(color, keep_alpha=False) for color in colors[:expected_color_count or None]]
    summary: dict[str, Any] = {
        "palette_type": str(definition.get("palette_type") or "sequential_discrete"),
        "source_colormap": str(definition.get("source_colormap") or "configured"),
        "color_levels": list(resolved_style.get("levels", [])),
        "color_ticks": list(resolved_style.get("ticks", [])),
        "color_sequence": color_sequence,
    }
    if len(colors) > len(color_sequence):
        summary["extend_color"] = mcolors.to_hex(colors[-1], keep_alpha=False)
    if product_id == "QL_PRECIP_6H" and precipitation_rendering is not None:
        summary.update(
            zero_color=precipitation_rendering["zero_color"],
            positive_low_color=precipitation_rendering["positive_first_interval_color"],
            missing_color=missing_color,
        )
    return summary


def _configure_precipitation_semantics(
    ax: Any,
    mesh: Any,
    field: Any,
    domain: MapDomain,
    *,
    product_id: str,
    layout: dict[str, Any],
    field_summary: dict[str, Any],
    mcolors: Any,
) -> dict[str, Any] | None:
    """Separate exact zero from positive low precipitation in Step05 only."""
    if product_id != "QL_PRECIP_6H":
        return None
    semantics_by_product = layout.get("precipitation_semantics", {})
    config = semantics_by_product.get("QL_PRECIP_6H") if isinstance(semantics_by_product, dict) else None
    if not isinstance(config, dict):
        return None
    try:
        import numpy as np

        zero_color = str(config["zero_color"])
        positive_color = str(config["positive_first_interval_color"])
        original_colors = list(mesh.cmap.colors)
        if not original_colors:
            raise ValueError("the resolved precipitation colormap has no discrete colors")
        original_colors[0] = positive_color
        semantic_cmap = mcolors.ListedColormap(original_colors, name="step05_precipitation_semantic")
        mesh.set_cmap(semantic_cmap)

        subset = subset_to_domain(field, domain)
        lat_name, lon_name = lat_lon_names(subset)
        values = np.asarray(subset.values, dtype="float64")
        zero_count = int((values == 0.0).sum())
        if zero_count:
            zero_values = np.where(values == 0.0, 1.0, np.nan)
            zero_cmap = mcolors.ListedColormap([zero_color], name="step05_precipitation_zero")
            ax.pcolormesh(
                subset[lon_name].values,
                subset[lat_name].values,
                zero_values,
                shading="auto",
                cmap=zero_cmap,
                vmin=0.0,
                vmax=1.0,
                zorder=float(mesh.get_zorder()) + 0.01,
            )
    except Exception as exc:  # noqa: BLE001
        raise Step05RenderError("PRECIPITATION_SEMANTICS_FAILED", f"Unable to apply Step05 precipitation semantics: {exc}") from exc
    return {
        "zero_color": zero_color,
        "positive_first_interval_color": positive_color,
        "zero_cell_overlay_count": zero_count,
        "zero_label": str(config.get("zero_label", "0 mm")),
        "positive_first_interval_label": str(config.get("positive_first_interval_label", ">0 mm")),
        "legend_x": float(config.get("legend_x", 0.925)),
        "legend_y": float(config.get("legend_y", 0.925)),
        "legend_fontsize": float(config.get("legend_fontsize", 8.5)),
    }


def _add_precipitation_semantics_legend(
    fig: Any,
    *,
    precipitation_rendering: dict[str, Any] | None,
    missing_rendering: dict[str, Any],
) -> dict[str, Any] | None:
    """Place an external legend that makes zero, positive low values, and NaN unambiguous."""
    if precipitation_rendering is None:
        return None
    try:
        from matplotlib.patches import Patch

        handles = [
            Patch(facecolor=precipitation_rendering["zero_color"], edgecolor="#303030", label=precipitation_rendering["zero_label"]),
            Patch(
                facecolor=precipitation_rendering["positive_first_interval_color"],
                edgecolor="#303030",
                label=precipitation_rendering["positive_first_interval_label"],
            ),
        ]
        if missing_rendering["label_shown"]:
            handles.append(Patch(facecolor=missing_rendering["color"], edgecolor="#303030", label="Missing (NaN)"))
        fig.legend(
            handles=handles,
            loc="center right",
            bbox_to_anchor=(precipitation_rendering["legend_x"], precipitation_rendering["legend_y"]),
            ncol=len(handles),
            frameon=True,
            framealpha=0.94,
            edgecolor="#B0B0B0",
            fontsize=precipitation_rendering["legend_fontsize"],
            handlelength=1.0,
            handletextpad=0.35,
            borderpad=0.30,
            columnspacing=0.75,
        )
    except Exception as exc:  # noqa: BLE001
        raise Step05RenderError("PRECIPITATION_SEMANTICS_FAILED", f"Unable to add Step05 precipitation semantic legend: {exc}") from exc
    precipitation_rendering["legend_labels"] = [handle.get_label() for handle in handles]
    return precipitation_rendering


def render_spatial_field_png(
    *,
    root: Path,
    init: str,
    request: dict[str, Any],
    forecast_hour: int,
    output_path: Path,
) -> dict[str, Any]:
    """Render one currently supported Step05 spatial-field product.

    The caller is responsible for Step04 request selection and overwrite
    policy.  This function performs no writes other than the requested PNG.
    """
    product_id = str(request.get("product_id") or "").strip()
    if not product_id:
        raise Step05RenderError("PRODUCT_ID_MISSING", "Selected Step04 request has no product_id.")
    if product_id not in SUPPORTED_PRODUCT_IDS:
        raise Step05RenderError(
            "UNSUPPORTED_PRODUCT",
            f"This test renderer supports {sorted(SUPPORTED_PRODUCT_IDS)}; selected product_id={product_id!r}.",
        )

    domain = _domain_from_request(request.get("requested_domain"))
    product, base_style = _resolve_product(root, product_id)
    layout = _load_step05_layout(root)
    presentation_by_product = layout.get("product_presentation", {})
    presentation = (
        dict(presentation_by_product.get(product_id, {}))
        if isinstance(presentation_by_product, dict)
        and isinstance(presentation_by_product.get(product_id, {}), dict)
        else {}
    )
    display_title = presentation.get("display_title")
    if display_title is not None:
        product["display_title"] = str(display_title)
    layout_style = dict(layout.get("map_style_override", {}))
    product_overrides = layout.get("product_style_overrides", {})
    product_style_override = product_overrides.get(product_id, {}) if isinstance(product_overrides, dict) else {}
    if not isinstance(product_style_override, dict):
        raise Step05RenderError("STYLE_LOAD_FAILED", f"product_style_overrides.{product_id} must be a mapping.")
    product["style"] = {**dict(product.get("style", {})), **layout_style, **product_style_override}
    remove_keys_by_product = layout.get("product_style_remove_keys", {})
    remove_keys = remove_keys_by_product.get(product_id, []) if isinstance(remove_keys_by_product, dict) else []
    if not isinstance(remove_keys, list) or not all(isinstance(key, str) for key in remove_keys):
        raise Step05RenderError("STYLE_LOAD_FAILED", f"product_style_remove_keys.{product_id} must be a list of style keys.")
    for key in remove_keys:
        product["style"].pop(key, None)

    raw_dir = runtime_path(root, "dain", init, "raw", "aifs")
    if not raw_dir.is_dir() or not aifs_data.list_grib_files(raw_dir):
        raise Step05RenderError(
            "GRIB_INPUT_MISSING",
            f"No AIFS GRIB input is available under {raw_dir}. Check dain/<init>/raw/aifs.",
        )

    try:
        index, _raw_loader, np, xr = aifs_data.build_loaders(root, init, raw_dir)
    except Exception as exc:  # noqa: BLE001
        raise Step05RenderError("FIELD_LOAD_FAILED", f"Unable to index or open AIFS GRIB input: {exc}") from exc
    if not index:
        raise Step05RenderError("GRIB_INPUT_MISSING", f"No usable AIFS GRIB fields were indexed from {raw_dir}.")

    plot_type = str(product.get("plot_type"))
    source_files: list[str] = []
    if plot_type in {"shaded_feature", "shaded_feature_plus_raw_vector"}:
        specs = {spec.feature_id: spec for spec in aifs_data.load_feature_definitions(runtime_path(root, "daba", "ai_pass1", "feature_definitions.yaml"))}
        feature_id = str(product.get("shaded_feature_id") or "")
        spec = specs.get(feature_id)
        if spec is None:
            raise Step05RenderError("FEATURE_MISSING", f"Required feature_id={feature_id!r} is not defined in feature_definitions.yaml.")
        try:
            field, missing, source_files = aifs_data.build_feature_array(spec=spec, step=int(forecast_hour), index=index, np=np, xr=xr)
        except Exception as exc:  # noqa: BLE001
            raise Step05RenderError("FIELD_LOAD_FAILED", f"Unable to build feature {feature_id} at F{forecast_hour:03d}: {exc}") from exc
        if field is None:
            raise Step05RenderError("FEATURE_MISSING", f"Required feature {feature_id} at F{forecast_hour:03d} could not be built. Missing inputs: {missing}")
        _validate_2d_field(field)
        fields: dict[str, Any] = {"shaded": field}
    elif plot_type == "raw_shaded_plus_raw_contour":
        shaded_spec = product.get("shaded_field")
        contour_spec = product.get("contour_field")
        if not isinstance(shaded_spec, dict) or not isinstance(contour_spec, dict):
            raise Step05RenderError("FEATURE_MISSING", f"{product_id} has no valid raw shaded/contour field declaration.")
        field, shaded_source = _load_raw_field(field_spec=shaded_spec, index=index, forecast_hour=forecast_hour, np=np, xr=xr)
        contour, contour_source = _load_raw_field(field_spec=contour_spec, index=index, forecast_hour=forecast_hour, np=np, xr=xr)
        fields = {"shaded": field, "contour": contour}
        source_files = [str(shaded_source), str(contour_source)]
        feature_id = f"{shaded_spec.get('short_name')}@{shaded_spec.get('level')}"
    else:
        raise Step05RenderError("UNSUPPORTED_PRODUCT", f"Step05 does not yet support plot_type={plot_type!r} for {product_id}.")
    operational_scope_mask: dict[str, Any] | None = None
    operational_scope_mask_array: Any | None = None
    operational_scope_field_value_summary: dict[str, Any] | None = None
    operational_scope_region_summaries: list[dict[str, Any]] | None = None
    precipitation_objects: list[dict[str, Any]] | None = None
    if (
        product_id == "QL_PRECIP_6H"
        and request.get("_system_direct_precipitation_operational_mask") is True
    ):
        try:
            regions = load_operational_regions(
                runtime_path(root, "daba", "ai_pass1", "discovery_operational_regions.yaml")
            )
            source_precipitation_field = field
            field, operational_scope_mask_array, operational_scope_mask = pass1_operational_forecast_region_scope(
                arr=source_precipitation_field,
                candidate_bbox=request.get("requested_domain"),
                regions=regions,
                root=root,
                np=np,
            )
        except Exception as exc:  # noqa: BLE001
            raise Step05RenderError(
                "OPERATIONAL_FORECAST_MASK_FAILED",
                f"Unable to resolve SYSTEM direct-precipitation operational forecast scope: {exc}",
            ) from exc
        # Keep the full candidate-domain precipitation field visible. The
        # operational eligibility scope is communicated separately by an
        # outline so out-of-scope cells are not misread as missing data.
        fields["shaded"] = field
        try:
            scoped_for_decision = field.where(operational_scope_mask_array)
            operational_scope_field_value_summary = _field_value_summary(
                scoped_for_decision,
                domain,
                product_id=product_id,
                style=dict(product.get("style", {})),
            )
            operational_scope_region_summaries = _operational_precipitation_region_summaries(
                field=source_precipitation_field,
                candidate_bbox=request.get("requested_domain"),
                regions=regions,
                root=root,
            )
            precipitation_objects = _precipitation_objects(
                field=field,
                operational_mask=operational_scope_mask_array,
                candidate_bbox=request.get("requested_domain"),
                regions=regions,
                region_summaries=operational_scope_region_summaries,
                root=root,
                threshold_mm=1.0,
            )
        except Step05RenderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise Step05RenderError(
                "OPERATIONAL_FORECAST_MASK_FAILED",
                f"Unable to summarize SYSTEM direct-precipitation decision scope: {exc}",
            ) from exc
        product["display_title"] = (
            f"{str(product.get('display_title') or product_id)} — 직접강수 판정 대상 경계(점선)"
        )

    if plot_type == "shaded_feature_plus_raw_vector":
        vector_fields, vector_sources = _load_vector_fields(
            product=product,
            index=index,
            forecast_hour=forecast_hour,
            np=np,
            xr=xr,
        )
        fields.update(vector_fields)
        source_files.extend(vector_sources)
    field_summary = _field_value_summary(field, domain, product_id=product_id, style=dict(product.get("style", {})))
    vector_summary = _vector_value_summary(fields["u"], fields["v"], domain) if "u" in fields and "v" in fields else None

    figure_cfg = dict(layout.get("figure", {}))
    axes_cfg = dict(layout.get("axes", {}))
    colorbar_cfg = dict(layout.get("colorbar", {}))
    title_cfg = dict(layout.get("title", {}))
    missing_cfg = dict(layout.get("missing", {}))
    try:
        figsize = tuple(float(value) for value in figure_cfg["figsize_inches"])
        dpi = int(figure_cfg["dpi"])
        axes_rect = [float(value) for value in axes_cfg["rect"]]
        colorbar_rect = [float(value) for value in colorbar_cfg["rect"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise Step05RenderError("STYLE_LOAD_FAILED", f"Invalid Step05 layout geometry: {exc}") from exc
    if len(figsize) != 2 or len(axes_rect) != 4 or len(colorbar_rect) != 4:
        raise Step05RenderError("STYLE_LOAD_FAILED", "Step05 layout requires 2-value figsize and 4-value axes/colorbar rectangles.")

    # A fixed Figure height leaves either wasted space (wide domains) or
    # title overlap (tall domains) after map_renderer enforces equal aspect.
    # Keep the Step05 map lane fixed and derive only the Figure height from
    # the requested-domain aspect ratio.
    map_top = float(axes_cfg.get("top", axes_rect[1] + axes_rect[3]))
    map_bottom = float(axes_cfg.get("bottom", axes_rect[1]))
    map_height_fraction = map_top - map_bottom
    lon_span = domain.lon_max - domain.lon_min
    lat_span = domain.lat_max - domain.lat_min
    if map_height_fraction <= 0.0 or lon_span <= 0.0 or lat_span <= 0.0:
        raise Step05RenderError("STYLE_LOAD_FAILED", "Step05 map layout requires positive top/bottom and requested-domain spans.")
    map_width_inches = figsize[0] * axes_rect[2]
    computed_height_inches = (map_width_inches * lat_span / lon_span) / map_height_fraction
    figsize = (figsize[0], max(computed_height_inches, float(figure_cfg.get("minimum_height_inches", 0.0))))
    axes_rect = [axes_rect[0], map_bottom, axes_rect[2], map_height_fraction]
    colorbar_rect = [colorbar_rect[0], map_bottom, colorbar_rect[2], map_height_fraction]

    vector_sampling: dict[str, Any] | None = None
    render_fields = fields
    sampling_config = _adaptive_vector_sampling_config(layout, product_id)
    if sampling_config is not None:
        if vector_summary is None:
            raise Step05RenderError(
                "VECTOR_SAMPLING_INVALID",
                f"Adaptive vector sampling is configured for non-vector product_id={product_id}.",
            )
        axes_width_px = float(figsize[0]) * float(dpi) * float(axes_rect[2])
        axes_height_px = float(figsize[1]) * float(dpi) * float(axes_rect[3])
        sampled_u_values, sampled_v_values = _subset_vector_values(fields["u"], fields["v"], domain)
        vector_sampling = resolve_adaptive_wind_stride(
            nx=int(vector_summary["grid_nx"]),
            ny=int(vector_summary["grid_ny"]),
            axes_width_px=axes_width_px,
            axes_height_px=axes_height_px,
            target_min_spacing_px=sampling_config["target_min_spacing_px"],  # type: ignore[arg-type]
            u_values=sampled_u_values,
            v_values=sampled_v_values,
            max_rendered_vectors=sampling_config.get("max_rendered_vectors"),
        )
        vector_sampling, checkerboard_mask = resolve_operational_vector_sampling(
            product_id=product_id,
            adaptive_sampling=vector_sampling,
            u_values=sampled_u_values,
            v_values=sampled_v_values,
        )
        resolved_wind_stride = int(vector_sampling["resolved_wind_stride"])
        if checkerboard_mask is not None:
            # Keep the adaptive/cap decision intact, then replace only a
            # completed full-grid target-product draw with the approved periodic
            # original-grid subset.  No second cap pass is applied.
            resolved_wind_stride = 1
            render_fields = _checkerboard_masked_vector_fields(
                fields=fields,
                domain=domain,
                mask=checkerboard_mask,
            )
        # _resolve_product and the style merge above already created local copies.
        # Override only this renderer call; do not mutate the source product/config mapping.
        product = {
            **product,
            "wind_stride": resolved_wind_stride,
            "style": {**dict(product.get("style", {})), "wind_stride": resolved_wind_stride},
        }

    valid_time = _valid_time(init, forecast_hour)
    plt, mcolors, _pe = import_matplotlib()
    fig = plt.figure(figsize=figsize, dpi=dpi, facecolor="white")
    ax = fig.add_axes(axes_rect, facecolor="white")
    cax = fig.add_axes(colorbar_rect, facecolor="white")
    context = RenderContext(project_root=root, domain=domain, style=base_style)
    try:
        panel_info = draw_product_panel(
            ax,
            product=product,
            fields=render_fields,
            panel_title="",
            domain=domain,
            context=context,
            overview_mode=False,
        )
        mesh = panel_info.get("mesh")
        if mesh is None:
            raise Step05RenderError("FIELD_LOAD_FAILED", f"{product_id} renderer returned no shaded field mesh.")
        # A context-domain map is surrounding evidence, NOT proof that its
        # strongest signals overlap the immutable candidate footprint. Show
        # the core candidate explicitly, including when it is <1 native cell.
        candidate_roi_annotation = None
        candidate_roi_bbox = request.get("_candidate_roi_bbox")
        if isinstance(candidate_roi_bbox, dict):
            try:
                from matplotlib.patches import Rectangle

                roi_w = float(candidate_roi_bbox["east"]) - float(candidate_roi_bbox["west"])
                roi_h = float(candidate_roi_bbox["north"]) - float(candidate_roi_bbox["south"])
                if roi_w <= 0 or roi_h <= 0:
                    raise ValueError("invalid candidate ROI bounds")
                west, south = float(candidate_roi_bbox["west"]), float(candidate_roi_bbox["south"])
                ax.add_patch(Rectangle(
                    (west, south), roi_w, roi_h,
                    edgecolor="#C62828", facecolor="none", linewidth=1.8,
                    linestyle="--", zorder=80,
                ))
                ax.scatter([west + roi_w / 2], [south + roi_h / 2],
                           marker="+", c="#C62828", s=110, linewidths=1.5, zorder=81)
                candidate_roi_annotation = dict(candidate_roi_bbox)
            except (KeyError, TypeError, ValueError) as exc:
                raise Step05RenderError("CANDIDATE_ROI_INVALID", f"Invalid Step04 candidate ROI: {exc}") from exc
        rendered_extent = _rendered_extent(ax, domain)
        resolved_style = dict(panel_info.get("style", {}))
        precipitation_rendering = _configure_precipitation_semantics(
            ax,
            mesh,
            fields["shaded"],
            domain,
            product_id=product_id,
            layout=layout,
            field_summary=field_summary,
            mcolors=mcolors,
        )
        operational_scope_boundary = None
        if operational_scope_mask_array is not None:
            operational_scope_boundary = _draw_operational_scope_boundary(
                ax,
                field=fields["shaded"],
                operational_mask=operational_scope_mask_array,
                domain=domain,
            )
        missing_color = str(missing_cfg.get("bad_color", "#8C8C8C"))
        mesh.cmap.set_bad(missing_color)
        try:
            import numpy as np

            rendered_masked_cell_count = int(np.ma.getmaskarray(mesh.get_array()).sum())
        except Exception as exc:  # noqa: BLE001
            raise Step05RenderError("MISSING_RENDER_MISMATCH", f"Unable to inspect rendered missing mask: {exc}") from exc
        if rendered_masked_cell_count != int(field_summary.get("nan_count", 0)):
            raise Step05RenderError(
                "MISSING_RENDER_MISMATCH",
                "Rendered masked-cell count does not equal input NaN count; valid low values must not be rendered as missing.",
            )
        missing_rendering = {
            "mode": "solid_bad_color",
            "color": missing_color,
            "label_shown": bool(field_summary.get("nan_count", 0) > 0),
            "rendered_masked_cell_count": rendered_masked_cell_count,
        }
        cbar = fig.colorbar(
            mesh,
            cax=cax,
            ticks=resolved_style.get("ticks"),
            extend=str(resolved_style.get("extend", "max")),
        )
        colorbar_label = str(
            presentation.get("colorbar_label") or product.get("unit_label") or "mm"
        )
        if missing_rendering["label_shown"]:
            missing_label = str(missing_cfg.get("label", "gray: missing/NaN"))
            colorbar_label = f"{colorbar_label}\n{missing_label}"
        cbar.set_label(colorbar_label, fontsize=float(colorbar_cfg.get("label_fontsize", 10)), labelpad=float(colorbar_cfg.get("label_pad", 7)))
        cbar.ax.tick_params(labelsize=float(colorbar_cfg.get("tick_fontsize", 9)))
        palette = _palette_summary(
            product_id=product_id,
            layout=layout,
            resolved_style=resolved_style,
            mesh=mesh,
            mcolors=mcolors,
            precipitation_rendering=precipitation_rendering,
            missing_color=missing_color,
        )
        precipitation_rendering = _add_precipitation_semantics_legend(
            fig,
            precipitation_rendering=precipitation_rendering,
            missing_rendering=missing_rendering,
        )
        reference_vector = _add_vector_reference(ax, product_id=product_id, layout=layout)
        if candidate_roi_annotation is not None:
            fig.text(0.085, 0.048,
                     "※ 붉은 점선·십자: 후보 핵심영역 / 주변 지도 전체의 신호를 핵심영역의 중첩 근거로 간주하지 않습니다.",
                     ha="left", va="bottom", fontsize=8)
        contour_support = panel_info.get("contour_support")
        if isinstance(contour_support, dict) and contour_support.get("uses_neighbouring_native_grid"):
            fig.text(
                0.085, 0.025,
                "※ 등고선은 인접 원본 격자를 참고해 표시했으며 분석 영역·정량값은 변경하지 않았습니다.",
                ha="left", va="bottom", fontsize=8,
            )
        contour_annotation = presentation.get("contour_annotation")
        if isinstance(contour_annotation, dict) and str(contour_annotation.get("text", "")).strip():
            fig.text(
                float(contour_annotation.get("x", 0.845)),
                float(contour_annotation.get("y", 0.925)),
                str(contour_annotation["text"]),
                ha=str(contour_annotation.get("ha", "left")),
                va=str(contour_annotation.get("va", "bottom")),
                fontsize=float(contour_annotation.get("fontsize", 8.5)),
                bbox={
                    "facecolor": "white",
                    "edgecolor": "#B0B0B0",
                    "alpha": 0.94,
                    "boxstyle": "round,pad=0.25",
                },
            )
        title_artist = fig.text(
            float(title_cfg.get("x", 0.085)),
            float(title_cfg.get("main_y", 0.975)),
            str(product.get("display_title") or product_id),
            ha="left",
            va="top",
            fontsize=float(title_cfg.get("main_fontsize", 13)),
            fontweight=str(title_cfg.get("main_weight", "normal")),
        )
        info_artist = fig.text(
            float(title_cfg.get("x", 0.085)),
            float(title_cfg.get("info_y", 0.928)),
            f"초기시각 {_format_kst_datetime(_valid_time(init, 0))} | 유효시각 {_format_kst_datetime(valid_time)}",
            ha="left",
            va="top",
            fontsize=float(title_cfg.get("info_fontsize", 10.5)),
        )
        layout_validation = _layout_validation(
            fig,
            data_axes=ax,
            colorbar_axes=cax,
            title_artists={"title": title_artist, "metadata": info_artist},
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(
            output_path,
            dpi=dpi,
            facecolor="white",
            bbox_inches="tight",
            pad_inches=float(figure_cfg.get("outer_padding_mm", 2.0)) / 25.4,
        )
    except Step05RenderError:
        raise
    except ValueError as exc:
        raise Step05RenderError("ARRAY_SHAPE_MISMATCH", f"Map rendering failed due to incompatible field shape or coordinates: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise Step05RenderError("PNG_RENDER_FAILED", f"Unable to render PNG {output_path}: {exc}") from exc
    finally:
        plt.close(fig)

    try:
        from PIL import Image

        with Image.open(output_path) as image:
            pixel_size = image.size
    except Exception as exc:  # noqa: BLE001
        raise Step05RenderError("PNG_RENDER_FAILED", f"PNG was written but could not be inspected: {exc}") from exc

    vector_rendering_mode = panel_info.get("vector_rendering_mode")
    if (
        panel_info.get("quiver_calm_threshold_ms") == 0.0
        and panel_info.get("quiver_min_display_speed_ms") is None
        and panel_info.get("quiver_max_display_speed_ms") is None
    ):
        # The reused Step02 helper calls this mode "clipped" generically.
        # In this Step05 configuration no magnitude clipping or calm filter
        # is in effect; non-zero finite U/V values pass through unchanged.
        vector_rendering_mode = "actual_uv_magnitude_after_stride"

    return {
        "output_path": output_path,
        "pixel_size": pixel_size,
        "file_size_bytes": output_path.stat().st_size,
        "product_id": product_id,
        "unit_label": str(product.get("unit_label") or ""),
        "presentation": {
            "display_title": str(product.get("display_title") or product_id),
            "colorbar_label": colorbar_label,
            "contour_annotation": contour_annotation if isinstance(contour_annotation, dict) else None,
        },
        "palette": palette,
        "layout_validation": layout_validation,
        "feature_id": feature_id,
        "forecast_hour": int(forecast_hour),
        "valid_time_utc": valid_time.isoformat(),
        "candidate_roi_bbox": candidate_roi_annotation,
        "contour_support": panel_info.get("contour_support"),
        "requested_domain": {
            "west": domain.lon_min,
            "east": domain.lon_max,
            "south": domain.lat_min,
            "north": domain.lat_max,
        },
        "rendered_extent": rendered_extent,
        "field_value_summary": field_summary,
        "operational_scope_mask": operational_scope_mask,
        "operational_scope_boundary": operational_scope_boundary,
        "operational_scope_field_value_summary": operational_scope_field_value_summary,
        "operational_scope_region_summaries": operational_scope_region_summaries,
        "precipitation_object_policy": (
            {"version": "1.0.0", "threshold_6h_mm": 1.0, "connectivity": 8}
            if precipitation_objects is not None else None
        ),
        "precipitation_objects": precipitation_objects,
        "missing_rendering": missing_rendering,
        "precipitation_rendering": precipitation_rendering,
        "vector_summary": vector_summary,
        "vector_rendering": {
            "mode": vector_rendering_mode,
            "sampling_mode": vector_sampling.get("sampling_mode", "fixed_wind_stride") if vector_sampling else "fixed_wind_stride",
            "target_min_spacing_px": vector_sampling.get("target_min_spacing_px") if vector_sampling else None,
            "native_x_spacing_px": vector_sampling.get("native_x_spacing_px") if vector_sampling else None,
            "native_y_spacing_px": vector_sampling.get("native_y_spacing_px") if vector_sampling else None,
            "base_wind_stride": vector_sampling.get("base_wind_stride") if vector_sampling else None,
            "resolved_wind_stride": panel_info.get("resolved_wind_stride"),
            "rendered_vector_count": vector_sampling.get("rendered_vector_count") if vector_sampling else None,
            "max_rendered_vectors": vector_sampling.get("max_rendered_vectors") if vector_sampling else None,
            "sampling_geometry": vector_sampling.get("sampling_geometry") if vector_sampling else "regular_rectangular_stride_original_grid_subset",
            "checkerboard_applied": vector_sampling.get("checkerboard_applied") if vector_sampling else False,
            "checkerboard_parity": vector_sampling.get("checkerboard_parity") if vector_sampling else None,
            "source_grid_shape_yx": vector_sampling.get("source_grid_shape_yx") if vector_sampling else None,
            "source_grid_points": vector_sampling.get("source_grid_points") if vector_sampling else None,
            "selected_grid_point_count": vector_sampling.get("selected_grid_point_count") if vector_sampling else None,
            "source_vector_count": vector_summary.get("speed_finite_count") if vector_summary else panel_info.get("source_vector_count"),
            "active_vector_count_before_stride": panel_info.get("active_vector_count_before_stride"),
            "wind_stride": panel_info.get("resolved_wind_stride"),
            "quiver_scale": panel_info.get("quiver_scale"),
            "quiver_zorder": panel_info.get("quiver_zorder"),
            "quiver_calm_threshold_ms": panel_info.get("quiver_calm_threshold_ms"),
            "quiver_min_display_speed_ms": panel_info.get("quiver_min_display_speed_ms"),
            "quiver_max_display_speed_ms": panel_info.get("quiver_max_display_speed_ms"),
            "reference_vector": reference_vector,
            "longitude_tick_interval": panel_info.get("longitude_tick_interval"),
            "latitude_tick_interval": panel_info.get("latitude_tick_interval"),
        } if vector_summary is not None else None,
        "source_files": [logical_ref(root, Path(path)) for path in source_files],
    }
