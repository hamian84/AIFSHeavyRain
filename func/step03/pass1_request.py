"""Build the compact Step03-to-Step04 discovery request contract."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from func.shared.runtime_paths import logical_ref, resolve_logical_ref
from func.shared.diagnostic_contracts import product_signal_components, semantic_capability_registry


PRODUCT_QUANTITATIVE_FEATURE_IDS: dict[str, tuple[str, ...]] = {
    "QL_PRECIP_6H": ("tp_6h_increment_mm", "tp_accum_mm"),
    "QL_TCW_WIND850": ("tcw_kg_m2", "wind850_ms", "q850_gkg", "moisture_flux850"),
    "QL_CONVERGENCE925_WIND925": ("convergence925_1e5_s", "wind925_ms", "q925_gkg", "moisture_flux925"),
    "QL_OMEGA700_Z500": ("omega700_upward", "z500_gpm"),
    "QL_MSLP_WIND10": ("msl_hpa", "wind10_ms"),
    "QL_JET300_Z300": ("upper_jet300_ms",),
}


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rel(root: Path, path: Path) -> str:
    return logical_ref(root, path)


def _sha256sum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _image_metadata(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as image:
        width, height = image.size
        image.verify()
    return width, height


def build_overview_attachment_manifest(
    root: Path,
    overview_manifest: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    attachments: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in overview_manifest.get("overviews", []):
        if (
            str(item.get("status")) not in {"created", "created_with_missing_panels"}
            or not bool(item.get("required_for_initial_pass1", True))
        ):
            continue
        asset_id = str(item.get("asset_id"))
        path = resolve_logical_ref(root, str(item.get("image_path")))
        if asset_id in seen_ids:
            errors.append({"code": "DUPLICATE_OVERVIEW_ASSET_ID", "asset_id": asset_id})
            continue
        seen_ids.add(asset_id)
        if not path.is_file():
            errors.append(
                {
                    "code": "OVERVIEW_ATTACHMENT_MISSING",
                    "asset_id": asset_id,
                    "path": _rel(root, path),
                }
            )
            continue
        try:
            width, height = _image_metadata(path)
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {
                    "code": "OVERVIEW_ATTACHMENT_INVALID",
                    "asset_id": asset_id,
                    "reason": str(exc),
                }
            )
            continue
        attachments.append(
            {
                "asset_id": asset_id,
                "attachment_role": "temporal_overview",
                "file_path": _rel(root, path),
                "product_id": str(item.get("product_id")),
                "analysis_tier": str(item.get("analysis_tier")),
                "cadence_hours": item.get("cadence_hours"),
                "group_id": item.get("group_id"),
                "forecast_hour_start": int(item.get("forecast_hour_start")),
                "forecast_hour_end": int(item.get("forecast_hour_end")),
                "forecast_hours": [int(value) for value in item.get("forecast_hours", [])],
                "valid_times_utc": list(item.get("valid_times_utc", [])),
                "source_asset_ids": [str(value) for value in item.get("source_asset_ids", [])],
                "source_panels": list(item.get("source_panels", [])),
                "panel_count": int(item.get("panel_count", 0)),
                "panel_columns": int(item.get("panel_columns", 0)),
                "panel_rows": int(item.get("panel_rows", 0)),
                "render_domain_id": item.get("render_domain_id"),
                "render_domain_name_ko": item.get("render_domain_name_ko"),
                "render_domain_bbox": item.get("render_domain_bbox"),
                "projection_type": item.get("projection_type"),
                "coordinate_reference": item.get("coordinate_reference"),
                "shared_colorbar": bool(item.get("shared_colorbar", False)),
                "unit_label": item.get("unit_label"),
                "sha256": _sha256sum(path),
                "size_bytes": path.stat().st_size,
                "width_px": width,
                "height_px": height,
                "required": True,
            }
        )
    attachments.sort(
        key=lambda item: (
            item["analysis_tier"],
            item["product_id"],
            item["forecast_hour_start"],
        )
    )
    tier_counts = Counter(item["analysis_tier"] for item in attachments)
    fingerprint = _canonical_hash(
        [
            {
                "asset_id": item["asset_id"],
                "product_id": item["product_id"],
                "forecast_hours": item["forecast_hours"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
            }
            for item in attachments
        ]
    )
    return {
        "package_id": None,
        "manifest_type": "ai_discovery_attachment_manifest",
        "schema_version": "2.0.0",
        "created_at_utc": _now_utc(),
        "attachment_mode": "pass1_overview_candidate_discovery",
        "attachment_count": len(attachments),
        "required_attachment_count": len(attachments),
        "required_attachment_count_by_tier": dict(sorted(tier_counts.items())),
        "attachment_fingerprint": fingerprint,
        "attachments": attachments,
    }, errors


def build_support_catalog(
    *,
    attachment_manifest: dict[str, Any],
    feature_catalog_ref: str,
    feature_catalog: dict[str, Any] | None = None,
    diagnostic_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    by_product: dict[str, dict[str, Any]] = {}
    for item in attachment_manifest.get("attachments", []):
        product_id = str(item["product_id"])
        entry = by_product.setdefault(
            product_id,
            {
                "product_id": product_id,
                "analysis_tier": item.get("analysis_tier"),
                "supported_request_types": ["spatial_field"],
                "allowed_forecast_hours": set(),
                "default_render_domain_id": item.get("render_domain_id"),
                "default_render_domain_name_ko": item.get("render_domain_name_ko"),
                "default_render_domain_bbox": item.get("render_domain_bbox"),
            },
        )
        entry["allowed_forecast_hours"].update(
            int(value) for value in item.get("forecast_hours", [])
        )

    diagnostic_execution_support: dict[str, dict[str, Any]] = {}
    derived_feature_ids: dict[str, list[str]] = {}
    if isinstance(diagnostic_catalog, dict):
        operational_ids = {
            str(item.get("diagnostic_id"))
            for item in diagnostic_catalog.get("diagnostics", [])
            if isinstance(item, dict)
            and str(item.get("maturity", "operational")) == "operational"
            and item.get("diagnostic_id")
        }
        compatibility = diagnostic_catalog.get("compatibility", {})
        if isinstance(compatibility, dict):
            for diagnostic_id, contract in compatibility.items():
                if diagnostic_id not in operational_ids or not isinstance(contract, dict):
                    continue
                compatible = [
                    str(value)
                    for value in contract.get("compatible_product_ids", [])
                    if str(value)
                ]
                diagnostic_execution_support[str(diagnostic_id)] = {
                    "compatible_product_ids": list(dict.fromkeys(compatible)),
                    "preferred_product_id": None,
                }

        calculation_contracts = diagnostic_catalog.get("calculation_contracts", {})
        product_contracts = diagnostic_catalog.get("product_contracts", {})
        if not isinstance(calculation_contracts, dict):
            calculation_contracts = {}
        if not isinstance(product_contracts, dict):
            product_contracts = {}
        # Product signal-component capability is owned by diagnostic_catalog.yaml.
        # Copy it into the Step03 support catalog so Step04 can plan against the
        # same contract that Step05/06 will later enforce.
        for product_id, entry in by_product.items():
            components = product_signal_components(diagnostic_catalog, str(product_id))
            if components:
                entry["signal_components"] = components

        requestable_statuses = {"implemented", "step05_planned"}
        for contract in calculation_contracts.values():
            if not isinstance(contract, dict):
                continue
            implementation_status = str(
                contract.get("implementation_status", "implemented")
            ).strip()
            if implementation_status not in requestable_statuses:
                continue
            calculation_product_id = str(contract.get("calculation_product_id", "")).strip()
            if not calculation_product_id:
                continue
            source_data_kind = str(contract.get("source_data_kind", "overview_product")).strip()
            source_product_ids = [
                str(value)
                for value in contract.get("source_product_ids", [])
                if str(value) in by_product
            ]
            availability_anchor_product_ids = [
                str(value)
                for value in contract.get(
                    "availability_anchor_product_ids", source_product_ids
                )
                if str(value) in by_product
            ]
            source_rows = [by_product[value] for value in availability_anchor_product_ids]
            if calculation_product_id not in by_product and source_rows:
                hour_sets = [set(row["allowed_forecast_hours"]) for row in source_rows]
                allowed_hours = set.intersection(*hour_sets) if hour_sets else set()
                domain_source = next(
                    (row for row in source_rows if row.get("analysis_tier") == "temporal_core"),
                    source_rows[0],
                )
                by_product[calculation_product_id] = {
                    "product_id": calculation_product_id,
                    "analysis_tier": "diagnostic_derived",
                    "supported_request_types": ["spatial_field"],
                    "allowed_forecast_hours": allowed_hours,
                    "default_render_domain_id": domain_source.get("default_render_domain_id"),
                    "default_render_domain_name_ko": domain_source.get("default_render_domain_name_ko"),
                    "default_render_domain_bbox": domain_source.get("default_render_domain_bbox"),
                    "source_data_kind": source_data_kind,
                    "source_product_ids": source_product_ids,
                    "availability_anchor_product_ids": availability_anchor_product_ids,
                    "raw_data_profile_ref": contract.get("raw_data_profile_ref"),
                    "required_pressure_level_fields": contract.get(
                        "required_pressure_level_fields", {}
                    ),
                    "optional_surface_fields": contract.get("optional_surface_fields", []),
                    "profile_domain_source": contract.get("profile_domain_source"),
                    "fixed_layer_pair_hpa": contract.get("fixed_layer_pair_hpa"),
                    "cross_section_line_source": contract.get("cross_section_line_source"),
                    "cross_section_endpoints_within_selected_domain": contract.get(
                        "cross_section_endpoints_within_selected_domain"
                    ),
                    "cross_section_must_intersect_candidate_area": contract.get(
                        "cross_section_must_intersect_candidate_area"
                    ),
                    "cross_section_sample_count": contract.get("cross_section_sample_count"),
                    "derived_diagnostic_product": True,
                    "implementation_status": implementation_status,
                    "step05_builder_required": implementation_status == "step05_planned",
                    "signal_components": product_signal_components(
                        diagnostic_catalog, calculation_product_id
                    ),
                }
            product_contract = product_contracts.get(calculation_product_id, {})
            if isinstance(product_contract, dict):
                if bool(product_contract.get("quantitative_excerpt_supported", True)):
                    derived_feature_ids[calculation_product_id] = [
                        str(value)
                        for value in product_contract.get("produced_variables", [])
                        if str(value)
                    ]
                else:
                    derived_feature_ids[calculation_product_id] = []
            for diagnostic_id in contract.get("diagnostic_ids", []):
                diagnostic_id = str(diagnostic_id)
                row = diagnostic_execution_support.setdefault(
                    diagnostic_id,
                    {"compatible_product_ids": [], "preferred_product_id": None},
                )
                compatible = [
                    calculation_product_id,
                    *[str(value) for value in row.get("compatible_product_ids", [])],
                ]
                row["compatible_product_ids"] = list(dict.fromkeys(compatible))
                row["preferred_product_id"] = calculation_product_id
                row["implementation_status"] = implementation_status
                row["source_data_kind"] = source_data_kind
                row["raw_data_profile_ref"] = contract.get("raw_data_profile_ref")
                row["required_pressure_level_fields"] = contract.get(
                    "required_pressure_level_fields", {}
                )
                row["optional_surface_fields"] = contract.get("optional_surface_fields", [])
                row["profile_domain_source"] = contract.get("profile_domain_source")
                row["fixed_layer_pair_hpa"] = contract.get("fixed_layer_pair_hpa")
                row["cross_section_line_source"] = contract.get("cross_section_line_source")
                row["cross_section_endpoints_within_selected_domain"] = contract.get(
                    "cross_section_endpoints_within_selected_domain"
                )
                row["cross_section_must_intersect_candidate_area"] = contract.get(
                    "cross_section_must_intersect_candidate_area"
                )
                row["cross_section_sample_count"] = contract.get("cross_section_sample_count")
                row["signal_components"] = product_signal_components(
                    diagnostic_catalog, calculation_product_id
                )

    products = []
    for entry in by_product.values():
        row = dict(entry)
        row["allowed_forecast_hours"] = sorted(entry["allowed_forecast_hours"])
        products.append(row)
    products.sort(key=lambda item: (str(item.get("analysis_tier")), str(item.get("product_id"))))

    known_feature_ids = {
        str(item.get("feature_id"))
        for item in (feature_catalog or {}).get("features", [])
        if isinstance(item, dict) and item.get("feature_id")
    }
    feature_ids_by_product = {
        product_id: [
            feature_id
            for feature_id in feature_ids
            if not known_feature_ids or feature_id in known_feature_ids
        ]
        for product_id, feature_ids in PRODUCT_QUANTITATIVE_FEATURE_IDS.items()
        if product_id in by_product
    }
    for product_id, feature_ids in derived_feature_ids.items():
        derived = [
            feature_id
            for feature_id in feature_ids
            if not known_feature_ids or feature_id in known_feature_ids
        ]
        feature_ids_by_product[product_id] = list(
            dict.fromkeys([*feature_ids_by_product.get(product_id, []), *derived])
        )

    capability_registry = (
        semantic_capability_registry(diagnostic_catalog)
        if isinstance(diagnostic_catalog, dict)
        else {"contract_version": "legacy-unversioned", "diagnostics": {}}
    )
    capability_registry["contract_hash"] = _canonical_hash(capability_registry)

    return {
        "catalog_type": "supported_analysis_data_requests",
        "schema_version": "4.7.1",
        "created_at_utc": _now_utc(),
        "supported_request_types": ["spatial_field", "quantitative_excerpt"],
        "spatial_field_products": products,
        "quantitative_excerpt_support": {
            "data_type": "quantitative_excerpt",
            "allowed_feature_catalog_ref": feature_catalog_ref,
            "forecast_hours_required": True,
            "requested_domain_required": False,
            "supported_statistics": [
                "valid_grid_count",
                "mean",
                "min",
                "max",
                "p95",
                "max_location",
                "min_location",
            ],
            "supported_derived_outputs": [
                "regional_statistics",
                "regional_ranking",
                "regional_max_location",
                "temporal_evolution",
            ],
            "ranking_statistics": ["mean", "max", "p95"],
            "maximum_top_n": 20,
            "feature_ids_by_product": feature_ids_by_product,
        },
        "diagnostic_execution_support": diagnostic_execution_support,
        "product_capability_contract": capability_registry,
        "analysis_plan_support": {
            "plan_required_per_candidate": True,
            "minimum_variable_check_count": 3,
            "maximum_variable_check_count": 10,
            "required_variable_roles": ["hazard_magnitude"],
            "recommended_explanatory_roles": [
                "moisture_supply",
                "low_level_forcing",
                "midlevel_forcing",
                "organization_persistence",
            ],
            "supported_target_types": ["candidate_domain", "context_domain", "operational_regions"],
            "supported_resolution_methods": [
                "explicit_bbox",
                "candidate_area_overlap",
                "explicit_region_ids",
            ],
            "supported_phase_types": ["pre_peak", "peak", "post_peak", "context"],
            "supported_analysis_methods": [
                "detailed_map",
                "regional_statistics",
                "regional_ranking",
                "regional_max_location",
                "temporal_evolution",
                "variable_cross_check",
            ],
            "legacy_analysis_methods_adapter_only": True,
            "diagnostic_catalog_ref": "daba/analysis/diagnostic_catalog.yaml",
            "supported_diagnostic_ids": sorted(diagnostic_execution_support),
            "step05_planned_diagnostic_ids": sorted(
                diagnostic_id
                for diagnostic_id, contract in diagnostic_execution_support.items()
                if isinstance(contract, dict)
                and str(contract.get("implementation_status")) == "step05_planned"
            ),
            "direct_precipitation_confirmation_required": True,
            "overview_exact_point_inference_forbidden": True,
            "step06_region_max_location_derivation_supported": True,
        },
    }

def build_batch_plan(
    *,
    attachment_manifest_ref: str,
    attachment_manifest: dict[str, Any],
    policy: dict[str, Any],
    batch_observation_prompt_ref: str,
    batch_observation_schema_ref: str,
) -> dict[str, Any]:
    batch_policy = dict(policy.get("pass1_batch", {}))
    max_images = int(batch_policy.get("max_images_per_request", 4))
    if max_images < 1 or max_images > 4:
        raise ValueError("pass1_batch.max_images_per_request must be between 1 and 4")

    tier_order = {"temporal_core": 0, "synoptic_context": 1}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in attachment_manifest.get("attachments", []):
        key = (str(item.get("analysis_tier")), str(item.get("group_id")))
        grouped.setdefault(key, []).append(item)

    batches: list[dict[str, Any]] = []
    for (tier, group_id), items in sorted(
        grouped.items(),
        key=lambda pair: (tier_order.get(pair[0][0], 99), pair[0][0], pair[0][1]),
    ):
        items.sort(
            key=lambda item: (str(item.get("product_id")), int(item.get("forecast_hour_start", 0)))
        )
        for chunk_start in range(0, len(items), max_images):
            chunk = items[chunk_start : chunk_start + max_images]
            suffix = "" if len(items) <= max_images else f"_P{chunk_start // max_images + 1:02d}"
            batch_id = f"PASS1_OBS_{tier}_{group_id}{suffix}".upper()
            batches.append(
                {
                    "batch_id": batch_id,
                    "stage": "image_observation",
                    "analysis_tier": tier,
                    "group_id": group_id,
                    "attachment_manifest_ref": attachment_manifest_ref,
                    "attachment_count": len(chunk),
                    "attachment_ids": [str(item["asset_id"]) for item in chunk],
                    "forecast_hours": sorted(
                        {int(hour) for item in chunk for hour in item.get("forecast_hours", [])}
                    ),
                    "source_panel_count": sum(
                        len(item.get("source_panels", [])) for item in chunk
                    ),
                }
            )

    attachment_ids = [str(item["asset_id"]) for item in attachment_manifest.get("attachments", [])]
    planned_ids = [asset_id for batch in batches for asset_id in batch["attachment_ids"]]
    if sorted(planned_ids) != sorted(attachment_ids) or len(planned_ids) != len(set(planned_ids)):
        raise ValueError("staged batch plan does not cover each attachment exactly once")

    expected_batch_count = int(batch_policy.get("expected_observation_batch_count", len(batches)))
    if expected_batch_count != len(batches):
        raise ValueError(
            f"staged batch count mismatch expected={expected_batch_count} actual={len(batches)}"
        )

    return {
        "package_id": None,
        "manifest_type": "ai_discovery_batch_plan",
        "schema_version": "2.1.0",
        "execution_mode": "staged_image_observation_then_text_synthesis",
        "grouping_strategy": str(
            batch_policy.get("grouping_strategy", "analysis_tier_and_group_id")
        ),
        "max_images_per_request": max_images,
        "batch_observation_prompt_ref": batch_observation_prompt_ref,
        "batch_observation_schema_ref": batch_observation_schema_ref,
        "batch_count": len(batches),
        "batches": batches,
        "synthesis": {
            "stage": "text_synthesis",
            "input_batch_ids": [str(batch["batch_id"]) for batch in batches],
            "direct_image_input": False,
        },
    }
