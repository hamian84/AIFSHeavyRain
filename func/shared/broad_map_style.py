"""Broad-map 렌더링 style profile 로딩 및 산출물 계약 검증."""

from __future__ import annotations

import copy
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from func.shared.runtime_paths import logical_ref, resolve_logical_ref

import yaml
from PIL import Image


SUPPORTED_STYLE_KEYS = {
    "shape_search_dirs", "dpi", "figsize", "colorbar_orientation",
    "wind_stride", "quiver_scale", "quiver_width", "quiver_key_enabled",
    "quiver_key_position", "quiver_key_x", "quiver_key_y", "quiver_key_length",
    "quiver_key_labelpos", "quiver_key_coordinates", "quiver_key_label",
    "halo_enabled", "halo_foreground", "halo_linewidth_text", "halo_linewidth_line",
    "contour_levels", "contour_linewidth", "contour_halo_linewidth",
    "contour_color", "contour_halo_color", "contour_label_fontsize",
    "contour_label_halo_linewidth", "contour_label_bbox_alpha",
    "title_fontsize", "title_pad", "title_linespacing", "title_loc",
    "title_main_template", "title_info_lines", "levels", "ticks", "colors",
    "extend", "mask_below", "cmap", "vmin", "vmax", "percentile_min",
    "percentile_max", "map_halo_enabled", "map_halo_color",
    "map_halo_linewidth", "map_line_color", "map_linewidth",
    "map_line_alpha", "map_line_zorder", "grid_color", "grid_linestyle",
    "grid_linewidth", "grid_alpha", "style_notes",
    "projection_type", "coordinate_reference", "longitude_axis_label",
    "latitude_axis_label", "tick_degree_symbol",
    "longitude_tick_interval", "latitude_tick_interval",
    "include_domain_boundary_ticks",
    "quiver_calm_threshold_ms", "quiver_min_display_speed_ms",
    "quiver_max_display_speed_ms",
    "coastline_color", "coastline_linewidth", "coastline_alpha",
    "coastline_zorder", "coastline_halo_color", "coastline_halo_linewidth",
    "border_color", "border_linewidth", "border_alpha", "border_zorder",
    "border_halo_color", "border_halo_linewidth",
    "admin1_color", "admin1_linewidth", "admin1_alpha", "admin1_zorder",
    "admin1_halo_color", "admin1_halo_linewidth",
}

PRODUCT_KEYS = {
    "product_id", "style_profile_id", "product_tier", "analysis_tier", "required_for_pass1", "cadence_hours", "enabled",
    "trigger_feature_id", "trigger_feature_ids", "plot_type",
    "shaded_feature_id", "shaded_field", "contour_field", "vector_field",
    "unit_label", "contour_label", "vector_unit_label", "display_title",
    "title", "meaning", "lead_hours", "style",
    "render_domain_id", "render_domain_name_ko", "render_domain",
    # map_renderer가 product-level fallback으로 직접 읽는 key
    "wind_stride", "quiver_scale", "quiver_width", "quiver_key_enabled",
    "quiver_key_position", "quiver_key_x", "quiver_key_y",
    "quiver_key_length", "quiver_key_labelpos", "quiver_key_coordinates",
    "quiver_key_label", "quiver_calm_threshold_ms",
    "quiver_min_display_speed_ms", "quiver_max_display_speed_ms",
    "contour_levels", "contour_linewidth",
    "contour_halo_linewidth", "contour_color", "contour_halo_color",
    "contour_label_fontsize", "contour_label_halo_linewidth",
}

ROOT_KEYS = {
    "schema_version", "render_style_file", "render_domain_id",
    "render_domain_name_ko", "default_domain", "default_domain_id",
    "default_domain_name_ko", "default_lead_hours", "broad_map_generation",
    "output_subdir", "asset_id_prefix", "default_style", "render_domains", "products",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"render style YAML root must be mapping: {path}")
    return value


