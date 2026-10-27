"""Step05 request resolution, calculations, and single-panel rendering.

This module is intentionally downstream-only.  It consumes the Step04 Pass1
result as-is and never amends the Step01--04 contracts.  The public helpers
are used by ``scripts/05_run_step05.py`` and are also small enough to test
with fixture requests without writing an operational package.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable

import numpy as np
import yaml

from func.shared import aifs_data
from func.shared.diagnostic_contracts import actual_signal_components, diagnostic_semantic_capability, load_diagnostic_catalog
from func.shared.map_renderer import (
    MapDomain,
    RenderContext,
    draw_product_panel,
    import_matplotlib,
    lat_lon_names,
    subset_to_domain,
)
from func.shared.operational_regions import (
    load_operational_regions,
    operational_stats_for,
    selectable_pass1_regions,
)
from func.step05.renderer import Step05RenderError, render_spatial_field_png
from func.shared.runtime_paths import logical_ref, resolve_logical_ref, resolve_step04_output_dir, runtime_path


PHYSICAL_PRODUCTS_BY_DIAGNOSTIC = {
    "accumulated_precipitation": ("QL_PRECIP_6H",),
    "moisture_flux": ("QL_MOISTURE_FLUX850_WIND850",),
    "horizontal_convergence": ("QL_CONVERGENCE925_WIND925",),
    "vertical_wind_shear": ("VERTICAL_WIND_SHEAR_850_500",),
    "time_height_section": ("TIME_HEIGHT_SECTION_CORE",),
    "vertical_cross_section": ("VERTICAL_CROSS_SECTION_CORE",),
    "multivariable_composite": (
        "QL_PRECIP_6H",
        "QL_TCW_WIND850",
        "QL_CONVERGENCE925_WIND925",
        "QL_OMEGA700_Z500",
    ),
}
_MAP_PRODUCTS = {
    "QL_PRECIP_6H",
    "QL_TCW_WIND850",
    "QL_MOISTURE_FLUX850_WIND850",
    "QL_CONVERGENCE925_WIND925",
    "QL_OMEGA700_Z500",
}
_SAFE_PART = re.compile(r"[^A-Za-z0-9_.-]+")
_REQUEST_ID = re.compile(r"^REQ_[A-Za-z0-9][A-Za-z0-9_.-]*$")
SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY = "SYS_DIRECT_PRECIPITATION_PRESENCE"
SYSTEM_DIRECT_PRECIPITATION_OPERATIONAL_MASK_KEY = "_system_direct_precipitation_operational_mask"

# The CORE requests are intentionally kept to the smallest set that can show
# the moisture, vertical-motion, and wind environments described by the Step04
# candidate context.  These are physical diagnostics, not new Step04 products.
_CORE_PHYSICAL_BY_LOGICAL_PRODUCT = {
    "TIME_HEIGHT_SECTION_CORE": (
        "specific_humidity_time_height",
        "omega_time_height",
        "wind_speed_time_height",
        "along_section_wind_time_height",
    ),
    "VERTICAL_CROSS_SECTION_CORE": (
        "specific_humidity_cross_section",
        "omega_cross_section",
        "wind_speed_cross_section",
        "along_section_wind_cross_section",
    ),
}
_CORE_PANEL_CONFIG: dict[str, dict[str, Any]] = {
    "specific_humidity_time_height": {
        "logical_product": "TIME_HEIGHT_SECTION_CORE",
        "raw_fields": ("q",),
        "layout": "time_height_specific_humidity",
        "palette": "specific_humidity_q",
        "scale": 1000.0,
        "units": "g kg-1",
        "title": "AIFS 후보영역 평균 비습 시간-기압 단면",
        "colorbar_label": "비습 (g kg$^{-1}$)",
    },
    "omega_time_height": {
        "logical_product": "TIME_HEIGHT_SECTION_CORE",
        "raw_fields": ("w",),
        "layout": "time_height_signed_omega",
        "palette": "signed_omega",
        "scale": 1.0,
        "units": "Pa s-1",
        "title": "AIFS 후보영역 평균 기압연직속도 ω 시간-기압 단면",
        "colorbar_label": "기압연직속도 ω (Pa s$^{-1}$; 음수=상승, 양수=하강)",
    },
    "wind_speed_time_height": {
        "logical_product": "TIME_HEIGHT_SECTION_CORE",
        "raw_fields": ("u", "v"),
        "layout": "time_height_wind_speed",
        "palette": "wind_speed",
        "scale": 1.0,
        "units": "m s-1",
        "title": "AIFS 후보영역 평균 풍속 시간-기압 단면",
        "colorbar_label": "풍속 (m s$^{-1}$)",
    },
    "along_section_wind_time_height": {
        "logical_product": "TIME_HEIGHT_SECTION_CORE",
        "raw_fields": ("u", "v"),
        # The existing catalog has the raw U/V contract but does not declare
        # this Step05-only, candidate-major-axis derived time-height panel.
        "step05_only_derived": True,
        "wind_mode": "along_section",
        "layout": "time_height_along_section_wind",
        "palette": "signed_along_section_wind",
        "scale": 1.0,
        "units": "m s-1",
        "title": "AIFS 후보영역 평균 기준축 바람 성분 시간-기압 단면",
        "colorbar_label": "기준축 바람 (m s$^{-1}$; 양수=A→B, 음수=B→A)",
    },
    "specific_humidity_cross_section": {
        "logical_product": "VERTICAL_CROSS_SECTION_CORE",
        "raw_fields": ("q",),
        "layout": "vertical_cross_section_specific_humidity",
        "palette": "specific_humidity_q",
        "scale": 1000.0,
        "units": "g kg-1",
        "title": "AIFS 비습 연직단면",
        "colorbar_label": "비습 (g kg$^{-1}$)",
    },
    "omega_cross_section": {
        "logical_product": "VERTICAL_CROSS_SECTION_CORE",
        "raw_fields": ("w",),
        "layout": "vertical_cross_section_signed_omega",
        "palette": "signed_omega",
        "scale": 1.0,
        "units": "Pa s-1",
        "title": "AIFS 기압연직속도 ω 연직단면",
        "colorbar_label": "기압연직속도 ω (Pa s$^{-1}$; 음수=상승, 양수=하강)",
    },
    "wind_speed_cross_section": {
        "logical_product": "VERTICAL_CROSS_SECTION_CORE",
        "raw_fields": ("u", "v"),
        "layout": "vertical_cross_section_wind_speed",
        "palette": "wind_speed",
        "scale": 1.0,
        "units": "m s-1",
        "title": "AIFS 풍속 연직단면",
        "colorbar_label": "풍속 (m s$^{-1}$)",
    },
    "along_section_wind_cross_section": {
        "logical_product": "VERTICAL_CROSS_SECTION_CORE",
        "raw_fields": ("u", "v"),
        "wind_mode": "along_section",
        "layout": "vertical_cross_section_along_section_wind",
        "palette": "signed_along_section_wind",
        "scale": 1.0,
        "units": "m s-1",
        "title": "AIFS 단면방향 바람 성분 연직단면",
        "colorbar_label": "단면방향 바람 (m s$^{-1}$; 양수=A→B, 음수=B→A)",
    },
}
_TIME_HEIGHT_PHYSICAL_DIAGNOSTICS = frozenset(_CORE_PHYSICAL_BY_LOGICAL_PRODUCT["TIME_HEIGHT_SECTION_CORE"])
_CROSS_SECTION_PHYSICAL_DIAGNOSTICS = frozenset(_CORE_PHYSICAL_BY_LOGICAL_PRODUCT["VERTICAL_CROSS_SECTION_CORE"])
_KNOWN_IMAGE_PRODUCTS = frozenset(
    set(_MAP_PRODUCTS)
    | {"VERTICAL_WIND_SHEAR_850_500", "TEMPORAL_EVOLUTION", "TIME_HEIGHT_SECTION_CORE", "VERTICAL_CROSS_SECTION_CORE"}
    | set(_CORE_PANEL_CONFIG)
)


class Step05DiagnosticError(RuntimeError):
    """An explicit, operationally actionable Step05 failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TargetSelection:
    """A direct spatial bbox or a candidate-clipped operational region set."""

    target_id: str
    kind: str
    bbox: dict[str, float] | None
    candidate_bbox: dict[str, float] | None
    regions: tuple[dict[str, Any], ...]


