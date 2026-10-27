"""Step06 deterministic Pass2 evidence preparation.

This module is intentionally downstream-only: it indexes immutable Step04
requests and existing Step05 outputs.  ``request_id`` is the Step04--Step05
traceability key; ``source_check_ids`` is the Step04 check--evidence key.
No AI call, GRIB read, image rendering, or source artifact duplication occurs
here.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from func.shared.pipeline_common import canonical_hash, sha256sum
from func.shared.runtime_paths import logical_ref, resolve_logical_ref, resolve_step04_output_dir, runtime_path


class Pass2PackageError(RuntimeError):
    """Deterministic Step06 input-contract failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY = "SYS_DIRECT_PRECIPITATION_PRESENCE"


def _is_system_direct_precipitation_anchor_check(check: dict[str, Any]) -> bool:
    intent = check.get("intent_contract")
    return bool(
        isinstance(intent, dict)
        and intent.get("direct_precipitation_anchor") is True
        and str(intent.get("source_check_key") or "")
        == SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY
    )


def _bbox_signature(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        return tuple(float(value[key]) for key in ("west", "east", "south", "north"))
    except (KeyError, TypeError, ValueError):
        return None


def _bbox_contains(outer: Any, inner: Any) -> bool:
    outer_sig = _bbox_signature(outer)
    inner_sig = _bbox_signature(inner)
    if outer_sig is None or inner_sig is None:
        return False
    ow, oe, os, on = outer_sig
    iw, ie, ins, inn = inner_sig
    return ow <= iw and oe >= ie and os <= ins and on >= inn


def _target_area_index(context: dict[str, Any], candidate_id: str) -> dict[str, dict[str, Any]]:
    plan = context.get("analysis_plan") if isinstance(context, dict) else None
    targets = plan.get("target_areas") if isinstance(plan, dict) else None
    if not isinstance(targets, list):
        raise Pass2PackageError("TARGET_AREAS_MISSING", f"{candidate_id} analysis_plan has no target_areas.")
    indexed: dict[str, dict[str, Any]] = {}
    for target in targets:
        if not isinstance(target, dict):
            raise Pass2PackageError("TARGET_AREA_INVALID", f"{candidate_id} has a non-object target_area.")
        target_id = str(target.get("target_id") or "").strip()
        if not target_id or target_id in indexed:
            raise Pass2PackageError("TARGET_AREA_INVALID", f"{candidate_id} has invalid or duplicate target_id={target_id!r}.")
        indexed[target_id] = target
    return indexed


def _validate_spatial_lineage(candidate_id: str, context: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    """Validate Step04 target scope -> request -> Step05 selected-domain lineage.

    This is geometric/traceability validation only.  It never infers weather or
    decides whether a meteorological signal is present.
    """
    candidate = context.get("candidate") if isinstance(context, dict) else None
    if not isinstance(candidate, dict):
        raise Pass2PackageError("CANDIDATE_CONTEXT_INVALID", f"{candidate_id} has no candidate context.")
    candidate_area = candidate.get("candidate_area")
    candidate_sig = _bbox_signature(candidate_area)
    if candidate_sig is None:
        raise Pass2PackageError("CANDIDATE_AREA_INVALID", f"{candidate_id} has no valid candidate_area.")

    targets = _target_area_index(context, candidate_id)
    for target_id, target in targets.items():
        target_type = str(target.get("target_type") or "")
        resolution_method = str(target.get("resolution_method") or "")
        requested_domain = target.get("requested_domain")
        target_sig = _bbox_signature(requested_domain)
        if target_type == "candidate_domain":
            if resolution_method != "explicit_bbox" or target_sig is None or target_sig != candidate_sig:
                raise Pass2PackageError(
                    "SPATIAL_LINEAGE_INVALID",
                    f"{candidate_id} target={target_id} candidate_domain must use explicit_bbox equal to candidate_area.",
                )
        elif target_type == "context_domain":
            if resolution_method != "explicit_bbox" or target_sig is None or not _bbox_contains(requested_domain, candidate_area):
                raise Pass2PackageError(
                    "SPATIAL_LINEAGE_INVALID",
                    f"{candidate_id} target={target_id} context_domain must use explicit_bbox containing candidate_area.",
                )
        elif target_type == "operational_regions":
            if resolution_method not in {"candidate_area_overlap", "explicit_region_ids"}:
                raise Pass2PackageError(
                    "SPATIAL_LINEAGE_INVALID",
                    f"{candidate_id} target={target_id} operational_regions has invalid resolution_method={resolution_method!r}.",
                )
            if requested_domain is not None:
                raise Pass2PackageError(
                    "SPATIAL_LINEAGE_INVALID",
                    f"{candidate_id} target={target_id} operational_regions must not declare requested_domain.",
                )
        else:
            raise Pass2PackageError(
                "SPATIAL_LINEAGE_INVALID",
                f"{candidate_id} target={target_id} has unsupported target_type={target_type!r}.",
            )

    for entry in entries:
        request = entry.get("request") if isinstance(entry, dict) else None
        step05 = entry.get("step05") if isinstance(entry, dict) else None
        if not isinstance(request, dict) or not isinstance(step05, dict):
            raise Pass2PackageError("REQUEST_ENTRY_INVALID", f"{candidate_id} has invalid request/Step05 spatial lineage entry.")
        request_id = str(request.get("request_id") or "")
        target_id = str(request.get("target_id") or "")
        target = targets.get(target_id)
        if target is None:
            raise Pass2PackageError(
                "SPATIAL_LINEAGE_INVALID",
                f"{candidate_id} request={request_id} references unknown target_id={target_id!r}.",
            )
        target_type = str(target.get("target_type") or "")
        target_domain = target.get("requested_domain")
        request_domain = request.get("requested_domain")
        if _bbox_signature(target_domain) != _bbox_signature(request_domain):
            raise Pass2PackageError(
                "SPATIAL_LINEAGE_INVALID",
                f"{candidate_id} request={request_id} requested_domain does not match target={target_id}.",
            )

        result = step05.get("result")
        if isinstance(result, dict):
            if str(result.get("target_id") or "") != target_id:
                raise Pass2PackageError(
                    "SPATIAL_LINEAGE_INVALID",
                    f"{candidate_id} request={request_id} Step05 target_id does not match Step04 target_id={target_id}.",
                )
            if step05.get("status") == "success" and target_type in {"candidate_domain", "context_domain"}:
                if _bbox_signature(result.get("selected_domain")) != _bbox_signature(target_domain):
                    raise Pass2PackageError(
                        "SPATIAL_LINEAGE_INVALID",
                        f"{candidate_id} request={request_id} Step05 selected_domain does not match target={target_id} requested_domain.",
                    )


def _legacy_check_scope(check: dict[str, Any]) -> str:
    diagnostic_id = str(check.get("diagnostic_id") or "")
    methods = {str(value) for value in check.get("analysis_methods", [])}
    if diagnostic_id == "multivariable_composite":
        return "multivariable_alignment"
    if diagnostic_id in {"vertical_cross_section", "time_height_section"}:
        return "vertical_structure"
    if diagnostic_id == "temporal_evolution" or "temporal_evolution" in methods:
        return "temporal_evolution"
    return "single_variable"


def _required_signal_components(check: dict[str, Any]) -> list[str]:
    values = check.get("required_signal_components")
    if not isinstance(values, list) or not values:
        raise Pass2PackageError(
            "REQUIRED_SIGNAL_COMPONENTS_MISSING",
            f"{check.get('check_id')} must declare non-empty required_signal_components; re-run Step04.",
        )
    normalized = list(dict.fromkeys(str(value) for value in values if str(value)))
    if not normalized:
        raise Pass2PackageError(
            "REQUIRED_SIGNAL_COMPONENTS_MISSING",
            f"{check.get('check_id')} has no usable required_signal_components; re-run Step04.",
        )
    return normalized


def _available_signal_components(linked: list[dict[str, Any]]) -> list[str]:
    """Return only components explicitly declared by successful Step05 results."""
    components: set[str] = set()
    for entry in linked:
        if not _request_has_successful_evidence(entry):
            continue
        step05 = entry.get("step05") if isinstance(entry, dict) else None
        result = step05.get("result") if isinstance(step05, dict) else None
        if not isinstance(result, dict):
            continue
        values = result.get("available_signal_components")
        if not isinstance(values, list):
            raise Pass2PackageError(
                "STEP05_SIGNAL_COMPONENTS_MISSING",
                f"{entry.get('request', {}).get('request_id')} Step05 result lacks available_signal_components; re-run Step05.",
            )
        components.update(str(value) for value in values if str(value))
    return sorted(components)


def _allowed_quantitative_forecast_hours(plan: dict[str, Any], check: dict[str, Any]) -> list[int]:
    phases = plan.get("analysis_phases")
    if not isinstance(phases, list):
        raise Pass2PackageError("ANALYSIS_PHASES_MISSING", "candidate analysis_plan has no analysis_phases")
    by_id: dict[str, list[int]] = {}
    for phase in phases:
        if not isinstance(phase, dict):
            continue
        phase_id = str(phase.get("phase_id") or "")
        hours = phase.get("forecast_hours")
        if phase_id and isinstance(hours, list):
            by_id[phase_id] = [int(hour) for hour in hours if isinstance(hour, int) and not isinstance(hour, bool)]
    phase_ids = check.get("phase_ids")
    if not isinstance(phase_ids, list) or not phase_ids:
        raise Pass2PackageError(
            "CHECK_PHASE_IDS_MISSING",
            f"{check.get('check_id')} must declare phase_ids for quantitative scope validation.",
        )
    unknown = [str(value) for value in phase_ids if str(value) not in by_id]
    if unknown:
        raise Pass2PackageError(
            "CHECK_PHASE_ID_INVALID",
            f"{check.get('check_id')} references unknown phase_ids={unknown}",
        )
    return sorted({hour for phase_id in phase_ids for hour in by_id[str(phase_id)]})


def _load_json(path: Path, *, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Pass2PackageError(code, f"Unable to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise Pass2PackageError(code, f"JSON root must be an object: {path}")
    return value


def _source_paths(root: Path, init: str) -> tuple[Path, Path, Path]:
    try:
        step04 = resolve_step04_output_dir(root, init) / "pass1_result.json"
    except FileNotFoundError as exc:
        raise Pass2PackageError("PASS1_RESULT_MISSING", str(exc)) from exc
    except (RuntimeError, ValueError, OSError) as exc:
        raise Pass2PackageError("PASS1_RESULT_AMBIGUOUS", str(exc)) from exc
    diagnostics = runtime_path(root, "daio", init, "step05", "diagnostics")
    images = runtime_path(root, "daio", init, "step05", "images")
    if not diagnostics.is_dir():
        raise Pass2PackageError("STEP05_DIAGNOSTICS_MISSING", f"Step05 diagnostics directory is missing: {diagnostics}")
    if not images.is_dir():
        raise Pass2PackageError("STEP05_IMAGES_MISSING", f"Step05 images directory is missing: {images}")
    return step04, diagnostics, images


def _unique_requests(payload: dict[str, Any]) -> list[dict[str, Any]]:
    requests = payload.get("analysis_data_requests")
    if not isinstance(requests, list) or not all(isinstance(item, dict) for item in requests):
        raise Pass2PackageError("ANALYSIS_DATA_REQUESTS_MISSING", "Step04 analysis_data_requests must be a list of objects.")
    seen: set[str] = set()
    for request in requests:
        request_id = str(request.get("request_id") or "").strip()
        if not request_id:
            raise Pass2PackageError("REQUEST_ID_MISSING", "Every Step04 request must have request_id.")
        if request_id in seen:
            raise Pass2PackageError("REQUEST_ID_DUPLICATE", f"Duplicate Step04 request_id: {request_id}")
        seen.add(request_id)
    return requests


def _relative(root: Path, path: Path) -> str:
    return logical_ref(root, path)


def _candidate_context(payload: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    candidates = payload.get("candidates")
    plans = payload.get("candidate_analysis_plans")
    candidate = next((item for item in candidates or [] if isinstance(item, dict) and item.get("candidate_id") == candidate_id), None)
    plan = next((item for item in plans or [] if isinstance(item, dict) and item.get("candidate_id") == candidate_id), None)
    if candidate is None:
        raise Pass2PackageError("CANDIDATE_ID_MISSING", f"No Step04 candidate context for {candidate_id!r}.")
    if plan is None:
        raise Pass2PackageError("CANDIDATE_PLAN_MISSING", f"No Step04 candidate analysis plan for {candidate_id!r}.")
    opportunities = [
        item
        for item in payload.get("precursor_opportunities", [])
        if isinstance(item, dict)
        and (
            candidate_id in item.get("related_direct_candidate_ids", [])
            or item.get("created_candidate_id") == candidate_id
        )
    ]
    return {
        "candidate": candidate,
        "analysis_plan": plan,
        "precursor_opportunity_provenance": {
            "availability": "available" if "precursor_opportunities" in payload else "legacy_unavailable",
            "opportunities": opportunities,
        },
    }


def _valid_time(init: str, hour: int | None) -> str | None:
    if hour is None:
        return None
    try:
        origin = datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise Pass2PackageError("INIT_INVALID", f"init must be YYYYMMDDHH: {init!r}") from exc
    return (origin + timedelta(hours=int(hour))).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cross_section_metadata(request: dict[str, Any]) -> dict[str, Any] | None:
    if request.get("diagnostic_id") != "vertical_cross_section":
        return None
    line = request.get("cross_section_line")
    strategy = request.get("cross_section_strategy")
    if not isinstance(line, dict) or not strategy or not line.get("selection_reason"):
        raise Pass2PackageError("CROSS_SECTION_CONTEXT_MISSING", f"{request.get('request_id')} has incomplete cross-section context.")
    return {
        "cross_section_line": line,
        "a_to_b_direction": "cross_section_line start point (A) to end point (B)",
        "cross_section_strategy": strategy,
        "reference_band_axis": request.get("reference_band_axis"),
        "selection_reason": line["selection_reason"],
        "selection_reason_is_ground_truth": False,
        "omega_sign": "raw signed omega: negative=upward motion, positive=downward motion",
        "section_parallel_wind_sign": "positive=A-to-B, negative=B-to-A",
    }


def _time_height_metadata(candidate: dict[str, Any], request: dict[str, Any]) -> dict[str, Any] | None:
    if request.get("diagnostic_id") != "time_height_section":
        return None
    return {
        "spatial_aggregation": "candidate-area mean",
        "candidate_area": candidate.get("candidate_area"),
        "forecast_time_axis": [int(hour) for hour in request.get("forecast_hours", [])],
        "pressure_axis": "pressure level (hPa)",
        "omega_sign": "raw signed omega: negative=upward motion, positive=downward motion",
        "reference_axis_definition": "candidate-area physical major axis resolved by Step05; signed wind is positive along A-to-B",
    }


def _image_metadata(root: Path, init: str, request: dict[str, Any], result: dict[str, Any], image: dict[str, Any]) -> dict[str, Any]:
    path_value = image.get("path")
    if not isinstance(path_value, str) or not path_value:
        raise Pass2PackageError("PNG_REFERENCE_INVALID", f"Invalid generated_images entry for {request.get('request_id')}.")
    image_path = resolve_logical_ref(root, Path(path_value))
    if image_path.suffix.lower() != ".png" or not image_path.is_file():
        raise Pass2PackageError("PNG_MISSING", f"Step05 PNG is missing for {request.get('request_id')}: {image_path}")
    forecast_hour = image.get("forecast_hour")
    forecast_hour = int(forecast_hour) if isinstance(forecast_hour, int) and not isinstance(forecast_hour, bool) else None
    request_hours = [int(value) for value in request.get("forecast_hours", [])]
    return {
        "path": _relative(root, image_path),
        "sha256": sha256sum(image_path),
        "request_id": request["request_id"],
        "diagnostic_id": request.get("diagnostic_id"),
        "physical_diagnostic": image.get("physical_diagnostic"),
        "forecast_hour": forecast_hour,
        "valid_time_utc": _valid_time(init, forecast_hour),
        "request_forecast_hours": request_hours,
        "request_valid_times_utc": [_valid_time(init, hour) for hour in request_hours],
        "units": image.get("units"),
        "selected_domain": result.get("selected_domain") or request.get("requested_domain"),
    }


def _step05_entry(root: Path, init: str, diagnostics_dir: Path, request: dict[str, Any]) -> dict[str, Any]:
    request_id = str(request["request_id"])
    result_path = diagnostics_dir / f"{request_id}.json"
    if not result_path.is_file():
        return {
            "result_path": _relative(root, result_path),
            "status": "missing",
            "result": None,
            "generated_images": [],
            "missing_reason": "Step05 result JSON is absent.",
        }
    result = _load_json(result_path, code="STEP05_RESULT_INVALID")
    if result.get("request_id") != request_id:
        raise Pass2PackageError("REQUEST_ID_MISMATCH", f"Step05 result {result_path} has request_id={result.get('request_id')!r}.")
    if result.get("candidate_id") != request.get("candidate_id"):
        raise Pass2PackageError("REQUEST_CONTEXT_MISMATCH", f"Step05 candidate_id does not match {request_id}.")
    # A source_check_ids relationship is allowed to share evidence between checks;
    # request/check diagnostic identifiers are intentionally not compared here.
    if result.get("diagnostic_id") != request.get("diagnostic_id"):
        raise Pass2PackageError("REQUEST_CONTEXT_MISMATCH", f"Step05 diagnostic_id does not match {request_id}.")
    images = [_image_metadata(root, init, request, result, image) for image in result.get("generated_images", [])]
    return {
        "result_path": _relative(root, result_path),
        "status": str(result.get("status") or "invalid"),
        "result": result,
        "generated_images": images,
        "missing_reason": None,
    }


def _request_has_successful_evidence(entry: dict[str, Any]) -> bool:
    request = entry["request"]
    step05 = entry["step05"]
    result = step05.get("result")
    if step05.get("status") != "success" or not isinstance(result, dict):
        return False
    if request.get("data_type") == "spatial_field":
        return bool(step05.get("generated_images"))
    if request.get("data_type") == "quantitative_excerpt":
        return bool(result.get("features"))
    return False


def _source_check_ids(request: dict[str, Any]) -> list[str]:
    values = request.get("source_check_ids")
    if not isinstance(values, list) or not values:
        raise Pass2PackageError(
            "SOURCE_CHECK_IDS_MISSING",
            f"{request.get('request_id')} must declare non-empty source_check_ids.",
        )
    normalized = [str(value).strip() for value in values if str(value).strip()]
    if not normalized:
        raise Pass2PackageError(
            "SOURCE_CHECK_IDS_MISSING",
            f"{request.get('request_id')} has no usable source_check_ids.",
        )
    return list(dict.fromkeys(normalized))


def _system_direct_precipitation_anchor_entries(
    candidate_id: str,
    context: dict[str, Any],
    entries: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Validate the dedicated SYSTEM Anchor through the concrete Step05 evidence."""
    candidate = context.get("candidate")
    plan = context.get("analysis_plan")
    if not isinstance(candidate, dict) or not isinstance(plan, dict):
        raise Pass2PackageError(
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID",
            f"{candidate_id} has incomplete candidate context.",
        )
    if str(candidate.get("detection_basis") or "") not in {
        "direct_precipitation", "precursor_environment", "mixed"
    }:
        return {}
    anchors = [
        check
        for check in plan.get("variable_checks", [])
        if isinstance(check, dict) and _is_system_direct_precipitation_anchor_check(check)
    ]
    if len(anchors) != 1:
        raise Pass2PackageError(
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID",
            f"{candidate_id} requires exactly one SYSTEM direct-precipitation presence anchor for every candidate type; actual={len(anchors)}. Rerun Step04 for this init.",
        )
    anchor = anchors[0]
    anchor_id = str(anchor.get("check_id") or "")
    if (
        not anchor_id
        or anchor.get("required_signal_components") != ["direct_precipitation"]
        or anchor.get("check_scope") != "single_variable"
        or anchor.get("diagnostic_id") != "detailed_spatial_map"
        or anchor.get("product_id") != "QL_PRECIP_6H"
        or anchor.get("priority") != "required"
    ):
        raise Pass2PackageError(
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID",
            f"{candidate_id} SYSTEM anchor Check violates its structural contract.",
        )
    linked = [
        entry for entry in entries
        if anchor_id in _source_check_ids(entry.get("request", {}))
    ]
    if len(linked) != 1:
        raise Pass2PackageError(
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID",
            f"{candidate_id} SYSTEM anchor must have exactly one dedicated request; actual={len(linked)}.",
        )
    entry = linked[0]
    request = entry["request"]
    source_ids = _source_check_ids(request)
    expected_hours = sorted(
        int(value)
        for value in candidate.get("peak_forecast_hours", [])
        if isinstance(value, int) and not isinstance(value, bool)
    )
    actual_hours = sorted(
        int(value)
        for value in request.get("forecast_hours", [])
        if isinstance(value, int) and not isinstance(value, bool)
    )
    expected_domain = _bbox_signature(candidate.get("candidate_area"))
    actual_domain = _bbox_signature(request.get("requested_domain"))
    violations: list[str] = []
    for label, actual, expected in (
        ("source_check_ids", source_ids, [anchor_id]),
        ("check_id", request.get("check_id"), anchor_id),
        ("data_type", request.get("data_type"), "spatial_field"),
        ("diagnostic_id", request.get("diagnostic_id"), "detailed_spatial_map"),
        ("product_id", request.get("product_id"), "QL_PRECIP_6H"),
        ("priority", request.get("priority"), "required"),
        ("forecast_hours", actual_hours, expected_hours),
        ("requested_domain", actual_domain, expected_domain),
    ):
        if actual != expected:
            violations.append(f"{label} expected={expected!r} actual={actual!r}")
    if violations:
        raise Pass2PackageError(
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID",
            f"{candidate_id} SYSTEM anchor request is not exclusive: " + "; ".join(violations),
        )

    step05 = entry.get("step05")
    result = step05.get("result") if isinstance(step05, dict) else None
    if isinstance(result, dict) and step05.get("status") == "success":
        result_hours = sorted(
            int(value)
            for value in result.get("forecast_hours", [])
            if isinstance(value, int) and not isinstance(value, bool)
        )
        result_domain = _bbox_signature(result.get("selected_domain"))
        images = step05.get("generated_images")
        images = images if isinstance(images, list) else []
        image_hours = sorted(
            int(image.get("forecast_hour"))
            for image in images
            if isinstance(image, dict)
            and isinstance(image.get("forecast_hour"), int)
            and not isinstance(image.get("forecast_hour"), bool)
        )
        physical_diagnostics = {
            str(image.get("physical_diagnostic") or "")
            for image in images if isinstance(image, dict)
        }
        step05_violations: list[str] = []
        if result_hours != expected_hours:
            step05_violations.append(
                f"Step05 forecast_hours expected={expected_hours} actual={result_hours}"
            )
        if result_domain != expected_domain:
            step05_violations.append(
                f"Step05 selected_domain expected={expected_domain!r} actual={result_domain!r}"
            )
        if len(images) != len(expected_hours) or image_hours != expected_hours:
            step05_violations.append(
                f"Step05 image forecast_hours expected={expected_hours} actual={image_hours} count={len(images)}"
            )
        if physical_diagnostics != {"QL_PRECIP_6H"}:
            step05_violations.append(
                f"Step05 physical_diagnostic expected=QL_PRECIP_6H actual={sorted(physical_diagnostics)}"
            )
        if step05_violations:
            raise Pass2PackageError(
                "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID",
                f"{candidate_id} SYSTEM anchor Step05 evidence violates its contract: "
                + "; ".join(step05_violations),
            )
    return {anchor_id: entry}


def _check_evidence(candidate_id: str, context: dict[str, Any], entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    plan = context["analysis_plan"]
    candidate = context["candidate"]
    checks = plan.get("variable_checks")
    if not isinstance(checks, list):
        raise Pass2PackageError("VARIABLE_CHECKS_MISSING", f"{candidate_id} has no Step04 variable_checks.")
    active_check_ids: set[str] = set()
    for check in checks:
        if not isinstance(check, dict):
            raise Pass2PackageError("VARIABLE_CHECK_INVALID", f"{candidate_id} has non-object variable_check.")
        check_id = str(check.get("check_id") or "").strip()
        if not check_id or check_id in active_check_ids:
            raise Pass2PackageError("CHECK_ID_DUPLICATE", f"{candidate_id} has invalid or duplicate check_id: {check_id!r}")
        active_check_ids.add(check_id)
    system_anchor_entries = _system_direct_precipitation_anchor_entries(
        candidate_id, context, entries
    )
    requests_by_check: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        source_ids = _source_check_ids(entry["request"])
        stale_ids = sorted(set(source_ids) - active_check_ids)
        if stale_ids:
            raise Pass2PackageError(
                "STALE_SOURCE_CHECK_ID",
                f"{entry['request'].get('request_id')} references inactive source_check_ids: {stale_ids}",
            )
        for check_id in source_ids:
            requests_by_check[check_id].append(entry)
    rows: list[dict[str, Any]] = []
    bundles: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for check in checks:
        if not isinstance(check, dict):
            raise Pass2PackageError("VARIABLE_CHECK_INVALID", f"{candidate_id} has non-object variable_check.")
        check_id = str(check.get("check_id") or "").strip()
        if not check_id:
            raise Pass2PackageError("CHECK_ID_MISSING", f"{candidate_id} has variable_check without check_id.")
        linked = sorted(requests_by_check.get(check_id, []), key=lambda item: str(item["request"]["request_id"]))
        request_ids = [str(item["request"]["request_id"]) for item in linked]
        available = [str(item["request"]["request_id"]) for item in linked if _request_has_successful_evidence(item)]
        missing = [request_id for request_id in request_ids if request_id not in available]
        check_scope = str(check.get("check_scope") or _legacy_check_scope(check))
        required_components = _required_signal_components(check)
        available_components = _available_signal_components(linked)
        if check_id in system_anchor_entries:
            allowed_quantitative_hours = sorted(
                int(value)
                for value in system_anchor_entries[check_id]["request"].get("forecast_hours", [])
                if isinstance(value, int) and not isinstance(value, bool)
            )
        else:
            allowed_quantitative_hours = _allowed_quantitative_forecast_hours(plan, check)
        missing_components = sorted(set(required_components) - set(available_components))
        if not request_ids:
            status = "none"
        elif not available:
            status = "none"
        elif missing or missing_components:
            status = "partial"
        else:
            status = "complete"
        key = tuple(request_ids)
        bundles[key].append(check_id)
        rows.append({
            "check_id": check_id,
            "diagnostic_id": check.get("diagnostic_id"),
            "variable_role": check.get("variable_role"),
            "check_scope": check_scope,
            "required_signal_components": required_components,
            "available_signal_components": available_components,
            "missing_signal_components": missing_components,
            "allowed_quantitative_forecast_hours": allowed_quantitative_hours,
            "expected_signal": check.get("expected_signal"),
            "decision_relevance": check.get("decision_relevance"),
            "priority": check.get("priority"),
            "source_intent_hash": check.get("intent_hash"),
            "intent_contract_version": (
                check.get("intent_contract", {}).get("contract_version")
                if isinstance(check.get("intent_contract"), dict)
                else None
            ),
            "source_atomic_claim_hash": check.get("atomic_claim_hash"),
            "atomic_claim_contract_version": (
                check.get("atomic_claim_contract", {}).get("contract_version")
                if isinstance(check.get("atomic_claim_contract"), dict)
                else None
            ),
            "atomic_claim_validation_status": (
                check.get("atomic_claim_validation", {}).get("status")
                if isinstance(check.get("atomic_claim_validation"), dict)
                else None
            ),
            "request_ids": request_ids,
            "available_request_ids": available,
            "missing_request_ids": missing,
            "evidence_complete": status == "complete",
            "evidence_status": status,
            "cross_section_context": next((
                _cross_section_metadata(item["request"])
                for item in linked if item["request"].get("diagnostic_id") == "vertical_cross_section"
            ), None),
            "time_height_context": next((
                _time_height_metadata(candidate, item["request"])
                for item in linked if item["request"].get("diagnostic_id") == "time_height_section"
            ), None),
        })
    bundle_rows: list[dict[str, Any]] = []
    bundle_id_by_key: dict[tuple[str, ...], str] = {}
    for request_ids, check_ids in sorted(bundles.items()):
        bundle_id = "BND_" + canonical_hash({"candidate_id": candidate_id, "request_ids": request_ids})[:16].upper()
        bundle_id_by_key[request_ids] = bundle_id
        linked = [entry for entry in entries if str(entry["request"]["request_id"]) in request_ids]
        bundle_rows.append({
            "bundle_id": bundle_id,
            "request_ids": list(request_ids),
            "check_ids": sorted(check_ids),
            "image_count": sum(len(item["step05"].get("generated_images", [])) for item in linked),
            "quantitative_request_count": sum(1 for item in linked if item["request"].get("data_type") == "quantitative_excerpt"),
        })
    for row in rows:
        row["bundle_id"] = bundle_id_by_key[tuple(row["request_ids"])]
    return rows, bundle_rows


def build_pass2_package(root: Path, init: str) -> dict[str, Any]:
    """Build the single Step06 source/evidence package without an AI call."""
    step04_path, diagnostics_dir, images_dir = _source_paths(root, init)
    payload = _load_json(step04_path, code="PASS1_RESULT_INVALID")
    requests = _unique_requests(payload)
    by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for request in requests:
        candidate_id = str(request.get("candidate_id") or "").strip()
        if not candidate_id:
            raise Pass2PackageError("CANDIDATE_ID_MISSING", f"{request.get('request_id')!r} has no candidate_id.")
        by_candidate[candidate_id].append(request)

    groups: list[dict[str, Any]] = []
    for candidate_id, candidate_requests in by_candidate.items():
        context = _candidate_context(payload, candidate_id)
        entries = [{"request": request, "step05": _step05_entry(root, init, diagnostics_dir, request)} for request in candidate_requests]
        _validate_spatial_lineage(candidate_id, context, entries)
        check_rows, bundle_rows = _check_evidence(candidate_id, context, entries)
        groups.append({
            "candidate_id": candidate_id,
            "candidate_context": context,
            "analysis_data_requests": entries,
            "check_evidence": check_rows,
            "exact_evidence_bundles": bundle_rows,
        })
    package = {
        "schema_version": "step06-pass2-input-v3",
        "init": init,
        "source_of_truth": {
            "step04_pass1_result": _relative(root, step04_path),
            "step05_diagnostics_dir": _relative(root, diagnostics_dir),
            "step05_images_dir": _relative(root, images_dir),
            "description": "Source/evidence package for Pass2; not an OpenAI API payload.",
        },
        "candidates": groups,
    }
    validate_pass2_package(root, package)
    return package


def validate_pass2_package(root: Path, package: dict[str, Any]) -> None:
    try:
        schema = json.loads(runtime_path(root, "prompts", "schemas", "pass2_input_package.schema.json").read_text(encoding="utf-8"))
        import jsonschema
        jsonschema.validate(package, schema)
    except (OSError, json.JSONDecodeError, ModuleNotFoundError) as exc:
        raise Pass2PackageError("SCHEMA_VALIDATION_FAILED", f"Unable to load Pass2 package schema: {exc}") from exc
    except Exception as exc:
        raise Pass2PackageError("SCHEMA_VALIDATION_FAILED", f"Pass2 package schema validation failed: {exc}") from exc
    if package.get("schema_version") != "step06-pass2-input-v3":
        raise Pass2PackageError("SCHEMA_VERSION_INVALID", "Unsupported Step06 Pass2 package schema version.")
    groups = package.get("candidates")
    if not isinstance(groups, list):
        raise Pass2PackageError("CANDIDATES_INVALID", "Pass2 package candidates must be an array.")
    seen_candidates: set[str] = set()
    for group in groups:
        candidate_id = str(group.get("candidate_id") or "") if isinstance(group, dict) else ""
        if not candidate_id or candidate_id in seen_candidates:
            raise Pass2PackageError("CANDIDATE_ID_DUPLICATE", f"Invalid or duplicate candidate_id: {candidate_id!r}")
        seen_candidates.add(candidate_id)
        entries = group.get("analysis_data_requests") if isinstance(group, dict) else None
        checks = group.get("check_evidence") if isinstance(group, dict) else None
        if not isinstance(entries, list) or not isinstance(checks, list):
            raise Pass2PackageError("CANDIDATE_GROUP_INVALID", f"{candidate_id} has invalid requests/check evidence.")
        request_ids: set[str] = set()
        entry_by_id: dict[str, dict[str, Any]] = {}
        for entry in entries:
            request = entry.get("request") if isinstance(entry, dict) else None
            step05 = entry.get("step05") if isinstance(entry, dict) else None
            if not isinstance(request, dict) or not isinstance(step05, dict):
                raise Pass2PackageError("REQUEST_ENTRY_INVALID", f"Invalid request entry in {candidate_id}.")
            request_id = str(request.get("request_id") or "")
            if not request_id or request_id in request_ids:
                raise Pass2PackageError("REQUEST_ID_DUPLICATE", f"Invalid or duplicate request_id in {candidate_id}: {request_id!r}")
            request_ids.add(request_id)
            entry_by_id[request_id] = entry
            result = step05.get("result")
            if result is not None:
                if not isinstance(result, dict) or result.get("request_id") != request_id or result.get("candidate_id") != candidate_id:
                    raise Pass2PackageError("REQUEST_CONTEXT_MISMATCH", f"request_id join failed for {request_id}.")
            for image in step05.get("generated_images", []):
                if not isinstance(image, dict) or not resolve_logical_ref(root, str(image.get("path"))).is_file():
                    raise Pass2PackageError("PNG_MISSING", f"Pass2 PNG reference is missing for {request_id}.")
        _validate_spatial_lineage(
            candidate_id,
            group.get("candidate_context", {}),
            [entry_by_id[request_id] for request_id in sorted(entry_by_id)],
        )
        active_check_ids = {
            str(check.get("check_id") or "")
            for check in checks
            if isinstance(check, dict) and check.get("check_id")
        }
        system_anchor_entries = _system_direct_precipitation_anchor_entries(
            candidate_id,
            group.get("candidate_context", {}),
            [entry_by_id[request_id] for request_id in sorted(entry_by_id)],
        )
        for request_id, entry in entry_by_id.items():
            stale_ids = sorted(set(_source_check_ids(entry["request"])) - active_check_ids)
            if stale_ids:
                raise Pass2PackageError(
                    "STALE_SOURCE_CHECK_ID",
                    f"{request_id} references inactive source_check_ids: {stale_ids}",
                )
        check_by_id: dict[str, dict[str, Any]] = {}
        for check in checks:
            if not isinstance(check, dict):
                raise Pass2PackageError("CHECK_EVIDENCE_INVALID", f"Invalid check evidence in {candidate_id}.")
            check_id = str(check.get("check_id") or "")
            resolved_ids = check.get("request_ids")
            if not check_id or not isinstance(resolved_ids, list) or any(value not in request_ids for value in resolved_ids):
                raise Pass2PackageError("CHECK_EVIDENCE_INVALID", f"{candidate_id} check={check_id!r} has invalid resolved requests.")
            if check_id in check_by_id:
                raise Pass2PackageError("CHECK_ID_DUPLICATE", f"{candidate_id} has duplicate check evidence: {check_id!r}")
            check_by_id[check_id] = check
            available_ids = check.get("available_request_ids")
            missing_ids = check.get("missing_request_ids")
            if not isinstance(available_ids, list) or not isinstance(missing_ids, list):
                raise Pass2PackageError("CHECK_EVIDENCE_INVALID", f"{candidate_id} check={check_id!r} has invalid evidence state lists.")
            resolved_set = set(resolved_ids)
            available_set = set(available_ids)
            missing_set = set(missing_ids)
            if (
                len(resolved_set) != len(resolved_ids)
                or len(available_set) != len(available_ids)
                or len(missing_set) != len(missing_ids)
                or not available_set.issubset(resolved_set)
                or not missing_set.issubset(resolved_set)
                or available_set & missing_set
                or available_set | missing_set != resolved_set
            ):
                raise Pass2PackageError("CHECK_EVIDENCE_INVALID", f"{candidate_id} check={check_id!r} has inconsistent evidence IDs.")
            expected_available = {
                request_id
                for request_id in resolved_set
                if _request_has_successful_evidence(entry_by_id[request_id])
            }
            if available_set != expected_available:
                raise Pass2PackageError("CHECK_EVIDENCE_INVALID", f"{candidate_id} check={check_id!r} does not match resolved Step05 evidence.")
            plan_checks = group.get("candidate_context", {}).get("analysis_plan", {}).get("variable_checks", [])
            specification = next(
                (item for item in plan_checks if isinstance(item, dict) and str(item.get("check_id")) == check_id),
                {},
            )
            expected_scope = str(specification.get("check_scope") or _legacy_check_scope(specification))
            expected_intent_hash = specification.get("intent_hash")
            actual_intent_hash = check.get("source_intent_hash")
            if expected_intent_hash is not None and actual_intent_hash != expected_intent_hash:
                raise Pass2PackageError(
                    "INTENT_LINEAGE_MISMATCH",
                    f"{candidate_id} check={check_id!r} source_intent_hash does not match Step04 intent_hash.",
                )
            expected_required_components = _required_signal_components(specification)
            linked_entries = [entry_by_id[request_id] for request_id in resolved_ids]
            expected_available_components = _available_signal_components(linked_entries)
            expected_missing_components = sorted(set(expected_required_components) - set(expected_available_components))
            if check_id in system_anchor_entries:
                expected_allowed_quantitative_hours = sorted(
                    int(value)
                    for value in system_anchor_entries[check_id]["request"].get("forecast_hours", [])
                    if isinstance(value, int) and not isinstance(value, bool)
                )
            else:
                expected_allowed_quantitative_hours = _allowed_quantitative_forecast_hours(
                    group.get("candidate_context", {}).get("analysis_plan", {}), specification
                )
            if check.get("check_scope") is not None and check.get("check_scope") != expected_scope:
                raise Pass2PackageError("CHECK_EVIDENCE_INVALID", f"{candidate_id} check={check_id!r} has inconsistent check_scope.")
            for field, expected_value in (
                ("required_signal_components", expected_required_components),
                ("available_signal_components", expected_available_components),
                ("missing_signal_components", expected_missing_components),
                ("allowed_quantitative_forecast_hours", expected_allowed_quantitative_hours),
            ):
                actual_value = check.get(field)
                if actual_value is not None and actual_value != expected_value:
                    raise Pass2PackageError("CHECK_EVIDENCE_INVALID", f"{candidate_id} check={check_id!r} has inconsistent {field}.")
            expected_status = (
                "complete"
                if resolved_set and available_set == resolved_set and not expected_missing_components
                else "partial"
                if available_set
                else "none"
            )
            if check.get("evidence_status") != expected_status or bool(check.get("evidence_complete")) != (expected_status == "complete"):
                raise Pass2PackageError("CHECK_EVIDENCE_INVALID", f"{candidate_id} check={check_id!r} has inconsistent evidence status.")
            for request_id in resolved_ids:
                if check_id not in _source_check_ids(entry_by_id[request_id]["request"]):
                    raise Pass2PackageError("SOURCE_CHECK_ID_MISMATCH", f"{check_id} is not in {request_id}.source_check_ids.")
        bundles = group.get("exact_evidence_bundles") if isinstance(group, dict) else None
        if not isinstance(bundles, list):
            raise Pass2PackageError("EVIDENCE_BUNDLE_INVALID", f"{candidate_id} has invalid exact_evidence_bundles.")
        bundle_by_id: dict[str, dict[str, Any]] = {}
        bundled_check_ids: set[str] = set()
        bundle_id_by_request_set: dict[tuple[str, ...], str] = {}
        for bundle in bundles:
            if not isinstance(bundle, dict):
                raise Pass2PackageError("EVIDENCE_BUNDLE_INVALID", f"{candidate_id} has a non-object evidence bundle.")
            bundle_id = str(bundle.get("bundle_id") or "")
            bundle_request_ids = bundle.get("request_ids")
            bundle_check_ids = bundle.get("check_ids")
            if (
                not bundle_id
                or bundle_id in bundle_by_id
                or not isinstance(bundle_request_ids, list)
                or not isinstance(bundle_check_ids, list)
                or len(set(bundle_request_ids)) != len(bundle_request_ids)
                or len(set(bundle_check_ids)) != len(bundle_check_ids)
            ):
                raise Pass2PackageError("EVIDENCE_BUNDLE_INVALID", f"{candidate_id} has an invalid exact evidence bundle.")
            bundle_by_id[bundle_id] = bundle
            request_set_key = tuple(sorted(str(request_id) for request_id in bundle_request_ids))
            previous_bundle_id = bundle_id_by_request_set.setdefault(request_set_key, bundle_id)
            if previous_bundle_id != bundle_id:
                raise Pass2PackageError("EVIDENCE_BUNDLE_INVALID", f"{candidate_id} split one exact request set across multiple bundles.")
            for check_id in bundle_check_ids:
                check = check_by_id.get(str(check_id))
                if (
                    check is None
                    or str(check_id) in bundled_check_ids
                    or str(check.get("bundle_id")) != bundle_id
                    or set(check.get("request_ids", [])) != set(bundle_request_ids)
                ):
                    raise Pass2PackageError("EVIDENCE_BUNDLE_INVALID", f"{candidate_id} bundle={bundle_id} does not match its checks.")
                bundled_check_ids.add(str(check_id))
            if any(request_id not in entry_by_id for request_id in bundle_request_ids):
                raise Pass2PackageError("EVIDENCE_BUNDLE_INVALID", f"{candidate_id} bundle={bundle_id} has an unknown request_id.")
            linked = [entry_by_id[request_id] for request_id in bundle_request_ids]
            expected_images = sum(len(entry["step05"].get("generated_images", [])) for entry in linked)
            expected_quantitative = sum(1 for entry in linked if entry["request"].get("data_type") == "quantitative_excerpt")
            if bundle.get("image_count") != expected_images or bundle.get("quantitative_request_count") != expected_quantitative:
                raise Pass2PackageError("EVIDENCE_BUNDLE_INVALID", f"{candidate_id} bundle={bundle_id} has incorrect evidence counts.")
        if bundled_check_ids != set(check_by_id):
            raise Pass2PackageError("EVIDENCE_BUNDLE_INVALID", f"{candidate_id} bundle coverage does not match active checks.")
