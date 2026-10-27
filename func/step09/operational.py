"""Step09 deterministic validation and final operational surfacing."""

from __future__ import annotations

import copy
from typing import Any

from func.shared.pass2_common import Pass2PipelineError, active_candidate_groups, load_json_object, validate_schema, write_validated_atomic
from func.shared.korean_narrative import validate_step08_response_narrative
from func.shared.tier_narrative import (TierNarrativeError, public_reason, canonical_guidance, assert_public_tier)
from func.shared.runtime_paths import runtime_path
from func.shared.signal_validation import (
    CANDIDATE_SIGNAL_STATUSES,
    candidate_operational_display_name,
    candidate_effective_peak_forecast_hours,
    candidate_peak_precipitation_details_from_group,
    OPERATIONAL_PRECURSOR_CATEGORIES,
    complete_coupled_environment_alignment_check_ids,
    derive_signal_class,
    derive_candidate_type,
    derive_display_tier,
    derive_validated_precursor_categories,
    has_operational_escalation_evidence,
    validate_operational_precursor_support_contract,
    validate_environment_persistence_contract,
    validate_pass2_revision,
    validate_validated_precursor_evidence,
)
from func.step07.checks import (
    MINORITY_REPORT_SCHEMA,
    load_step06_package,
    load_step07_results,
    step07_minority_report_path,
    validate_candidate_spatial_localizations,
    validate_candidate_precipitation_motions,
    validate_check_evidence_references,
    validate_step07_ensemble_consensus,
    validate_step07_minority_report_consistency,
    normalize_step07_result_for_synthesis,
)
from func.step08.synthesis import (
    _candidate_analysis_region_index,
    _candidate_name_grounding_audit,
    _step08_alignment_narrative_errors,
    _candidate_name_grounding_contract,
    _derive_environment_localization,
    _derive_final_spatial_identity,
    _provenance,
    migrate_step08_document,
    step08_output_path,
    validate_candidate_synthesis,
)


RESULT_SCHEMA = "pass2_result.schema.json"
OPERATIONAL_SCHEMA = "operational_result.schema.json"
OPERATIONAL_VERSION = "step09-operational-result-v5"


def step09_output_path(root: Any, init: str) -> Any:
    return runtime_path(root, "daio", init, "step09", "operational_result.json")


def _envelope(candidate: dict[str, Any]) -> dict[str, Any]:
    window = candidate.get("time_window") if isinstance(candidate.get("time_window"), dict) else {}
    padding = ((candidate.get("analysis_envelope_request") or {}).get("temporal_padding_hours") if isinstance(candidate.get("analysis_envelope_request"), dict) else 0) or 0
    try:
        temporal = {"forecast_hour_start": int(window["forecast_hour_start"]) - int(padding), "forecast_hour_end": int(window["forecast_hour_end"]) + int(padding)}
    except (KeyError, TypeError, ValueError) as exc:
        raise Pass2PipelineError("REVISION_ENVELOPE_INVALID", "Candidate has no usable Step04 time envelope.") from exc
    return {"spatial": candidate.get("candidate_area"), "temporal": temporal}