@dataclass
class AIFSFields:
    """Read only the local, indexed GRIB fields needed by one request."""

    root: Path
    init: str
    index: dict[tuple[str, int | None, int], Path]
    np_module: Any
    xr: Any
    feature_specs: dict[str, Any]

    @classmethod
    def create(cls, root: Path, init: str) -> "AIFSFields":
        raw_dir = runtime_path(root, "dain", init, "raw", "aifs")
        if not raw_dir.is_dir() or not aifs_data.list_grib_files(raw_dir):
            raise Step05DiagnosticError(
                "GRIB_INPUT_MISSING",
                f"No AIFS GRIB input is available under {raw_dir}.",
            )
        try:
            index, _loader, np_module, xr = aifs_data.build_loaders(root, init, raw_dir)
            specs = {
                spec.feature_id: spec
                for spec in aifs_data.load_feature_definitions(
                    runtime_path(root, "daba", "ai_pass1", "feature_definitions.yaml")
                )
            }
        except Exception as exc:  # noqa: BLE001
            raise Step05DiagnosticError("FIELD_LOAD_FAILED", f"Unable to index local AIFS GRIB: {exc}") from exc
        if not index:
            raise Step05DiagnosticError("GRIB_INPUT_MISSING", f"No usable AIFS GRIB fields were indexed under {raw_dir}.")
        return cls(root=root, init=init, index=index, np_module=np_module, xr=xr, feature_specs=specs)

    def raw(self, short_name: str, level: int | None, hour: int) -> Any:
        path = self.index.get((short_name, level, int(hour)))
        if path is None:
            level_label = "surface" if level is None else f"{level} hPa"
            raise Step05DiagnosticError(
                "PRESSURE_LEVEL_MISSING" if level is not None else "FEATURE_MISSING",
                f"Required AIFS field {short_name} ({level_label}) is absent at F{hour:03d}.",
            )
        try:
            return aifs_data.open_field(path, short_name, level, self.np_module, self.xr)
        except Exception as exc:  # noqa: BLE001
            raise Step05DiagnosticError(
                "FIELD_LOAD_FAILED",
                f"Unable to load {short_name} level={level} at F{hour:03d}: {exc}",
            ) from exc

    def feature(self, feature_id: str, hour: int) -> Any:
        spec = self.feature_specs.get(feature_id)
        if spec is None:
            raise Step05DiagnosticError("FEATURE_MISSING", f"Feature definition is absent: {feature_id}.")
        try:
            field, missing, _sources = aifs_data.build_feature_array(
                spec=spec,
                step=int(hour),
                index=self.index,
                np=self.np_module,
                xr=self.xr,
            )
        except Exception as exc:  # noqa: BLE001
            raise Step05DiagnosticError("FIELD_LOAD_FAILED", f"Unable to calculate {feature_id} at F{hour:03d}: {exc}") from exc
        if field is None:
            raise Step05DiagnosticError(
                "FEATURE_MISSING",
                f"Required feature {feature_id} is unavailable at F{hour:03d}: {missing}",
            )
        return field

    def feature_unit(self, feature_id: str) -> str:
        """Return the unit declared by the same feature contract used for calculation."""
        spec = self.feature_specs.get(feature_id)
        if spec is None:
            raise Step05DiagnosticError("FEATURE_MISSING", f"Feature definition is absent: {feature_id}.")
        unit = str(getattr(spec, "units", "")).strip()
        if not unit:
            raise Step05DiagnosticError("FEATURE_UNIT_MISSING", f"Feature definition has no units: {feature_id}.")
        return unit


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise Step05DiagnosticError("CONFIG_INVALID", f"Unable to load YAML {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise Step05DiagnosticError("CONFIG_INVALID", f"YAML root must be a mapping: {path}")
    return value


def load_pass1_result(root: Path, init: str) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    """Load the completed Step04 provider result as the immutable Step05 source."""
    try:
        path = resolve_step04_output_dir(root, init) / "pass1_result.json"
    except FileNotFoundError as exc:
        raise Step05DiagnosticError("PASS1_RESULT_MISSING", str(exc)) from exc
    except (RuntimeError, ValueError, OSError) as exc:
        raise Step05DiagnosticError("PASS1_RESULT_AMBIGUOUS", str(exc)) from exc
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Step05DiagnosticError("PASS1_RESULT_INVALID", f"Unable to read {path}: {exc}") from exc
    requests = payload.get("analysis_data_requests") if isinstance(payload, dict) else None
    if not isinstance(requests, list) or not all(isinstance(item, dict) for item in requests):
        raise Step05DiagnosticError(
            "ANALYSIS_DATA_REQUESTS_MISSING",
            "Step04 analysis_data_requests must be a list of request mappings.",
        )
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list) or not all(isinstance(item, dict) for item in candidates):
        raise Step05DiagnosticError(
            "CANDIDATE_METADATA_MISSING",
            "Step04 candidates must be a list of candidate mappings.",
        )
    candidate_ids = [str(item.get("candidate_id") or "").strip() for item in candidates]
    if any(not candidate_id for candidate_id in candidate_ids) or len(candidate_ids) != len(set(candidate_ids)):
        raise Step05DiagnosticError(
            "CANDIDATE_METADATA_INVALID",
            "Step04 candidates must have unique, non-empty candidate_id values.",
        )
    request_candidate_ids = [str(item.get("candidate_id") or "").strip() for item in requests]
    if any(not candidate_id for candidate_id in request_candidate_ids):
        raise Step05DiagnosticError(
            "REQUEST_CANDIDATE_ID_MISSING",
            "Every Step04 analysis_data_request must reference a candidate_id.",
        )
    if set(request_candidate_ids) != set(candidate_ids):
        raise Step05DiagnosticError(
            "STEP04_REQUEST_CANDIDATE_MISMATCH",
            "Step04 candidates and analysis_data_requests must cover the same candidate_id set.",
        )
    return path, payload, list(requests)


def _bbox(value: Any, *, code: str = "INVALID_BBOX", owner: str = "requested_domain") -> dict[str, float]:
    if not isinstance(value, dict):
        raise Step05DiagnosticError(code, f"{owner} must be a west/east/south/north mapping.")
    try:
        result = {key: float(value[key]) for key in ("west", "east", "south", "north")}
    except (KeyError, TypeError, ValueError) as exc:
        raise Step05DiagnosticError(code, f"{owner} requires numeric west, east, south, north values.") from exc
    if not (-180.0 <= result["west"] < result["east"] <= 360.0 and -90.0 <= result["south"] < result["north"] <= 90.0):
        raise Step05DiagnosticError(code, f"{owner} has unordered or out-of-range bounds: {result}")
    return result


def _bbox_domain(value: dict[str, float]) -> MapDomain:
    return MapDomain(value["west"], value["east"], value["south"], value["north"])


def _candidate_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise Step05DiagnosticError("CANDIDATE_METADATA_MISSING", "Step04 candidates must be a list.")
    result: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        if not candidate_id:
            continue
        if candidate_id in result:
            raise Step05DiagnosticError("CANDIDATE_ID_DUPLICATE", f"Duplicate candidate_id in Step04 result: {candidate_id}")
        result[candidate_id] = candidate
    return result


def _target_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    plans = payload.get("candidate_analysis_plans")
    if not isinstance(plans, list):
        raise Step05DiagnosticError("TARGET_METADATA_MISSING", "candidate_analysis_plans must be a list.")
    result: dict[str, dict[str, Any]] = {}
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        for target in plan.get("target_areas", []):
            if not isinstance(target, dict):
                continue
            target_id = str(target.get("target_id") or "").strip()
            if not target_id:
                continue
            if target_id in result:
                raise Step05DiagnosticError("TARGET_ID_DUPLICATE", f"Duplicate target_id in Step04 result: {target_id}")
            item = dict(target)
            item["_candidate_id"] = str(plan.get("candidate_id") or "")
            result[target_id] = item
    return result


def resolve_request(
    requests: Iterable[dict[str, Any]],
    *,
    request_id: str | None = None,
    candidate_id: str | None = None,
    diagnostic_id: str | None = None,
) -> dict[str, Any]:
    """Select one request without a silent arbitrary tie-breaker."""
    values = list(requests)
    if request_id:
        matches = [item for item in values if str(item.get("request_id") or "") == request_id]
        if not matches:
            raise Step05DiagnosticError("REQUEST_ID_MISSING", f"No Step04 request has request_id={request_id!r}.")
        if len(matches) != 1:
            raise Step05DiagnosticError("REQUEST_ID_DUPLICATE", f"request_id={request_id!r} occurs {len(matches)} times.")
        return matches[0]
    if not candidate_id or not diagnostic_id:
        raise Step05DiagnosticError(
            "REQUEST_SELECTION_INVALID",
            "Select one request with --request-id or the exact pair --candidate-id and --diagnostic-id.",
        )
    matches = [
        item for item in values
        if str(item.get("candidate_id") or "") == candidate_id
        and str(item.get("diagnostic_id") or "") == diagnostic_id
    ]
    if not matches:
        raise Step05DiagnosticError(
            "REQUEST_SELECTION_MISSING",
            f"No Step04 request matches candidate_id={candidate_id!r}, diagnostic_id={diagnostic_id!r}.",
        )
    if len(matches) != 1:
        raise Step05DiagnosticError(
            "REQUEST_SELECTION_AMBIGUOUS",
            f"candidate_id={candidate_id!r}, diagnostic_id={diagnostic_id!r} matches {len(matches)} requests; use --request-id.",
        )
    return matches[0]


def validate_request(request: dict[str, Any], *, selected_hour: int | None = None) -> list[int]:
    request_id = str(request.get("request_id") or "").strip()
    if not request_id:
        raise Step05DiagnosticError("REQUEST_ID_MISSING", "Step04 request has no request_id.")
    if not str(request.get("candidate_id") or "").strip():
        raise Step05DiagnosticError("CANDIDATE_ID_MISSING", f"{request_id} has no candidate_id.")
    if not str(request.get("diagnostic_id") or "").strip():
        raise Step05DiagnosticError("DIAGNOSTIC_ID_MISSING", f"{request_id} has no diagnostic_id.")
    hours = request.get("forecast_hours")
    if not isinstance(hours, list) or not hours or not all(isinstance(hour, int) and not isinstance(hour, bool) for hour in hours):
        raise Step05DiagnosticError("FORECAST_HOURS_INVALID", f"{request_id}.forecast_hours must be a non-empty integer list.")
    if len(set(hours)) != len(hours) or any(hour < 0 for hour in hours):
        raise Step05DiagnosticError("FORECAST_HOURS_INVALID", f"{request_id}.forecast_hours has duplicate or negative values.")
    if selected_hour is not None:
        if int(selected_hour) not in hours:
            raise Step05DiagnosticError(
                "FORECAST_HOUR_NOT_REQUESTED",
                f"{request_id} did not request F{int(selected_hour):03d}; requested={hours}.",
            )
        return [int(selected_hour)]
    return [int(hour) for hour in hours]


def physical_products_for(request: dict[str, Any]) -> tuple[str, ...]:
    """Resolve physical meaning from diagnostic, not merely the source product."""
    diagnostic_id = str(request.get("diagnostic_id") or "")
    product_id = str(request.get("product_id") or "")
    if diagnostic_id in PHYSICAL_PRODUCTS_BY_DIAGNOSTIC:
        return PHYSICAL_PRODUCTS_BY_DIAGNOSTIC[diagnostic_id]
    if diagnostic_id == "detailed_spatial_map":
        if product_id in _MAP_PRODUCTS:
            return (product_id,)
        raise Step05DiagnosticError(
            "UNSUPPORTED_DIAGNOSTIC",
            f"detailed_spatial_map product_id={product_id!r} is not a supported Step05 single-panel map.",
        )
    if diagnostic_id == "temporal_evolution":
        if product_id not in _MAP_PRODUCTS:
            raise Step05DiagnosticError("UNSUPPORTED_DIAGNOSTIC", f"temporal_evolution product_id={product_id!r} is unsupported.")
        methods = {str(value) for value in request.get("analysis_methods", [])}
        data_type = str(request.get("data_type") or "")
        if data_type == "spatial_field" and "detailed_map" in methods:
            # Step04 AI explicitly selected representative forecast hours for a
            # temporal-change check.  Render the same physical field used by the
            # quantitative time series at those hours; do not create a separate
            # time-series PNG or choose representative hours in Python.
            representative_product = {
                "QL_TCW_WIND850": "QL_MOISTURE_FLUX850_WIND850",
                "QL_PRECIP_6H": "QL_PRECIP_6H",
                "QL_CONVERGENCE925_WIND925": "QL_CONVERGENCE925_WIND925",
                "QL_OMEGA700_Z500": "QL_OMEGA700_Z500",
            }.get(product_id)
            if representative_product is None:
                raise Step05DiagnosticError(
                    "UNSUPPORTED_DIAGNOSTIC",
                    f"temporal_evolution representative map product_id={product_id!r} is unsupported.",
                )
            return (representative_product,)
        return ("TEMPORAL_EVOLUTION",)
    raise Step05DiagnosticError(
        "UNSUPPORTED_DIAGNOSTIC",
        f"Step05 has no verified renderer/calculation for diagnostic_id={diagnostic_id!r} product_id={product_id!r}.",
    )


