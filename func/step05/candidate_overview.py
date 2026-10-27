"""Build Step05 candidate-specific overview images for Step10 web presentation.

These overviews are rendered from raw/model-derived fields, not copied from
Step02. For every candidate, one operational Step02 time-group asset is selected
for each available overview variable according to the candidate time window.
The original Step02 panel layout is preserved, and the candidate domain is
highlighted on relevant panels.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import math
from typing import Any

from func.shared import aifs_data
from func.step02.aifs_feature_engine import load_yaml_mapping
from func.shared.broad_map_builder import _resolve_product_render_domain, _title_context, _load_raw_field  # noqa: SLF001
from func.shared.broad_map_style import resolve_render_style_config
from func.shared.map_renderer import MapDomain, RenderContext, draw_product_panel, import_matplotlib
from func.shared.overview_builder import _load_panel_fields, _save_figure
from func.shared.pipeline_common import rel, sha256sum, write_json
from func.shared.runtime_paths import resolve_step04_output_dir, runtime_path
from func.step05.diagnostics import Step05DiagnosticError, physical_products_for


def _load_json(path: Path) -> dict[str, Any]:
    import json

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise Step05DiagnosticError("STEP05_OVERVIEW_INPUT_MISSING", f"Required input file is missing: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise Step05DiagnosticError("STEP05_OVERVIEW_INPUT_INVALID", f"Unable to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise Step05DiagnosticError("STEP05_OVERVIEW_INPUT_INVALID", f"JSON root must be a mapping: {path}")
    return value


def _step04_result_path(root: Path, init: str) -> Path:
    try:
        return resolve_step04_output_dir(root, init) / "pass1_result.json"
    except FileNotFoundError as exc:
        raise Step05DiagnosticError("STEP05_OVERVIEW_INPUT_MISSING", str(exc)) from exc
    except (RuntimeError, ValueError, OSError) as exc:
        raise Step05DiagnosticError("STEP05_OVERVIEW_INPUT_INVALID", str(exc)) from exc


def _valid_candidate_bbox(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    required = ("west", "east", "south", "north")
    try:
        bbox = {key: float(value[key]) for key in required}
    except (KeyError, TypeError, ValueError):
        return None
    if bbox["west"] >= bbox["east"] or bbox["south"] >= bbox["north"]:
        return None
    return bbox


def _domain_from_bbox(bbox: dict[str, float]) -> MapDomain:
    return MapDomain(
        lon_min=float(bbox["west"]),
        lon_max=float(bbox["east"]),
        lat_min=float(bbox["south"]),
        lat_max=float(bbox["north"]),
    )


def _format_kst(iso_value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(iso_value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")
    except Exception:
        return str(iso_value)


def _panel_title(*, panel_index: int, panel_count: int, valid_time_utc: str) -> str:
    return f"{panel_index}/{panel_count} | {_format_kst(valid_time_utc)}"


def _overview_title(*, product_id: str, product_name: str, candidate_name: str) -> str:
    return f"{product_id} | {product_name} | {candidate_name}"


def _candidate_name(candidate: dict[str, Any]) -> str:
    value = str(candidate.get("candidate_name") or "").strip()
    return value or str(candidate.get("candidate_id") or "감시후보")


def _request_hours_by_candidate_product(step04: dict[str, Any]) -> dict[str, dict[str, set[int]]]:
    result: dict[str, dict[str, set[int]]] = {}
    for request in step04.get("analysis_data_requests", []):
        if not isinstance(request, dict) or str(request.get("data_type") or "") != "spatial_field":
            continue
        candidate_id = str(request.get("candidate_id") or "")
        if not candidate_id:
            continue
        hours_raw = request.get("forecast_hours")
        if not isinstance(hours_raw, list):
            continue
        try:
            hours = {int(value) for value in hours_raw}
        except (TypeError, ValueError):
            continue
        by_product = result.setdefault(candidate_id, {})
        try:
            products = physical_products_for(request)
        except Exception:
            product_id = str(request.get("product_id") or "")
            products = (product_id,) if product_id else ()
        for product_id in products:
            by_product.setdefault(str(product_id), set()).update(hours)
    return result


def _candidate_window(candidate: dict[str, Any]) -> tuple[int, int] | None:
    window = candidate.get("time_window")
    if not isinstance(window, dict):
        return None
    try:
        start = int(window["forecast_hour_start"])
        end = int(window["forecast_hour_end"])
    except (KeyError, TypeError, ValueError):
        return None
    if start > end:
        return None
    return start, end


def _asset_forecast_hours(asset: dict[str, Any]) -> list[int]:
    raw = asset.get("forecast_hours")
    if isinstance(raw, list):
        values: list[int] = []
        for item in raw:
            try:
                values.append(int(item))
            except (TypeError, ValueError):
                continue
        if values:
            return sorted(set(values))
    try:
        start = int(asset["forecast_hour_start"])
        end = int(asset["forecast_hour_end"])
    except (KeyError, TypeError, ValueError):
        return []
    if start > end:
        return []
    return [start] if start == end else [start, end]


def _select_candidate_overview_assets(
    candidate: dict[str, Any],
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Choose one existing Step02 time-group image for every overview variable.

    The candidate's existing Step04 time_window is the target.  For each
    product, the Step02 asset containing the largest number of forecast hours
    inside that window is selected.  This preserves each product's operational
    Step02 time grouping while exposing all available overview variables.
    """
    window = _candidate_window(candidate)
    if window is None:
        related = {str(value) for value in candidate.get("related_overview_asset_ids", [])}
        return sorted(
            [row for row in assets if str(row.get("asset_id") or "") in related],
            key=lambda row: (str(row.get("product_id") or ""), str(row.get("asset_id") or "")),
        )
    start, end = window
    by_product: dict[str, list[dict[str, Any]]] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        product_id = str(asset.get("product_id") or "")
        if not product_id:
            continue
        by_product.setdefault(product_id, []).append(asset)

    selected: list[dict[str, Any]] = []
    for product_id in sorted(by_product):
        scored: list[tuple[tuple[int, int, float, int, str], dict[str, Any]]] = []
        midpoint = (start + end) / 2.0
        for asset in by_product[product_id]:
            hours = _asset_forecast_hours(asset)
            in_window = [hour for hour in hours if start <= hour <= end]
            try:
                asset_start = int(asset.get("forecast_hour_start"))
                asset_end = int(asset.get("forecast_hour_end"))
            except (TypeError, ValueError):
                if hours:
                    asset_start, asset_end = min(hours), max(hours)
                else:
                    continue
            overlap = max(0, min(end, asset_end) - max(start, asset_start))
            asset_midpoint = (asset_start + asset_end) / 2.0
            score = (
                len(in_window),
                overlap,
                -abs(asset_midpoint - midpoint),
                -asset_start,
                str(asset.get("asset_id") or ""),
            )
            scored.append((score, asset))
        if not scored:
            continue
        score, best = max(scored, key=lambda item: item[0])
        if score[0] <= 0 and score[1] <= 0:
            continue
        selected.append(best)
    return selected