def _validate_step08_candidate(root: Any, group: dict[str, Any], step08: dict[str, Any], check_results: dict[str, dict[str, Any]]) -> list[str]:
    candidate_id = str(group["candidate_id"])
    errors: list[str] = []
    signal = step08.get("signal_assessment") if isinstance(step08.get("signal_assessment"), dict) else {}
    if signal.get("direct_precipitation_status") not in CANDIDATE_SIGNAL_STATUSES:
        errors.append("direct_precipitation_status is invalid")
    if signal.get("environment_support_status") not in CANDIDATE_SIGNAL_STATUSES:
        errors.append("environment_support_status is invalid")
    candidate = group["candidate_context"]["candidate"]
    errors.extend(
        validate_pass2_revision(
            step08.get("revision"),
            _envelope(candidate),
            candidate_peak_hours=candidate.get("peak_forecast_hours", []),
        )
    )
    try:
        validate_candidate_synthesis(group, check_results, step08, root=root)
    except Pass2PipelineError as exc:
        errors.append(str(exc))
    alignment_narrative_errors = _step08_alignment_narrative_errors(root, group, check_results, step08)
    if alignment_narrative_errors:
        errors.extend(alignment_narrative_errors)
    expected_localization = _derive_environment_localization(group, check_results, signal)
    if signal.get("environment_localization") != expected_localization:
        errors.append(
            "environment_localization does not match the validated Step07 localization and Step08 signal contract"
        )
    expected_final_identity = _derive_final_spatial_identity(group, check_results, signal)
    if step08.get("final_spatial_identity") != expected_final_identity:
        errors.append(
            "final_spatial_identity does not match the validated Step07 localization and Step08 signal contract"
        )
    if signal.get("direct_precipitation_status") == "confirmed" and expected_final_identity.get("status") != "precipitation_object":
        errors.append("confirmed direct precipitation requires precipitation-object final_spatial_identity")
    try:
        naming_contract = _candidate_name_grounding_contract(root, group, check_results)
        expected_naming_audit = _candidate_name_grounding_audit(naming_contract)
        if step08.get("candidate_name_grounding_audit") != expected_naming_audit:
            errors.append("candidate_name_grounding_audit does not match current SYSTEM grounding")
    except Pass2PipelineError as exc:
        errors.append(str(exc))
    errors.extend(validate_step08_response_narrative(step08, include_source_text=True))
    return errors


def _validate_step08_provenance(
    root: Any,
    group: dict[str, Any],
    step08: dict[str, Any],
    check_results: dict[str, dict[str, Any]],
    step07_schema_version: str,
) -> list[str]:
    """Reject a synthesis whose stored Step07 input identity is no longer current."""
    provenance = step08.get("provenance")
    if not isinstance(provenance, dict):
        return ["Step08 provenance is invalid"]
    try:
        config = {
            key: str(provenance[key])
            for key in ("provider", "model", "reasoning_effort", "image_detail")
        }
        # Step08 may use a stronger reasoning effort only for semantic repair.
        # It is part of the Step08 input identity for current products, so Step09
        # must reconstruct it when present.  Legacy products did not record this
        # field and must retain their original fingerprint/provenance contract.
        if "repair_reasoning_effort" in provenance:
            config["repair_reasoning_effort"] = str(provenance["repair_reasoning_effort"])
        analysis_region_index = None
        if (
            "analysis_region_naming_contract_version" in provenance
            or "candidate_analysis_region_index_sha256" in provenance
        ):
            analysis_region_index = _candidate_analysis_region_index(root, group)
        naming_contract = _candidate_name_grounding_contract(root, group, check_results)
        expected_fingerprint, expected_provenance = _provenance(
            group,
            check_results,
            step07_schema_version,
            config,
            str(provenance["synthesis_prompt_sha256"]),
            str(provenance["synthesis_schema_sha256"]),
            analysis_region_index,
            candidate_name_grounding_contract=naming_contract,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return [f"Step08 provenance is invalid: {exc}"]
    errors: list[str] = []
    if step08.get("input_fingerprint") != expected_fingerprint:
        errors.append("Step08 input fingerprint does not match current Step07 inputs")
    # _provenance() emits repair_reasoning_effort for current products.  Remove
    # that derived field only when validating a legacy product that never stored
    # it, so old Step08 outputs remain readable without weakening current checks.
    expected_for_compare = dict(expected_provenance)
    if "repair_reasoning_effort" not in provenance:
        expected_for_compare.pop("repair_reasoning_effort", None)
    if provenance != expected_for_compare:
        errors.append("Step08 provenance does not match current Step07 inputs")
    return errors


def _index_step08_candidates(step08: dict[str, Any], active_candidate_ids: set[str]) -> dict[str, dict[str, Any]]:
    values = step08.get("candidates")
    if not isinstance(values, list):
        raise Pass2PipelineError("STEP08_RESULT_INVALID", "Step08 candidates must be an array.")
    indexed: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, dict):
            raise Pass2PipelineError("STEP08_RESULT_INVALID", "Step08 candidate must be an object.")
        candidate_id = str(value.get("candidate_id") or "")
        if candidate_id not in active_candidate_ids:
            raise Pass2PipelineError("STEP08_RESULT_INVALID", f"Step08 has an unexpected candidate {candidate_id!r}.")
        if candidate_id in indexed:
            raise Pass2PipelineError("STEP08_RESULT_INVALID", f"Step08 has duplicate candidate {candidate_id!r}.")
        indexed[candidate_id] = value
    if set(indexed) != active_candidate_ids:
        missing = sorted(active_candidate_ids - set(indexed))
        raise Pass2PipelineError("STEP08_RESULT_INVALID", f"Step08 omitted active candidates: {', '.join(missing)}.")
    return indexed