def resolve_target(
    *,
    root: Path,
    payload: dict[str, Any],
    request: dict[str, Any],
) -> TargetSelection:
    """Resolve a direct bbox or an explicit candidate-clipped operational target."""
    request_id = str(request.get("request_id") or "")
    target_id = str(request.get("target_id") or "").strip()
    domain = request.get("requested_domain")
    if domain is not None:
        return TargetSelection(target_id=target_id, kind="requested_domain", bbox=_bbox(domain, owner=f"{request_id}.requested_domain"), candidate_bbox=None, regions=())
    if str(request.get("data_type") or "") != "quantitative_excerpt":
        raise Step05DiagnosticError(
            "REQUESTED_DOMAIN_MISSING",
            f"{request_id} is not quantitative_excerpt and has no requested_domain.",
        )
    if not target_id:
        raise Step05DiagnosticError("TARGET_ID_MISSING", f"{request_id} has requested_domain=null but no target_id.")
    target = _target_map(payload).get(target_id)
    if target is None:
        raise Step05DiagnosticError("TARGET_RESOLUTION_FAILED", f"{request_id} references unknown target_id={target_id!r}.")
    if str(target.get("target_type") or "") != "operational_regions" or str(target.get("resolution_method") or "") != "candidate_area_overlap":
        raise Step05DiagnosticError(
            "TARGET_RESOLUTION_FAILED",
            f"{target_id} has no direct bbox and is not the Step04 candidate_area_overlap operational target.",
        )
    candidate_id = str(request.get("candidate_id") or "")
    if str(target.get("_candidate_id") or "") != candidate_id:
        raise Step05DiagnosticError("TARGET_RESOLUTION_FAILED", f"{target_id} belongs to a different candidate.")
    candidate = _candidate_map(payload).get(candidate_id)
    if candidate is None:
        raise Step05DiagnosticError("CANDIDATE_METADATA_MISSING", f"No candidate metadata for {candidate_id}.")
    candidate_bbox = _bbox(candidate.get("candidate_area"), owner=f"{candidate_id}.candidate_area")
    try:
        regions = load_operational_regions(runtime_path(root, "daba", "ai_pass1", "discovery_operational_regions.yaml"))
        selected = selectable_pass1_regions(
            regions=regions,
            candidate_bbox=candidate_bbox,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise Step05DiagnosticError("TARGET_RESOLUTION_FAILED", f"Unable to resolve {target_id}: {exc}") from exc
    if not selected:
        raise Step05DiagnosticError(
            "TARGET_RESOLUTION_FAILED",
            f"{target_id} resolves to no enabled Pass1 operational regions for candidate {candidate_id}.",
        )
    return TargetSelection(
        target_id=target_id,
        kind="operational_regions",
        bbox=None,
        candidate_bbox=candidate_bbox,
        regions=tuple(selected),
    )


def _field_for_product(fields: AIFSFields, product_id: str, hour: int) -> tuple[Any, str, str]:
    """Return the scalar whose numerical meaning matches the physical panel."""
    if product_id == "QL_PRECIP_6H":
        return fields.feature("tp_6h_increment_mm", hour), "mm", "tp_6h_increment_mm"
    if product_id == "QL_TCW_WIND850":
        return fields.feature("tcw_kg_m2", hour), "kg m-2", "tcw_kg_m2"
    if product_id == "QL_MOISTURE_FLUX850_WIND850":
        return fields.feature("moisture_flux850", hour), "g kg-1 m s-1", "moisture_flux850"
    if product_id == "QL_CONVERGENCE925_WIND925":
        return fields.feature("convergence925_1e5_s", hour), "1e-5 s-1", "convergence925_1e5_s"
    if product_id == "QL_OMEGA700_Z500":
        return fields.raw("w", 700, hour), "Pa s-1", "omega700_pa_s"
    if product_id == "VERTICAL_WIND_SHEAR_850_500":
        u850, v850 = fields.raw("u", 850, hour), fields.raw("v", 850, hour)
        u500, v500 = fields.raw("u", 500, hour), fields.raw("v", 500, hour)
        return u850.copy(data=np.hypot(np.asarray(u850.values) - np.asarray(u500.values), np.asarray(v850.values) - np.asarray(v500.values))), "m s-1", "vertical_wind_shear_850_500"
    raise Step05DiagnosticError("UNSUPPORTED_DIAGNOSTIC", f"No scalar calculation is registered for product {product_id}.")


_TEMPORAL_FIELD_LABELS = {
    "QL_PRECIP_6H": "6시간 강수량",
    "QL_TCW_WIND850": "대기 전층 수분량(TCW)",
    "QL_MOISTURE_FLUX850_WIND850": "850 hPa 수증기 플럭스 크기",
    "QL_CONVERGENCE925_WIND925": "925 hPa 수평 수렴",
    "QL_OMEGA700_Z500": "700 hPa 기압연직속도",
}


def _temporal_field_product_id(root: Path, request: dict[str, Any]) -> str:
    """Resolve the scalar rendered by a temporal-evolution request from the catalog."""
    source_product_id = str(request.get("product_id") or "QL_PRECIP_6H")
    diagnostic_id = str(request.get("diagnostic_id") or "temporal_evolution")
    profile = diagnostic_semantic_capability(
        load_diagnostic_catalog(root),
        diagnostic_id=diagnostic_id,
        product_id=source_product_id,
    )
    return str(profile.get("field_product_id") or source_product_id)


def _validate_field_values(field: Any, *, label: str) -> np.ndarray:
    values = np.asarray(field.values, dtype="float64")
    if values.ndim != 2:
        values = np.squeeze(values)
    if values.ndim != 2:
        raise Step05DiagnosticError("ARRAY_SHAPE_MISMATCH", f"{label} must be a two-dimensional field, got {values.shape}.")
    if not np.isfinite(values).any():
        raise Step05DiagnosticError("EMPTY_DATA", f"{label} contains no finite values.")
    return values


def _candidate_for_request(payload: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(request.get("candidate_id") or "")
    candidate = _candidate_map(payload).get(candidate_id)
    if candidate is None:
        raise Step05DiagnosticError("CANDIDATE_METADATA_MISSING", f"No candidate metadata for {candidate_id}.")
    return candidate


def _direct_stats(field: Any, domain: dict[str, float]) -> dict[str, Any]:
    subset = subset_to_domain(field, _bbox_domain(domain))
    values = _validate_field_values(subset, label="requested-domain subset")
    finite = np.isfinite(values)
    lat_name, _lon_name = lat_lon_names(subset)
    latitude = np.asarray(subset[lat_name].values, dtype="float64")
    weights = np.cos(np.deg2rad(latitude))[:, None] * np.ones(values.shape, dtype="float64")
    weights = np.where(finite, weights, 0.0)
    finite_values = values[finite]
    min_index = np.unravel_index(int(np.nanargmin(values)), values.shape)
    max_index = np.unravel_index(int(np.nanargmax(values)), values.shape)
    lat_name, lon_name = lat_lon_names(subset)
    lat_values = np.asarray(subset[lat_name].values, dtype="float64")
    lon_values = np.asarray(subset[lon_name].values, dtype="float64")

    def location(index: tuple[int, int]) -> dict[str, float]:
        return {
            "latitude": float(lat_values[index[0]]),
            "longitude": float(lon_values[index[1]]),
            "value": float(values[index]),
        }

    weight_total = float(weights.sum())
    weighted = float((np.where(finite, values, 0.0) * weights).sum() / weight_total) if weight_total > 0.0 else float(np.nanmean(finite_values))
    return {
        "valid_grid_count": int(finite.sum()),
        "finite_count": int(finite.sum()),
        "nan_count": int(np.isnan(values).sum()),
        "mean": float(np.nanmean(finite_values)),
        "area_weighted_mean": weighted,
        "min": float(np.nanmin(finite_values)),
        "max": float(np.nanmax(finite_values)),
        "p95": float(np.nanpercentile(finite_values, 95)),
        "min_location": location(min_index),
        "max_location": location(max_index),
    }


def _requested_stat_keys(request: dict[str, Any]) -> set[str]:
    output_spec = request.get("output_spec")
    values = output_spec.get("statistics") if isinstance(output_spec, dict) else []
    if not isinstance(values, list):
        raise Step05DiagnosticError("OUTPUT_SPEC_INVALID", "output_spec.statistics must be a list when output_spec is supplied.")
    allowed = {"valid_grid_count", "finite_count", "nan_count", "mean", "area_weighted_mean", "min", "max", "p95", "min_location", "max_location"}
    unknown = {str(value) for value in values} - allowed
    if unknown:
        raise Step05DiagnosticError("OUTPUT_SPEC_UNSUPPORTED", f"Requested unsupported statistics: {sorted(unknown)}")
    return {str(value) for value in values}


def _quantitative_feature_ids(request: dict[str, Any]) -> list[str]:
    value = request.get("feature_ids")
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise Step05DiagnosticError(
            "FEATURE_IDS_INVALID",
            f"{request.get('request_id')} quantitative_excerpt requires a non-empty feature_ids string list.",
        )
    feature_ids = [item.strip() for item in value]
    if len(set(feature_ids)) != len(feature_ids):
        raise Step05DiagnosticError("FEATURE_IDS_INVALID", f"{request.get('request_id')} feature_ids contains duplicates.")
    return feature_ids


def quantitative_statistics(
    *,
    root: Path,
    payload: dict[str, Any],
    request: dict[str, Any],
    fields: AIFSFields,
    target: TargetSelection,
    hours: list[int],
) -> dict[str, dict[str, Any]]:
    """Calculate every and only Step04-requested quantitative feature."""
    requested_stats = _requested_stat_keys(request)
    methods = {str(value) for value in request.get("analysis_methods", [])}
    output_spec = request.get("output_spec") if isinstance(request.get("output_spec"), dict) else {}
    include_series = bool(output_spec.get("include_time_series", False)) or "temporal_evolution" in methods
    include_ranking = "regional_ranking" in methods
    include_locations = bool(output_spec.get("include_max_location", False)) or "regional_max_location" in methods
    if include_locations:
        requested_stats.add("max_location")
    features: dict[str, dict[str, Any]] = {}
    for feature_id in _quantitative_feature_ids(request):
        per_hour: list[dict[str, Any]] = []
        total_finite = 0
        total_nan = 0
        ranking_rows: list[dict[str, Any]] = []
        for hour in hours:
            field = fields.feature(feature_id, hour)
            _validate_field_values(field, label=f"{feature_id} F{hour:03d}")
            if target.kind == "requested_domain":
                assert target.bbox is not None
                all_stats = _direct_stats(field, target.bbox)
                total_finite += int(all_stats["finite_count"])
                total_nan += int(all_stats["nan_count"])
                selected = {key: all_stats[key] for key in requested_stats if key in all_stats}
                per_hour.append({"forecast_hour": hour, "statistics": selected})
                continue
            assert target.candidate_bbox is not None
            # Counts describe actual field values in the candidate envelope;
            # regional statistics below may additionally apply region geometry masks.
            envelope_stats = _direct_stats(field, target.candidate_bbox)
            total_finite += int(envelope_stats["finite_count"])
            total_nan += int(envelope_stats["nan_count"])
            regional: list[dict[str, Any]] = []
            for region in target.regions:
                try:
                    stats = operational_stats_for(
                        arr=field,
                        region=region,
                        np=np,
                        root=root,
                        clip_bbox=target.candidate_bbox,
                    )
                except Exception as exc:  # noqa: BLE001
                    raise Step05DiagnosticError("TARGET_STATISTICS_FAILED", f"Unable to calculate {region['region_id']} statistics: {exc}") from exc
                selected = {key: stats.get(key) for key in requested_stats if key in stats}
                regional.append({
                    "region_id": str(region["region_id"]),
                    "region_name": str(region.get("name_ko", region["region_id"])),
                    "statistics": selected,
                })
                if include_ranking and stats.get("p95") is not None:
                    ranking_rows.append({
                        "region_id": str(region["region_id"]),
                        "region_name": str(region.get("name_ko", region["region_id"])),
                        "forecast_hour": hour,
                        "p95": float(stats["p95"]),
                    })
            per_hour.append({"forecast_hour": hour, "regional_statistics": regional})
        if total_finite == 0:
            raise Step05DiagnosticError("EMPTY_DATA", f"{request.get('request_id')} {feature_id} has no finite values in the selected target.")
        result: dict[str, Any] = {
            "units": fields.feature_unit(feature_id),
            "finite_count": total_finite,
            "nan_count": total_nan,
            "per_forecast_hour": per_hour,
        }
        if include_series:
            series: list[dict[str, Any]] = []
            for row in per_hour:
                if "statistics" in row:
                    stats = row["statistics"]
                    value = stats.get("area_weighted_mean", stats.get("mean"))
                    series.append({"forecast_hour": row["forecast_hour"], "value": value})
                else:
                    for regional in row["regional_statistics"]:
                        stats = regional["statistics"]
                        value = stats.get("area_weighted_mean", stats.get("mean"))
                        series.append({"forecast_hour": row["forecast_hour"], "region_id": regional["region_id"], "value": value})
            result["time_series"] = series
        if include_ranking:
            statistic = str(output_spec.get("ranking_statistic", "p95"))
            if statistic != "p95":
                raise Step05DiagnosticError("OUTPUT_SPEC_UNSUPPORTED", f"regional_ranking supports p95 only, got {statistic!r}.")
            top_n = int(output_spec.get("top_n", len(ranking_rows)))
            result["regional_ranking"] = sorted(ranking_rows, key=lambda item: item["p95"], reverse=True)[:top_n]
        features[feature_id] = result
    return features


def _styles(root: Path) -> dict[str, Any]:
    return _read_yaml(runtime_path(root, "daba", "analysis", "step05_render_styles.yaml"))


def _layout(styles: dict[str, Any], key: str) -> dict[str, Any]:
    values = styles.get("special_layout")
    layout = values.get(key) if isinstance(values, dict) else None
    if not isinstance(layout, dict):
        raise Step05DiagnosticError("STYLE_LOAD_FAILED", f"special_layout.{key} is missing.")
    return layout


def _text_position(layout: dict[str, Any], key: str, fallback: tuple[float, float]) -> tuple[float, float]:
    text = layout.get("text")
    item = text.get(key) if isinstance(text, dict) else None
    if not isinstance(item, dict):
        return fallback
    return float(item.get("x", fallback[0])), float(item.get("y", fallback[1]))


def _palette(styles: dict[str, Any], key: str) -> tuple[Any, Any, dict[str, Any]]:
    definitions = styles.get("diagnostic_palettes")
    item = definitions.get(key) if isinstance(definitions, dict) else None
    if not isinstance(item, dict):
        raise Step05DiagnosticError("STYLE_LOAD_FAILED", f"diagnostic_palettes.{key} is missing.")
    try:
        levels = [float(value) for value in item["levels"]]
        ticks = [float(value) for value in item["ticks"]]
        colors = [str(value) for value in item["colors"]]
        extend = str(item["extend"])
        missing = str(item["missing_color"])
    except (KeyError, TypeError, ValueError) as exc:
        raise Step05DiagnosticError("STYLE_LOAD_FAILED", f"diagnostic_palettes.{key} is malformed: {exc}") from exc
    if extend not in {"neither", "min", "max", "both"}:
        raise Step05DiagnosticError("STYLE_LOAD_FAILED", f"diagnostic_palettes.{key} has invalid extend={extend!r}.")
    expected_colors = len(levels) + 1 if extend == "both" else len(levels) if extend in {"min", "max"} else len(levels) - 1
    if len(levels) < 2 or levels != sorted(levels) or len(set(levels)) != len(levels) or len(colors) != expected_colors:
        raise Step05DiagnosticError("STYLE_LOAD_FAILED", f"diagnostic_palettes.{key} has incompatible levels/colors.")
    _plt, mcolors, _pe = import_matplotlib()
    cmap = mcolors.ListedColormap(colors, name=str(item.get("palette_id", key)))
    cmap.set_bad(missing)
    cmap.set_over(colors[-1])
    return cmap, mcolors.BoundaryNorm(levels, cmap.N, extend=extend), {
        "levels": levels, "ticks": ticks, "colors": colors, "extend": extend, "missing_color": missing,
    }


def _valid_time(init: str, hour: int) -> datetime:
    try:
        base = datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise Step05DiagnosticError("INIT_INVALID", f"init must be YYYYMMDDHH, got {init!r}.") from exc
    return base + timedelta(hours=int(hour))


def _format_kst_datetime(value: datetime) -> str:
    return value.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")


def _save_figure(fig: Any, output_path: Path) -> None:
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, bbox_inches="tight", pad_inches=2.0 / 25.4, facecolor="white")
    except Exception as exc:  # noqa: BLE001
        raise Step05DiagnosticError("PNG_RENDER_FAILED", f"Unable to write {output_path}: {exc}") from exc


def _png_path_details(path: Path) -> dict[str, Any]:
    try:
        from PIL import Image
        with Image.open(path) as image:
            return {"path": str(path), "pixel_size": list(image.size), "file_size_bytes": path.stat().st_size}
    except Exception as exc:  # noqa: BLE001
        raise Step05DiagnosticError("PNG_RENDER_FAILED", f"PNG could not be inspected: {path}: {exc}") from exc


def _value_summary(values: Any) -> dict[str, Any]:
    """Return compact finite/NaN metadata for data actually rendered."""
    array = np.asarray(values, dtype="float64")
    finite = np.isfinite(array)
    summary: dict[str, Any] = {
        "finite_count": int(finite.sum()),
        "nan_count": int(np.isnan(array).sum()),
        "minimum": None,
        "maximum": None,
    }
    if finite.any():
        summary["minimum"] = float(np.nanmin(array))
        summary["maximum"] = float(np.nanmax(array))
    return summary


def _check_extent(ax: Any, domain: MapDomain) -> None:
    actual = [float(ax.get_xlim()[0]), float(ax.get_xlim()[1]), float(ax.get_ylim()[0]), float(ax.get_ylim()[1])]
    expected = [domain.lon_min, domain.lon_max, domain.lat_min, domain.lat_max]
    if not all(math.isclose(left, right, rel_tol=0.0, abs_tol=1e-9) for left, right in zip(actual, expected, strict=True)):
        raise Step05DiagnosticError("EXTENT_MISMATCH", f"Rendered extent={actual} differs from requested_domain={expected}.")


def render_vertical_wind_shear_png(
    *, root: Path, init: str, request: dict[str, Any], hour: int, output_path: Path, fields: AIFSFields,
) -> dict[str, Any]:
    """Render the verified Step05 850--500 hPa ΔV single panel."""
    styles = _styles(root)
    layout = _layout(styles, "vertical_wind_shear")
    cmap, _norm, palette = _palette(styles, "vertical_wind_shear")
    domain_values = _bbox(request.get("requested_domain"), owner=f"{request.get('request_id')}.requested_domain")
    domain = _bbox_domain(domain_values)
    try:
        figure_size = tuple(float(value) for value in layout["figure_size_inches"])
        axes_rect = [float(value) for value in layout["axes_rect"]]
        colorbar_rect = [float(value) for value in layout["colorbar_rect"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise Step05DiagnosticError("STYLE_LOAD_FAILED", f"vertical_wind_shear layout is invalid: {exc}") from exc
    plt, _mcolors, _pe = import_matplotlib()
    u850, v850, u500, v500 = (fields.raw("u", 850, hour), fields.raw("v", 850, hour), fields.raw("u", 500, hour), fields.raw("v", 500, hour))
    u850, v850, u500, v500 = (subset_to_domain(item, domain) for item in (u850, v850, u500, v500))
    shapes = {tuple(np.asarray(item.values).shape) for item in (u850, v850, u500, v500)}
    if len(shapes) != 1:
        raise Step05DiagnosticError("ARRAY_SHAPE_MISMATCH", "850/500 hPa U/V shapes differ in requested_domain.")
    delta_u = np.asarray(u850.values) - np.asarray(u500.values)
    delta_v = np.asarray(v850.values) - np.asarray(v500.values)
    magnitude = np.hypot(delta_u, delta_v)
    if not np.isfinite(magnitude).any():
        raise Step05DiagnosticError("EMPTY_DATA", f"vertical wind shear F{hour:03d} is all NaN.")
    field, shear_u, shear_v = u850.copy(data=magnitude), u850.copy(data=delta_u), v850.copy(data=delta_v)
    fig = plt.figure(figsize=figure_size, dpi=180, facecolor="white")
    try:
        ax, cax = fig.add_axes(axes_rect, facecolor="white"), fig.add_axes(colorbar_rect, facecolor="white")
        map_style = layout.get("map_style", {})
        if not isinstance(map_style, dict):
            raise Step05DiagnosticError("STYLE_LOAD_FAILED", "vertical_wind_shear map_style must be a mapping.")
        style = {
            "cmap": "viridis", "longitude_tick_interval": 1.0, "latitude_tick_interval": 1.0,
            "wind_stride": 2, "quiver_scale": 125, "quiver_calm_threshold_ms": 0.0,
            "quiver_min_display_speed_ms": None, "quiver_max_display_speed_ms": None,
            "levels": palette["levels"], "ticks": palette["ticks"], "colors": palette["colors"], "extend": palette["extend"], **map_style,
        }
        panel = draw_product_panel(
            ax,
            product={"plot_type": "shaded_feature_plus_raw_vector", "style": style},
            fields={"shaded": field, "u": shear_u, "v": shear_v},
            panel_title="", domain=domain, context=RenderContext(project_root=root, domain=domain, style={}), overview_mode=False,
        )
        _check_extent(ax, domain)
        mesh = panel.get("mesh")
        if mesh is None:
            raise Step05DiagnosticError("PNG_RENDER_FAILED", "vertical_wind_shear did not create a shaded mesh.")
        mesh.cmap.set_bad(palette["missing_color"])
        mesh.cmap.set_over(palette["colors"][-1])
        cbar = fig.colorbar(mesh, cax=cax, boundaries=palette["levels"], ticks=palette["ticks"], spacing="proportional", extend=palette["extend"])
        cbar.set_label("850-500 hPa 연직바람시어 크기 (m s$^{-1}$)")
        from matplotlib.quiver import Quiver
        quivers = [artist for artist in ax.get_children() if isinstance(artist, Quiver)]
        if len(quivers) != 1:
            raise Step05DiagnosticError("PNG_RENDER_FAILED", f"vertical_wind_shear expected one quiver, got {len(quivers)}.")
        ref_x, ref_y = _text_position(layout, "reference", (0.55, 0.915))
        ax.quiverkey(quivers[0], ref_x, ref_y, 5.0, "전단벡터 ΔV = V850 - V500 | 5 m s$^{-1}$", coordinates="figure", labelpos="E", fontproperties={"size": 8.5})
        title_x, title_y = _text_position(layout, "title", (0.085, 0.987))
        metadata_x, metadata_y = _text_position(layout, "metadata", (0.085, 0.950))
        valid = _valid_time(init, hour)
        fig.text(title_x, title_y, "AIFS 850-500 hPa 연직바람시어", ha="left", va="top", fontsize=13)
        fig.text(metadata_x, metadata_y, f"초기시각 {_format_kst_datetime(_valid_time(init, 0))} | 유효시각 {_format_kst_datetime(valid)}", ha="left", va="top", fontsize=11)
        _save_figure(fig, output_path)
    finally:
        plt.close(fig)
    return {
        **_png_path_details(output_path),
        "physical_diagnostic": "VERTICAL_WIND_SHEAR_850_500",
        "forecast_hour": int(hour),
        "unit_label": "m s-1",
        "field_value_summary": _value_summary(magnitude),
    }


def _nonnegative_limits(values: list[float]) -> tuple[float, float]:
    maximum = float(np.nanmax(np.asarray(values, dtype="float64")))
    if not np.isfinite(maximum) or maximum < 0.0:
        raise Step05DiagnosticError("EMPTY_DATA", "Temporal series has no finite non-negative values.")
    from matplotlib.ticker import MaxNLocator
    candidates = [float(value) for value in MaxNLocator(nbins=6, steps=[1, 2, 2.5, 5, 10]).tick_values(0.0, maximum) if float(value) >= maximum]
    return 0.0, min(candidates) if candidates else maximum


def render_temporal_png(
    *, root: Path, init: str, payload: dict[str, Any], request: dict[str, Any], output_path: Path, fields: AIFSFields, hours: list[int],
) -> dict[str, Any]:
    """Render one requested candidate-domain scalar time series, never a nearest F-hour."""
    styles = _styles(root)
    layout = _layout(styles, "temporal_precipitation")
    product_id = str(request.get("product_id") or "QL_PRECIP_6H")
    field_product_id = _temporal_field_product_id(root, request)
    candidate = _candidate_for_request(payload, request)
    domain_values = _bbox(candidate.get("candidate_area"), owner=f"{request.get('candidate_id')}.candidate_area")
    domain = _bbox_domain(domain_values)
    values: list[float] = []
    unit = ""
    for hour in hours:
        physical = field_product_id if field_product_id in _MAP_PRODUCTS else product_id
        field, unit, _name = _field_for_product(fields, physical, hour)
        subset = subset_to_domain(field, domain)
        array = _validate_field_values(subset, label=f"temporal F{hour:03d}")
        values.append(float(np.nanmean(array)))
    try:
        figure_size = tuple(float(value) for value in layout["figure_size_inches"])
        adjust = {str(key): float(value) for key, value in dict(layout["subplot_adjust"]).items()}
    except (KeyError, TypeError, ValueError) as exc:
        raise Step05DiagnosticError("STYLE_LOAD_FAILED", f"temporal layout is invalid: {exc}") from exc
    plt, _mcolors, _pe = import_matplotlib()
    fig, ax = plt.subplots(figsize=figure_size, dpi=180, facecolor="white")
    try:
        fig.subplots_adjust(**adjust)
        ax.plot(hours, values, marker="o")
        ax.set_xticks(hours, [f"F{hour:03d}" for hour in hours])
        ax.set(xlabel="예측시간", ylabel=f"후보영역 평균 {unit}")
        if product_id == "QL_PRECIP_6H":
            ax.set_ylim(*_nonnegative_limits(values))
        ax.grid(True, alpha=0.35)
        title_x, title_y = _text_position(layout, "title", (0.100, 0.987))
        metadata_x, metadata_y = _text_position(layout, "metadata", (0.100, 0.950))
        field_label = _TEMPORAL_FIELD_LABELS.get(field_product_id, field_product_id)
        fig.text(title_x, title_y, f"AIFS 후보영역 평균 {field_label} 시계열", ha="left", va="top", fontsize=13)
        fig.text(metadata_x, metadata_y, f"초기시각 {_format_kst_datetime(_valid_time(init, 0))} | {request.get('candidate_id')} | {domain.lon_min:.1f}–{domain.lon_max:.1f}E, {domain.lat_min:.1f}–{domain.lat_max:.1f}N", ha="left", va="top", fontsize=11)
        _save_figure(fig, output_path)
    finally:
        plt.close(fig)
    return {
        **_png_path_details(output_path),
        "physical_diagnostic": field_product_id,
        "forecast_hour": None,
        "unit_label": unit,
        "field_value_summary": _value_summary(values),
    }


def _section_distance_km(lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    radius = 6371.0088
    lon, lat = np.deg2rad(lons), np.deg2rad(lats)
    dlon, dlat = np.diff(lon), np.diff(lat)
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(dlon / 2.0) ** 2
    return np.concatenate(([0.0], np.cumsum(2.0 * radius * np.arctan2(np.sqrt(a), np.sqrt(np.maximum(0.0, 1.0 - a))))))


def _core_panel_config(physical_diagnostic: str) -> dict[str, Any]:
    config = _CORE_PANEL_CONFIG.get(physical_diagnostic)
    if config is None:
        raise Step05DiagnosticError("UNSUPPORTED_DIAGNOSTIC", f"No CORE panel configuration exists for {physical_diagnostic!r}.")
    return config


def core_physical_diagnostics(root: Path, logical_product: str) -> tuple[str, ...]:
    """Resolve and validate the minimal physical CORE panels from the catalog."""
    diagnostics = _CORE_PHYSICAL_BY_LOGICAL_PRODUCT.get(logical_product)
    if diagnostics is None:
        raise Step05DiagnosticError("UNSUPPORTED_DIAGNOSTIC", f"{logical_product!r} is not a CORE section product.")
    catalog = _read_yaml(runtime_path(root, "daba", "analysis", "diagnostic_catalog.yaml"))
    contract = (catalog.get("product_contracts") or {}).get(logical_product)
    raw_fields = contract.get("raw_fields") if isinstance(contract, dict) else None
    produced = contract.get("produced_variables") if isinstance(contract, dict) else None
    if not isinstance(raw_fields, dict) or not isinstance(produced, list):
        raise Step05DiagnosticError("CORE_CONTRACT_INVALID", f"{logical_product} has no usable raw-field/produced-variable contract.")
    for physical_diagnostic in diagnostics:
        config = _core_panel_config(physical_diagnostic)
        catalog_declared = physical_diagnostic in produced
        step05_derived = bool(config.get("step05_only_derived"))
        if str(config["logical_product"]) != logical_product or (not catalog_declared and not step05_derived):
            raise Step05DiagnosticError(
                "CORE_CONTRACT_INVALID",
                f"{logical_product} does not declare physical diagnostic {physical_diagnostic}.",
            )
        for short_name in config["raw_fields"]:
            raw_spec = raw_fields.get(short_name)
            levels = raw_spec.get("levels") if isinstance(raw_spec, dict) else None
            if not isinstance(levels, list) or not levels or not all(isinstance(value, int) for value in levels):
                raise Step05DiagnosticError(
                    "PRESSURE_LEVEL_MISSING",
                    f"{logical_product}.{short_name} has no declared pressure-level contract for {physical_diagnostic}.",
                )
    return diagnostics


def _core_levels(root: Path, physical_diagnostic: str) -> list[int]:
    config = _core_panel_config(physical_diagnostic)
    logical_product = str(config["logical_product"])
    core_physical_diagnostics(root, logical_product)
    catalog = _read_yaml(runtime_path(root, "daba", "analysis", "diagnostic_catalog.yaml"))
    raw_fields = ((catalog.get("product_contracts") or {}).get(logical_product) or {}).get("raw_fields", {})
    declared: list[list[int]] = []
    for short_name in config["raw_fields"]:
        raw_spec = raw_fields.get(short_name)
        levels = raw_spec.get("levels") if isinstance(raw_spec, dict) else None
        if not isinstance(levels, list) or not all(isinstance(value, int) for value in levels):
            raise Step05DiagnosticError("PRESSURE_LEVEL_MISSING", f"{logical_product}.{short_name} has no usable levels.")
        declared.append([int(value) for value in levels])
    if not declared or any(levels != declared[0] for levels in declared[1:]):
        raise Step05DiagnosticError(
            "PRESSURE_LEVEL_MISMATCH",
            f"{physical_diagnostic} requires coincident pressure levels for {tuple(config['raw_fields'])}.",
        )
    return declared[0]


def _core_uv_grids(fields: AIFSFields, *, level: int, hour: int, physical_diagnostic: str) -> tuple[Any, Any]:
    first, second = fields.raw("u", level, hour), fields.raw("v", level, hour)
    first_lat, first_lon = lat_lon_names(first)
    second_lat, second_lon = lat_lon_names(second)
    first_grid, second_grid = first.transpose(first_lat, first_lon), second.transpose(second_lat, second_lon)
    if (
        first_grid.shape != second_grid.shape
        or not np.array_equal(np.asarray(first_grid[first_lat].values), np.asarray(second_grid[second_lat].values))
        or not np.array_equal(np.asarray(first_grid[first_lon].values), np.asarray(second_grid[second_lon].values))
    ):
        raise Step05DiagnosticError(
            "CORE_FIELD_GRID_MISMATCH",
            f"{physical_diagnostic} U/V grids do not have identical horizontal coordinates at {level} hPa F{hour:03d}.",
        )
    return first_grid, second_grid


def _project_along_section_wind(u_values: Any, v_values: Any, *, east_component: float, north_component: float) -> Any:
    """Project eastward/northward wind onto the explicitly oriented A-to-B axis."""
    return np.asarray(u_values, dtype="float64") * float(east_component) + np.asarray(v_values, dtype="float64") * float(north_component)


def _section_unit_vector(line: dict[str, Any]) -> tuple[float, float]:
    """Return east/north components of the great-circle initial A-to-B bearing."""
    try:
        start_latitude = math.radians(float(line["start_latitude"]))
        start_longitude = math.radians(float(line["start_longitude"]))
        end_latitude = math.radians(float(line["end_latitude"]))
        end_longitude = math.radians(float(line["end_longitude"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise Step05DiagnosticError("ALONG_SECTION_DIRECTION_INVALID", "A-to-B direction requires numeric line endpoints.") from exc
    delta_longitude = (end_longitude - start_longitude + math.pi) % (2.0 * math.pi) - math.pi
    east = math.sin(delta_longitude) * math.cos(end_latitude)
    north = math.cos(start_latitude) * math.sin(end_latitude) - math.sin(start_latitude) * math.cos(end_latitude) * math.cos(delta_longitude)
    magnitude = math.hypot(east, north)
    if not math.isfinite(magnitude) or magnitude <= 1e-12:
        raise Step05DiagnosticError("ALONG_SECTION_DIRECTION_INVALID", "A-to-B endpoints must define a non-zero horizontal direction.")
    return east / magnitude, north / magnitude


def _time_height_reference(candidate_area: dict[str, float]) -> dict[str, Any]:
    """Use the candidate-area physical major axis when Step04 has no section line."""
    midpoint_latitude = (candidate_area["south"] + candidate_area["north"]) / 2.0
    midpoint_longitude = (candidate_area["west"] + candidate_area["east"]) / 2.0
    zonal_span = abs(math.radians(candidate_area["east"] - candidate_area["west"]) * math.cos(math.radians(midpoint_latitude)))
    meridional_span = abs(math.radians(candidate_area["north"] - candidate_area["south"]))
    if zonal_span >= meridional_span:
        line = {
            "start_latitude": midpoint_latitude,
            "start_longitude": candidate_area["west"],
            "end_latitude": midpoint_latitude,
            "end_longitude": candidate_area["east"],
        }
        source = "candidate-area major axis (west-to-east)"
    else:
        line = {
            "start_latitude": candidate_area["south"],
            "start_longitude": midpoint_longitude,
            "end_latitude": candidate_area["north"],
            "end_longitude": midpoint_longitude,
        }
        source = "candidate-area major axis (south-to-north)"
    east, north = _section_unit_vector(line)
    return {**line, "east_component": east, "north_component": north, "source": source}


def _core_values(
    fields: AIFSFields,
    physical_diagnostic: str,
    level: int,
    hour: int,
    *,
    section_vector: tuple[float, float] | None = None,
) -> Any:
    """Return raw q, raw signed omega, wind speed, or a signed A-to-B component."""
    config = _core_panel_config(physical_diagnostic)
    raw_fields = tuple(str(value) for value in config["raw_fields"])
    if len(raw_fields) == 1:
        return fields.raw(raw_fields[0], level, hour)
    first_grid, second_grid = _core_uv_grids(
        fields,
        level=level,
        hour=hour,
        physical_diagnostic=physical_diagnostic,
    )
    wind_mode = str(config.get("wind_mode") or "speed")
    if wind_mode == "speed":
        values = np.hypot(np.asarray(first_grid.values), np.asarray(second_grid.values))
    elif wind_mode == "along_section":
        if section_vector is None:
            raise Step05DiagnosticError("ALONG_SECTION_DIRECTION_MISSING", f"{physical_diagnostic} has no A-to-B reference vector.")
        values = _project_along_section_wind(
            first_grid.values,
            second_grid.values,
            east_component=section_vector[0],
            north_component=section_vector[1],
        )
    else:
        raise Step05DiagnosticError("CORE_PANEL_CONFIG_INVALID", f"Unknown wind mode {wind_mode!r} for {physical_diagnostic}.")
    return first_grid.copy(data=values)


def _core_mean_value(
    fields: AIFSFields,
    physical_diagnostic: str,
    level: int,
    hour: int,
    domain: MapDomain,
    section_vector: tuple[float, float] | None = None,
) -> float:
    config = _core_panel_config(physical_diagnostic)
    if str(config.get("wind_mode") or "") == "along_section":
        if section_vector is None:
            raise Step05DiagnosticError("ALONG_SECTION_DIRECTION_MISSING", f"{physical_diagnostic} has no candidate-area reference vector.")
        u_grid, v_grid = _core_uv_grids(fields, level=level, hour=hour, physical_diagnostic=physical_diagnostic)
        u_values = _validate_field_values(subset_to_domain(u_grid, domain), label=f"u {level} hPa F{hour:03d}")
        v_values = _validate_field_values(subset_to_domain(v_grid, domain), label=f"v {level} hPa F{hour:03d}")
        return float(_project_along_section_wind(
            np.nanmean(u_values), np.nanmean(v_values), east_component=section_vector[0], north_component=section_vector[1],
        ) * float(config["scale"]))
    subset = subset_to_domain(_core_values(fields, physical_diagnostic, level, hour), domain)
    values = _validate_field_values(subset, label=f"{physical_diagnostic} {level} hPa F{hour:03d}")
    return float(np.nanmean(values) * float(config["scale"]))


def render_time_height_png(
    *, root: Path, init: str, payload: dict[str, Any], request: dict[str, Any], output_path: Path, fields: AIFSFields, hours: list[int],
    physical_diagnostic: str = "specific_humidity_time_height",
) -> dict[str, Any]:
    if physical_diagnostic not in _TIME_HEIGHT_PHYSICAL_DIAGNOSTICS:
        raise Step05DiagnosticError("UNSUPPORTED_DIAGNOSTIC", f"{physical_diagnostic!r} is not a time-height physical diagnostic.")
    config = _core_panel_config(physical_diagnostic)
    styles = _styles(root)
    layout = _layout(styles, str(config["layout"]))
    cmap, norm, palette = _palette(styles, str(config["palette"]))
    candidate = _candidate_for_request(payload, request)
    domain_values = _bbox(candidate.get("candidate_area"), owner=f"{request.get('candidate_id')}.candidate_area")
    domain, levels = _bbox_domain(domain_values), _core_levels(root, physical_diagnostic)
    reference = _time_height_reference(domain_values) if str(config.get("wind_mode") or "") == "along_section" else None
    section_vector = None if reference is None else (float(reference["east_component"]), float(reference["north_component"]))
    matrix = np.asarray([
        [_core_mean_value(fields, physical_diagnostic, level, hour, domain, section_vector=section_vector) for hour in hours]
        for level in levels
    ])
    if not np.isfinite(matrix).any():
        raise Step05DiagnosticError("EMPTY_DATA", f"Time-height {physical_diagnostic} matrix is all NaN.")
    try:
        figure_size = tuple(float(value) for value in layout["figure_size_inches"])
        axes_rect, cbar_rect = [float(value) for value in layout["axes_rect"]], [float(value) for value in layout["colorbar_rect"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise Step05DiagnosticError("STYLE_LOAD_FAILED", f"time-height layout is invalid: {exc}") from exc
    plt, _mcolors, _pe = import_matplotlib()
    fig = plt.figure(figsize=figure_size, dpi=180, facecolor="white")
    try:
        ax, cax = fig.add_axes(axes_rect, facecolor="white"), fig.add_axes(cbar_rect, facecolor="white")
        mesh = ax.pcolormesh(hours, levels, np.ma.masked_invalid(matrix), shading="auto", cmap=cmap, norm=norm)
        ax.invert_yaxis(); ax.set_xticks(hours, [f"F{hour:03d}" for hour in hours]); ax.set_yticks(levels); ax.set(xlabel="예측시간", ylabel="기압 (hPa)")
        fig.colorbar(mesh, cax=cax, label=str(config["colorbar_label"]), boundaries=palette["levels"], ticks=palette["ticks"], spacing="proportional", extend=palette["extend"])
        tx, ty = _text_position(layout, "title", (0.105, 0.987)); mx, my = _text_position(layout, "metadata", (0.105, 0.950))
        fig.text(tx, ty, str(config["title"]), ha="left", va="top", fontsize=13)
        fig.text(mx, my, f"초기시각 {_format_kst_datetime(_valid_time(init, 0))} | {request.get('candidate_id')} | {domain.lon_min:.1f}–{domain.lon_max:.1f}E, {domain.lat_min:.1f}–{domain.lat_max:.1f}N", ha="left", va="top", fontsize=11)
        if reference is not None:
            sx, sy = _text_position(layout, "axis", (0.105, 0.918))
            fig.text(sx, sy, f"Reference axis A {reference['start_longitude']:.1f}E, {reference['start_latitude']:.1f}N -> B {reference['end_longitude']:.1f}E, {reference['end_latitude']:.1f}N | {reference['source']}", ha="left", va="top", fontsize=10)
        _save_figure(fig, output_path)
    finally:
        plt.close(fig)
    return {
        **_png_path_details(output_path),
        "physical_diagnostic": physical_diagnostic,
        "forecast_hour": None,
        "unit_label": str(config["units"]),
        "field_value_summary": _value_summary(matrix),
    }


def _line_inside(domain: dict[str, float], line: dict[str, Any]) -> bool:
    return all(
        domain["west"] <= float(line[key_lon]) <= domain["east"] and domain["south"] <= float(line[key_lat]) <= domain["north"]
        for key_lat, key_lon in (("start_latitude", "start_longitude"), ("end_latitude", "end_longitude"))
    )


def _bilinear_axis_indices(axis: np.ndarray, point: float, *, label: str) -> tuple[np.ndarray, int, int, float]:
    """Normalize one monotonic grid axis and locate a point without nearest-cell fallback."""
    values = np.asarray(axis, dtype="float64")
    if values.ndim != 1 or values.size < 2:
        raise Step05DiagnosticError("CROSS_SECTION_INTERPOLATION_INVALID", f"{label} coordinates must be a one-dimensional grid with at least two points.")
    differences = np.diff(values)
    if np.all(differences > 0.0):
        normalized = values
        reverse = False
    elif np.all(differences < 0.0):
        normalized = values[::-1]
        reverse = True
    else:
        raise Step05DiagnosticError("CROSS_SECTION_INTERPOLATION_INVALID", f"{label} coordinates must be strictly monotonic.")
    tolerance = max(1.0, float(np.nanmax(np.abs(normalized)))) * 1e-10
    if point < normalized[0] - tolerance or point > normalized[-1] + tolerance:
        raise Step05DiagnosticError(
            "CROSS_SECTION_OUT_OF_DOMAIN",
            f"Cross-section point {point:.8f} is outside available {label} grid [{normalized[0]:.8f}, {normalized[-1]:.8f}].",
        )
    if math.isclose(point, float(normalized[0]), rel_tol=0.0, abs_tol=tolerance):
        lower = upper = 0
    elif math.isclose(point, float(normalized[-1]), rel_tol=0.0, abs_tol=tolerance):
        lower = upper = normalized.size - 1
    else:
        upper = int(np.searchsorted(normalized, point, side="right"))
        lower = upper - 1
        if math.isclose(point, float(normalized[lower]), rel_tol=0.0, abs_tol=tolerance):
            upper = lower
        elif math.isclose(point, float(normalized[upper]), rel_tol=0.0, abs_tol=tolerance):
            lower = upper
    fraction = 0.0 if lower == upper else float((point - normalized[lower]) / (normalized[upper] - normalized[lower]))
    if reverse:
        return normalized, values.size - 1 - lower, values.size - 1 - upper, fraction
    return normalized, lower, upper, fraction


def bilinear_horizontal_value(
    data: Any,
    latitude: Any,
    longitude: Any,
    *,
    sample_latitude: float,
    sample_longitude: float,
) -> float:
    """Bilinearly interpolate one scalar grid value; required NaN corners remain NaN.

    Exact grid-line samples require only the one or two geometrically relevant
    cells.  Every required corner must be finite; otherwise no nearest-value
    substitution is made and NaN is propagated.
    """
    values = np.asarray(data, dtype="float64")
    latitudes = np.asarray(latitude, dtype="float64")
    longitudes = np.asarray(longitude, dtype="float64")
    if values.ndim != 2 or values.shape != (latitudes.size, longitudes.size):
        raise Step05DiagnosticError(
            "CROSS_SECTION_INTERPOLATION_INVALID",
            f"Cross-section field shape {values.shape} does not match latitude/longitude grids {(latitudes.size, longitudes.size)}.",
        )
    _normalized_latitudes, lat_low, lat_high, lat_fraction = _bilinear_axis_indices(latitudes, float(sample_latitude), label="latitude")
    _normalized_longitudes, lon_low, lon_high, lon_fraction = _bilinear_axis_indices(longitudes, float(sample_longitude), label="longitude")
    corner_indices = {
        (lat_low, lon_low),
        (lat_low, lon_high),
        (lat_high, lon_low),
        (lat_high, lon_high),
    }
    if any(not np.isfinite(values[row, column]) for row, column in corner_indices):
        return float("nan")
    lower = (1.0 - lon_fraction) * values[lat_low, lon_low] + lon_fraction * values[lat_low, lon_high]
    upper = (1.0 - lon_fraction) * values[lat_high, lon_low] + lon_fraction * values[lat_high, lon_high]
    return float((1.0 - lat_fraction) * lower + lat_fraction * upper)


def render_cross_section_png(
    *, root: Path, init: str, payload: dict[str, Any], request: dict[str, Any], hour: int, output_path: Path, fields: AIFSFields,
    physical_diagnostic: str = "specific_humidity_cross_section",
) -> dict[str, Any]:
    if physical_diagnostic not in _CROSS_SECTION_PHYSICAL_DIAGNOSTICS:
        raise Step05DiagnosticError("UNSUPPORTED_DIAGNOSTIC", f"{physical_diagnostic!r} is not a cross-section physical diagnostic.")
    config = _core_panel_config(physical_diagnostic)
    styles = _styles(root)
    layout = _layout(styles, str(config["layout"]))
    cmap, norm, palette = _palette(styles, str(config["palette"]))
    requested = _bbox(request.get("requested_domain"), owner=f"{request.get('request_id')}.requested_domain")
    line = request.get("cross_section_line")
    required = ("start_latitude", "start_longitude", "end_latitude", "end_longitude")
    if not isinstance(line, dict) or any(key not in line for key in required):
        raise Step05DiagnosticError("CROSS_SECTION_INVALID", f"{request.get('request_id')} has no complete Step04 cross_section_line.")
    if not _line_inside(requested, line):
        raise Step05DiagnosticError("CROSS_SECTION_INVALID", "Step04 cross-section A/B endpoints are outside requested_domain.")
    candidate_bbox = _bbox(_candidate_for_request(payload, request).get("candidate_area"), owner=f"{request.get('candidate_id')}.candidate_area")
    n = 80
    lons = np.linspace(float(line["start_longitude"]), float(line["end_longitude"]), n)
    lats = np.linspace(float(line["start_latitude"]), float(line["end_latitude"]), n)
    if not np.any((lons >= candidate_bbox["west"]) & (lons <= candidate_bbox["east"]) & (lats >= candidate_bbox["south"]) & (lats <= candidate_bbox["north"])):
        raise Step05DiagnosticError("CROSS_SECTION_INVALID", "Step04 cross-section does not intersect the candidate area.")
    levels = _core_levels(root, physical_diagnostic)
    section_vector = _section_unit_vector(line) if str(config.get("wind_mode") or "") == "along_section" else None
    rows: list[list[float]] = []
    for level in levels:
        if section_vector is not None:
            u_grid, v_grid = _core_uv_grids(fields, level=level, hour=hour, physical_diagnostic=physical_diagnostic)
            lat_name, lon_name = lat_lon_names(u_grid)
            lat_values = np.asarray(u_grid[lat_name].values)
            lon_values = np.asarray(u_grid[lon_name].values)
            u_data, v_data = np.asarray(u_grid.values), np.asarray(v_grid.values)
            rows.append([
                float(_project_along_section_wind(
                    bilinear_horizontal_value(u_data, lat_values, lon_values, sample_latitude=float(y), sample_longitude=float(x)),
                    bilinear_horizontal_value(v_data, lat_values, lon_values, sample_latitude=float(y), sample_longitude=float(x)),
                    east_component=section_vector[0],
                    north_component=section_vector[1],
                ) * float(config["scale"]))
                for x, y in zip(lons, lats, strict=True)
            ])
        else:
            field = _core_values(fields, physical_diagnostic, level, hour)
            lat_name, lon_name = lat_lon_names(field)
            grid = field.transpose(lat_name, lon_name)
            lat_values = np.asarray(grid[lat_name].values)
            lon_values = np.asarray(grid[lon_name].values)
            data = np.asarray(grid.values)
            rows.append([
                bilinear_horizontal_value(
                    data,
                    lat_values,
                    lon_values,
                    sample_latitude=float(y),
                    sample_longitude=float(x),
                ) * float(config["scale"])
                for x, y in zip(lons, lats, strict=True)
            ])
    matrix = np.asarray(rows)
    if not np.isfinite(matrix).any():
        raise Step05DiagnosticError("EMPTY_DATA", f"Cross-section {physical_diagnostic} matrix is all NaN.")
    try:
        figure_size = tuple(float(value) for value in layout["figure_size_inches"])
        axes_rect, cbar_rect = [float(value) for value in layout["axes_rect"]], [float(value) for value in layout["colorbar_rect"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise Step05DiagnosticError("STYLE_LOAD_FAILED", f"cross-section layout is invalid: {exc}") from exc
    distance = _section_distance_km(lons, lats)
    plt, _mcolors, _pe = import_matplotlib()
    fig = plt.figure(figsize=figure_size, dpi=180, facecolor="white")
    try:
        ax, cax = fig.add_axes(axes_rect, facecolor="white"), fig.add_axes(cbar_rect, facecolor="white")
        mesh = ax.pcolormesh(distance, levels, np.ma.masked_invalid(matrix), shading="auto", cmap=cmap, norm=norm)
        ax.invert_yaxis(); ax.set_yticks(levels)
        ticks = np.arange(0.0, float(distance[-1]) + 1e-9, 50.0)
        ax.set_xticks(ticks, [f"{value:.0f}" for value in ticks]); ax.set(xlabel="단면거리 (km)", ylabel="기압 (hPa)")
        fig.colorbar(mesh, cax=cax, label=str(config["colorbar_label"]), boundaries=palette["levels"], ticks=palette["ticks"], spacing="proportional", extend=palette["extend"])
        tx, ty = _text_position(layout, "title", (0.100, 0.989)); mx, my = _text_position(layout, "metadata", (0.100, 0.951)); sx, sy = _text_position(layout, "section", (0.100, 0.918))
        valid = _valid_time(init, hour)
        fig.text(tx, ty, str(config["title"]), ha="left", va="top", fontsize=13)
        fig.text(mx, my, f"초기시각 {_format_kst_datetime(_valid_time(init, 0))} | 유효시각 {_format_kst_datetime(valid)}", ha="left", va="top", fontsize=11)
        fig.text(sx, sy, f"A {lons[0]:.1f}E, {lats[0]:.1f}N  →  B {lons[-1]:.1f}E, {lats[-1]:.1f}N | 총 {distance[-1]:.1f} km", ha="left", va="top", fontsize=10)
        _save_figure(fig, output_path)
    finally:
        plt.close(fig)
    return {
        **_png_path_details(output_path),
        "physical_diagnostic": physical_diagnostic,
        "forecast_hour": int(hour),
        "unit_label": str(config["units"]),
        "field_value_summary": _value_summary(matrix),
    }


def _safe(value: Any) -> str:
    return _SAFE_PART.sub("_", str(value or "UNKNOWN")).strip("_") or "UNKNOWN"


def image_filename(request_id: str, physical_product: str, hour: int | None = None) -> str:
    suffix = f"_F{int(hour):03d}" if hour is not None else ""
    return f"{_safe(request_id)}_{_safe(physical_product)}{suffix}.png"


def cleanup_request_artifacts(*, diagnostics_dir: Path, images_dir: Path, request_id: str) -> None:
    """Remove only one request's operational JSON and PNG artifacts."""
    result_path = diagnostics_dir / f"{request_id}.json"
    try:
        if result_path.is_file():
            result_path.unlink()
        image_prefix = f"{_safe(request_id)}_"
        if images_dir.is_dir():
            for path in images_dir.glob(f"{image_prefix}*.png"):
                if path.is_file():
                    path.unlink()
    except OSError as exc:
        raise Step05DiagnosticError("OUTPUT_CLEANUP_FAILED", f"Unable to clean artifacts for {request_id}: {exc}") from exc


def _request_id_from_diagnostic_filename(filename: str) -> str | None:
    if not filename.endswith(".json"):
        return None
    request_id = filename.removesuffix(".json")
    return request_id if _REQUEST_ID.fullmatch(request_id) else None


def _request_id_from_image_filename(filename: str) -> str | None:
    if not filename.endswith(".png"):
        return None
    stem = filename.removesuffix(".png")
    for product in sorted(_KNOWN_IMAGE_PRODUCTS, key=len, reverse=True):
        marker = f"_{_safe(product)}"
        if stem.endswith(marker):
            request_id = stem[: -len(marker)]
        else:
            match = re.fullmatch(rf"(.+){re.escape(marker)}_F\d{{3}}", stem)
            if match is None:
                continue
            request_id = match.group(1)
        if _REQUEST_ID.fullmatch(request_id):
            return request_id
    return None


def cleanup_stale_init_artifacts(
    *, diagnostics_dir: Path, images_dir: Path, live_request_ids: Iterable[str],
) -> tuple[str, ...]:
    """On a full overwrite only, remove valid stale Step05 artifacts for this init."""
    live = {str(request_id) for request_id in live_request_ids}
    if any(_REQUEST_ID.fullmatch(request_id) is None for request_id in live):
        raise Step05DiagnosticError("REQUEST_ID_INVALID", "Full-init stale cleanup requires valid Step04 REQ_* request IDs.")
    stale_paths: list[Path] = []
    if diagnostics_dir.is_dir():
        for path in diagnostics_dir.glob("REQ_*.json"):
            request_id = _request_id_from_diagnostic_filename(path.name)
            if request_id is not None and request_id not in live:
                stale_paths.append(path)
    if images_dir.is_dir():
        for path in images_dir.glob("REQ_*.png"):
            request_id = _request_id_from_image_filename(path.name)
            if request_id is not None and request_id not in live:
                stale_paths.append(path)
    try:
        for path in stale_paths:
            if path.is_file():
                path.unlink()
    except OSError as exc:
        raise Step05DiagnosticError("OUTPUT_CLEANUP_FAILED", f"Unable to remove stale Step05 init artifacts: {exc}") from exc
    return tuple(path.name for path in stale_paths)


def physical_diagnostics_for(root: Path, request: dict[str, Any]) -> tuple[str, ...]:
    """Expand logical CORE products into their validated physical panel set."""
    physical: list[str] = []
    for logical_product in physical_products_for(request):
        if logical_product in _CORE_PHYSICAL_BY_LOGICAL_PRODUCT:
            physical.extend(core_physical_diagnostics(root, logical_product))
        else:
            physical.append(logical_product)
    return tuple(physical)


def _image_jobs(root: Path, request: dict[str, Any], images_dir: Path, hours: list[int]) -> list[tuple[str, int | None, Path]]:
    request_id = str(request.get("request_id") or "")
    jobs: list[tuple[str, int | None, Path]] = []
    for product in physical_diagnostics_for(root, request):
        if product == "TEMPORAL_EVOLUTION" or product in _TIME_HEIGHT_PHYSICAL_DIAGNOSTICS:
            jobs.append((product, None, images_dir / image_filename(request_id, product)))
        else:
            jobs.extend((product, hour, images_dir / image_filename(request_id, product, hour)) for hour in hours)
    return jobs


def _image_artifact(root: Path, product: str, rendered: dict[str, Any], fallback_hour: int | None) -> dict[str, Any]:
    summary = rendered.get("field_value_summary")
    if not isinstance(summary, dict):
        raise Step05DiagnosticError("SPATIAL_METADATA_MISSING", f"{product} renderer returned no field value summary.")
    output_path = rendered.get("output_path") or rendered.get("path")
    if output_path is None:
        raise Step05DiagnosticError("SPATIAL_METADATA_MISSING", f"{product} renderer returned no output path.")
    path = Path(output_path)
    relative_path = logical_ref(root, path)
    finite_count = summary.get("finite_count")
    nan_count = summary.get("nan_count")
    if not isinstance(finite_count, int) or not isinstance(nan_count, int):
        raise Step05DiagnosticError("SPATIAL_METADATA_MISSING", f"{product} renderer returned invalid finite/NaN counts.")
    artifact = {
        "path": relative_path,
        "physical_diagnostic": str(rendered.get("physical_diagnostic") or rendered.get("product_id") or product),
        "forecast_hour": rendered.get("forecast_hour", fallback_hour),
        "units": str(rendered.get("unit_label") or "") or None,
        "finite_count": finite_count,
        "nan_count": nan_count,
        "min": summary.get("minimum"),
        "max": summary.get("maximum"),
    }
    for key in (
        "operational_scope_field_value_summary",
        "operational_scope_region_summaries",
        "precipitation_object_policy",
        "precipitation_objects",
    ):
        value = rendered.get(key)
        if value is not None:
            artifact[key] = value
    return artifact


def _remove_created_paths(paths: Iterable[Path]) -> None:
    """Best-effort rollback that never hides the actionable originating error."""
    for path in paths:
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass


def _assert_generated_image_completeness(
    jobs: Iterable[tuple[str, int | None, Path]], generated: Iterable[dict[str, Any]],
) -> None:
    expected = {(product, hour) for product, hour, _output in jobs}
    actual = {(str(item["physical_diagnostic"]), item["forecast_hour"]) for item in generated}
    if actual != expected:
        raise Step05DiagnosticError(
            "SPATIAL_ARTIFACT_INCOMPLETE",
            f"Physical panels differ from request: expected={sorted(expected)}, generated={sorted(actual)}.",
        )


def _is_system_direct_precipitation_request(
    payload: dict[str, Any], request: dict[str, Any]
) -> bool:
    """Return whether a Step04 request belongs to the Python-owned presence anchor."""
    candidate_id = str(request.get("candidate_id") or "")
    source_check_ids = {
        str(value) for value in request.get("source_check_ids", [])
        if isinstance(value, str) and value
    }
    check_id = str(request.get("check_id") or "")
    if check_id:
        source_check_ids.add(check_id)
    if not candidate_id or not source_check_ids:
        return False
    for plan in payload.get("candidate_analysis_plans", []):
        if not isinstance(plan, dict) or str(plan.get("candidate_id") or "") != candidate_id:
            continue
        for check in plan.get("variable_checks", []):
            if not isinstance(check, dict) or str(check.get("check_id") or "") not in source_check_ids:
                continue
            intent = check.get("intent_contract")
            if not isinstance(intent, dict):
                continue
            if (
                intent.get("direct_precipitation_anchor") is True
                and str(intent.get("source_check_key") or "")
                == SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY
            ):
                return True
    return False


def render_request_images(
    *, root: Path, init: str, payload: dict[str, Any], request: dict[str, Any], fields: AIFSFields, images_dir: Path, hours: list[int], overwrite: bool,
) -> list[dict[str, Any]]:
    """Produce only the physical, single-panel images demanded by one request."""
    if str(request.get("data_type") or "") != "spatial_field":
        return []
    jobs = _image_jobs(root, request, images_dir, hours)
    for _product, _hour, output in jobs:
        if output.exists() and not overwrite:
            raise Step05DiagnosticError("OUTPUT_EXISTS", f"Output exists: {output}; rerun with --overwrite.")
    generated: list[dict[str, Any]] = []
    created: list[Path] = []
    try:
        for product, hour, output in jobs:
            # A path is tracked before rendering so a renderer that writes and
            # then fails during post-render validation cannot leave a partial file.
            created.append(output)
            if product in _MAP_PRODUCTS:
                assert hour is not None
                rendered_request = dict(request)
                rendered_request["product_id"] = product
                if product == "QL_PRECIP_6H" and _is_system_direct_precipitation_request(payload, request):
                    # The candidate bbox remains the analysis canvas, but the
                    # SYSTEM presence anchor may only be satisfied by cells in
                    # KMA operational land/marine forecast regions.
                    rendered_request[SYSTEM_DIRECT_PRECIPITATION_OPERATIONAL_MASK_KEY] = True
                target = _target_map(payload).get(str(request.get("target_id") or ""))
                if isinstance(target, dict) and target.get("target_type") == "context_domain":
                    candidate = _candidate_for_request(payload, request)
                    rendered_request["_candidate_roi_bbox"] = _bbox(
                        candidate.get("candidate_area"), owner=f"{request.get('candidate_id')}.candidate_area"
                    )
                try:
                    rendered = render_spatial_field_png(
                        root=root, init=init, request=rendered_request, forecast_hour=hour, output_path=output,
                    )
                except Step05RenderError as exc:
                    raise Step05DiagnosticError(exc.code, str(exc)) from exc
            elif product == "VERTICAL_WIND_SHEAR_850_500":
                assert hour is not None
                rendered = render_vertical_wind_shear_png(
                    root=root, init=init, request=request, hour=hour, output_path=output, fields=fields,
                )
            elif product in _TIME_HEIGHT_PHYSICAL_DIAGNOSTICS:
                rendered = render_time_height_png(
                    root=root, init=init, payload=payload, request=request, output_path=output, fields=fields, hours=hours,
                    physical_diagnostic=product,
                )
            elif product in _CROSS_SECTION_PHYSICAL_DIAGNOSTICS:
                assert hour is not None
                rendered = render_cross_section_png(
                    root=root, init=init, payload=payload, request=request, hour=hour, output_path=output, fields=fields,
                    physical_diagnostic=product,
                )
            elif product == "TEMPORAL_EVOLUTION":
                rendered = render_temporal_png(
                    root=root, init=init, payload=payload, request=request, output_path=output, fields=fields, hours=hours,
                )
            else:
                raise Step05DiagnosticError("UNSUPPORTED_DIAGNOSTIC", f"No Step05 renderer for {product}.")
            generated.append(_image_artifact(root, product, rendered, hour))
        _assert_generated_image_completeness(jobs, generated)
    except Exception:
        _remove_created_paths(created)
        raise
    return generated


def result_document(
    *, root: Path, request: dict[str, Any], target: TargetSelection | None, hours: list[int], status: str, features: dict[str, dict[str, Any]] | None = None, generated_images: list[dict[str, Any]] | None = None, failure: Step05DiagnosticError | None = None,
) -> dict[str, Any]:
    """Build the compact, schema-valid request result persisted by the CLI."""
    selected_domain = target.bbox if target is not None and target.kind == "requested_domain" else target.candidate_bbox if target is not None else None
    feature_payload = features or {}
    image_payload = generated_images or []
    available_components = (
        actual_signal_components(
            load_diagnostic_catalog(root),
            product_id=str(request.get("product_id") or ""),
            generated_images=image_payload,
            features=feature_payload,
        )
        if status == "success"
        else []
    )
    return {
        "request_id": str(request.get("request_id") or ""),
        "candidate_id": str(request.get("candidate_id") or ""),
        "diagnostic_id": str(request.get("diagnostic_id") or ""),
        "data_type": str(request.get("data_type") or ""),
        "status": status,
        "forecast_hours": [int(hour) for hour in hours],
        "target_id": str(request.get("target_id") or "") or None,
        "selected_domain": selected_domain,
        "features": feature_payload,
        "generated_images": image_payload,
        "available_signal_components": available_components,
        "failure_code": failure.code if failure is not None else None,
        "failure_message": str(failure) if failure is not None else None,
    }


def validate_result_schema(root: Path, result: dict[str, Any]) -> None:
    schema_path = runtime_path(root, "prompts", "schemas", "step05_diagnostic_result.schema.json")
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        import jsonschema
        jsonschema.validate(result, schema)
    except (OSError, json.JSONDecodeError, ModuleNotFoundError) as exc:
        raise Step05DiagnosticError("SCHEMA_VALIDATION_FAILED", f"Unable to load Step05 result schema: {exc}") from exc
    except Exception as exc:  # jsonschema.ValidationError without importing a private class.
        raise Step05DiagnosticError("SCHEMA_VALIDATION_FAILED", f"Step05 result schema validation failed: {exc}") from exc


def write_result(root: Path, path: Path, result: dict[str, Any]) -> None:
    validate_result_schema(root, result)
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary_path.replace(path)
    except (OSError, ValueError) as exc:
        raise Step05DiagnosticError("RESULT_WRITE_FAILED", f"Unable to write Step05 result {path}: {exc}") from exc
    finally:
        try:
            if temporary_path.is_file():
                temporary_path.unlink()
        except OSError:
            pass


def cleanup_generated_image_artifacts(*, root: Path, images_dir: Path, generated_images: Iterable[dict[str, Any]]) -> None:
    """Remove only verified image paths created by the currently failing request."""
    images_path = images_dir.resolve()
    rollback_paths: list[Path] = []
    for image in generated_images:
        relative = image.get("path") if isinstance(image, dict) else None
        if not isinstance(relative, str) or not relative:
            continue
        candidate = Path(relative)
        resolved = candidate.resolve() if candidate.is_absolute() else resolve_logical_ref(root, candidate)
        if resolved.suffix.lower() == ".png" and resolved.is_relative_to(images_path):
            rollback_paths.append(resolved)
    _remove_created_paths(rollback_paths)


def write_spatial_result_atomically(
    *, root: Path, images_dir: Path, result_path: Path, result: dict[str, Any], generated_images: list[dict[str, Any]],
) -> None:
    """Commit a spatial result or roll back its current request's image artifacts."""
    try:
        write_result(root, result_path, result)
    except Exception:
        cleanup_generated_image_artifacts(root=root, images_dir=images_dir, generated_images=generated_images)
        raise