def _candidate_highlight_hours(
    candidate: dict[str, Any],
    asset: dict[str, Any],
    requested_hours: set[int],
) -> list[int]:
    asset_hours = _asset_forecast_hours(asset)
    requested = sorted(hour for hour in asset_hours if hour in requested_hours)
    if requested:
        return requested
    window = _candidate_window(candidate)
    if window is None:
        return []
    start, end = window
    return [hour for hour in asset_hours if start <= hour <= end]


def _draw_candidate_outline(ax: Any, candidate_bbox: dict[str, float], render_domain: MapDomain, *, label: str = "") -> None:
    west = max(float(candidate_bbox["west"]), float(render_domain.lon_min))
    east = min(float(candidate_bbox["east"]), float(render_domain.lon_max))
    south = max(float(candidate_bbox["south"]), float(render_domain.lat_min))
    north = min(float(candidate_bbox["north"]), float(render_domain.lat_max))
    if west >= east or south >= north:
        return
    _plt, _mcolors, pe = import_matplotlib()
    from matplotlib.patches import Rectangle

    width = east - west
    height = north - south
    patch = Rectangle(
        (west, south),
        width,
        height,
        linewidth=2.0,
        edgecolor="#d62828",
        facecolor="none",
        linestyle="solid",
        zorder=20,
    )
    patch.set_path_effects([
        pe.Stroke(linewidth=3.4, foreground="white", alpha=0.95),
        pe.Normal(),
    ])
    ax.add_patch(patch)
    label_y = min(render_domain.lat_max - 0.02 * (render_domain.lat_max - render_domain.lat_min), north)
    if label:
        ax.text(
            west,
            label_y,
            label,
            fontsize=8,
            color="#b42318",
            ha="left",
            va="bottom",
            zorder=21,
            bbox={
                "facecolor": "white",
                "edgecolor": "#d62828",
                "alpha": 0.92,
                "boxstyle": "round,pad=0.18",
                "linewidth": 0.9,
            },
        )