def _index_step07_results(step07: dict[str, Any], groups_by_candidate: dict[str, dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    values = step07.get("results")
    if not isinstance(values, list):
        raise Pass2PipelineError("STEP07_RESULTS_INVALID", "Step07 results must be an array.")
    indexed: dict[str, dict[str, dict[str, Any]]] = {}
    for value in values:
        if not isinstance(value, dict):
            raise Pass2PipelineError("STEP07_RESULTS_INVALID", "Step07 result must be an object.")
        candidate_id = str(value.get("candidate_id") or "")
        group = groups_by_candidate.get(candidate_id)
        if group is None:
            raise Pass2PipelineError("STEP07_RESULTS_INVALID", f"Step07 has an unexpected candidate {candidate_id!r}.")
        check_id = str(value.get("check_id") or "")
        valid_check_ids = {
            str(item["check_id"])
            for item in group.get("check_evidence", [])
            if isinstance(item, dict) and item.get("check_id")
        }
        if check_id not in valid_check_ids:
            raise Pass2PipelineError("STEP07_RESULTS_INVALID", f"Step07 has an unexpected check {candidate_id}/{check_id}.")
        candidate_results = indexed.setdefault(candidate_id, {})
        if check_id in candidate_results:
            raise Pass2PipelineError("STEP07_RESULTS_INVALID", f"Step07 has duplicate check {candidate_id}/{check_id}.")
        candidate_results[check_id] = normalize_step07_result_for_synthesis(value)
    return indexed


def _confirmed_direct_precipitation_validation(
    group: dict[str, Any], signal: dict[str, Any], check_results: dict[str, dict[str, Any]],
) -> list[str]:
    """Require a compatible, usable hazard-magnitude anchor for direct hazards."""
    plan = group["candidate_context"]["analysis_plan"]
    check_specifications = {
        str(item["check_id"]): item
        for item in plan.get("variable_checks", [])
        if isinstance(item, dict) and isinstance(item.get("check_id"), str) and item["check_id"]
    }
    _categories, anchors, errors = validate_validated_precursor_evidence(
        signal.get("validated_precursor_evidence"),
        check_specifications=check_specifications,
        check_results=check_results,
    )
    direct_anchors = anchors.get("direct_precipitation", set())
    if not direct_anchors:
        errors.append("confirmed direct precipitation has no compatible evidence anchor")
    elif not any(check_results[check_id].get("assessment") == "observed" for check_id in direct_anchors):
        errors.append("confirmed direct precipitation has no observed direct precipitation evidence anchor")
    signal_check_ids = set(signal.get("signal_evidence_check_ids", []))
    if not direct_anchors.issubset(signal_check_ids):
        errors.append("confirmed direct precipitation signal evidence omits a direct precipitation anchor")
    return errors


def _signal_evidence_sufficiency(signal: dict[str, Any], check_results: dict[str, dict[str, Any]]) -> tuple[bool, list[str]]:
    """Validate only the evidence explicitly used by the Step08 signal assessment."""
    errors: list[str] = []
    try:
        signal_class = derive_signal_class(
            signal.get("direct_precipitation_status"),
            signal.get("environment_support_status"),
        )
    except ValueError:
        return False, ["independent signal statuses are invalid"]
    check_ids = signal.get("signal_evidence_check_ids")
    if not isinstance(check_ids, list):
        return False, ["signal_evidence_check_ids is invalid"]
    has_usable_check = any(
        result.get("assessment") != "insufficient_evidence"
        and result.get("usable_for_synthesis") is not False
        for result in check_results.values()
    )
    if signal_class != "insufficient_evidence" and has_usable_check and not check_ids:
        errors.append("non-insufficient signal assessment has no signal evidence")
    for check_id in check_ids:
        result = check_results.get(str(check_id))
        if result is None:
            errors.append(f"signal evidence check does not exist: {check_id}")
            continue
        if result.get("assessment") == "insufficient_evidence" or not result.get("available_request_ids"):
            errors.append(f"signal evidence check is insufficient: {check_id}")
        if result.get("usable_for_synthesis") is False:
            errors.append(f"signal evidence check was excluded from synthesis: {check_id}")
    return not errors, errors


def _precursor_watch_validation(
    group: dict[str, Any], signal: dict[str, Any], check_results: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Validate category anchors, complete evidence, and direct multivariable alignment."""
    plan = group["candidate_context"]["analysis_plan"]
    check_specifications = {
        str(item["check_id"]): item
        for item in plan.get("variable_checks", [])
        if isinstance(item, dict) and isinstance(item.get("check_id"), str) and item["check_id"]
    }
    categories, anchors, errors = validate_validated_precursor_evidence(
        signal.get("validated_precursor_evidence"),
        check_specifications=check_specifications,
        check_results=check_results,
    )
    core_categories, support_contract_errors = validate_operational_precursor_support_contract(
        categories,
        anchors,
        spatial_temporal_overlap=bool(signal.get("validated_spatiotemporal_alignment")),
        check_specifications=check_specifications,
    )
    candidate = group.get("candidate_context", {}).get("candidate", {})
    peak_hours = candidate.get("peak_forecast_hours", []) if isinstance(candidate, dict) else []
    alignment_ids_for_coupling = signal.get("spatiotemporal_alignment_evidence_check_ids")
    coupled_alignment_ids = complete_coupled_environment_alignment_check_ids(
        alignment_ids_for_coupling if isinstance(alignment_ids_for_coupling, list) else [],
        check_specifications=check_specifications,
        check_results=check_results,
        candidate_peak_hours=peak_hours,
    ) if signal.get("direct_precipitation_status") == "confirmed" else []
    if support_contract_errors and not coupled_alignment_ids:
        errors.extend(support_contract_errors)
    core_anchors = {category: anchors.get(category, set()) for category in core_categories}

    def require_complete_usable(
        check_id: str,
        *,
        role: str,
        allowed_assessments: set[str],
    ) -> None:
        result = check_results.get(str(check_id))
        if result is None:
            errors.append(f"{role} evidence check does not exist: {check_id}")
            return
        assessment = str(result.get("assessment") or "")
        if assessment == "insufficient_evidence":
            errors.append(f"{role} evidence check is insufficient: {check_id}")
        elif assessment not in allowed_assessments:
            errors.append(
                f"{role} evidence check assessment is not allowed: "
                f"{check_id} assessment={assessment or 'missing'}"
            )
        if result.get("evidence_status") != "complete" or not result.get("evidence_complete"):
            errors.append(f"{role} evidence check is not complete: {check_id}")
        if result.get("usable_for_synthesis") is False:
            errors.append(f"{role} evidence check failed deterministic consistency validation: {check_id}")

    core_anchor_ids = set().union(*(anchors.get(category, set()) for category in core_categories)) if core_categories else set()
    for check_id in sorted(core_anchor_ids):
        # A confirmed environment may be supported by category-specific precursor
        # anchors that are either fully or partially observed when Step08 also has
        # directly observed multivariable alignment.  Do not downgrade that
        # Step08 contract by requiring every precursor component to be observed.
        require_complete_usable(
            check_id,
            role="precursor anchor",
            allowed_assessments={"observed", "partially_observed"},
        )

    alignment_check_ids = signal.get("spatiotemporal_alignment_evidence_check_ids")
    if not bool(signal.get("validated_spatiotemporal_alignment")):
        errors.append("precursor_watch requires validated_spatiotemporal_alignment=true")
    elif not isinstance(alignment_check_ids, list) or not alignment_check_ids:
        errors.append("positive alignment has no evidence checks")
    else:
        for check_id in alignment_check_ids:
            # Positive spatiotemporal alignment remains strict: only a complete,
            # usable Check assessed as observed may anchor confirmed environment.
            require_complete_usable(
                str(check_id),
                role="alignment",
                allowed_assessments={"observed"},
            )
            specification = check_specifications.get(str(check_id))
            if not isinstance(specification, dict) or specification.get("check_scope") != "multivariable_alignment":
                errors.append(f"alignment evidence is not a multivariable_alignment check: {check_id}")

    signal_check_ids = set(signal.get("signal_evidence_check_ids", []))
    required_signal_ids = core_anchor_ids | set(alignment_check_ids or [])
    if not required_signal_ids.issubset(signal_check_ids):
        errors.append("precursor_watch signal evidence omits operational category anchor or alignment checks")
    return categories, errors



def _environment_persistence_validation(
    group: dict[str, Any],
    signal: dict[str, Any],
    check_results: dict[str, dict[str, Any]],
) -> list[str]:
    """Revalidate Step08 persistence with the same deterministic evidence contract."""
    plan = group["candidate_context"]["analysis_plan"]
    check_specifications = {
        str(item["check_id"]): item
        for item in plan.get("variable_checks", [])
        if isinstance(item, dict) and isinstance(item.get("check_id"), str) and item["check_id"]
    }
    return validate_environment_persistence_contract(
        signal.get("environment_persistence_status"),
        signal.get("environment_persistence_evidence_check_ids"),
        environment_support_status=signal.get("environment_support_status"),
        check_specifications=check_specifications,
        check_results=check_results,
    )


def _environment_persistence_operational_fields(signal: dict[str, Any]) -> dict[str, Any]:
    """Copy validated persistence state into the Step09 operational record."""
    status = str(signal.get("environment_persistence_status") or "")
    raw_ids = signal.get("environment_persistence_evidence_check_ids")
    evidence_ids = [str(value) for value in raw_ids] if isinstance(raw_ids, list) else []
    return {
        "environment_persistence_status": status,
        "environment_persistence_evidence_check_ids": evidence_ids,
    }

def _count_values(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _candidate_time_coverage_hours(groups: list[dict[str, Any]]) -> int:
    intervals: list[tuple[int, int]] = []
    for group in groups:
        candidate = ((group.get("candidate_context") or {}).get("candidate") or {})
        window = candidate.get("time_window") if isinstance(candidate, dict) else None
        if not isinstance(window, dict):
            continue
        start = window.get("forecast_hour_start")
        end = window.get("forecast_hour_end")
        if (
            isinstance(start, int) and not isinstance(start, bool)
            and isinstance(end, int) and not isinstance(end, bool)
            and 0 <= start <= end
        ):
            intervals.append((start, end))
    if not intervals:
        return 0
    intervals.sort()
    merged: list[list[int]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return int(sum(max(0, end - start) for start, end in merged))


def _build_operational_metrics(
    groups: list[dict[str, Any]],
    check_by_candidate: dict[str, dict[str, dict[str, Any]]],
    synth_by_candidate: dict[str, dict[str, Any]],
    operational: list[dict[str, Any]],
) -> dict[str, Any]:
    all_results = [
        result
        for candidate_results in check_by_candidate.values()
        for result in candidate_results.values()
        if isinstance(result, dict)
    ]
    candidates = [
        ((group.get("candidate_context") or {}).get("candidate") or {})
        for group in groups
        if isinstance(group, dict)
    ]
    precipitation_present = sum(
        1 for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("precipitation_signal_status") == "present"
    )
    precursor_only = 0
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("precipitation_signal_status") == "present":
            continue
        categories = candidate.get("precursor_signal_categories")
        if isinstance(categories, list) and any(str(value) != "direct_precipitation" for value in categories):
            precursor_only += 1

    exclusion_reasons = [
        str(reason)
        for result in all_results
        for reason in (result.get("excluded_from_synthesis_reasons") or [])
        if str(reason)
    ]
    quantitative_failure_reasons = [
        str(reason)
        for result in all_results
        for reason in (result.get("quantitative_consistency_failure_reasons") or [])
        if str(reason)
    ]
    return {
        "candidate_count": len(groups),
        "surfaced_candidate_count": sum(1 for item in operational if item.get("should_surface") is True),
        "non_surfaced_candidate_count": sum(1 for item in operational if item.get("should_surface") is False),
        "direct_precipitation_discovery_count": precipitation_present,
        "precursor_only_discovery_count": precursor_only,
        "candidate_time_coverage_hours": _candidate_time_coverage_hours(groups),
        "check_count": len(all_results),
        "usable_check_count": sum(1 for result in all_results if result.get("usable_for_synthesis") is not False),
        "excluded_check_count": sum(1 for result in all_results if result.get("usable_for_synthesis") is False),
        "assessment_counts": _count_values([str(result.get("assessment") or "unknown") for result in all_results]),
        "quantitative_consistency_counts": _count_values([str(result.get("quantitative_consistency_status") or "unknown") for result in all_results]),
        "quantitative_failure_reason_counts": _count_values(quantitative_failure_reasons),
        "exclusion_reason_counts": _count_values(exclusion_reasons),
        "signal_class_counts": _count_values([str(item.get("signal_class") or "unknown") for item in operational]),
        "direct_precipitation_status_counts": _count_values([str(item.get("direct_precipitation_status") or "unknown") for item in operational]),
        "environment_support_status_counts": _count_values([str(item.get("environment_support_status") or "unknown") for item in operational]),
        "environment_persistence_status_counts": _count_values([str(item.get("environment_persistence_status") or "unknown") for item in operational]),
        "priority_monitoring_candidate_count": sum(1 for item in operational if item.get("monitoring_emphasis") == "priority" and item.get("should_surface") is True),
        "validated_spatiotemporal_alignment_count": sum(
            1
            for synthesis in synth_by_candidate.values()
            if isinstance(synthesis.get("signal_assessment"), dict)
            and synthesis["signal_assessment"].get("validated_spatiotemporal_alignment") is True
        ),
        "surfaced_fraction": (
            round(sum(1 for item in operational if item.get("should_surface") is True) / len(groups), 4)
            if groups else 0.0
        ),
    }


def finalize_step09(*, root: Any, init: str) -> dict[str, Any]:
    package = load_step06_package(root, init)
    step07 = load_step07_results(root, init)
    minority_report = load_json_object(step07_minority_report_path(root, init), code="STEP07_MINORITY_REPORT_MISSING")
    validate_schema(root, MINORITY_REPORT_SCHEMA, minority_report, code="STEP07_MINORITY_REPORT_INVALID")
    validate_step07_minority_report_consistency(step07, minority_report)
    step08 = migrate_step08_document(load_json_object(step08_output_path(root, init), code="STEP08_RESULT_MISSING"))
    validate_schema(root, RESULT_SCHEMA, step08)
    active_groups = active_candidate_groups(package)
    groups_by_candidate = {str(group["candidate_id"]): group for group in active_groups}
    if len(groups_by_candidate) != len(active_groups):
        raise Pass2PipelineError("STEP06_PACKAGE_INVALID", "Step06 has duplicate active candidate IDs.")
    synth_by_candidate = _index_step08_candidates(step08, set(groups_by_candidate))
    check_by_candidate = _index_step07_results(step07, groups_by_candidate)
    operational: list[dict[str, Any]] = []
    for group in active_groups:
        candidate_id = str(group["candidate_id"])
        synthesis = synth_by_candidate.get(candidate_id)
        if synthesis is None:
            raise Pass2PipelineError("STEP08_RESULT_INVALID", f"Step08 omitted candidate {candidate_id}.")
        indexed = {str(item["request"]["request_id"]): item for item in group["analysis_data_requests"]}
        checks = {str(item["check_id"]): item for item in group["check_evidence"]}
        results = check_by_candidate.get(candidate_id, {})
        if set(results) != set(checks):
            raise Pass2PipelineError("STEP07_RESULTS_INVALID", f"Step07 checks differ from Step06 active checks for {candidate_id}.")
        for check_id, result in results.items():
            validate_step07_ensemble_consensus(result)
            validate_check_evidence_references(checks[check_id], [indexed[item] for item in checks[check_id]["request_ids"]], result)
        validate_candidate_spatial_localizations(root, group, results)
        validate_candidate_precipitation_motions(root, group, results)
        errors = _validate_step08_candidate(root, group, synthesis, results)
        errors.extend(_validate_step08_provenance(root, group, synthesis, results, str(step07.get("schema_version") or "")))
        signal = synthesis["signal_assessment"]
        direct_status = signal.get("direct_precipitation_status")
        environment_status = signal.get("environment_support_status")
        if direct_status not in CANDIDATE_SIGNAL_STATUSES or environment_status not in CANDIDATE_SIGNAL_STATUSES:
            raise Pass2PipelineError("SIGNAL_STATUS_INVALID", f"Step08 independent statuses are invalid: {candidate_id}.")
        signal_class = derive_signal_class(direct_status, environment_status)
        errors.extend(_environment_persistence_validation(group, signal, results))
        persistence_fields = _environment_persistence_operational_fields(signal)
        derived_categories = derive_validated_precursor_categories(signal.get("validated_precursor_evidence"))
        if environment_status == "confirmed":
            derived_categories, precursor_errors = _precursor_watch_validation(group, signal, results)
            errors.extend(precursor_errors)
        if direct_status == "confirmed":
            errors.extend(_confirmed_direct_precipitation_validation(group, signal, results))
        evidence_sufficient, signal_errors = _signal_evidence_sufficiency(signal, results)
        errors.extend(signal_errors)
        status = "valid" if not errors else "blocked"
        surface_decision = str(signal.get("operational_surface_decision") or "not_applicable")
        candidate_context_for_tier = group.get("candidate_context") if isinstance(group.get("candidate_context"), dict) else {}
        source_candidate_for_tier = candidate_context_for_tier.get("candidate") if isinstance(candidate_context_for_tier.get("candidate"), dict) else {}
        candidate_type = derive_candidate_type(
            source_candidate_for_tier.get("detection_basis"),
            direct_status,
        )
        complete_core_environment_coupling = False
        if status == "valid" and direct_status == "confirmed" and environment_status == "confirmed":
            plan = group["candidate_context"]["analysis_plan"]
            check_specifications = {
                str(item["check_id"]): item
                for item in plan.get("variable_checks", [])
                if isinstance(item, dict) and isinstance(item.get("check_id"), str) and item["check_id"]
            }
            candidate = group.get("candidate_context", {}).get("candidate", {})
            peak_hours = candidate.get("peak_forecast_hours", []) if isinstance(candidate, dict) else []
            alignment_ids = signal.get("spatiotemporal_alignment_evidence_check_ids")
            complete_core_environment_coupling = bool(
                complete_coupled_environment_alignment_check_ids(
                    alignment_ids if isinstance(alignment_ids, list) else [],
                    check_specifications=check_specifications,
                    check_results=results,
                    candidate_peak_hours=peak_hours,
                )
            )
        operational_escalation_evidence = has_operational_escalation_evidence(
            signal,
            results,
            derived_categories,
        )
        assessment = {
            "signal_validation": signal_class,
            "candidate_type": candidate_type,
            "direct_precipitation_status": direct_status,
            "environment_support_status": environment_status,
            "physical_consistency_status": str(signal.get("physical_consistency_status") or ""),
            "complete_core_environment_coupling": complete_core_environment_coupling,
            **persistence_fields,
            "operational_surface_decision": surface_decision,
            "monitoring_value": str(signal.get("monitoring_value") or ""),
            "independent_signal_categories": derived_categories,
            "spatiotemporal_alignment": signal.get("validated_spatiotemporal_alignment", False),
            "evidence_completeness": {"sufficient": evidence_sufficient},
            "operational_escalation_evidence": operational_escalation_evidence,
            "validation_status": status,
        }
        display_tier = derive_display_tier(assessment)
        # Step09 owns the final tier. Step08 can retain a contradicting prose
        # classification in an otherwise valid, provenance-matched result.
        # Normalize *only* that explicit classification in the public copy.
        # Keep Step08 source unchanged for traceability.
        try:
            public_surface_reason, tier_reason_repaired = public_reason(
                str(signal.get("operational_surface_reason") or ""), display_tier
            )
            public_guidance, tier_guidance_repaired = canonical_guidance(
                synthesis.get("operational_guidance"), display_tier
            )
            assert_public_tier({
                "candidate_id": candidate_id, "display_tier": display_tier,
                "operational_surface_reason": public_surface_reason,
                "operational_guidance": public_guidance,
            })
        except TierNarrativeError as exc:
            errors.append(f"STEP09_TIER_NARRATIVE_INVALID: {exc}")
            status = "blocked"
            assessment["validation_status"] = status
            display_tier = derive_display_tier(assessment)
            public_surface_reason = str(signal.get("operational_surface_reason") or "")
            public_guidance = synthesis.get("operational_guidance")
            tier_reason_repaired = tier_guidance_repaired = False
        if tier_reason_repaired or tier_guidance_repaired:
            print(
                f"STEP09_TIER_NARRATIVE_NORMALIZED candidate={candidate_id} tier={display_tier} "
                f"reason={int(tier_reason_repaired)} guidance={int(tier_guidance_repaired)}"
            )
        surface = display_tier != "hidden"
        emphasis = "priority" if display_tier == "priority" else "standard"
        if status != "valid":
            validation_reason = "; ".join(errors)
        else:
            surface_reason = str(signal.get("operational_surface_reason") or "").strip()
            if not surface_reason:
                raise Pass2PipelineError(
                    "STEP08_RESULT_INVALID",
                    f"Step08 operational_surface_reason is missing: {candidate_id}.",
                )
            validation_reason = public_surface_reason
        candidate_context = group.get("candidate_context") if isinstance(group.get("candidate_context"), dict) else {}
        source_candidate = candidate_context.get("candidate") if isinstance(candidate_context.get("candidate"), dict) else {}
        final_spatial_identity = synthesis.get("final_spatial_identity")
        region_index = _candidate_analysis_region_index(root, group)
        region_names = {
            str(item.get("region_id") or ""): str(item.get("name_ko") or "")
            for item in region_index.get("regions", [])
            if isinstance(item, dict) and str(item.get("region_id") or "")
        }
        effective_peak_hours = candidate_effective_peak_forecast_hours(
            source_candidate,
            synthesis.get("revision"),
        )
        peak_precipitation_details = candidate_peak_precipitation_details_from_group(
            group,
            effective_peak_hours,
        )
        analysis_plan = candidate_context.get("analysis_plan") if isinstance(candidate_context.get("analysis_plan"), dict) else {}
        display_check_specifications = {
            str(item.get("check_id")): item
            for item in analysis_plan.get("variable_checks", [])
            if isinstance(item, dict) and str(item.get("check_id") or "")
        }
        display_candidate_name = candidate_operational_display_name(
            source_candidate,
            synthesis.get("revision"),
            final_spatial_identity,
            region_names,
            direct_precipitation_status=direct_status,
            peak_precipitation_details=peak_precipitation_details,
            check_specifications=display_check_specifications,
            check_results=results,
        )
        if not display_candidate_name:
            raise Pass2PipelineError("STEP08_RESULT_INVALID", f"Step08 candidate display name is invalid: {candidate_id}.")
        operational_record = {
            "candidate_id": candidate_id,
            "display_candidate_name": display_candidate_name,
            "final_spatial_identity": copy.deepcopy(final_spatial_identity),
            "candidate_name_grounding_audit": copy.deepcopy(synthesis.get("candidate_name_grounding_audit")),
            "signal_class": signal_class,
            "candidate_type": candidate_type,
            "display_tier": display_tier,
            "direct_precipitation_status": direct_status,
            "environment_support_status": environment_status,
            **persistence_fields,
            "physical_consistency_status": str(signal.get("physical_consistency_status") or ""),
            "monitoring_value": str(signal.get("monitoring_value") or ""),
            "operational_surface_reason": public_surface_reason,
            "operational_surface_decision": surface_decision,
            "monitoring_emphasis": emphasis,
            "validation_status": status,
            "should_surface": surface,
            "validation_reason": validation_reason,
        }
        # Step09 never creates operational guidance.  It preserves the Step08
        # object only after validate_candidate_synthesis() has checked its
        # Step07 references and status-sensitive wording.
        if "operational_brief" in synthesis:
            operational_record["operational_brief"] = synthesis["operational_brief"]
        if "operational_guidance" in synthesis:
            operational_record["operational_guidance"] = copy.deepcopy(public_guidance)
        operational.append(operational_record)
    metrics = _build_operational_metrics(active_groups, check_by_candidate, synth_by_candidate, operational)
    document = {
        "schema_version": OPERATIONAL_VERSION,
        "init": init,
        "candidates": operational,
        "metrics": metrics,
    }
    write_validated_atomic(root, step09_output_path(root, init), document, schema_name=OPERATIONAL_SCHEMA)
    return document
