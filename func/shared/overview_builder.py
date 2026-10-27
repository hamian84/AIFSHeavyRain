"""step03 overview direct renderer.

개요 이미지를 기존 PNG 재조합 없이 Matplotlib 다중 패널로 직접 생성한다.
동일 product overview 내부의 패널은 동일 렌더링 기준을 공유한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import Counter
import math
from typing import Any, Callable

from func.shared.broad_map_builder import _resolve_product_render_domain, _title_context, _load_raw_field  # noqa: SLF001
from func.shared.broad_map_style import resolve_render_style_config
from func.shared.map_renderer import RenderContext, draw_product_panel, import_matplotlib
from func.shared.runtime_paths import resolve_logical_ref


SourceLoader = Callable[[str, int], tuple[Any | None, list[dict[str, Any]], list[str], Any | None]]
RawLoader = Callable[[str, int | None, int], tuple[Any | None, list[dict[str, Any]], list[str]]]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _logical_source_asset_id(product_id: str, render_domain_id: str, forecast_hour: int) -> str:
    return f"PANEL_{product_id}_{render_domain_id}_F{int(forecast_hour):03d}"


def build_discovery_source_manifest(*, init: str, source_panel_config: dict[str, Any], root: Path) -> dict[str, Any]:
    """Create a logical source-panel manifest without generating individual PNG files."""
    source_panel_config, style_contract = resolve_render_style_config(root, source_panel_config)
    default_render_domain_id = str(
        source_panel_config.get("render_domain_id")
        or source_panel_config.get("default_domain_id")
        or "korea_surrounding"
    )
    default_render_domain_name = str(
        source_panel_config.get("render_domain_name_ko")
        or source_panel_config.get("default_domain_name_ko")
        or "한반도 주변 광역"
    )
    items: list[dict[str, Any]] = []
    product_forecast_hours: dict[str, list[int]] = {}
    tier_forecast_hours: dict[str, list[int]] = {}
    required_product_ids: list[str] = []

    for product in source_panel_config.get("products", []):
        if not isinstance(product, dict) or not product.get("enabled", True):
            continue
        if not bool(product.get("required_for_pass1", product.get("product_tier") != "supplementary")):
            continue
        product_id = str(product["product_id"])
        tier = str(product.get("product_tier", "temporal_core"))
        lead_hours = [int(x) for x in product.get("lead_hours", [])]
        product_forecast_hours[product_id] = lead_hours
        tier_forecast_hours.setdefault(tier, [])
        tier_forecast_hours[tier].extend(lead_hours)
        required_product_ids.append(product_id)
        render_domain_id = str(product.get("render_domain_id") or default_render_domain_id)
        render_domain_name = str(product.get("render_domain_name_ko") or default_render_domain_name)
        for lead in lead_hours:
            valid_time_utc = _title_context(init=init, lead=lead, valid_time_utc=None)["valid_time_utc"]
            items.append(
                {
                    "source_panel_id": _logical_source_asset_id(product_id, render_domain_id, lead),
                    "product_id": product_id,
                    "product_tier": tier,
                    "analysis_tier": tier,
                    "required_for_pass1": True,
                    "status": "declared",
                    "materialized_image": False,
                    "plot_type": product.get("plot_type"),
                    "forecast_hour": lead,
                    "valid_time_utc": valid_time_utc,
                    "render_domain_id": render_domain_id,
                    "render_domain_name_ko": render_domain_name,
                    "style_profile_id": str(product.get("style_profile_id", product_id)),
                    "title": str(product.get("display_title") or product.get("title") or product_id),
                }
            )
    return {
        "init": init,
        "manifest_type": "discovery_source_panel_manifest",
        "created_at_utc": now_utc(),
        "source_panel_count": len(items),
        "source_panels": items,
        "common_forecast_hours": sorted({int(item["forecast_hour"]) for item in items}),
        "product_forecast_hours": {key: sorted(set(value)) for key, value in sorted(product_forecast_hours.items())},
        "tier_forecast_hours": {key: sorted(set(value)) for key, value in sorted(tier_forecast_hours.items())},
        "required_pass1_product_ids": sorted(required_product_ids),
        "generated_from": "direct_overview_rendering",
        "style_contract": style_contract,
    }



def _load_panel_fields(
    *,
    product: dict[str, Any],
    forecast_hour: int,
    feature_array_loader: SourceLoader,
    raw_field_loader: RawLoader,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    plot_type = str(product.get("plot_type"))
    missing_all: list[dict[str, Any]] = []
    sources_all: list[str] = []

    if plot_type == "shaded_feature":
        arr, missing, sources, _spec = feature_array_loader(str(product["shaded_feature_id"]), forecast_hour)
        missing_all.extend(missing)
        sources_all.extend(sources)
        if arr is None:
            return None, missing_all, sources_all
        return {"shaded": arr}, missing_all, sources_all

    if plot_type == "shaded_feature_plus_raw_vector":
        arr, missing, sources, _spec = feature_array_loader(str(product["shaded_feature_id"]), forecast_hour)
        missing_all.extend(missing)
        sources_all.extend(sources)
        vector = product["vector_field"]
        u, m1, s1 = raw_field_loader(str(vector.get("u_short_name", "u")), vector.get("level"), forecast_hour)
        v, m2, s2 = raw_field_loader(str(vector.get("v_short_name", "v")), vector.get("level"), forecast_hour)
        missing_all.extend(m1 + m2)
        sources_all.extend(s1 + s2)
        if arr is None or u is None or v is None:
            return None, missing_all, sources_all
        return {"shaded": arr, "u": u, "v": v}, missing_all, sources_all

    if plot_type in {"raw_wind_speed_plus_raw_contour", "raw_wind_speed_shaded"}:
        vector = product["vector_field"]
        u, m1, s1 = raw_field_loader(str(vector.get("u_short_name", "u")), vector.get("level"), forecast_hour)
        v, m2, s2 = raw_field_loader(str(vector.get("v_short_name", "v")), vector.get("level"), forecast_hour)
        missing_all.extend(m1 + m2)
        sources_all.extend(s1 + s2)
        if plot_type == "raw_wind_speed_plus_raw_contour":
            contour, m3, s3 = _load_raw_field(raw_field_loader, product["contour_field"], forecast_hour)
            missing_all.extend(m3)
            sources_all.extend(s3)
            if u is None or v is None or contour is None:
                return None, missing_all, sources_all
            return {"u": u, "v": v, "contour": contour}, missing_all, sources_all
        if u is None or v is None:
            return None, missing_all, sources_all
        return {"u": u, "v": v}, missing_all, sources_all

    if plot_type == "raw_shaded_plus_raw_contour":
        shaded, m1, s1 = _load_raw_field(raw_field_loader, product["shaded_field"], forecast_hour)
        contour, m2, s2 = _load_raw_field(raw_field_loader, product["contour_field"], forecast_hour)
        missing_all.extend(m1 + m2)
        sources_all.extend(s1 + s2)
        if shaded is None or contour is None:
            return None, missing_all, sources_all
        return {"shaded": shaded, "contour": contour}, missing_all, sources_all

    if plot_type == "raw_contour_plus_raw_vector":
        contour, m1, s1 = _load_raw_field(raw_field_loader, product["contour_field"], forecast_hour)
        vector = product["vector_field"]
        u, m2, s2 = raw_field_loader(str(vector.get("u_short_name", "u")), vector.get("level"), forecast_hour)
        v, m3, s3 = raw_field_loader(str(vector.get("v_short_name", "v")), vector.get("level"), forecast_hour)
        missing_all.extend(m1 + m2 + m3)
        sources_all.extend(s1 + s2 + s3)
        if contour is None or u is None or v is None:
            return None, missing_all, sources_all
        return {"contour": contour, "u": u, "v": v}, missing_all, sources_all

    raise ValueError(f"unsupported overview plot_type: {plot_type}")



def _overview_title(*, product_id: str, product_name: str, group_id: str, forecast_hours: list[int]) -> str:
    return f"{product_id} | {product_name} | {group_id.upper()}"



def _panel_title(*, panel_index: int, panel_count: int, forecast_hour: int, valid_time_utc: str) -> str:
    """Return the panel timestamp in KST without redundant forecast-hour text."""
    try:
        parsed = datetime.fromisoformat(str(valid_time_utc).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        valid_kst = parsed.astimezone(timezone(timedelta(hours=9)))
        time_text = valid_kst.strftime("%Y-%m-%d %H:%M KST")
    except Exception:
        time_text = str(valid_time_utc).replace("T", " ").replace("+00:00", "")
    return f"{panel_index}/{panel_count} | {time_text}"



def _save_figure(fig: Any, path: Path, *, dpi: int) -> tuple[int, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi)
    # The saved PNG is the contract.  Read its metadata instead of inferring
    # dimensions from the figure size and DPI.
    from PIL import Image

    with Image.open(path) as image:
        width_px, height_px = image.size
        image.verify()
    return width_px, height_px


def enrich_source_manifest_with_overview_links(
    source_panel_manifest: dict[str, Any], overview_manifest: dict[str, Any]
) -> dict[str, Any]:
    """Attach direct-render provenance to each logical source panel."""
    overview_links: dict[str, dict[str, Any]] = {}
    for overview in overview_manifest.get("overviews", []):
        if not isinstance(overview, dict):
            continue
        for panel in overview.get("source_panels", []):
            if not isinstance(panel, dict):
                continue
            panel_id = str(panel.get("source_panel_id") or panel.get("source_asset_id") or "")
            if panel_id:
                overview_links[panel_id] = {
                    "overview_asset_id": overview.get("asset_id"),
                    "overview_image_path": overview.get("image_path"),
                    "overview_panel_index": panel.get("panel_index"),
                    "source_field_paths": list(panel.get("source_field_paths", [])),
                }

    for panel in source_panel_manifest.get("source_panels", []):
        if not isinstance(panel, dict):
            continue
        link = overview_links.get(str(panel.get("source_panel_id")))
        if link is None:
            panel["overview_asset_id"] = None
            panel["overview_image_path"] = None
            panel["overview_panel_index"] = None
            panel["source_field_paths"] = []
            continue
        panel.update(link)
    source_panel_manifest["overview_linked_source_panel_count"] = sum(
        1 for panel in source_panel_manifest.get("source_panels", [])
        if isinstance(panel, dict) and panel.get("overview_asset_id")
    )
    return source_panel_manifest


def build_discovery_overview_validation_report(
    *,
    root: Path,
    source_panel_manifest: dict[str, Any],
    overview_manifest: dict[str, Any],
    source_panel_config: dict[str, Any],
) -> dict[str, Any]:
    """Validate the emitted overview files and their logical-panel contract."""
    from PIL import Image

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    overviews = [item for item in overview_manifest.get("overviews", []) if isinstance(item, dict)]
    declared_panels = [item for item in source_panel_manifest.get("source_panels", []) if isinstance(item, dict)]
    declared_ids = {str(item.get("source_panel_id")) for item in declared_panels}
    observed_ids: list[str] = []
    actual_dimensions: dict[str, dict[str, int]] = {}
    product_counts = Counter(str(item.get("product_id")) for item in overviews)

    expected_overview_count = int(overview_manifest.get("expected_overview_count", 0))
    if len(overviews) != expected_overview_count:
        errors.append({"code": "OVERVIEW_COUNT_MISMATCH", "expected": expected_overview_count, "actual": len(overviews)})

    for overview in overviews:
        asset_id = str(overview.get("asset_id"))
        image_path = resolve_logical_ref(root, str(overview.get("image_path", "")))
        if not image_path.is_file():
            errors.append({"code": "OVERVIEW_PNG_MISSING", "asset_id": asset_id, "path": str(image_path)})
            continue
        try:
            with Image.open(image_path) as image:
                width_px, height_px = image.size
                if image.format != "PNG":
                    errors.append({"code": "OVERVIEW_NOT_PNG", "asset_id": asset_id, "format": image.format})
                image.verify()
        except Exception as exc:  # noqa: BLE001 - validation boundary
            errors.append({"code": "OVERVIEW_PNG_INVALID", "asset_id": asset_id, "reason": str(exc)})
            continue
        actual_dimensions[asset_id] = {"width_px": width_px, "height_px": height_px}
        if (width_px, height_px) != (int(overview.get("width_px", -1)), int(overview.get("height_px", -1))):
            errors.append({"code": "OVERVIEW_DIMENSION_MISMATCH", "asset_id": asset_id, "actual": [width_px, height_px], "manifest": [overview.get("width_px"), overview.get("height_px")]})
        panel_hours = [int(panel.get("forecast_hour", -1)) for panel in overview.get("source_panels", []) if isinstance(panel, dict)]
        if panel_hours != sorted(panel_hours):
            errors.append({"code": "OVERVIEW_FORECAST_ORDER_INVALID", "asset_id": asset_id, "forecast_hours": panel_hours})
        if len(panel_hours) != int(overview.get("panel_count", -1)):
            errors.append({"code": "OVERVIEW_PANEL_COUNT_INVALID", "asset_id": asset_id})
        for panel in overview.get("source_panels", []):
            if isinstance(panel, dict):
                observed_ids.append(str(panel.get("source_panel_id") or panel.get("source_asset_id") or ""))
        plot_type = str(next((item.get("plot_type") for item in source_panel_config.get("products", []) if isinstance(item, dict) and item.get("product_id") == overview.get("product_id")), ""))
        expected_colorbar = plot_type != "raw_contour_plus_raw_vector"
        if bool(overview.get("shared_colorbar")) != expected_colorbar:
            errors.append({"code": "OVERVIEW_COLORBAR_CONTRACT_INVALID", "asset_id": asset_id, "plot_type": plot_type, "expected_shared_colorbar": expected_colorbar})
        if not overview.get("style_profile_id") or not overview.get("render_domain_id"):
            errors.append({"code": "OVERVIEW_STYLE_OR_DOMAIN_MISSING", "asset_id": asset_id})

    duplicate_ids = sorted(panel_id for panel_id, count in Counter(observed_ids).items() if panel_id and count != 1)
    missing_ids = sorted(declared_ids - set(observed_ids))
    unknown_ids = sorted(set(observed_ids) - declared_ids)
    if duplicate_ids:
        errors.append({"code": "SOURCE_PANEL_DUPLICATED", "source_panel_ids": duplicate_ids})
    if missing_ids:
        errors.append({"code": "SOURCE_PANEL_NOT_MAPPED", "source_panel_ids": missing_ids})
    if unknown_ids:
        errors.append({"code": "UNKNOWN_SOURCE_PANEL", "source_panel_ids": unknown_ids})
    if any(not panel.get("source_field_paths") or not panel.get("overview_asset_id") for panel in declared_panels):
        errors.append({"code": "SOURCE_PANEL_PROVENANCE_OR_OVERVIEW_LINK_MISSING"})

    return {
        "init": overview_manifest.get("init"),
        "report_type": "discovery_overview_validation_report",
        "created_at_utc": now_utc(),
        "validation_status": "pass" if not errors else "fail",
        "overview_count": len(overviews),
        "expected_overview_count": expected_overview_count,
        "product_overview_counts": dict(sorted(product_counts.items())),
        "source_panel_count": len(declared_panels),
        "mapped_source_panel_count": len(observed_ids),
        "actual_png_dimensions": actual_dimensions,
        "render_error_count": int(overview_manifest.get("error_count", 0)),
        "style_contract_validation": "pass" if not errors else "fail",
        "errors": errors,
        "warnings": warnings,
    }



def build_discovery_overviews(
    *,
    init: str,
    root: Path,
    output_dir: Path,
    source_panel_config: dict[str, Any],
    relpath_func: Callable[[Path], str],
    overview_policy: dict[str, Any],
    feature_array_loader: SourceLoader,
    raw_field_loader: RawLoader,
) -> dict[str, Any]:
    source_panel_config, style_contract = resolve_render_style_config(root, source_panel_config)
    default_style = dict(source_panel_config.get("default_style", {}))
    products = {
        str(item["product_id"]): item
        for item in source_panel_config.get("products", [])
        if isinstance(item, dict) and item.get("enabled", True)
           and bool(item.get("required_for_pass1", item.get("product_tier") != "supplementary"))
    }

    plt, _mcolors, _pe = import_matplotlib()
    panel_columns = int(overview_policy.get("panel_columns", 2))
    tiers = overview_policy.get("tiers", {})
    out_dir = output_dir / "discovery_overviews"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("OV_*.png"):
        old.unlink()

    overviews: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for tier_name, tier_cfg in tiers.items():
        product_ids = [pid for pid in tier_cfg.get("product_ids", []) if pid in products] if isinstance(tier_cfg, dict) else []
        if not product_ids:
            product_ids = [pid for pid, product in products.items() if str(product.get("product_tier", "temporal_core")) == str(tier_name)]
        groups = tier_cfg.get("groups", []) if isinstance(tier_cfg, dict) else []
        for product_id in product_ids:
            product = products[product_id]
            render_domain_id, render_domain_name_ko, product_domain, product_domain_bbox = _resolve_product_render_domain(product, source_panel_config)
            context = RenderContext(project_root=root, domain=product_domain, style=default_style)
            product_name = str(product.get("display_title") or product.get("title") or product_id)
            for group in groups:
                forecast_hours = [int(value) for value in group.get("forecast_hours", [])]
                if not forecast_hours:
                    continue
                group_id = str(group.get("group_id", "gxx")).upper()
                panel_count = len(forecast_hours)
                panel_rows = int(math.ceil(panel_count / panel_columns))
                uses_shared_colorbar = str(product.get("plot_type")) != "raw_contour_plus_raw_vector"
                # Contour/vector products do not have a colorbar.  Let their
                # panels use the otherwise reserved right-hand lane.
                fig = plt.figure(figsize=(13.4, 4.65 * panel_rows + 0.72))
                grid_top = 0.890 if panel_rows == 1 else 0.930
                panel_hspace = 0.06 if panel_rows > 1 else 0.0
                grid_right = 0.925 if uses_shared_colorbar else 0.975
                gridspec = fig.add_gridspec(panel_rows, panel_columns, left=0.035, right=grid_right, top=grid_top, bottom=0.055, wspace=0.055, hspace=panel_hspace)
                axes: list[Any] = []
                for idx in range(panel_rows * panel_columns):
                    ax = fig.add_subplot(gridspec[idx // panel_columns, idx % panel_columns])
                    axes.append(ax)
                panel_meta: list[dict[str, Any]] = []
                first_mesh = None
                first_style: dict[str, Any] | None = None
                map_backgrounds: list[dict[str, Any]] = []
                failed = False
                for idx, lead in enumerate(forecast_hours, start=1):
                    ax = axes[idx - 1]
                    title_context = _title_context(init=init, lead=lead, valid_time_utc=None)
                    valid_time_utc = str(title_context["valid_time_utc"])
                    fields, missing, sources = _load_panel_fields(
                        product=product,
                        forecast_hour=lead,
                        feature_array_loader=feature_array_loader,
                        raw_field_loader=raw_field_loader,
                    )
                    if fields is None:
                        failed = True
                        errors.append(
                            {
                                "code": "OVERVIEW_PANEL_INPUT_MISSING",
                                "product_id": product_id,
                                "group_id": group_id,
                                "forecast_hour": lead,
                                "missing": missing,
                            }
                        )
                        ax.set_axis_off()
                        continue
                    panel_info = draw_product_panel(
                        ax,
                        product=product,
                        fields=fields,
                        panel_title=_panel_title(
                            panel_index=idx,
                            panel_count=panel_count,
                            forecast_hour=lead,
                            valid_time_utc=valid_time_utc,
                        ),
                        domain=product_domain,
                        context=context,
                        overview_mode=True,
                    )
                    # In a two-row overview the upper-row longitude labels are
                    # duplicated by the lower row and collide with its titles.
                    if panel_rows > 1 and idx <= panel_columns:
                        ax.set_xlabel("")
                        ax.set_xticks([])
                    if first_mesh is None and panel_info.get("mesh") is not None:
                        first_mesh = panel_info["mesh"]
                        first_style = panel_info.get("style")
                    map_backgrounds.append(panel_info.get("map_background", {}))
                    source_asset_id = _logical_source_asset_id(product_id, render_domain_id, lead)
                    panel_meta.append(
                        {
                            "panel_index": idx,
                            "forecast_hour": lead,
                            "valid_time_utc": valid_time_utc,
                            "panel_title": _panel_title(
                                panel_index=idx,
                                panel_count=panel_count,
                                forecast_hour=lead,
                                valid_time_utc=valid_time_utc,
                            ),
                            "source_asset_id": source_asset_id,
                            "source_panel_id": source_asset_id,
                            "source_field_paths": sources,
                        }
                    )
                for ax in axes[panel_count:]:
                    ax.set_axis_off()
                if uses_shared_colorbar and first_mesh is not None and first_style is not None:
                    cax = fig.add_axes([0.935, 0.115, 0.016, 0.73])
                    cbar = fig.colorbar(first_mesh, cax=cax, orientation='vertical', extend=first_style.get('extend', 'neither'))
                    cbar.set_label(str(product.get('unit_label', '')), fontsize=10)
                    ticks = first_style.get('ticks')
                    if ticks:
                        cbar.set_ticks(ticks)
                elif uses_shared_colorbar:
                    warnings.append(
                        {
                            "code": "OVERVIEW_SHARED_COLORBAR_NOT_USED",
                            "product_id": product_id,
                            "group_id": group_id,
                            "reason": "contour_plus_vector_product" if str(product.get('plot_type')) == 'raw_contour_plus_raw_vector' else 'no_mesh',
                        }
                    )
                fig.suptitle(_overview_title(product_id=product_id, product_name=product_name, group_id=group_id, forecast_hours=forecast_hours), fontsize=13, x=0.035, y=0.975, ha='left')
                overview_id = f"OV_{product_id}_{group_id}"
                image_path = out_dir / f"{overview_id}.png"
                width_px, height_px = _save_figure(fig, image_path, dpi=int(default_style.get('dpi', 150)))
                plt.close(fig)
                overviews.append(
                    {
                        "asset_id": overview_id,
                        "overview_id": overview_id,
                        "status": "created" if not failed else "created_with_missing_panels",
                        "required_for_initial_pass1": True,
                        "product_id": product_id,
                        "product_name_ko": product_name,
                        "analysis_tier": str(product.get("product_tier", tier_name)),
                        "cadence_hours": int(product.get("cadence_hours", 6)),
                        "group_id": group_id,
                        "forecast_hour_start": forecast_hours[0],
                        "forecast_hour_end": forecast_hours[-1],
                        "forecast_hours": forecast_hours,
                        "valid_times_utc": [item["valid_time_utc"] for item in panel_meta],
                        "source_asset_ids": [item["source_asset_id"] for item in panel_meta],
                        "source_panels": panel_meta,
                        "panel_count": panel_count,
                        "panel_columns": panel_columns,
                        "panel_rows": panel_rows,
                        "image_path": relpath_func(image_path),
                        "width_px": width_px,
                        "height_px": height_px,
                        "render_domain_id": render_domain_id,
                        "render_domain_name_ko": render_domain_name_ko,
                        "render_domain_bbox": product_domain_bbox,
                        "projection_type": str(first_style.get("projection_type", default_style.get("projection_type", "equirectangular_lonlat"))) if first_style else str(default_style.get("projection_type", "equirectangular_lonlat")),
                        "coordinate_reference": str(first_style.get("coordinate_reference", default_style.get("coordinate_reference", "longitude_latitude_degrees"))) if first_style else str(default_style.get("coordinate_reference", "longitude_latitude_degrees")),
                        "shared_colorbar": first_mesh is not None,
                        "unit_label": str(product.get("unit_label", "")),
                        "full_title": _overview_title(product_id=product_id, product_name=product_name, group_id=group_id, forecast_hours=forecast_hours),
                        "map_background": map_backgrounds[0] if map_backgrounds else {},
                        "style_profile_id": str(product.get("style_profile_id", product_id)),
                    }
                )

    expected_count = int(overview_policy.get("initial_attachment_count", len(overviews)))
    validation_status = "pass" if len(overviews) == expected_count and not errors else "fail"
    return {
        "init": init,
        "manifest_type": "discovery_overview_manifest",
        "created_at_utc": now_utc(),
        "overview_count": len(overviews),
        "expected_overview_count": expected_count,
        "overviews": overviews,
        "generated": True,
        "rendering_mode": "direct_matplotlib_multipanel",
        "validation_status": validation_status,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "style_contract": style_contract,
    }