def build_step05_candidate_overviews(*, root: Path, init: str) -> dict[str, Any]:
    step04 = _load_json(_step04_result_path(root, init))
    if step04.get("candidates") == []:
        out_dir = runtime_path(root, "daio", init, "step05", "overview")
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in out_dir.glob("STEP05_OV_*.png"):
            old.unlink()
        manifest = {
            "schema_version": "1.0.0",
            "manifest_type": "step05_candidate_overview_manifest",
            "init": init,
            "asset_count": 0,
            "candidate_overview_assets": [],
            "error_count": 0,
            "errors": [],
        }
        write_json(runtime_path(root, "daio", init, "step05", "candidate_overview_data.json"), manifest)
        return manifest

    step02 = _load_json(runtime_path(root, "daio", init, "step02", "overview_data.json"))
    source_config_path = runtime_path(root, "daba", "ai_pass1", "discovery_source_panel_products.yaml")
    try:
        source_config = load_yaml_mapping(source_config_path)
        source_config, _style_contract = resolve_render_style_config(root, source_config)
    except Exception as exc:  # noqa: BLE001
        raise Step05DiagnosticError("STYLE_LOAD_FAILED", f"Unable to load overview style configuration: {exc}") from exc

    products = {
        str(item.get("product_id")): dict(item)
        for item in source_config.get("products", [])
        if isinstance(item, dict) and item.get("enabled", True)
    }
    overview_assets = {
        str(item.get("asset_id")): item
        for item in step02.get("overview_assets", [])
        if isinstance(item, dict) and isinstance(item.get("asset_id"), str)
    }
    candidates = [item for item in step04.get("candidates", []) if isinstance(item, dict)]
    selected_hours = _request_hours_by_candidate_product(step04)

    raw_dir = runtime_path(root, "dain", init, "raw", "aifs")
    if not raw_dir.is_dir() or not aifs_data.list_grib_files(raw_dir):
        raise Step05DiagnosticError(
            "GRIB_INPUT_MISSING",
            f"No AIFS GRIB input is available under {raw_dir}. Check dain/<init>/raw/aifs.",
        )
    try:
        index, _raw_loader, np_maps, xr_maps = aifs_data.build_loaders(root, init, raw_dir)
    except Exception as exc:  # noqa: BLE001
        raise Step05DiagnosticError("FIELD_LOAD_FAILED", f"Unable to index or open AIFS GRIB input: {exc}") from exc
    if not index:
        raise Step05DiagnosticError("GRIB_INPUT_MISSING", f"No usable AIFS GRIB fields were indexed from {raw_dir}.")
    specs_by_id = {spec.feature_id: spec for spec in aifs_data.load_feature_definitions(runtime_path(root, "daba", "ai_pass1", "feature_definitions.yaml"))}

    def feature_array_loader(feature_id: str, step: int):
        spec = specs_by_id.get(feature_id)
        if spec is None:
            return None, [{"feature_id": feature_id, "forecast_hour": step, "reason": "unknown feature_id"}], [], None
        arr, missing, src = aifs_data.build_feature_array(spec=spec, step=step, index=index, np=np_maps, xr=xr_maps)
        return arr, missing, [rel(root, Path(p)) for p in src], spec

    def raw_field_loader(short_name: str, level: int | None, step: int):
        source_path = index.get((short_name, level, step))
        if source_path is None:
            return None, [{"short_name": short_name, "level": level, "forecast_hour": step, "reason": "raw field not found"}], []
        arr = aifs_data.open_field(source_path, short_name, level, np_maps, xr_maps)
        return arr, [], [rel(root, source_path)]

    default_style = dict(source_config.get("default_style", {}))
    plt, _mcolors, _pe = import_matplotlib()
    panel_columns = 2
    out_dir = runtime_path(root, "daio", init, "step05", "overview")
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("STEP05_OV_*.png"):
        old.unlink()

    created_assets: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id:
            continue
        candidate_bbox = _valid_candidate_bbox(candidate.get("candidate_area"))
        if candidate_bbox is None:
            errors.append({"code": "CANDIDATE_BBOX_INVALID", "candidate_id": candidate_id})
            continue
        candidate_name = _candidate_name(candidate)
        by_product = selected_hours.get(candidate_id, {})
        candidate_assets = _select_candidate_overview_assets(candidate, list(overview_assets.values()))
        for asset_meta in candidate_assets:
            source_asset_id = str(asset_meta.get("asset_id") or "")
            if not source_asset_id:
                continue
            product_id = str(asset_meta.get("product_id") or "")
            product = products.get(product_id)
            if product is None:
                errors.append({"code": "PRODUCT_CONFIG_MISSING", "candidate_id": candidate_id, "asset_id": str(source_asset_id), "product_id": product_id})
                continue
            asset_hours = _asset_forecast_hours(asset_meta)
            selected_set = set(by_product.get(product_id, set()))
            selected = _candidate_highlight_hours(candidate, asset_meta, selected_set)
            if not selected:
                continue
            display_hours = list(asset_hours) if asset_hours else list(selected)
            render_product = dict(product)
            if asset_meta.get("render_domain_id"):
                render_product["render_domain_id"] = asset_meta.get("render_domain_id")
            if asset_meta.get("render_domain_name_ko"):
                render_product["render_domain_name_ko"] = asset_meta.get("render_domain_name_ko")
            render_domain_id, render_domain_name_ko, product_domain, product_domain_bbox = _resolve_product_render_domain(render_product, source_config)
            context = RenderContext(project_root=root, domain=product_domain, style=default_style)
            product_name = str(asset_meta.get("product_name_ko") or render_product.get("display_title") or product_id)
            panel_count = len(display_hours)
            panel_rows = int(math.ceil(panel_count / panel_columns))
            uses_shared_colorbar = str(render_product.get("plot_type")) != "raw_contour_plus_raw_vector"
            fig = plt.figure(figsize=(13.4, 4.65 * panel_rows + 0.72))
            grid_top = 0.890 if panel_rows == 1 else 0.930
            panel_hspace = 0.06 if panel_rows > 1 else 0.0
            grid_right = 0.925 if uses_shared_colorbar else 0.975
            gridspec = fig.add_gridspec(panel_rows, panel_columns, left=0.035, right=grid_right, top=grid_top, bottom=0.055, wspace=0.055, hspace=panel_hspace)
            axes = [fig.add_subplot(gridspec[idx // panel_columns, idx % panel_columns]) for idx in range(panel_rows * panel_columns)]
            panel_meta: list[dict[str, Any]] = []
            map_backgrounds: list[dict[str, Any]] = []
            first_mesh = None
            first_style = None
            failed = False
            for idx, hour in enumerate(display_hours, start=1):
                ax = axes[idx - 1]
                valid_time_utc = str(_title_context(init=init, lead=hour, valid_time_utc=None)["valid_time_utc"])
                fields, missing, sources = _load_panel_fields(
                    product=render_product,
                    forecast_hour=hour,
                    feature_array_loader=feature_array_loader,
                    raw_field_loader=raw_field_loader,
                )
                if fields is None:
                    failed = True
                    errors.append({
                        "code": "OVERVIEW_PANEL_INPUT_MISSING",
                        "candidate_id": candidate_id,
                        "asset_id": str(source_asset_id),
                        "product_id": product_id,
                        "forecast_hour": hour,
                        "missing": missing,
                    })
                    ax.set_axis_off()
                    continue
                panel_info = draw_product_panel(
                    ax,
                    product=render_product,
                    fields=fields,
                    panel_title=_panel_title(panel_index=idx, panel_count=panel_count, valid_time_utc=valid_time_utc),
                    domain=product_domain,
                    context=context,
                    overview_mode=True,
                )
                if int(hour) in selected_set:
                    _draw_candidate_outline(ax, candidate_bbox, product_domain)
                if panel_rows > 1 and idx <= panel_columns:
                    ax.set_xlabel("")
                    ax.set_xticks([])
                if first_mesh is None and panel_info.get("mesh") is not None:
                    first_mesh = panel_info["mesh"]
                    first_style = panel_info.get("style")
                map_backgrounds.append(panel_info.get("map_background", {}))
                panel_meta.append({
                    "panel_index": idx,
                    "forecast_hour": int(hour),
                    "valid_time_utc": valid_time_utc,
                    "panel_title": _panel_title(panel_index=idx, panel_count=panel_count, valid_time_utc=valid_time_utc),
                    "source_overview_asset_id": str(source_asset_id),
                    "source_field_paths": sources,
                    "candidate_outline_applied": int(hour) in selected_set,
                })
            for ax in axes[panel_count:]:
                ax.set_axis_off()
            if uses_shared_colorbar and first_mesh is not None and first_style is not None:
                cax = fig.add_axes([0.935, 0.115, 0.016, 0.73])
                cbar = fig.colorbar(first_mesh, cax=cax, orientation="vertical", extend=first_style.get("extend", "neither"))
                cbar.set_label(str(render_product.get("unit_label", "")), fontsize=10)
                ticks = first_style.get("ticks")
                if ticks:
                    cbar.set_ticks(ticks)
            fig.suptitle(_overview_title(product_id=product_id, product_name=product_name, candidate_name=candidate_name), fontsize=13, x=0.035, y=0.975, ha="left")
            file_name = f"STEP05_OV_{candidate_id}_{product_id}_{str(asset_meta.get('group_id') or 'GXX')}.png"
            image_path = out_dir / file_name
            width_px, height_px = _save_figure(fig, image_path, dpi=int(default_style.get("dpi", 150)))
            plt.close(fig)
            created_assets.append({
                "asset_id": f"STEP05_OV_{candidate_id}_{product_id}_{str(asset_meta.get('group_id') or 'GXX')}",
                "candidate_id": candidate_id,
                "candidate_name": candidate_name,
                "source_overview_asset_id": str(source_asset_id),
                "status": "created" if not failed else "created_with_missing_panels",
                "product_id": product_id,
                "product_name_ko": product_name,
                "group_id": str(asset_meta.get("group_id") or "GXX"),
                "forecast_hour_start": display_hours[0],
                "forecast_hour_end": display_hours[-1],
                "forecast_hours": display_hours,
                "highlight_forecast_hours": selected,
                "valid_times_utc": [item["valid_time_utc"] for item in panel_meta],
                "source_panels": panel_meta,
                "panel_count": panel_count,
                "panel_columns": panel_columns,
                "panel_rows": panel_rows,
                "image_path": rel(root, image_path),
                "width_px": width_px,
                "height_px": height_px,
                "render_domain_id": render_domain_id,
                "render_domain_name_ko": render_domain_name_ko,
                "render_domain_bbox": product_domain_bbox,
                "candidate_bbox": candidate_bbox,
                "shared_colorbar": first_mesh is not None,
                "unit_label": str(render_product.get("unit_label", "")),
                "full_title": _overview_title(product_id=product_id, product_name=product_name, candidate_name=candidate_name),
                "map_background": map_backgrounds[0] if map_backgrounds else {},
                "style_profile_id": str(render_product.get("style_profile_id", product_id)),
                "file_name": file_name,
                "size_bytes": image_path.stat().st_size,
                "sha256": sha256sum(image_path),
            })

    manifest = {
        "schema_version": "1.0.0",
        "manifest_type": "step05_candidate_overview_manifest",
        "init": init,
        "asset_count": len(created_assets),
        "candidate_overview_assets": created_assets,
        "error_count": len(errors),
        "errors": errors,
    }
    write_json(runtime_path(root, "daio", init, "step05", "candidate_overview_data.json"), manifest)
    return manifest