def resolve_render_style_config(root: Path, config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """공통 style profile을 product style에 병합하고 unsupported key를 기록한다."""

    resolved = copy.deepcopy(config)
    unsupported: list[str] = []
    for key in resolved:
        if key not in ROOT_KEYS:
            unsupported.append(f"root.{key}")

    style_file_raw = resolved.get("render_style_file")
    if not style_file_raw:
        style_doc: dict[str, Any] = {"default_style": {}, "style_profiles": {}}
        style_file_ref = None
    else:
        style_file = resolve_logical_ref(root, str(style_file_raw))
        style_doc = _load_yaml(style_file)
        style_file_ref = logical_ref(root, style_file)

    common_default = style_doc.get("default_style", {})
    profiles = style_doc.get("style_profiles", {})
    if not isinstance(common_default, dict) or not isinstance(profiles, dict):
        raise ValueError("broad_map_render_styles.yaml requires default_style and style_profiles mappings")
    config_default = resolved.get("default_style", {})
    if not isinstance(config_default, dict):
        raise ValueError("default_style must be a mapping")
    for key in [*common_default, *config_default]:
        if key not in SUPPORTED_STYLE_KEYS:
            unsupported.append(f"default_style.{key}")
    resolved["default_style"] = {**common_default, **config_default}

    products = resolved.get("products", [])
    if not isinstance(products, list):
        raise ValueError("products must be a list")
    profile_errors: list[str] = []
    for index, product in enumerate(products):
        if not isinstance(product, dict):
            profile_errors.append(f"products[{index}] is not a mapping")
            continue
        product_id = str(product.get("product_id", f"index_{index}"))
        for key in product:
            if key not in PRODUCT_KEYS:
                unsupported.append(f"products.{product_id}.{key}")
        profile_id = str(product.get("style_profile_id", "")).strip()
        profile = profiles.get(profile_id)
        if not profile_id or not isinstance(profile, dict):
            profile_errors.append(f"{product_id}: unknown style_profile_id={profile_id!r}")
            profile = {}
        inline = product.get("style", {})
        if not isinstance(inline, dict):
            profile_errors.append(f"{product_id}: style must be a mapping")
            inline = {}
        for key in [*profile, *inline]:
            if key not in SUPPORTED_STYLE_KEYS:
                unsupported.append(f"style_profiles.{profile_id}.{key}")
        product["style"] = {**profile, **inline}
        product["style_profile_id"] = profile_id

    diagnostics = {
        "style_schema_version": str(style_doc.get("schema_version", "unspecified")),
        "style_profile_version": str(style_doc.get("profile_version", "unspecified")),
        "render_style_file": style_file_ref,
        "unsupported_style_keys": sorted(set(unsupported)),
        "style_profile_errors": profile_errors,
    }
    return resolved, diagnostics


def validate_discovery_render_styles(
    *,
    root: Path,
    init: str,
    discovery_manifest: dict[str, Any],
    legacy_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Discovery image style consistency와 legacy shared profile 정합성을 검증한다."""

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    unsupported = list(discovery_manifest.get("style_contract", {}).get("unsupported_style_keys", []))
    profile_errors = list(discovery_manifest.get("style_contract", {}).get("style_profile_errors", []))
    if unsupported:
        errors.append({"code": "UNSUPPORTED_STYLE_KEYS", "items": unsupported})
    if profile_errors:
        errors.append({"code": "STYLE_PROFILE_ERROR", "items": profile_errors})

    items = [
        item for item in discovery_manifest.get("broad_maps", [])
        if item.get("status") == "created" and bool(item.get("required_for_pass1", item.get("product_tier") != "supplementary"))
    ]
    duplicates = [
        {"product_id": product, "forecast_hour": hour}
        for (product, hour), count in Counter(
            (str(item.get("product_id")), int(item.get("forecast_hour"))) for item in items
        ).items() if count > 1
    ]
    if duplicates:
        errors.append({"code": "DUPLICATE_PRODUCT_TIME", "items": duplicates})

    missing_title: list[str] = []
    missing_colorbar: list[str] = []
    auto_range: list[str] = []
    image_sizes: set[tuple[int, int]] = set()
    product_items: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        asset_id = str(item.get("asset_id"))
        product_items[str(item.get("product_id"))].append(item)
        title_context = item.get("title_context", {})
        title_lines = item.get("title_info_lines", [])
        if not all(title_context.get(key) for key in ("init_time_utc", "valid_time_utc", "lead_text")) or len(title_lines) < 2:
            missing_title.append(asset_id)
        if item.get("plot_type") != "raw_contour_plus_raw_vector":
            if not item.get("colorbar_levels") or not item.get("colorbar_ticks") or not item.get("colorbar_colors"):
                missing_colorbar.append(asset_id)
        if item.get("auto_range_used"):
            auto_range.append(asset_id)
        path = resolve_logical_ref(root, str(item.get("image_path")))
        if not path.is_file():
            errors.append({"code": "IMAGE_MISSING", "asset_id": asset_id, "path": str(path)})
            continue
        with Image.open(path) as image:
            size = tuple(int(x) for x in image.size)
            image_sizes.add(size)
            if list(size) != [item.get("image_width_px"), item.get("image_height_px")]:
                errors.append({"code": "IMAGE_SIZE_METADATA_MISMATCH", "asset_id": asset_id, "actual": size})

    if missing_title:
        errors.append({"code": "MISSING_TITLE_METADATA", "asset_ids": missing_title})
    if missing_colorbar:
        errors.append({"code": "MISSING_COLORBAR_METADATA", "asset_ids": missing_colorbar})
    if auto_range:
        errors.append({"code": "AUTO_PERCENTILE_RANGE_USED", "asset_ids": auto_range})
    if len(image_sizes) != 1:
        errors.append({"code": "INCONSISTENT_CORE_IMAGE_SIZE", "sizes": sorted(image_sizes)})

    signature_keys = [
        "domain_bbox", "dpi", "figure_width_in", "figure_height_in",
        "colorbar_orientation", "colorbar_levels", "colorbar_ticks",
        "colorbar_colors", "extend", "wind_stride", "quiver_scale",
        "contour_levels", "style_profile_id",
        "longitude_ticks", "latitude_ticks",
        "vector_rendering_mode", "quiver_calm_threshold_ms",
        "quiver_min_display_speed_ms", "quiver_max_display_speed_ms",
    ]
    inconsistent: list[dict[str, Any]] = []
    for product_id, grouped in sorted(product_items.items()):
        signatures = {
            json.dumps({key: item.get(key) for key in signature_keys}, sort_keys=True)
            for item in grouped
        }
        if len(signatures) != 1:
            inconsistent.append({"product_id": product_id, "signature_count": len(signatures)})
    if inconsistent:
        errors.append({"code": "INCONSISTENT_PRODUCT_STYLE", "items": inconsistent})

    legacy_profiles = {
        str(item.get("product_id")): item.get("style_profile_id")
        for item in legacy_manifest.get("broad_maps", []) if item.get("status") == "created"
    }
    profile_mismatches = [
        {"product_id": product_id, "legacy": legacy_profiles.get(product_id), "discovery": grouped[0].get("style_profile_id")}
        for product_id, grouped in sorted(product_items.items())
        if product_id in legacy_profiles and legacy_profiles[product_id] != grouped[0].get("style_profile_id")
    ]
    if profile_mismatches:
        errors.append({"code": "LEGACY_DISCOVERY_PROFILE_MISMATCH", "items": profile_mismatches})

    return {
        "init": init,
        "report_type": "discovery_render_style_validation_report",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "style_validation_status": "pass" if not errors else "fail",
        "style_error_count": len(errors),
        "style_warning_count": len(warnings),
        "unsupported_style_keys": unsupported,
        "inconsistent_product_styles": inconsistent,
        "missing_title_metadata": missing_title,
        "missing_colorbar_metadata": missing_colorbar,
        "auto_percentile_assets": auto_range,
        "duplicate_product_time": duplicates,
        "pass1_image_sizes": [list(size) for size in sorted(image_sizes)],
        "legacy_discovery_profile_mismatches": profile_mismatches,
        "errors": errors,
        "warnings": warnings,
    }
