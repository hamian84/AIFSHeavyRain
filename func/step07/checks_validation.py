"""Step step07 validation responsibility; original function bodies preserved."""
from __future__ import annotations
from . import checks_context as _step_context

# Original func/step07/checks.py:161
def validate_scope_coverage_contract(result: dict[str, Any]) -> None:
    """Require the model's scope-coverage declaration to match its assessment.

    This does not infer weather.  The model declares both the meteorological
    assessment and how broadly the expected proposition held; Python only
    checks that those two model-authored fields are internally consistent.
    """
    if "scope_coverage" not in result:
        # Historical v3 Step07 artifacts remain readable by Step09/10/history.
        # Current Step08 synthesis explicitly requires the v4 checkpoint.
        return
    assessment = str(result.get("assessment") or "")
    scope_coverage = str(result.get("scope_coverage") or "")
    allowed = _SCOPE_COVERAGE_BY_ASSESSMENT.get(assessment)
    if allowed is None or scope_coverage not in allowed:
        raise Pass2PipelineError(
            "ASSESSMENT_SCOPE_COVERAGE_INVALID",
            f"{result.get('check_id')} assessment={assessment!r} is inconsistent with scope_coverage={scope_coverage!r}.",
        )

# Original func/step07/checks.py:293
def _required_spatial_localization_diagnostics(
    specification: dict[str, Any] | None,
) -> set[str]:
    """Return the exact weather fields requested by this alignment Check."""
    if not isinstance(specification, dict) or not _spatial_localization_enabled(specification):
        return set()
    components = {
        str(value)
        for value in specification.get("required_signal_components", [])
        if isinstance(value, str) and value
    }
    return {
        diagnostic
        for component, diagnostic in SPATIAL_LOCALIZATION_DIAGNOSTIC_BY_COMPONENT.items()
        if component in components
    }

# Original func/step07/checks.py:529
def validate_precipitation_motion_contract(
    root: Path,
    group: dict[str, Any],
    specification: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """Validate the structured continuity judgement used only for movement naming."""
    motion = result.get("precipitation_motion")
    if not isinstance(motion, dict):
        raise Pass2PipelineError(
            "PRECIPITATION_MOTION_INVALID",
            f"{result.get('check_id')} precipitation_motion must be an object.",
        )
    classification = str(motion.get("classification") or "")
    origin = motion.get("origin_region_id")
    destination = motion.get("destination_region_id")
    confidence = motion.get("confidence")
    enabled = _pure_direct_precipitation_move_check(specification)

    if result.get("assessment") == "insufficient_evidence":
        if motion != _empty_precipitation_motion(enabled=False):
            raise Pass2PipelineError(
                "PRECIPITATION_MOTION_INVALID",
                f"{result.get('check_id')} insufficient-evidence Check must use not_applicable precipitation_motion.",
            )
        return

    if not enabled:
        if motion != _empty_precipitation_motion(enabled=False):
            raise Pass2PipelineError(
                "PRECIPITATION_MOTION_INVALID",
                f"{result.get('check_id')} non-movement Check must return precipitation_motion=not_applicable.",
            )
        return

    if classification not in PRECIPITATION_MOTION_CLASSIFICATIONS - {"not_applicable"}:
        raise Pass2PipelineError(
            "PRECIPITATION_MOTION_INVALID",
            f"{result.get('check_id')} movement Check has invalid precipitation_motion classification={classification!r}.",
        )
    if classification == "indeterminate":
        if origin is not None or destination is not None or confidence is not None:
            raise Pass2PipelineError(
                "PRECIPITATION_MOTION_INVALID",
                f"{result.get('check_id')} indeterminate motion must not assert origin/destination/confidence.",
            )
    else:
        if confidence not in SPATIAL_LOCALIZATION_CONFIDENCE:
            raise Pass2PipelineError(
                "PRECIPITATION_MOTION_INVALID",
                f"{result.get('check_id')} classified motion requires high/medium confidence.",
            )

    if classification == "coherent_movement":
        if result.get("assessment") != "observed" or result.get("scope_coverage") != "complete_expected_scope":
            raise Pass2PipelineError(
                "PRECIPITATION_MOTION_INVALID",
                f"{result.get('check_id')} coherent movement requires assessment=observed and complete_expected_scope.",
            )
        if result.get("contradictory_findings"):
            raise Pass2PipelineError(
                "PRECIPITATION_MOTION_INVALID",
                f"{result.get('check_id')} coherent movement cannot retain contradictory findings.",
            )
        if (origin is None) != (destination is None):
            raise Pass2PipelineError(
                "PRECIPITATION_MOTION_INVALID",
                f"{result.get('check_id')} coherent movement must provide both region IDs or neither.",
            )
        if origin is not None:
            allowed = {
                str(item.get("region_id") or "")
                for item in _precipitation_motion_context(root, group, specification).get("allowed_regions", [])
                if isinstance(item, dict)
            }
            if not isinstance(origin, str) or not isinstance(destination, str) or origin not in allowed or destination not in allowed:
                raise Pass2PipelineError(
                    "PRECIPITATION_MOTION_INVALID",
                    f"{result.get('check_id')} coherent movement region IDs are outside the allowed operational-region namespace.",
                )
            if origin == destination:
                raise Pass2PipelineError(
                    "PRECIPITATION_MOTION_INVALID",
                    f"{result.get('check_id')} coherent movement origin and destination must differ.",
                )
    else:
        if origin is not None or destination is not None:
            raise Pass2PipelineError(
                "PRECIPITATION_MOTION_INVALID",
                f"{result.get('check_id')} non-coherent motion must not assert origin/destination region IDs.",
            )
        if result.get("assessment") == "observed":
            raise Pass2PipelineError(
                "PRECIPITATION_MOTION_INVALID",
                f"{result.get('check_id')} pure move Check assessed as observed must be classified coherent_movement.",
            )

# Original func/step07/checks.py:1129
def validate_step07_ensemble_consensus(result: dict[str, Any]) -> None:
    consensus = result.get("ensemble_consensus")
    if not isinstance(consensus, dict):
        raise Pass2PipelineError("STEP07_ENSEMBLE_INVALID", f"{result.get('check_id')} ensemble_consensus is missing.")
    if consensus.get("contract_version") != ENSEMBLE_CONTRACT_VERSION:
        raise Pass2PipelineError("STEP07_ENSEMBLE_INVALID", f"{result.get('check_id')} ensemble contract metadata is invalid.")
    mode = consensus.get("mode")
    if mode == "deterministic_insufficient_evidence":
        if (
            consensus.get("member_count") != 0
            or consensus.get("assessment_agreement") != "not_applicable"
            or consensus.get("consensus_assessment") != "insufficient_evidence"
            or consensus.get("selected_member_index") is not None
            or consensus.get("vote_counts") != []
            or consensus.get("precipitation_motion_agreement") != "not_applicable"
            or consensus.get("consensus_precipitation_motion_classification") != "not_applicable"
            or consensus.get("precipitation_motion_selected_member_index") is not None
            or consensus.get("precipitation_motion_vote_counts") != []
            or result.get("assessment") != "insufficient_evidence"
        ):
            raise Pass2PipelineError("STEP07_ENSEMBLE_INVALID", f"{result.get('check_id')} deterministic insufficient-evidence metadata is inconsistent.")
        return
    if mode != "llm_ensemble" or consensus.get("member_count") != ENSEMBLE_MEMBER_COUNT:
        raise Pass2PipelineError("STEP07_ENSEMBLE_INVALID", f"{result.get('check_id')} ensemble mode/member_count is invalid.")
    vote_counts = consensus.get("vote_counts")
    if not isinstance(vote_counts, list) or sum(int(row.get("count", 0)) for row in vote_counts if isinstance(row, dict)) != ENSEMBLE_MEMBER_COUNT:
        raise Pass2PipelineError("STEP07_ENSEMBLE_INVALID", f"{result.get('check_id')} ensemble votes do not sum to 3.")
    agreement = consensus.get("assessment_agreement")
    consensus_assessment = consensus.get("consensus_assessment")
    selected = consensus.get("selected_member_index")
    counts = {str(row.get("assessment")): int(row.get("count", 0)) for row in vote_counts if isinstance(row, dict)}
    winning_count = counts.get(str(consensus_assessment), 0)
    if agreement == "unanimous" and winning_count != 3:
        raise Pass2PipelineError("STEP07_ENSEMBLE_INVALID", f"{result.get('check_id')} unanimous consensus does not have 3 votes.")
    if agreement == "majority" and winning_count != 2:
        raise Pass2PipelineError("STEP07_ENSEMBLE_INVALID", f"{result.get('check_id')} majority consensus does not have 2 votes.")
    if agreement == "unresolved":
        if selected is not None or consensus_assessment != "uncertain" or max(counts.values(), default=0) >= 2:
            raise Pass2PipelineError("STEP07_ENSEMBLE_INVALID", f"{result.get('check_id')} unresolved consensus metadata is inconsistent.")
        if result.get("usable_for_synthesis") is not False or "ENSEMBLE_NO_CONSENSUS" not in (result.get("excluded_from_synthesis_reasons") or []):
            raise Pass2PipelineError("STEP07_ENSEMBLE_INVALID", f"{result.get('check_id')} unresolved consensus must be excluded from synthesis.")
    elif selected not in {1, 2, 3}:
        raise Pass2PipelineError("STEP07_ENSEMBLE_INVALID", f"{result.get('check_id')} selected_member_index is invalid.")
    if result.get("assessment") != consensus_assessment:
        raise Pass2PipelineError("STEP07_ENSEMBLE_INVALID", f"{result.get('check_id')} final assessment differs from ensemble consensus.")

    motion_agreement = consensus.get("precipitation_motion_agreement")
    motion_classification = consensus.get("consensus_precipitation_motion_classification")
    motion_selected = consensus.get("precipitation_motion_selected_member_index")
    motion_votes = consensus.get("precipitation_motion_vote_counts")
    if not isinstance(motion_votes, list) or sum(
        int(row.get("count", 0)) for row in motion_votes if isinstance(row, dict)
    ) != ENSEMBLE_MEMBER_COUNT:
        raise Pass2PipelineError(
            "STEP07_ENSEMBLE_INVALID",
            f"{result.get('check_id')} precipitation-motion votes do not sum to 3.",
        )
    motion_counts: dict[str, int] = {}
    for row in motion_votes:
        if not isinstance(row, dict):
            raise Pass2PipelineError(
                "STEP07_ENSEMBLE_INVALID",
                f"{result.get('check_id')} precipitation-motion vote is invalid.",
            )
        classification = str(row.get("classification") or "")
        if classification not in PRECIPITATION_MOTION_CLASSIFICATIONS:
            raise Pass2PipelineError(
                "STEP07_ENSEMBLE_INVALID",
                f"{result.get('check_id')} precipitation-motion vote classification is invalid.",
            )
        motion_counts[classification] = int(row.get("count", 0))
    result_motion = result.get("precipitation_motion")
    if not isinstance(result_motion, dict):
        raise Pass2PipelineError(
            "STEP07_ENSEMBLE_INVALID",
            f"{result.get('check_id')} final precipitation_motion is invalid.",
        )
    max_motion_count = max(motion_counts.values(), default=0)
    if motion_agreement == "not_applicable":
        if (
            motion_selected is not None
            or motion_classification != "not_applicable"
            or result_motion != _empty_precipitation_motion(enabled=False)
            or set(motion_counts) != {"not_applicable"}
        ):
            raise Pass2PipelineError(
                "STEP07_ENSEMBLE_INVALID",
                f"{result.get('check_id')} not-applicable precipitation-motion consensus is inconsistent.",
            )
    elif motion_agreement in {"unanimous", "majority"}:
        expected_count = 3 if motion_agreement == "unanimous" else 2
        if motion_selected not in {1, 2, 3} or max_motion_count != expected_count:
            raise Pass2PipelineError(
                "STEP07_ENSEMBLE_INVALID",
                f"{result.get('check_id')} precipitation-motion consensus count/selection is invalid.",
            )
        if motion_counts.get(str(motion_classification), 0) != expected_count:
            raise Pass2PipelineError(
                "STEP07_ENSEMBLE_INVALID",
                f"{result.get('check_id')} precipitation-motion consensus class does not match its votes.",
            )
        if result_motion.get("classification") != motion_classification:
            raise Pass2PipelineError(
                "STEP07_ENSEMBLE_INVALID",
                f"{result.get('check_id')} final precipitation_motion differs from ensemble class consensus.",
            )
    elif motion_agreement == "unresolved":
        if (
            motion_selected is not None
            or max_motion_count >= 2
            or motion_classification != "indeterminate"
            or result_motion != _empty_precipitation_motion(enabled=True)
        ):
            raise Pass2PipelineError(
                "STEP07_ENSEMBLE_INVALID",
                f"{result.get('check_id')} unresolved precipitation-motion consensus is inconsistent.",
            )
    else:
        raise Pass2PipelineError(
            "STEP07_ENSEMBLE_INVALID",
            f"{result.get('check_id')} precipitation_motion_agreement is invalid: {motion_agreement!r}.",
        )

# Original func/step07/checks.py:1253
def validate_step07_minority_report_consistency(
    step07: dict[str, Any], report: dict[str, Any]
) -> None:
    if (
        report.get("schema_version") != MINORITY_REPORT_VERSION
        or report.get("init") != step07.get("init")
        or report.get("ensemble_contract_version") != ENSEMBLE_CONTRACT_VERSION
        or report.get("member_count") != ENSEMBLE_MEMBER_COUNT
    ):
        raise Pass2PipelineError("STEP07_MINORITY_REPORT_INVALID", "Step07 minority report metadata does not match the current ensemble result.")
    results = {
        (str(row.get("candidate_id")), str(row.get("check_id"))): row
        for row in step07.get("results", []) if isinstance(row, dict)
    }
    report_rows = report.get("entries")
    if not isinstance(report_rows, list):
        raise Pass2PipelineError("STEP07_MINORITY_REPORT_INVALID", "Step07 minority report entries are invalid.")
    entries: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in report_rows:
        if not isinstance(entry, dict):
            raise Pass2PipelineError("STEP07_MINORITY_REPORT_INVALID", "Step07 minority report contains a non-object entry.")
        key = (str(entry.get("candidate_id")), str(entry.get("check_id")))
        if key in entries:
            raise Pass2PipelineError("STEP07_MINORITY_REPORT_INVALID", f"Duplicate minority report entry: {key}.")
        entries[key] = entry
    expected = {
        key for key, result in results.items()
        if isinstance(result.get("ensemble_consensus"), dict)
        and result["ensemble_consensus"].get("mode") == "llm_ensemble"
        and bool(result["ensemble_consensus"].get("disagreement_dimensions"))
    }
    if set(entries) != expected:
        missing = sorted(expected - set(entries))
        extra = sorted(set(entries) - expected)
        raise Pass2PipelineError(
            "STEP07_MINORITY_REPORT_INVALID",
            f"Minority report Check set differs from Step07 consensus. missing={missing} extra={extra}",
        )
    for key, entry in entries.items():
        consensus = results[key]["ensemble_consensus"]
        for field in (
            "consensus_assessment", "assessment_agreement", "selected_member_index",
            "vote_counts", "localization_agreement", "disagreement_severity", "disagreement_dimensions",
        ):
            if entry.get(field) != consensus.get(field):
                raise Pass2PipelineError(
                    "STEP07_MINORITY_REPORT_INVALID",
                    f"{key} minority report field {field} differs from Step07 consensus.",
                )
        minority_indices = entry.get("minority_member_indices")
        members = entry.get("member_reports")
        if not isinstance(minority_indices, list) or not isinstance(members, list):
            raise Pass2PipelineError("STEP07_MINORITY_REPORT_INVALID", f"{key} minority member records are invalid.")
        member_indices = [member.get("member_index") for member in members if isinstance(member, dict)]
        if sorted(minority_indices) != sorted(member_indices) or len(member_indices) != len(set(member_indices)):
            raise Pass2PipelineError("STEP07_MINORITY_REPORT_INVALID", f"{key} minority member indices do not match member reports.")
        agreement = consensus.get("assessment_agreement")
        dimensions = set(consensus.get("disagreement_dimensions") or [])
        vote_rows = [row for row in entry.get("vote_counts", []) if isinstance(row, dict)]
        if agreement == "unanimous":
            if "assessment" in dimensions or len(vote_rows) != 1:
                raise Pass2PipelineError(
                    "STEP07_MINORITY_REPORT_INVALID",
                    f"{key} unanimous assessment may appear in Minority Report only for non-assessment disagreement.",
                )
        elif agreement == "majority":
            if "assessment" not in dimensions or len(vote_rows) != 2:
                raise Pass2PipelineError(
                    "STEP07_MINORITY_REPORT_INVALID",
                    f"{key} majority assessment disagreement must preserve exactly two assessment vote states.",
                )
        elif agreement == "unresolved":
            if "assessment" not in dimensions or len(vote_rows) != 3:
                raise Pass2PipelineError(
                    "STEP07_MINORITY_REPORT_INVALID",
                    f"{key} unresolved assessment disagreement must preserve three distinct assessment vote states.",
                )
        else:
            raise Pass2PipelineError(
                "STEP07_MINORITY_REPORT_INVALID",
                f"{key} minority report assessment agreement is invalid: {agreement}",
            )
        if agreement == "unresolved":
            if sorted(minority_indices) != [1, 2, 3]:
                raise Pass2PipelineError("STEP07_MINORITY_REPORT_INVALID", f"{key} unresolved consensus must preserve all three member reports.")
            reconstructed: dict[str, int] = {}
            for member in members:
                assessment = str(member.get("assessment") or "")
                reconstructed[assessment] = reconstructed.get(assessment, 0) + 1
            stored = {str(row.get("assessment")): int(row.get("count", 0)) for row in entry.get("vote_counts", []) if isinstance(row, dict)}
            if reconstructed != stored:
                raise Pass2PipelineError("STEP07_MINORITY_REPORT_INVALID", f"{key} unresolved member reports do not reproduce the stored votes.")
        elif "assessment" in dimensions:
            if not any(
                isinstance(member, dict) and member.get("assessment") != consensus.get("consensus_assessment")
                for member in members
            ):
                raise Pass2PipelineError("STEP07_MINORITY_REPORT_INVALID", f"{key} assessment disagreement has no preserved minority assessment.")

# Original func/step07/checks.py:1353
def _positive_finding_supported_forecast_hours(result: dict[str, Any]) -> set[int]:
    """Return forecast hours grounded by at least one positive finding.

    Spatial localization selects one representative hour for a multivariable
    alignment.  A multi-hour Check may legitimately describe F042 and F048 in
    separate positive findings, so requiring every finding to repeat the same
    hour incorrectly rejects otherwise grounded localizations.  The stricter
    requirement that *all required image diagnostics* exist at the selected
    hour is enforced separately below.
    """
    supported: set[int] = set()
    findings = result.get("findings")
    if not isinstance(findings, list):
        return supported
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        refs = finding.get("evidence_refs")
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if (
                isinstance(ref, dict)
                and isinstance(ref.get("forecast_hour"), int)
                and not isinstance(ref.get("forecast_hour"), bool)
                and int(ref["forecast_hour"]) >= 0
            ):
                supported.add(int(ref["forecast_hour"]))
    return supported

# Original func/step07/checks.py:1384
def _positive_image_diagnostics_at_hour(result: dict[str, Any], forecast_hour: int) -> set[str]:
    diagnostics: set[str] = set()
    findings = result.get("findings")
    if not isinstance(findings, list):
        return diagnostics
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        refs = finding.get("evidence_refs")
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if (
                isinstance(ref, dict)
                and ref.get("evidence_type") == "image"
                and ref.get("forecast_hour") == forecast_hour
                and isinstance(ref.get("physical_diagnostic"), str)
            ):
                diagnostics.add(str(ref["physical_diagnostic"]))
    return diagnostics

# Original func/step07/checks.py:1406
def validate_spatial_localization_contract(
    root: Path,
    group: dict[str, Any],
    specification: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """Validate Step07's closed-vocabulary spatial localization metadata.

    Python validates only namespace, time/evidence linkage and structural
    consistency.  It never decides which region contains the meteorological
    overlap.
    """
    context = _candidate_operational_localization_context(root, group, specification)
    localization = result.get("spatial_localization")
    if not isinstance(localization, dict):
        raise Pass2PipelineError(
            "SPATIAL_LOCALIZATION_INVALID",
            f"{result.get('check_id')} spatial_localization must be an object.",
        )
    status = localization.get("status")
    forecast_hour = localization.get("forecast_hour")
    confidence = localization.get("confidence")
    regions = localization.get("regions")
    if not isinstance(regions, list):
        raise Pass2PipelineError("SPATIAL_LOCALIZATION_INVALID", f"{result.get('check_id')} localization regions must be an array.")

    if context["enabled"] is not True:
        if status != "not_applicable" or forecast_hour is not None or confidence is not None or regions:
            raise Pass2PipelineError(
                "SPATIAL_LOCALIZATION_INVALID",
                f"{result.get('check_id')} non-multivariable Check must use not_applicable localization.",
            )
        return

    if status == "indeterminate":
        if forecast_hour is not None or confidence is not None or regions:
            raise Pass2PipelineError(
                "SPATIAL_LOCALIZATION_INVALID",
                f"{result.get('check_id')} indeterminate localization must not invent a time or region.",
            )
        return
    localization_eligible = bool(
        result.get("assessment") == "observed"
        and result.get("scope_coverage") == "complete_expected_scope"
        and result.get("evidence_status") == "complete"
        and result.get("evidence_complete") is True
        and result.get("usable_for_synthesis") is not False
        and not result.get("contradictory_findings")
        and not result.get("uncertainties")
    )
    if status == "localized" and not localization_eligible:
        raise Pass2PipelineError(
            "SPATIAL_LOCALIZATION_INVALID",
            f"{result.get('check_id')} localized status requires observed + complete_expected_scope, complete usable evidence, and no contradictory findings or uncertainties.",
        )
    if status != "localized":
        raise Pass2PipelineError(
            "SPATIAL_LOCALIZATION_INVALID",
            f"{result.get('check_id')} eligible multivariable Check must use localized or indeterminate status.",
        )
    if not isinstance(forecast_hour, int) or isinstance(forecast_hour, bool) or forecast_hour < 0:
        raise Pass2PipelineError("SPATIAL_LOCALIZATION_INVALID", f"{result.get('check_id')} localized forecast_hour is invalid.")
    if confidence not in SPATIAL_LOCALIZATION_CONFIDENCE:
        raise Pass2PipelineError("SPATIAL_LOCALIZATION_INVALID", f"{result.get('check_id')} localization confidence is invalid.")
    if not regions:
        raise Pass2PipelineError("SPATIAL_LOCALIZATION_INVALID", f"{result.get('check_id')} localized result has no operational region.")
    supported_hours = _positive_finding_supported_forecast_hours(result)
    if forecast_hour not in supported_hours:
        raise Pass2PipelineError(
            "SPATIAL_LOCALIZATION_EVIDENCE_INVALID",
            f"{result.get('check_id')} localization F{forecast_hour:03d} is not referenced by any positive finding; supported_hours={sorted(supported_hours)}.",
        )
    observed_diagnostics = _positive_image_diagnostics_at_hour(result, forecast_hour)
    required_diagnostics = _required_spatial_localization_diagnostics(specification)
    missing_diagnostics = sorted(required_diagnostics - observed_diagnostics)
    if missing_diagnostics:
        raise Pass2PipelineError(
            "SPATIAL_LOCALIZATION_EVIDENCE_INVALID",
            f"{result.get('check_id')} localization F{forecast_hour:03d} lacks required image references: {missing_diagnostics}.",
        )
    allowed_ids = {str(item["region_id"]) for item in context["allowed_regions"]}
    seen: set[str] = set()
    for item in regions:
        if not isinstance(item, dict):
            raise Pass2PipelineError("SPATIAL_LOCALIZATION_INVALID", f"{result.get('check_id')} localized region must be an object.")
        region_id = str(item.get("region_id") or "")
        sector = str(item.get("relative_sector") or "")
        if region_id not in allowed_ids:
            raise Pass2PipelineError(
                "SPATIAL_LOCALIZATION_REGION_INVALID",
                f"{result.get('check_id')} localization uses region outside the candidate-overlap operational namespace: {region_id!r}.",
            )
        if region_id in seen:
            raise Pass2PipelineError("SPATIAL_LOCALIZATION_REGION_INVALID", f"{result.get('check_id')} localization repeats region {region_id}.")
        if sector not in SPATIAL_LOCALIZATION_SECTORS:
            raise Pass2PipelineError("SPATIAL_LOCALIZATION_REGION_INVALID", f"{result.get('check_id')} localization sector is invalid: {sector!r}.")
        seen.add(region_id)

# Original func/step07/checks.py:1505
def validate_candidate_spatial_localizations(
    root: Path,
    group: dict[str, Any],
    results: dict[str, dict[str, Any]],
) -> None:
    """Revalidate all persisted Step07 localizations for downstream stages."""
    specifications = _check_specifications(group)
    for check_id, result in results.items():
        specification = specifications.get(str(check_id))
        if specification is None:
            raise Pass2PipelineError("VARIABLE_CHECKS_INVALID", f"{group.get('candidate_id')} has no Step04 definition for {check_id}.")
        validate_spatial_localization_contract(root, group, specification, result)

# Original func/step07/checks.py:1519
def validate_candidate_precipitation_motions(
    root: Path,
    group: dict[str, Any],
    results: dict[str, dict[str, Any]],
) -> None:
    """Revalidate all persisted Step07 precipitation-motion metadata."""
    specifications = _check_specifications(group)
    for check_id, result in results.items():
        specification = specifications.get(str(check_id))
        if specification is None:
            raise Pass2PipelineError(
                "VARIABLE_CHECKS_INVALID",
                f"{group.get('candidate_id')} has no Step04 definition for {check_id}.",
            )
        validate_precipitation_motion_contract(root, group, specification, result)

# Original func/step07/checks.py:1716
def _validate_system_direct_precipitation_assessment(
    check_id: str,
    anchor_context: dict[str, Any] | None,
    assessment: dict[str, Any],
) -> None:
    """Prevent an unavailable operational-scope check from asserting absence."""
    if not isinstance(anchor_context, dict) or not anchor_context.get("direct_precipitation_anchor"):
        return
    if anchor_context.get("threshold_met") or anchor_context.get("threshold_evaluation_complete"):
        return
    if (
        assessment.get("assessment") == "insufficient_evidence"
        and assessment.get("scope_coverage") == "indeterminate"
    ):
        return
    raise Pass2PipelineError(
        "DIRECT_PRECIPITATION_EVALUATION_UNAVAILABLE_ASSESSMENT_INVALID",
        f"{check_id} has unavailable or incomplete operational-scope precipitation evaluation; "
        "assessment must be insufficient_evidence with scope_coverage=indeterminate.",
    )

# Original func/step07/checks.py:1904
def _allowed_refs(check: dict[str, Any], entries: list[dict[str, Any]]) -> set[tuple[str, str, str | None, int | None]]:
    allowed: set[tuple[str, str, str | None, int | None]] = set()
    available = set(check["available_request_ids"])
    for entry in entries:
        request = entry["request"]
        request_id = str(request["request_id"])
        if request_id not in available:
            continue
        step05 = entry["step05"]
        if request.get("data_type") == "quantitative_excerpt" and step05.get("status") == "success":
            allowed.add((request_id, "quantitative", None, None))
            result = step05.get("result")
            features = result.get("features") if isinstance(result, dict) else None
            if isinstance(features, dict):
                for feature_id, feature in features.items():
                    if not isinstance(feature_id, str) or not isinstance(feature, dict):
                        continue
                    per_forecast_hour = feature.get("per_forecast_hour")
                    if not isinstance(per_forecast_hour, list):
                        continue
                    for item in per_forecast_hour:
                        forecast_hour = item.get("forecast_hour") if isinstance(item, dict) else None
                        if isinstance(forecast_hour, int):
                            allowed.add((request_id, "quantitative", feature_id, forecast_hour))
        for image in step05.get("generated_images", []):
            physical_diagnostic = image.get("physical_diagnostic")
            forecast_hour = image.get("forecast_hour")
            allowed.add((request_id, "image", physical_diagnostic, forecast_hour))
            if forecast_hour is None:
                request_forecast_hours = image.get("request_forecast_hours")
                if isinstance(request_forecast_hours, list):
                    for request_forecast_hour in request_forecast_hours:
                        if isinstance(request_forecast_hour, int):
                            allowed.add((request_id, "image", physical_diagnostic, request_forecast_hour))
    return allowed

# Original func/step07/checks.py:2070
def _validate_specific_quantitative_selector_preflight(
    specification: dict[str, Any], check: dict[str, Any], entries: list[dict[str, Any]],
    selectors: Any,
) -> None:
    """Reject wrong-source, wrong-region and mixed series *before* relation math.

    Apply only to the supported single-specific contract with fully available
    Step05 cells. Non-specific/majority checks preserve the existing rules.
    """
    context = _required_specific_quantitative_selectors(specification, check, entries)
    if context is None or context["status"] != "available":
        return
    allowed_request_ids = set(context["candidate_request_ids"])
    expected = context["required_identity"]
    selected = selectors if isinstance(selectors, list) else []
    target_hours = set(expected["forecast_hours"])
    allowed_hours = set(expected["allowed_forecast_hours"])
    source_ids: set[str] = set()
    selected_hours: set[int] = set()
    for selector in selected:
        if not isinstance(selector, dict):
            continue  # Existing resolver reports malformed selectors.
        # Leave malformed field types to the existing strict resolver instead
        # of raising an unhandled Python TypeError during a set membership test.
        if (
            not isinstance(selector.get("request_id"), str)
            or not isinstance(selector.get("feature_id"), str)
            or not isinstance(selector.get("region_id"), str)
            or not isinstance(selector.get("statistic"), str)
            or isinstance(selector.get("forecast_hour"), bool)
            or not isinstance(selector.get("forecast_hour"), int)
        ):
            continue
        if (
            selector.get("request_id") not in allowed_request_ids
            or selector.get("feature_id") != expected["feature_id"]
            or selector.get("region_id") != expected["region_id"]
            or selector.get("statistic") != expected["statistic"]
            or selector.get("forecast_hour") not in allowed_hours
        ):
            raise Pass2PipelineError(
                "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
                f"{check.get('check_id')} specific Check selected a cell outside the Step04 scope: "
                f"{ {key: selector.get(key) for key in ('request_id','feature_id','region_id','forecast_hour','statistic')} }",
            )
        source_ids.add(selector["request_id"])
        selected_hours.add(selector["forecast_hour"])
    if len(source_ids) > 1 or (source_ids and not target_hours.issubset(selected_hours)):
        raise Pass2PipelineError(
            "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
            f"{check.get('check_id')} specific quantitative series must cover all required hours "
            "from one Step05 request.",
        )

# Original func/step07/checks.py:2125
def _quantitative_selector_namespace_issues(
    check: dict[str, Any], entries: list[dict[str, Any]], selectors: Any,
    *, source: str,
) -> list[dict[str, Any]]:
    """Report well-formed AI selector tuples absent from Step05's exact namespace.

    Do not guess a replacement region, feature, hour or statistic. Malformed
    selectors remain the responsibility of the existing strict resolver.
    """
    if not isinstance(selectors, list):
        return []
    namespace = _allowed_quantitative_selector_rows(check, entries)
    permitted = {
        (row["request_id"], row["feature_id"], row["forecast_hour"], row["region_id"], statistic)
        for row in namespace for statistic in row["statistics"]
    }
    issues: list[dict[str, Any]] = []
    for index, selector in enumerate(selectors):
        if not isinstance(selector, dict):
            continue
        request_id = selector.get("request_id")
        feature_id = selector.get("feature_id")
        hour = selector.get("forecast_hour")
        region_id = selector.get("region_id")
        statistic = selector.get("statistic")
        if not (
            all(isinstance(v, str) and v for v in (request_id, feature_id, region_id))
            and isinstance(hour, int) and not isinstance(hour, bool) and hour >= 0
            and statistic in QUANTITATIVE_STATISTICS
        ):
            continue
        if (request_id, feature_id, hour, region_id, statistic) in permitted:
            continue
        context = [
            row for row in namespace
            if row["request_id"] == request_id and row["feature_id"] == feature_id
            and row["forecast_hour"] == hour and statistic in row["statistics"]
        ]
        issues.append({
            "source": f"{source}[{index}]",
            "selector": {name: selector[name] for name in (
                "request_id", "feature_id", "forecast_hour", "region_id", "statistic"
            )},
            "valid_region_ids_for_same_series": sorted({row["region_id"] for row in context})[:40],
        })
    return issues

# Original func/step07/checks.py:2173
def _quantitative_response_namespace_issues(
    check: dict[str, Any], entries: list[dict[str, Any]], ai: dict[str, Any],
) -> list[dict[str, Any]]:
    issues = _quantitative_selector_namespace_issues(
        check, entries, ai.get("quantitative_claims"), source="quantitative_claims",
    )
    relations = ai.get("quantitative_relations")
    if isinstance(relations, list):
        for index, relation in enumerate(relations):
            if isinstance(relation, dict):
                issues.extend(_quantitative_selector_namespace_issues(
                    check, entries, relation.get("selectors"),
                    source=f"quantitative_relations[{index}].selectors",
                ))
    return issues

# Original func/step07/checks.py:2190
def _quantitative_relation_diagnostics(
    check: dict[str, Any], entries: list[dict[str, Any]], ai: dict[str, Any],
) -> list[dict[str, Any]]:
    """Bounded source-backed error audit. Does not alter forecast assessment."""
    diagnostics: list[dict[str, Any]] = []
    relations = ai.get("quantitative_relations")
    if not isinstance(relations, list):
        return diagnostics
    fields = ("request_id", "feature_id", "forecast_hour", "region_id", "statistic")
    for index, relation in enumerate(relations[:12]):
        if not isinstance(relation, dict):
            diagnostics.append({"index": index, "error": "relation_not_object"})
            continue
        selectors = relation.get("selectors")
        row: dict[str, Any] = {
            "index": index,
            "relation_id": str(relation.get("relation_id") or "")[:80],
            "relation_type": str(relation.get("relation_type") or "")[:80],
            "claimed_relation": str(relation.get("claimed_relation") or "")[:80],
            "selectors": [
                {key: item.get(key) for key in fields}
                for item in selectors[:20] if isinstance(item, dict)
            ] if isinstance(selectors, list) else [],
        }
        try:
            points = resolve_quantitative_claims(check, entries, selectors)
            row["resolved_points"] = [{
                "feature_id": point.get("physical_diagnostic"),
                "region_id": point.get("region_id"),
                "forecast_hour": point.get("forecast_hour"),
                "statistic": point.get("statistic"),
                "value": point.get("resolved_value"),
                "units": point.get("units"),
            } for point in points[:20]]
            calculated = compute_quantitative_relation(relation, points)
            row["computed_relation"] = calculated.get("computed_relation")
            row["consistent"] = bool(calculated.get("consistent"))
        except (Pass2PipelineError, QuantitativeConsistencyError) as exc:
            row["validation_error_type"] = type(exc).__name__
            # Resolver/compute messages are mechanical, not AI-authored prose.
            row["validation_error"] = str(exc)[:280]
        diagnostics.append(row)
    return diagnostics

# Original func/step07/checks.py:2235
def _write_step07_grounding_failure(
    root: Path, init: str, *, candidate: str, bundle: str, member: int,
    attempt: int, error: Pass2PipelineError, response: dict[str, Any],
    rows: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str]],
) -> None:
    """Keep bounded, metadata-only failures; never persist model prose or API secrets.

    Logging must not change a Step07 forecast decision or mask its exception.
    """
    try:
        assessments = {
            str(value.get("check_id")): value
            for value in response.get("assessments", []) if isinstance(value, dict)
        }
        check_details = []
        for _group, check, entries, _fingerprint in rows:
            check_id = str(check["check_id"])
            ai = assessments.get(check_id)
            if not isinstance(ai, dict):
                continue
            issues = _quantitative_response_namespace_issues(check, entries, ai)
            if error.code.startswith("QUANTITATIVE_"):
                specification = _check_specifications(_group).get(check_id, {})
                check_details.append({
                    "check_id": check_id,
                    "invalid_selectors": issues[:20],
                    "specific_selector_context": _required_specific_quantitative_selectors(
                        specification, check, entries,
                    ),
                    "relation_diagnostics": _quantitative_relation_diagnostics(check, entries, ai),
                })
            elif error.code in {
                "SPATIAL_LOCALIZATION_INVALID", "SPATIAL_LOCALIZATION_EVIDENCE_INVALID",
                "SPATIAL_LOCALIZATION_REGION_INVALID", "SPATIAL_NARRATIVE_GROUNDING_INVALID",
            }:
                specification = _check_specifications(_group).get(check_id, {})
                spatial_issues = _spatial_narrative_grounding_issues(root, _group, specification, ai)
                spatial_state = _spatial_localization_retry_diagnostics(root, _group, specification, ai, check)
                check_details.append({
                    "check_id": check_id,
                    "spatial_localization": spatial_state,
                    "spatial_narrative_issues": spatial_issues,
                })
            elif issues:
                check_details.append({"check_id": check_id, "invalid_selectors": issues[:20]})
        destination = runtime_path(root, "daio", init, "step07", "diagnostics", "grounding_failures.json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(destination.read_text(encoding="utf-8")) if destination.is_file() else None
        records = existing.get("failures", []) if isinstance(existing, dict) else []
        if not isinstance(records, list):
            records = []
        # Keep exception prose only for quantitative-contract failures; other
        # validator messages can contain untrusted model narrative.
        quantitative_error = (
            str(error)[:500] if error.code.startswith("QUANTITATIVE_") else None
        )
        records.append({
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "candidate_id": candidate, "bundle_id": bundle,
            "member_index": member, "attempt": attempt,
            "code": error.code, "quantitative_error": quantitative_error,
            "checks": check_details,
        })
        document = {"version": "step07-grounding-failures-v1", "init": init, "failures": records[-100:]}
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(destination)
    except (OSError, ValueError, TypeError, KeyError, Pass2PipelineError) as exc:
        print(f"STEP07_GROUNDING_DIAGNOSTIC_WRITE_FAILED candidate={candidate} bundle={bundle} reason={type(exc).__name__}")

# Original func/step07/checks.py:2306
def _quantitative_selector_has_namespace_mismatch(check: dict[str, Any], entries: list[dict[str, Any]], selectors: Any) -> bool:
    """Distinguish VLM selector hallucinations from source or implementation failures."""
    if not isinstance(selectors, list):
        return False
    entries_by_request = {
        str(entry.get("request", {}).get("request_id")): entry
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("request"), dict)
    }
    available_request_ids = {str(value) for value in check.get("available_request_ids", [])}
    for selector in selectors:
        if not isinstance(selector, dict):
            return False
        request_id = selector.get("request_id")
        feature_id = selector.get("feature_id")
        forecast_hour = selector.get("forecast_hour")
        region_id = selector.get("region_id")
        statistic = selector.get("statistic")
        if not all(isinstance(value, str) and value for value in (request_id, feature_id, region_id)):
            return False
        if not isinstance(forecast_hour, int) or isinstance(forecast_hour, bool) or forecast_hour < 0 or statistic not in QUANTITATIVE_STATISTICS:
            return False
        if request_id not in available_request_ids:
            return True
        entry = entries_by_request.get(request_id)
        if entry is None:
            return False
        request = entry.get("request")
        step05 = entry.get("step05")
        if not isinstance(request, dict) or not isinstance(step05, dict):
            return False
        if request.get("data_type") != "quantitative_excerpt" or step05.get("status") != "success":
            return True
        result = step05.get("result")
        features = result.get("features") if isinstance(result, dict) else None
        feature = features.get(feature_id) if isinstance(features, dict) else None
        if not isinstance(feature, dict):
            return True
        hourly = feature.get("per_forecast_hour")
        hour_row = next((item for item in hourly if isinstance(item, dict) and item.get("forecast_hour") == forecast_hour), None) if isinstance(hourly, list) else None
        if not isinstance(hour_row, dict):
            return True
        regional_statistics = hour_row.get("regional_statistics")
        if not any(isinstance(item, dict) and item.get("region_id") == region_id for item in regional_statistics or []):
            return True
        if _quantitative_selector_namespace_issues(check, entries, [selector], source="selector"):
            return True
    return False

# Original func/step07/checks.py:2356
def _normalize_ai_evidence_references(
    check: dict[str, Any], entries: list[dict[str, Any]], findings: Any,
) -> list[dict[str, Any]]:
    """Validate the AI response namespace and normalize it to the stored result contract."""
    if not isinstance(findings, list):
        raise Pass2PipelineError("CHECK_RESULT_INVALID", f"{check.get('check_id')} has invalid findings.")
    allowed = _allowed_refs(check, entries)
    normalized_findings: list[dict[str, Any]] = []
    for finding in findings:
        refs = finding.get("evidence_refs") if isinstance(finding, dict) else None
        if not isinstance(refs, list) or not refs:
            raise Pass2PipelineError("EVIDENCE_REF_INVALID", f"{check.get('check_id')} finding has no evidence_refs.")
        normalized_refs: list[dict[str, Any]] = []
        for ref in refs:
            if not isinstance(ref, dict):
                raise Pass2PipelineError("EVIDENCE_REF_INVALID", "evidence_ref must be an object.")
            request_id = ref.get("request_id")
            evidence_type = ref.get("evidence_type")
            physical_diagnostic = ref.get("physical_diagnostic")
            feature_id = ref.get("feature_id")
            forecast_hour = ref.get("forecast_hour")
            if not isinstance(request_id, str) or not request_id:
                raise Pass2PipelineError("EVIDENCE_REF_INVALID", "evidence_ref requires a request_id.")
            if evidence_type == "image":
                if feature_id is not None or not isinstance(physical_diagnostic, str) or not physical_diagnostic:
                    raise Pass2PipelineError("EVIDENCE_REF_INVALID", "image evidence_ref requires only a physical_diagnostic.")
                stored_diagnostic = physical_diagnostic
            elif evidence_type == "quantitative":
                if physical_diagnostic is not None:
                    raise Pass2PipelineError("EVIDENCE_REF_INVALID", "quantitative evidence_ref must not use physical_diagnostic.")
                if feature_id is None:
                    if forecast_hour is not None:
                        raise Pass2PipelineError("EVIDENCE_REF_INVALID", "quantitative request-level evidence_ref requires feature_id=null and forecast_hour=null.")
                    stored_diagnostic = None
                else:
                    if not isinstance(feature_id, str) or not feature_id:
                        raise Pass2PipelineError("EVIDENCE_REF_INVALID", "quantitative evidence_ref feature_id must be a non-empty string or null.")
                    if not isinstance(forecast_hour, int) or isinstance(forecast_hour, bool) or forecast_hour < 0:
                        raise Pass2PipelineError("EVIDENCE_REF_INVALID", "quantitative detailed evidence_ref requires a non-negative forecast_hour.")
                    stored_diagnostic = feature_id
            else:
                raise Pass2PipelineError("EVIDENCE_REF_INVALID", "evidence_ref has unsupported evidence_type.")
            normalized_ref = {
                "request_id": request_id,
                "evidence_type": evidence_type,
                "physical_diagnostic": stored_diagnostic,
                "forecast_hour": forecast_hour,
            }
            key = (request_id, evidence_type, stored_diagnostic, forecast_hour)
            if key not in allowed:
                raise Pass2PipelineError("EVIDENCE_REF_INVALID", f"{check.get('check_id')} references unavailable evidence: {key}")
            normalized_refs.append(normalized_ref)
        normalized_findings.append({"statement": finding.get("statement"), "evidence_refs": normalized_refs})
    return normalized_findings

# Original func/step07/checks.py:2427
def resolve_quantitative_claims(
    check: dict[str, Any], entries: list[dict[str, Any]], selectors: Any,
) -> list[dict[str, Any]]:
    """Resolve Step07 AI selectors against Step05 quantitative JSON source values."""
    if not isinstance(selectors, list):
        raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", f"{check.get('check_id')} quantitative_claims must be an array.")
    entries_by_request = {
        str(entry.get("request", {}).get("request_id")): entry
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("request"), dict)
    }
    available_request_ids = {str(item) for item in check.get("available_request_ids", [])}
    seen: set[tuple[str, str, int, str, str]] = set()
    resolved: list[dict[str, Any]] = []
    for selector in selectors:
        if not isinstance(selector, dict):
            raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", "quantitative selector must be an object.")
        request_id = selector.get("request_id")
        feature_id = selector.get("feature_id")
        forecast_hour = selector.get("forecast_hour")
        region_id = selector.get("region_id")
        statistic = selector.get("statistic")
        if not all(isinstance(value, str) and value for value in (request_id, feature_id, region_id)):
            raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", "quantitative selector requires non-empty request_id, feature_id, and region_id.")
        if not isinstance(forecast_hour, int) or isinstance(forecast_hour, bool) or forecast_hour < 0:
            raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", "quantitative selector has invalid forecast_hour.")
        allowed_hours = check.get("allowed_quantitative_forecast_hours")
        if isinstance(allowed_hours, list) and allowed_hours and forecast_hour not in allowed_hours:
            raise Pass2PipelineError(
                "QUANTITATIVE_FORECAST_HOUR_OUT_OF_SCOPE",
                f"{check.get('check_id')} quantitative forecast hour F{forecast_hour:03d} is outside allowed phase hours={allowed_hours}.",
            )
        if statistic not in QUANTITATIVE_STATISTICS:
            raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", f"quantitative selector has unsupported statistic: {statistic!r}")
        selector_key = (request_id, feature_id, forecast_hour, region_id, statistic)
        if selector_key in seen:
            raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", f"duplicate quantitative selector: {selector_key}")
        seen.add(selector_key)
        if request_id not in available_request_ids:
            raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", f"{check.get('check_id')} quantitative selector uses unavailable request: {request_id}")
        entry = entries_by_request.get(request_id)
        if entry is None:
            raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", f"quantitative selector request is not resolved: {request_id}")
        request = entry["request"]
        step05 = entry.get("step05")
        if request.get("data_type") != "quantitative_excerpt" or not isinstance(step05, dict) or step05.get("status") != "success":
            raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", f"quantitative selector request is not successful quantitative evidence: {request_id}")
        result = step05.get("result")
        features = result.get("features") if isinstance(result, dict) else None
        feature = features.get(feature_id) if isinstance(features, dict) else None
        if not isinstance(feature, dict):
            raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", f"quantitative feature does not exist for request: {request_id}/{feature_id}")
        units = feature.get("units")
        if not isinstance(units, str) or not units:
            raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", f"quantitative feature has no units: {request_id}/{feature_id}")
        hourly = feature.get("per_forecast_hour")
        hour_row = next((item for item in hourly if isinstance(item, dict) and item.get("forecast_hour") == forecast_hour), None) if isinstance(hourly, list) else None
        if not isinstance(hour_row, dict):
            raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", f"quantitative forecast hour does not exist: {request_id}/{feature_id}/F{forecast_hour:03d}")
        regional_statistics = hour_row.get("regional_statistics")
        region = next((item for item in regional_statistics if isinstance(item, dict) and item.get("region_id") == region_id), None) if isinstance(regional_statistics, list) else None
        if not isinstance(region, dict):
            raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", f"quantitative region does not exist: {request_id}/{feature_id}/F{forecast_hour:03d}/{region_id}")
        region_name = region.get("region_name")
        statistics = region.get("statistics")
        value = statistics.get(statistic) if isinstance(statistics, dict) else None
        if not isinstance(region_name, str) or not region_name or isinstance(value, bool) or not isinstance(value, Real):
            raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", f"quantitative statistic is not resolvable: {request_id}/{feature_id}/{region_id}/{statistic}")
        resolved_location: dict[str, float | int] | None = None
        max_location = statistics.get("max_location") if statistic == "max" and isinstance(statistics, dict) else None
        if max_location is not None:
            latitude = max_location.get("latitude") if isinstance(max_location, dict) else None
            longitude = max_location.get("longitude") if isinstance(max_location, dict) else None
            if isinstance(latitude, bool) or isinstance(longitude, bool) or not isinstance(latitude, Real) or not isinstance(longitude, Real):
                raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", f"max_location is invalid: {request_id}/{feature_id}/{region_id}")
            resolved_location = {"latitude": latitude, "longitude": longitude}
        resolved.append({
            "request_id": request_id,
            # Keep the existing stored-result field for CAND_001/CAND_004 compatibility.
            "physical_diagnostic": feature_id,
            "forecast_hour": forecast_hour,
            "region_id": region_id,
            "region_name": region_name,
            "statistic": statistic,
            "resolved_value": value,
            "units": units,
            "resolved_location": resolved_location,
        })
    return resolved

# Original func/step07/checks.py:2518
def validate_resolved_quantitative_claims(check: dict[str, Any], entries: list[dict[str, Any]], result: dict[str, Any]) -> None:
    """Accept legacy results without claims; require exact Step05 binding when claims exist."""
    if "quantitative_claims" not in result:
        return
    claims = result.get("quantitative_claims")
    if not isinstance(claims, list):
        raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", f"{result.get('check_id')} quantitative_claims must be an array.")
    selectors: list[dict[str, Any]] = []
    for claim in claims:
        if not isinstance(claim, dict):
            raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", "resolved quantitative claim must be an object.")
        selectors.append({
            "request_id": claim.get("request_id"),
            "feature_id": claim.get("physical_diagnostic"),
            "forecast_hour": claim.get("forecast_hour"),
            "region_id": claim.get("region_id"),
            "statistic": claim.get("statistic"),
        })
    if claims != resolve_quantitative_claims(check, entries, selectors):
        raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", f"{result.get('check_id')} resolved quantitative claim differs from Step05 JSON.")

# Original func/step07/checks.py:2540
def resolve_quantitative_relations(
    check: dict[str, Any],
    entries: list[dict[str, Any]],
    relations: Any,
    quantitative_claim_selectors: list[dict[str, Any]],
    *,
    pass_status: str = "passed",
    allow_mismatch: bool = False,
    autocorrect_relation_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], str, bool, list[dict[str, Any]]]:
    """Resolve AI-authored comparisons against Step05 after the independent VLM pass.

    The model chooses what to compare. Python resolves those exact selectors and
    deterministically computes the arithmetic relation. A narrow class of
    same-region, different-hour pairwise field mismatches can be normalized when
    the surrounding Korean narrative does not contradict the computed relation.
    Meteorological interpretation remains model-authored.
    """
    if not isinstance(relations, list):
        raise Pass2PipelineError("QUANTITATIVE_RELATION_INVALID", f"{check.get('check_id')} quantitative_relations must be an array.")
    claim_keys = relation_selector_keys(quantitative_claim_selectors)
    seen_relation_ids: set[str] = set()
    resolved_relations: list[dict[str, Any]] = []
    mismatches: list[str] = []
    corrections: list[dict[str, Any]] = []
    autocorrect_relation_ids = autocorrect_relation_ids or set()
    for relation in relations:
        if not isinstance(relation, dict):
            raise Pass2PipelineError("QUANTITATIVE_RELATION_INVALID", "quantitative relation must be an object.")
        relation_id = str(relation.get("relation_id") or "").strip()
        relation_type = str(relation.get("relation_type") or "")
        claimed_relation = str(relation.get("claimed_relation") or "")
        if not relation_id or relation_id in seen_relation_ids:
            raise Pass2PipelineError("QUANTITATIVE_RELATION_INVALID", f"duplicate or empty relation_id={relation_id!r}")
        seen_relation_ids.add(relation_id)
        if relation_type == "pairwise_comparison" and claimed_relation not in PAIRWISE_RELATIONS:
            raise Pass2PipelineError("QUANTITATIVE_RELATION_INVALID", f"{relation_id} pairwise comparison has invalid claimed_relation={claimed_relation!r}")
        if relation_type == "temporal_trend" and claimed_relation not in TREND_RELATIONS:
            raise Pass2PipelineError("QUANTITATIVE_RELATION_INVALID", f"{relation_id} temporal trend has invalid claimed_relation={claimed_relation!r}")
        if relation_type == "extremum" and claimed_relation not in EXTREMUM_RELATIONS:
            raise Pass2PipelineError("QUANTITATIVE_RELATION_INVALID", f"{relation_id} extremum has invalid claimed_relation={claimed_relation!r}")
        selectors = relation.get("selectors")
        relation_keys = relation_selector_keys(selectors)
        if not relation_keys or not relation_keys.issubset(claim_keys):
            raise Pass2PipelineError(
                "QUANTITATIVE_RELATION_INVALID",
                f"{relation_id} selectors must also be declared in quantitative_claims.",
            )
        points = resolve_quantitative_claims(check, entries, selectors)
        try:
            computed = compute_quantitative_relation(relation, points)
        except QuantitativeConsistencyError as exc:
            raise Pass2PipelineError("QUANTITATIVE_RELATION_INVALID", f"{relation_id}: {exc}") from exc
        consistent = bool(computed["consistent"])
        autocorrected = (
            not consistent
            and relation_id in autocorrect_relation_ids
            and not allow_mismatch
        )
        relation_status = "corrected" if autocorrected else pass_status if consistent else "failed"
        resolved_relations.append({
            "relation_id": relation_id,
            "relation_type": relation_type,
            "points": points,
            # Preserve the model-authored claim for auditability. Downstream
            # synthesis uses computed/display relations only when status is
            # passed/corrected.
            "claimed_relation": claimed_relation,
            "computed_relation": computed["computed_relation"],
            "claimed_forecast_hour": relation.get("claimed_forecast_hour"),
            "computed_forecast_hour": computed["computed_forecast_hour"],
            "consistency_status": relation_status,
        })
        if autocorrected:
            corrections.append({
                "relation_id": relation_id,
                "claimed_relation": claimed_relation,
                "computed_relation": computed["computed_relation"],
                "claimed_forecast_hour": relation.get("claimed_forecast_hour"),
                "computed_forecast_hour": computed["computed_forecast_hour"],
            })
        elif not consistent:
            values = ", ".join(
                f"F{int(point['forecast_hour']):03d}={point['resolved_value']} {point['units']}"
                for point in points
            )
            mismatches.append(
                f"{relation_id} claimed={claimed_relation} computed={computed['computed_relation']} values=[{values}]"
            )
    if mismatches and not allow_mismatch:
        raise Pass2PipelineError("QUANTITATIVE_RELATION_MISMATCH", "; ".join(mismatches[:8]))
    status = (
        "not_applicable" if not resolved_relations
        else "failed" if mismatches
        else "corrected" if corrections
        else pass_status
    )
    return resolved_relations, status, not mismatches, corrections

# Original func/step07/checks.py:2937
def _majority_count_text_failure_details(
    result: dict[str, Any],
    aggregation: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Reject explicit LLM aggregate counts that disagree with Python values."""
    if not isinstance(aggregation, dict):
        return []
    expected_total = aggregation.get("total_regions")
    expected_not = aggregation.get("not_strengthening_count")
    if not isinstance(expected_total, int) or not isinstance(expected_not, int):
        return []
    statements: list[str] = []
    for field_name in ("findings", "contradictory_findings"):
        for item in result.get(field_name, []) if isinstance(result.get(field_name), list) else []:
            if isinstance(item, dict) and isinstance(item.get("statement"), str):
                statements.append(item["statement"])
    pattern = re.compile(r"(\d+)\s*개(?:의)?\s*(?:운영\s*)?권역\s*중\s*(\d+)\s*개")
    for statement in statements:
        if "강화되지" not in statement:
            continue
        match = pattern.search(statement)
        if not match:
            continue
        stated_total, stated_not = (int(match.group(1)), int(match.group(2)))
        if stated_total != expected_total or stated_not != expected_not:
            return [{
                "code": "QUANTITATIVE_EXPECTED_STATE_MISMATCH",
                "detail": (
                    "925 hPa 수렴 majority 서술의 권역 집계가 Python 정량 재계산과 일치하지 않습니다. "
                    f"서술={stated_not}/{stated_total}, 재계산={expected_not}/{expected_total}. "
                    "정량 집계 숫자와 예외 권역을 재계산 결과에 맞춰 전체 판독을 다시 생성하십시오."
                ),
                "majority_total_regions": expected_total,
                "not_strengthening_count": expected_not,
                "strengthening_count": int(aggregation.get("strengthening_count") or 0),
                "strengthening_regions": list(aggregation.get("strengthening_regions") or []),
                "not_strengthening_regions": list(aggregation.get("not_strengthening_regions") or []),
            }]
    return []

# Original func/step07/checks.py:2978
def _relation_matches_specific_quantitative_scope(
    relation: dict[str, Any], scope: dict[str, Any], expected_state: str,
) -> bool:
    points = relation.get("points")
    if not isinstance(points, list) or len(points) < 2:
        return False
    target_feature = scope["feature_id"]
    target_region = scope["region_ids"][0]
    target_hours = list(scope["forecast_hours"])
    target_statistic = scope["statistic"]
    if not all(
        isinstance(point, dict)
        and point.get("physical_diagnostic") == target_feature
        and point.get("region_id") == target_region
        and point.get("statistic") == target_statistic
        and point.get("forecast_hour") in target_hours
        for point in points
    ):
        return False
    point_hours = [int(point["forecast_hour"]) for point in points]
    relation_type = str(relation.get("relation_type") or "")
    if expected_state in {"greater_than", "less_than"}:
        return relation_type == "pairwise_comparison" and point_hours == list(scope["comparison_forecast_hours"])
    if expected_state in {"increase", "decrease"}:
        if relation_type == "pairwise_comparison":
            return len(target_hours) == 2 and point_hours == target_hours
        return relation_type == "temporal_trend" and point_hours == target_hours
    if expected_state in {"strengthen", "weaken"}:
        if len(target_hours) == 2 and relation_type == "pairwise_comparison":
            return point_hours == target_hours
        return relation_type == "temporal_trend" and point_hours == target_hours
    return False

# Original func/step07/checks.py:3013
def _quantitative_expected_state_outcome(claim: dict[str, Any], relation: dict[str, Any]) -> str:
    """Return support/opposite/other/unverified without composing prose."""
    state = str(claim.get("expected_state") or "")
    computed = str(relation.get("computed_relation") or "")
    points = relation.get("points")
    if state == "greater_than":
        return "support" if computed == "first_greater_than_second" else "opposite" if computed == "first_less_than_second" else "other"
    if state == "less_than":
        return "support" if computed == "first_less_than_second" else "opposite" if computed == "first_greater_than_second" else "other"
    if state in {"increase", "decrease"}:
        support_relations = {"increasing", "first_less_than_second"} if state == "increase" else {"decreasing", "first_greater_than_second"}
        opposite_relations = {"decreasing", "first_greater_than_second"} if state == "increase" else {"increasing", "first_less_than_second"}
        return "support" if computed in support_relations else "opposite" if computed in opposite_relations else "other"
    if state in {"strengthen", "weaken"} and isinstance(points, list) and points:
        valid_points = [
            point for point in points
            if isinstance(point, dict)
            and isinstance(point.get("forecast_hour"), int) and not isinstance(point.get("forecast_hour"), bool)
            and isinstance(point.get("resolved_value"), Real) and not isinstance(point.get("resolved_value"), bool)
        ]
        if len(valid_points) < 2:
            return "unverified"
        feature_id = str(valid_points[0].get("physical_diagnostic") or "")
        if feature_id != "convergence925_1e5_s":
            # Unsupported strengthen/weaken features remain model-authored rather
            # than being deterministically rejected as an artificial 'other'.
            return "unverified"
        ordered = sorted(valid_points, key=lambda point: int(point["forecast_hour"]))
        transition = compute_physical_transition(
            feature_id, [float(point["resolved_value"]) for point in ordered]
        )
        strengthen_support = {"convergence_strengthening", "divergence_to_convergence", "neutral_to_convergence"}
        strengthen_opposite = {"convergence_weakening", "convergence_to_divergence", "convergence_to_neutral", "divergence_strengthening"}
        if state == "strengthen":
            return "support" if transition in strengthen_support else "opposite" if transition in strengthen_opposite else "other"
        weaken_support = {"convergence_weakening", "convergence_to_divergence", "convergence_to_neutral"}
        weaken_opposite = {"convergence_strengthening", "divergence_to_convergence", "neutral_to_convergence"}
        return "support" if transition in weaken_support else "opposite" if transition in weaken_opposite else "other"
    return "unverified"

# Original func/step07/checks.py:3055
def _quantitative_contract_shape_failure_details(
    check_specification: dict[str, Any],
) -> list[dict[str, Any]]:
    """Reject malformed v0.3 enforced quantitative shapes instead of silently skipping them."""
    contract = check_specification.get("atomic_claim_contract")
    if not isinstance(contract, dict):
        return []
    claims = contract.get("canonical_claims")
    version = contract.get("quantitative_enforcement")
    if version not in SUPPORTED_QUANTITATIVE_ENFORCEMENT_VERSIONS:
        # Non-quantitative legacy contracts retain their existing behavior.
        if isinstance(claims, list) and any(
            isinstance(item, dict) and item.get("quantitative_scope") is not None
            for item in claims
        ):
            return [{
                "code": "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
                "detail": "Step04 정량 계약 버전을 Step07에서 지원하지 않습니다.",
                "quantitative_enforcement": str(version),
            }]
        return []
    if not isinstance(claims, list):
        return [{
            "code": "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
            "detail": "Step04 정량 계약 canonical_claims 배열이 없습니다.",
        }]
    quantitative_claims = [
        claim for claim in claims
        if isinstance(claim, dict) and isinstance(claim.get("quantitative_scope"), dict)
    ]
    if quantitative_claims and (len(claims) != 1 or len(quantitative_claims) != 1):
        return [{
            "code": "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
            "detail": "enforced quantitative_scope를 가진 Check는 정확히 하나의 atomic claim으로 구성되어야 합니다.",
        }]
    if len(claims) != 1 or not isinstance(claims[0], dict):
        return []
    claim = claims[0]
    quantifier = str(claim.get("scope_quantifier") or "")
    state = str(claim.get("expected_state") or "")
    if quantifier == "majority" and state == "not_strengthen" and any(
        opposite_majority_not_strengthen_wording(text) for text in (
            claim.get("claim_statement"), check_specification.get("expected_signal")
        )
    ):
        return [{
            "code": "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
            "detail": (
                "Step04 natural-language majority strengthening proposition contradicts "
                "the not_strengthen structured contract. This is a plan defect, "
                "not a meteorological opposite signal."
            ),
        }]
    if quantifier == "specific" and state in {"greater_than", "less_than", "increase", "decrease", "strengthen", "weaken"}:
        for proposition in dict.fromkeys((
            str(claim.get("claim_statement") or ""),
            str(check_specification.get("expected_signal") or ""),
        )):
            fixed_cutoff = absolute_threshold_in_hour_comparison(proposition)
            if fixed_cutoff is not None:
                return [{
                    "code": "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
                    "detail": (
                        "Step04 absolute numeric cutoff proposition cannot be evaluated "
                        "with a relative forecast-hour comparison contract. This is a plan defect, "
                        "not a meteorological opposite signal."
                    ),
                    "fixed_cutoff": fixed_cutoff,
                }]
    if quantifier == "specific" and state in {"greater_than", "less_than"}:
        scope = claim.get("quantitative_scope")
        if isinstance(scope, dict) and isinstance(scope.get("comparison_forecast_hours"), list):
            for proposition in dict.fromkeys((
                str(claim.get("claim_statement") or ""),
                str(check_specification.get("expected_signal") or ""),
            )):
                expected = inconsistent_pairwise_hour_order(
                    proposition, scope["comparison_forecast_hours"]
                )
                if expected is not None:
                    return [{
                        "code": "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
                        "detail": (
                            "Step04 natural-language comparator order disagrees with "
                            "comparison_forecast_hours. This is a Step04 contract defect, "
                            "not an opposite meteorological observation."
                        ),
                        "required_comparison_forecast_hours": list(expected),
                        "actual_comparison_forecast_hours": scope["comparison_forecast_hours"],
                    }]
    if quantifier == "specific" and state in {"greater_than", "less_than", "increase", "decrease", "strengthen", "weaken"}:
        if _single_specific_quantitative_atomic_claim(check_specification) is None:
            return [{
                "code": "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
                "detail": "Step04 specific 정량계약의 feature·단일 권역·시간범위·비교순서·통계 구조가 v2 계약과 맞지 않습니다.",
            }]
    if quantifier == "majority" and state == "not_strengthen":
        if _single_majority_convergence_not_strengthen_claim(check_specification) is None:
            return [{
                "code": "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
                "detail": "Step04 convergence majority not_strengthen 정량계약 구조가 지원되는 계약과 맞지 않습니다.",
            }]
    return []

# Original func/step07/checks.py:3159
def quantitative_atomic_claim_failure_details(
    check_specification: dict[str, Any],
    result: dict[str, Any],
    *,
    check: dict[str, Any] | None = None,
    entries: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Validate the explicitly supported structured quantitative atomic claims."""
    shape_failures = _quantitative_contract_shape_failure_details(check_specification)
    if shape_failures:
        return shape_failures
    claim = _single_specific_quantitative_atomic_claim(check_specification)
    if claim is not None:
        scope = claim["quantitative_scope"]
        expected_state = str(claim.get("expected_state") or "")
        target_feature = scope["feature_id"]
        target_region = scope["region_ids"][0]
        target_hours = list(scope["forecast_hours"])
        target_statistic = scope["statistic"]
        resolved_claims = result.get("quantitative_claims")
        if isinstance(resolved_claims, list):
            out_of_scope = [
                point for point in resolved_claims
                if isinstance(point, dict) and (
                    point.get("physical_diagnostic") != target_feature
                    or point.get("region_id") != target_region
                    or point.get("forecast_hour") not in target_hours
                    or point.get("statistic") != target_statistic
                )
            ]
            if out_of_scope:
                return [{
                    "code": "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
                    "detail": "특정 정량 명제의 판독에 선언된 feature·지역·시각·통계 밖의 정량값이 사용되었습니다.",
                    "required_feature_id": target_feature,
                    "required_region_id": target_region,
                    "required_statistic": target_statistic,
                    "required_forecast_hours": target_hours,
                    "comparison_forecast_hours": scope.get("comparison_forecast_hours"),
                }]
        relations = result.get("quantitative_relations")
        matching = [
            relation for relation in relations
            if isinstance(relation, dict)
            and _relation_matches_specific_quantitative_scope(relation, scope, expected_state)
        ] if isinstance(relations, list) else []
        if not matching:
            return [{
                "code": "QUANTITATIVE_RELATION_REQUIRED",
                "detail": "특정 정량 명제의 feature·지역·시각·통계와 비교방향을 정확히 포괄하는 관계가 없습니다.",
                "missing_relation_type": "atomic_quantitative_scope_v2",
                "required_feature_id": target_feature,
                "required_region_id": target_region,
                "required_statistic": target_statistic,
                "required_forecast_hours": target_hours,
                "comparison_forecast_hours": scope.get("comparison_forecast_hours"),
            }]
        relation = matching[0]
        outcome = _quantitative_expected_state_outcome(claim, relation)
        assessment = str(result.get("assessment") or "")
        if outcome == "opposite" and assessment != "opposite_observed":
            return [{
                "code": "QUANTITATIVE_EXPECTED_STATE_MISMATCH",
                "detail": "검증된 정량관계가 특정 명제의 기대방향과 반대이지만 판정이 반대 신호로 작성되지 않았습니다.",
                "required_feature_id": target_feature,
                "required_region_id": target_region,
                "required_statistic": target_statistic,
                "required_forecast_hours": target_hours,
            }]
        if outcome == "support" and assessment in {"not_observed", "opposite_observed"}:
            return [{
                "code": "QUANTITATIVE_EXPECTED_STATE_MISMATCH",
                "detail": "검증된 정량관계가 특정 명제의 기대방향을 지지하지만 판정이 이에 반하도록 작성되었습니다.",
                "required_feature_id": target_feature,
                "required_region_id": target_region,
                "required_statistic": target_statistic,
                "required_forecast_hours": target_hours,
            }]
        if outcome == "other" and assessment in {"observed", "partially_observed"}:
            return [{
                "code": "QUANTITATIVE_EXPECTED_STATE_MISMATCH",
                "detail": "검증된 정량관계만으로 특정 명제의 기대방향이 성립하지 않는데 확인 또는 부분 확인으로 판정되었습니다.",
                "required_feature_id": target_feature,
                "required_region_id": target_region,
                "required_statistic": target_statistic,
                "required_forecast_hours": target_hours,
            }]
        # outcome=unverified deliberately leaves unsupported strengthen/weaken
        # features to the model rather than creating a false negative.
        return []

    majority_claim = _single_majority_convergence_not_strengthen_claim(check_specification)
    if majority_claim is None or check is None or entries is None:
        return []
    scope = majority_claim["quantitative_scope"]
    feature_id = scope["feature_id"]
    hours = list(scope["forecast_hours"])
    statistic = scope["statistic"]
    namespace_rows = _allowed_quantitative_selector_rows(check, entries)
    region_hours: dict[str, set[int]] = {}
    for row in namespace_rows:
        if row.get("feature_id") != feature_id or statistic not in row.get("statistics", []):
            continue
        hour = row.get("forecast_hour")
        region_id = row.get("region_id")
        if hour in hours and isinstance(region_id, str) and region_id:
            region_hours.setdefault(region_id, set()).add(int(hour))
    expected_regions = sorted(
        region_id for region_id, available_hours in region_hours.items()
        if set(hours).issubset(available_hours)
    )
    if not expected_regions:
        return [{
            "code": "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
            "detail": "candidate_overlap majority 정량계약을 평가할 공통 운영권역이 없습니다.",
            "required_feature_id": feature_id,
            "required_forecast_hours": hours,
            "required_statistic": statistic,
        }]

    claims = result.get("quantitative_claims")
    target_points = [
        point for point in claims or []
        if isinstance(point, dict)
        and point.get("physical_diagnostic") == feature_id
        and point.get("forecast_hour") in hours
        and point.get("statistic") == statistic
        and point.get("region_id") in expected_regions
    ] if isinstance(claims, list) else []
    values_by_key: dict[tuple[str, int], list[float]] = {}
    names_by_region: dict[str, str] = {}
    for point in target_points:
        value = point.get("resolved_value")
        region_id = str(point.get("region_id") or "")
        hour = point.get("forecast_hour")
        if isinstance(value, Real) and not isinstance(value, bool) and isinstance(hour, int) and not isinstance(hour, bool):
            values_by_key.setdefault((region_id, hour), []).append(float(value))
            names_by_region[region_id] = str(point.get("region_name") or region_id)
    missing = [
        (region_id, hour) for region_id in expected_regions for hour in hours
        if (region_id, hour) not in values_by_key
    ]
    ambiguous = [key for key, values in values_by_key.items() if len({round(value, 12) for value in values}) > 1]
    if missing or ambiguous:
        return [{
            "code": "QUANTITATIVE_RELATION_REQUIRED",
            "detail": "candidate_overlap majority 판정을 위해 두 비교시각의 모든 공통 운영권역 정량값이 필요합니다.",
            "missing_relation_type": "candidate_overlap_majority_coverage",
            "required_feature_id": feature_id,
            "required_forecast_hours": hours,
            "required_statistic": statistic,
            "missing_region_hours": [f"{region_id}:F{hour:03d}" for region_id, hour in missing[:24]],
        }]

    strengthening_regions: list[str] = []
    not_strengthening_regions: list[str] = []
    transitions: dict[str, str | None] = {}
    for region_id in expected_regions:
        values = [values_by_key[(region_id, hour)][0] for hour in hours]
        transition = compute_physical_transition(feature_id, values)
        transitions[region_id] = transition
        is_strengthening = transition in {
            "convergence_strengthening", "divergence_to_convergence", "neutral_to_convergence"
        }
        target = strengthening_regions if is_strengthening else not_strengthening_regions
        target.append(region_id)
    total = len(expected_regions)
    support = len(not_strengthening_regions) > total / 2
    opposite = len(strengthening_regions) > total / 2
    assessment = str(result.get("assessment") or "")
    detail_common = {
        "required_feature_id": feature_id,
        "required_forecast_hours": hours,
        "required_statistic": statistic,
        "majority_total_regions": total,
        "not_strengthening_count": len(not_strengthening_regions),
        "strengthening_count": len(strengthening_regions),
        "strengthening_regions": [names_by_region.get(region_id, region_id) for region_id in strengthening_regions],
        "not_strengthening_regions": [names_by_region.get(region_id, region_id) for region_id in not_strengthening_regions],
        "physical_transitions": transitions,
    }
    aggregation = _majority_convergence_aggregation_from_result(check_specification, result)
    text_failures = _majority_count_text_failure_details(result, aggregation)
    if text_failures:
        return text_failures
    if support and assessment != "observed":
        return [{
            "code": "QUANTITATIVE_EXPECTED_STATE_MISMATCH",
            "detail": (
                f"925 hPa 수렴 not_strengthen majority가 {len(not_strengthening_regions)}/{total}개 권역에서 성립합니다. "
                f"수렴 강화 권역은 {', '.join(detail_common['strengthening_regions']) or '없음'}입니다. "
                "majority 명제는 observed로 판정하고 예외 권역은 상반 근거에 기록하십시오."
            ),
            **detail_common,
        }]
    if opposite and assessment != "opposite_observed":
        return [{
            "code": "QUANTITATIVE_EXPECTED_STATE_MISMATCH",
            "detail": (
                f"925 hPa 수렴 not_strengthen majority의 반대가 {len(strengthening_regions)}/{total}개 권역에서 성립합니다. "
                "opposite_observed로 판정하십시오."
            ),
            **detail_common,
        }]
    if not support and not opposite and assessment not in {"partially_observed", "uncertain"}:
        return [{
            "code": "QUANTITATIVE_EXPECTED_STATE_MISMATCH",
            "detail": "925 hPa 수렴 not_strengthen majority가 정확한 다수를 이루지 못했습니다. 부분 확인 또는 불확실로 제한하십시오.",
            **detail_common,
        }]
    return []

# Original func/step07/checks.py:3373
def quantitative_consistency_failure_details(
    check_specification: dict[str, Any],
    result: dict[str, Any],
    *,
    check: dict[str, Any] | None = None,
    entries: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return reproducible detail for each deterministic quantitative rejection."""
    details: list[dict[str, Any]] = []
    details.extend(quantitative_atomic_claim_failure_details(
        check_specification, result, check=check, entries=entries
    ))
    relations = result.get("quantitative_relations")
    if isinstance(relations, list) and any(
        isinstance(relation, dict) and relation.get("consistency_status") == "failed"
        for relation in relations
    ):
        details.append({
            "code": "QUANTITATIVE_RELATION_MISMATCH",
            "detail": "저장된 정량 비교 관계 중 Python 재계산 결과와 일치하지 않는 관계가 있습니다.",
        })
    for metric, region_id, statistic, missing_hours in _missing_temporal_quantitative_relation_series(result):
        details.append({
            "code": "QUANTITATIVE_RELATION_REQUIRED",
            "detail": "선택한 복수 시각 정량 계열의 모든 시각이 검증된 비교 관계에 포함되지 않았습니다.",
            "missing_relation_type": "temporal_series_coverage",
            "required_metric": metric,
            "required_region_id": region_id,
            "required_statistic": statistic,
            "required_forecast_hours": list(missing_hours),
        })
    if requires_temporal_precipitation_increment_claim(check_specification):
        if not isinstance(relations, list) or not relations:
            details.append({
                "code": "QUANTITATIVE_RELATION_REQUIRED",
                "detail": "시간 변화 강수 판독에 필요한 6시간 강수량 비교 관계가 선언되지 않았습니다.",
                "missing_relation_type": "temporal_precipitation_relation",
                "required_metric": "tp_6h_increment_mm",
            })
        elif not any(
            isinstance(relation, dict)
            and isinstance(relation.get("points"), list)
            and relation["points"]
            and all(
                isinstance(point, dict)
                and point.get("physical_diagnostic") == "tp_6h_increment_mm"
                for point in relation["points"]
            )
            for relation in relations
        ):
            details.append({
                "code": "QUANTITATIVE_FEATURE_USE_INVALID",
                "detail": "시간 변화 강수 판독에 필요한 6시간 강수량 비교 관계가 없습니다.",
                "missing_relation_type": "required_metric_relation",
                "required_metric": "tp_6h_increment_mm",
            })
    return details

# Original func/step07/checks.py:3433
def quantitative_consistency_failure_reasons(
    check_specification: dict[str, Any],
    result: dict[str, Any],
    *,
    check: dict[str, Any] | None = None,
    entries: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Return compatibility reason codes derived from structured failure detail."""
    return list(dict.fromkeys(
        str(detail["code"])
        for detail in quantitative_consistency_failure_details(
            check_specification, result, check=check, entries=entries
        )
    ))

# Original func/step07/checks.py:3462
def validate_step07_result_invariants(result: dict[str, Any]) -> None:
    """Validate aggregate consistency and exclusion provenance when the fields exist.

    The optional-field handling keeps pre-extension Step07 checkpoints readable.
    Newly generated results always include all provenance fields and therefore receive the
    full invariant checks here.
    """
    status = result.get("quantitative_consistency_status")
    relations = result.get("quantitative_relations")
    failed_relation = isinstance(relations, list) and any(
        isinstance(relation, dict) and relation.get("consistency_status") == "failed"
        for relation in relations
    )
    raw_failure_reasons = result.get("quantitative_consistency_failure_reasons")
    if raw_failure_reasons is not None:
        if (
            not isinstance(raw_failure_reasons, list)
            or not all(reason in QUANTITATIVE_FAILURE_REASONS for reason in raw_failure_reasons)
            or len(set(raw_failure_reasons)) != len(raw_failure_reasons)
        ):
            raise Pass2PipelineError("QUANTITATIVE_CONSISTENCY_INVARIANT", "quantitative_consistency_failure_reasons is invalid.")
        if status == "failed" and not raw_failure_reasons and not failed_relation:
            raise Pass2PipelineError(
                "QUANTITATIVE_CONSISTENCY_INVARIANT",
                "failed quantitative consistency has neither a failed relation nor a failure reason.",
            )
        if status != "failed" and raw_failure_reasons:
            raise Pass2PipelineError(
                "QUANTITATIVE_CONSISTENCY_INVARIANT",
                "non-failed quantitative consistency cannot retain failure reasons.",
            )
    elif status == "failed" and not failed_relation:
        # Older checkpoints may not include the new reason field.  Do not reject
        # them solely for that omission, but do not treat an absent field as proof.
        pass

    raw_failure_details = result.get("quantitative_consistency_failure_details")
    if raw_failure_details is not None:
        if not isinstance(raw_failure_details, list):
            raise Pass2PipelineError("QUANTITATIVE_CONSISTENCY_INVARIANT", "quantitative_consistency_failure_details is invalid.")
        detail_codes: set[str] = set()
        for detail in raw_failure_details:
            if (
                not isinstance(detail, dict)
                or detail.get("code") not in QUANTITATIVE_FAILURE_REASONS
                or not isinstance(detail.get("detail"), str)
                or not detail["detail"].strip()
            ):
                raise Pass2PipelineError("QUANTITATIVE_CONSISTENCY_INVARIANT", "quantitative_consistency_failure_details is invalid.")
            if "missing_relation_type" in detail and not isinstance(detail["missing_relation_type"], str):
                raise Pass2PipelineError("QUANTITATIVE_CONSISTENCY_INVARIANT", "failure detail missing_relation_type is invalid.")
            if "required_metric" in detail and not isinstance(detail["required_metric"], str):
                raise Pass2PipelineError("QUANTITATIVE_CONSISTENCY_INVARIANT", "failure detail required_metric is invalid.")
            if "required_region_id" in detail and not isinstance(detail["required_region_id"], str):
                raise Pass2PipelineError("QUANTITATIVE_CONSISTENCY_INVARIANT", "failure detail required_region_id is invalid.")
            if "required_statistic" in detail and not isinstance(detail["required_statistic"], str):
                raise Pass2PipelineError("QUANTITATIVE_CONSISTENCY_INVARIANT", "failure detail required_statistic is invalid.")
            if "required_forecast_hours" in detail and (
                not isinstance(detail["required_forecast_hours"], list)
                or not all(isinstance(hour, int) and not isinstance(hour, bool) and hour >= 0 for hour in detail["required_forecast_hours"])
            ):
                raise Pass2PipelineError("QUANTITATIVE_CONSISTENCY_INVARIANT", "failure detail required_forecast_hours is invalid.")
            if "required_feature_id" in detail and not isinstance(detail["required_feature_id"], str):
                raise Pass2PipelineError("QUANTITATIVE_CONSISTENCY_INVARIANT", "failure detail required_feature_id is invalid.")
            if "comparison_forecast_hours" in detail and detail["comparison_forecast_hours"] is not None and (
                not isinstance(detail["comparison_forecast_hours"], list)
                or not all(isinstance(hour, int) and not isinstance(hour, bool) and hour >= 0 for hour in detail["comparison_forecast_hours"])
            ):
                raise Pass2PipelineError("QUANTITATIVE_CONSISTENCY_INVARIANT", "failure detail comparison_forecast_hours is invalid.")
            if "missing_region_hours" in detail and (
                not isinstance(detail["missing_region_hours"], list)
                or not all(isinstance(item, str) and item for item in detail["missing_region_hours"])
            ):
                raise Pass2PipelineError("QUANTITATIVE_CONSISTENCY_INVARIANT", "failure detail missing_region_hours is invalid.")
            for count_key in ("majority_total_regions", "not_strengthening_count", "strengthening_count"):
                if count_key in detail and (
                    not isinstance(detail[count_key], int)
                    or isinstance(detail[count_key], bool)
                    or detail[count_key] < 0
                ):
                    raise Pass2PipelineError("QUANTITATIVE_CONSISTENCY_INVARIANT", f"failure detail {count_key} is invalid.")
            for regions_key in ("strengthening_regions", "not_strengthening_regions"):
                if regions_key in detail and (
                    not isinstance(detail[regions_key], list)
                    or not all(isinstance(item, str) and item for item in detail[regions_key])
                ):
                    raise Pass2PipelineError("QUANTITATIVE_CONSISTENCY_INVARIANT", f"failure detail {regions_key} is invalid.")
            if "physical_transitions" in detail and (
                not isinstance(detail["physical_transitions"], dict)
                or not all(
                    isinstance(region_id, str) and region_id
                    and (transition is None or isinstance(transition, str))
                    for region_id, transition in detail["physical_transitions"].items()
                )
            ):
                raise Pass2PipelineError("QUANTITATIVE_CONSISTENCY_INVARIANT", "failure detail physical_transitions is invalid.")
            detail_codes.add(str(detail["code"]))
        reason_codes = set(raw_failure_reasons or []) if isinstance(raw_failure_reasons, list) else set()
        if detail_codes != reason_codes:
            raise Pass2PipelineError("QUANTITATIVE_CONSISTENCY_INVARIANT", "failure detail codes must match quantitative consistency failure reasons.")

    raw_exclusion_reasons = result.get("excluded_from_synthesis_reasons")
    if raw_exclusion_reasons is not None:
        if (
            not isinstance(raw_exclusion_reasons, list)
            or not all(reason in EXCLUDED_FROM_SYNTHESIS_REASONS for reason in raw_exclusion_reasons)
            or len(set(raw_exclusion_reasons)) != len(raw_exclusion_reasons)
        ):
            raise Pass2PipelineError("SYNTHESIS_EXCLUSION_INVARIANT", "excluded_from_synthesis_reasons is invalid.")
        if result.get("usable_for_synthesis") is False and not raw_exclusion_reasons:
            raise Pass2PipelineError(
                "SYNTHESIS_EXCLUSION_INVARIANT",
                "unusable Step07 result must declare an exclusion reason.",
            )
        if result.get("usable_for_synthesis") is not False and raw_exclusion_reasons:
            raise Pass2PipelineError(
                "SYNTHESIS_EXCLUSION_INVARIANT",
                "usable Step07 result cannot declare synthesis exclusion reasons.",
            )

# Original func/step07/checks.py:3583
def _resolved_relation_point_key(point: dict[str, Any]) -> tuple[str, str, str, int] | None:
    feature_id = point.get("physical_diagnostic")
    region_id = point.get("region_id")
    statistic = point.get("statistic")
    forecast_hour = point.get("forecast_hour")
    if (
        isinstance(feature_id, str) and feature_id
        and isinstance(region_id, str) and region_id
        and isinstance(statistic, str) and statistic
        and isinstance(forecast_hour, int) and not isinstance(forecast_hour, bool)
    ):
        return feature_id, region_id, statistic, forecast_hour
    return None

# Original func/step07/checks.py:3598
def _missing_temporal_quantitative_relation_series(
    result: dict[str, Any],
    *,
    feature_filter: str | None = None,
) -> list[tuple[str, str, str, tuple[int, ...]]]:
    """Return selected temporal series not fully covered by structured relations.

    The model still decides which quantitative cells matter. Once it selects two
    or more hours from the same feature/region/statistic series, every selected
    hour must be exposed through at least one structured relation. This avoids
    parsing free prose while making temporal increase/decrease/comparison claims
    deterministically auditable for precipitation, moisture, convergence, omega,
    and other Step05 quantitative features.
    """
    claims = result.get("quantitative_claims")
    relations = result.get("quantitative_relations")
    if not isinstance(claims, list) or not isinstance(relations, list):
        return []

    selected_hours: dict[tuple[str, str, str], set[int]] = {}
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        key = _resolved_relation_point_key(claim)
        if key is None:
            continue
        feature_id, region_id, statistic, forecast_hour = key
        if feature_filter is not None and feature_id != feature_filter:
            continue
        selected_hours.setdefault((feature_id, region_id, statistic), set()).add(forecast_hour)

    required = {identity: hours for identity, hours in selected_hours.items() if len(hours) >= 2}
    if not required:
        return []

    covered_hours: dict[tuple[str, str, str], set[int]] = {identity: set() for identity in required}
    for relation in relations:
        points = relation.get("points") if isinstance(relation, dict) else None
        if not isinstance(points, list) or len(points) < 2:
            continue
        point_keys = [_resolved_relation_point_key(point) for point in points if isinstance(point, dict)]
        if len(point_keys) != len(points) or any(key is None for key in point_keys):
            continue
        identities = {(key[0], key[1], key[2]) for key in point_keys if key is not None}
        if len(identities) != 1:
            continue
        identity = next(iter(identities))
        if identity not in required:
            continue
        covered_hours[identity].update(key[3] for key in point_keys if key is not None)

    missing: list[tuple[str, str, str, tuple[int, ...]]] = []
    for identity, hours in required.items():
        uncovered = sorted(hours - covered_hours.get(identity, set()))
        if uncovered:
            missing.append((*identity, tuple(uncovered)))
    return sorted(missing)

# Original func/step07/checks.py:3657
def _missing_incremental_precipitation_relation_series(
    result: dict[str, Any],
) -> list[tuple[str, str, str, tuple[int, ...]]]:
    return _missing_temporal_quantitative_relation_series(
        result,
        feature_filter="tp_6h_increment_mm",
    )

# Original func/step07/checks.py:3666
def validate_temporal_quantitative_relation_coverage(result: dict[str, Any]) -> None:
    """Require structured relations for every multi-hour quantitative series.

    Python does not infer a comparison from narrative text. The LLM first selects
    its own quantitative cells; when two or more hours from the same series are
    selected, those selected hours must be represented in ``quantitative_relations``
    so their arithmetic can be verified after the independent VLM pass.
    """
    if result.get("assessment") == "insufficient_evidence":
        return
    missing = _missing_temporal_quantitative_relation_series(result)
    if missing:
        raise Pass2PipelineError(
            "QUANTITATIVE_RELATION_REQUIRED",
            f"{result.get('check_id')} selected multi-hour quantitative series without complete relation coverage: {missing}",
        )

# Original func/step07/checks.py:3684
def validate_incremental_precipitation_relation_coverage(result: dict[str, Any]) -> None:
    """Backward-compatible precipitation-specific relation-coverage guard.

    The general temporal coverage validator is stricter and is applied to every
    newly generated Step07 result. This helper remains available for focused
    precipitation regression checks.
    """
    if result.get("assessment") == "insufficient_evidence":
        return
    missing = _missing_incremental_precipitation_relation_series(result)
    if missing:
        raise Pass2PipelineError(
            "QUANTITATIVE_RELATION_REQUIRED",
            f"{result.get('check_id')} selected multiple tp_6h_increment_mm times without complete relation coverage: {missing}",
        )

# Original func/step07/checks.py:3701
def validate_quantitative_relation_use(
    check_specification: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """Require a relation when a temporal precipitation Check makes time comparisons."""
    if not requires_temporal_precipitation_increment_claim(check_specification):
        return
    if result.get("assessment") == "insufficient_evidence":
        return
    relations = result.get("quantitative_relations")
    if not isinstance(relations, list) or not relations:
        raise Pass2PipelineError(
            "QUANTITATIVE_RELATION_REQUIRED",
            f"{result.get('check_id')} temporal precipitation assessment must declare a quantitative relation.",
        )
    if not any(
        isinstance(relation, dict)
        and isinstance(relation.get("points"), list)
        and relation["points"]
        and all(
            isinstance(point, dict) and point.get("physical_diagnostic") == "tp_6h_increment_mm"
            for point in relation["points"]
        )
        for relation in relations
    ):
        raise Pass2PipelineError(
            "QUANTITATIVE_FEATURE_USE_INVALID",
            f"{result.get('check_id')} temporal precipitation assessment requires at least one tp_6h_increment_mm relation.",
        )

# Original func/step07/checks.py:3732
def validate_evidence_component_use(check: dict[str, Any], result: dict[str, Any]) -> None:
    """Prevent a complete cross-variable conclusion when declared evidence components are missing."""
    missing = check.get("missing_signal_components")
    if not isinstance(missing, list) or not missing:
        return
    if result.get("assessment") in {"observed", "not_observed", "opposite_observed"}:
        raise Pass2PipelineError(
            "EVIDENCE_COMPLETENESS_ASSESSMENT_INVALID",
            f"{result.get('check_id')} cannot make a complete assessment while required signal components are missing: {missing}",
        )

# Original func/step07/checks.py:3744
def validate_quantitative_feature_use(check_specification: dict[str, Any], result: dict[str, Any]) -> None:
    """Require incremental precipitation for temporal peak/intensity checks.

    Legacy Step07 outputs did not persist quantitative claims and remain readable;
    every newly generated Step07 response has the required claim array.
    """
    if not requires_temporal_precipitation_increment_claim(check_specification):
        return
    if result.get("assessment") == "insufficient_evidence" or "quantitative_claims" not in result:
        return
    claims = result.get("quantitative_claims")
    if not isinstance(claims, list) or not any(
        isinstance(claim, dict) and claim.get("physical_diagnostic") == "tp_6h_increment_mm"
        for claim in claims
    ):
        raise Pass2PipelineError(
            "QUANTITATIVE_FEATURE_USE_INVALID",
            f"{result.get('check_id')} temporal precipitation evidence requires a tp_6h_increment_mm resolved claim.",
        )

# Original func/step07/checks.py:3765
def _has_available_incremental_precipitation_evidence(check: dict[str, Any], entries: list[dict[str, Any]]) -> bool:
    available_request_ids = {str(value) for value in check.get("available_request_ids", [])}
    for entry in entries:
        request = entry.get("request")
        step05 = entry.get("step05")
        if not isinstance(request, dict) or not isinstance(step05, dict):
            continue
        if str(request.get("request_id")) not in available_request_ids or request.get("data_type") != "quantitative_excerpt":
            continue
        result = step05.get("result")
        features = result.get("features") if isinstance(result, dict) else None
        if step05.get("status") == "success" and isinstance(features, dict) and isinstance(features.get("tp_6h_increment_mm"), dict):
            return True
    return False

# Original func/step07/checks.py:3781
def validate_check_evidence_references(
    check: dict[str, Any], entries: list[dict[str, Any]], result: dict[str, Any], *,
    check_specification: dict[str, Any] | None = None,
) -> None:
    validate_step07_result_invariants(result)
    validate_scope_coverage_contract(result)
    allowed = _allowed_refs(check, entries)
    for finding_key in ("findings", "contradictory_findings"):
        findings = result.get(finding_key)
        if not isinstance(findings, list):
            raise Pass2PipelineError("CHECK_RESULT_INVALID", f"{result.get('check_id')} has invalid {finding_key}.")
        for finding in findings:
            refs = finding.get("evidence_refs") if isinstance(finding, dict) else None
            if not isinstance(refs, list) or not refs:
                raise Pass2PipelineError("EVIDENCE_REF_INVALID", f"{result.get('check_id')} finding has no evidence_refs.")
            for ref in refs:
                if not isinstance(ref, dict):
                    raise Pass2PipelineError("EVIDENCE_REF_INVALID", "evidence_ref must be an object.")
                key = (ref.get("request_id"), ref.get("evidence_type"), ref.get("physical_diagnostic"), ref.get("forecast_hour"))
                if key not in allowed:
                    raise Pass2PipelineError("EVIDENCE_REF_INVALID", f"{result.get('check_id')} references unavailable evidence: {key}")
    validate_resolved_quantitative_claims(check, entries, result)
    validate_evidence_component_use(check, result)
    if result.get("quantitative_consistency_status") != "failed":
        validate_temporal_quantitative_relation_coverage(result)
    if check_specification is not None and _has_available_incremental_precipitation_evidence(check, entries):
        if result.get("quantitative_consistency_status") != "failed":
            validate_incremental_precipitation_relation_coverage(result)
            validate_quantitative_feature_use(check_specification, result)
            validate_quantitative_relation_use(check_specification, result)

# Original func/step07/checks.py:4592
def _expected_kst_for_forecast_hour(init: str, forecast_hour: int) -> datetime:
    init_utc = datetime.strptime(str(init), "%Y%m%d%H").replace(tzinfo=timezone.utc)
    return (init_utc + timedelta(hours=int(forecast_hour))).astimezone(timezone(timedelta(hours=9)))

# Original func/step07/checks.py:4597
def _step07_forecast_time_narrative_errors(init: str, assessment: dict[str, Any]) -> list[str]:
    """Reject a KST timestamp that disagrees with an adjacent F-hour.

    The LLM may describe F096 correctly but append a UTC clock value labelled KST.
    User-facing absolute time is therefore checked deterministically against init+F.
    """
    errors: list[str] = []
    for text in _step07_narrative_texts(assessment):
        for pattern in (_FORECAST_KST_PREFIX_RE, _FORECAST_KST_SUFFIX_RE):
            for match in pattern.finditer(text):
                fh = int(match.group("fh"))
                observed = f"{match.group('date')} {match.group('hour')}:{match.group('minute')}"
                expected_dt = _expected_kst_for_forecast_hour(init, fh)
                expected = expected_dt.strftime("%Y-%m-%d %H:%M")
                if observed != expected:
                    errors.append(
                        f"F{fh:03d} KST mismatch observed={observed} KST expected={expected} KST"
                    )
    return errors

# Original func/step07/checks.py:4618
def _step07_narrative_texts(assessment: dict[str, Any]) -> list[str]:
    """Return user-facing Step07 prose fields for conservative relation checks."""
    texts: list[str] = []
    for key in ("findings", "contradictory_findings"):
        rows = assessment.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            statement = row.get("statement") if isinstance(row, dict) else None
            if isinstance(statement, str) and statement.strip():
                texts.append(statement.strip())
    uncertainties = assessment.get("uncertainties")
    if isinstance(uncertainties, list):
        texts.extend(value.strip() for value in uncertainties if isinstance(value, str) and value.strip())
    return texts

# Original func/step07/checks.py:4732
def _normalize_specific_quantitative_relation_order(
    assessment: dict[str, Any],
    check_specification: dict[str, Any],
) -> int:
    """Canonicalize selector order for one enforced two-time specific claim.

    The LLM may express the same pairwise comparison in reverse selector order
    (for example F090 then F066).  For temporal increase/decrease contracts the
    canonical order is chronological.  Reordering the selectors is semantics-
    preserving only when the claimed pairwise relation is inverted at the same
    time.  This is a deterministic formatting repair, not meteorological
    reinterpretation.
    """
    claim = _single_specific_quantitative_atomic_claim(check_specification)
    if claim is None:
        return 0
    scope = claim["quantitative_scope"]
    target_hours = (
        list(scope["comparison_forecast_hours"])
        if claim.get("expected_state") in {"greater_than", "less_than"}
        else list(scope["forecast_hours"])
    )
    if len(target_hours) != 2:
        return 0
    relations = assessment.get("quantitative_relations")
    if not isinstance(relations, list):
        return 0
    inverse = {
        "first_greater_than_second": "first_less_than_second",
        "first_less_than_second": "first_greater_than_second",
        "equal": "equal",
    }
    fixes = 0
    for relation in relations:
        if not isinstance(relation, dict) or relation.get("relation_type") != "pairwise_comparison":
            continue
        selectors = relation.get("selectors")
        if not isinstance(selectors, list) or len(selectors) != 2:
            continue
        if not all(isinstance(selector, dict) for selector in selectors):
            continue
        if not all(
            selector.get("feature_id") == scope["feature_id"]
            and selector.get("region_id") == scope["region_ids"][0]
            and selector.get("statistic") == scope["statistic"]
            for selector in selectors
        ):
            continue
        point_hours = [selector.get("forecast_hour") for selector in selectors]
        if point_hours == target_hours:
            continue
        if point_hours != list(reversed(target_hours)):
            continue
        claimed = str(relation.get("claimed_relation") or "")
        if claimed not in inverse:
            continue
        relation["selectors"] = [selectors[1], selectors[0]]
        relation["claimed_relation"] = inverse[claimed]
        fixes += 1
    return fixes

# Original func/step07/checks.py:4952
def _validate_system_anchor_region_narrative(
    group: dict[str, Any],
    check: dict[str, Any],
    specification: dict[str, Any],
    entries: list[dict[str, Any]],
    assessment: dict[str, Any],
) -> None:
    """Require standardized region names in public direct-precipitation facts.

    The deterministic Step05 scope metadata supplies the closed set of names
    that are eligible at the candidate peak hours.  This validator does not
    decide whether precipitation is meteorologically observed; it only prevents
    a positive VLM finding from falling back to opaque phrases such as
    '후보영역의 운영 예보권역'.
    """
    if not _is_system_direct_precipitation_anchor_check(specification):
        return
    if str(assessment.get("assessment") or "") not in {"observed", "partially_observed"}:
        return
    context = _system_direct_precipitation_anchor_context(group, check, specification, entries)
    if not isinstance(context, dict) or not context.get("threshold_met"):
        return
    names = {
        str(region.get("region_name") or "").strip()
        for regions in context.get("threshold_met_regions_by_forecast_hour", {}).values()
        if isinstance(regions, list)
        for region in regions
        if isinstance(region, dict) and str(region.get("region_name") or "").strip()
    }
    if not names:
        raise Pass2PipelineError(
            "REGION_NARRATIVE_INVALID",
            f"{check.get('check_id')} has positive direct precipitation but no deterministic operational-region names.",
        )
    findings = assessment.get("findings")
    if not isinstance(findings, list) or not findings:
        return
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue
        statement = str(finding.get("statement") or "").strip()
        if "강수" not in statement:
            continue
        if not any(name in statement for name in names):
            allowed = ", ".join(sorted(names))
            raise Pass2PipelineError(
                "REGION_NARRATIVE_INVALID",
                f"{check.get('check_id')} findings[{index}] must name at least one supported operational region ({allowed}) instead of a generic area phrase.",
            )

# Original func/step07/checks.py:5003
def _validate_localized_alignment_region_narrative(
    root: Path,
    group: dict[str, Any],
    check: dict[str, Any],
    specification: dict[str, Any],
    assessment: dict[str, Any],
) -> None:
    """Require localized alignment findings to name their operational region.

    A localized ``spatial_localization`` is already a structured, evidence-
    validated statement that the requested multivariable overlap can be tied
    to one or more operational forecast regions.  Public findings should not
    discard that information and fall back to opaque phrases such as
    '남부 내륙 일부'.
    """
    localization = assessment.get("spatial_localization")
    if not isinstance(localization, dict) or localization.get("status") != "localized":
        return
    naming = _candidate_operational_region_naming_context(root, group)
    name_by_id = {
        str(row.get("region_id") or ""): str(row.get("name_ko") or "").strip()
        for row in naming.get("regions", [])
        if isinstance(row, dict)
    }
    localized_names = {
        name_by_id.get(str(row.get("region_id") or ""), "")
        for row in localization.get("regions", [])
        if isinstance(row, dict)
    }
    localized_names.discard("")
    if not localized_names:
        raise Pass2PipelineError(
            "REGION_NARRATIVE_INVALID",
            f"{check.get('check_id')} localized alignment has no resolvable standardized region name.",
        )
    findings = assessment.get("findings")
    if not isinstance(findings, list):
        return
    relation_tokens = ("겹", "결합", "중첩", "함께 나타", "같은 위치")
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue
        statement = str(finding.get("statement") or "").strip()
        if not statement or not any(token in statement for token in relation_tokens):
            continue
        if not any(name in statement for name in localized_names):
            allowed = ", ".join(sorted(localized_names))
            raise Pass2PipelineError(
                "REGION_NARRATIVE_INVALID",
                f"{check.get('check_id')} findings[{index}] describes localized overlap but omits the standardized operational region name ({allowed}).",
            )

# Original func/step07/checks.py:5061
def _spatial_narrative_grounding_issues(
    root: Path,
    group: dict[str, Any],
    specification: dict[str, Any],
    assessment: dict[str, Any],
) -> list[dict[str, str]]:
    """Describe unsupported region-sector prose without storing model-authored text.

    This is the same fail-closed rule used by the narrative validator. Structured
    metadata (field path, exact operational region, declared/used sector) is
    safe to log and can guide one complete-Check regeneration. Do not repair
    or infer an unverified meteorological location here.
    """
    if not _spatial_localization_enabled(specification):
        return []
    localization = assessment.get("spatial_localization")
    if not isinstance(localization, dict):
        return []
    raw_status = localization.get("status")
    status = raw_status if isinstance(raw_status, str) and raw_status in {"localized", "indeterminate", "not_applicable"} else "invalid"
    naming = _candidate_operational_region_naming_context(root, group)
    name_by_id = {
        str(row.get("region_id") or ""): str(row.get("name_ko") or "").strip()
        for row in naming.get("regions", [])
        if isinstance(row, dict) and str(row.get("region_id") or "") and str(row.get("name_ko") or "").strip()
    }
    if not name_by_id:
        return []

    sector_by_region: dict[str, str] = {}
    if status == "localized":
        for row in localization.get("regions", []):
            if isinstance(row, dict) and isinstance(row.get("region_id"), str):
                raw_sector = row.get("relative_sector")
                sector_by_region[row["region_id"]] = raw_sector if isinstance(raw_sector, str) and raw_sector in SPATIAL_LOCALIZATION_SECTORS else "invalid"

    entries: list[tuple[str, str]] = []
    for field in ("findings", "contradictory_findings"):
        for index, item in enumerate(assessment.get(field, [])):
            if isinstance(item, dict) and isinstance(item.get("statement"), str):
                entries.append((f"{field}[{index}].statement", item["statement"]))
    for index, value in enumerate(assessment.get("uncertainties", [])):
        if isinstance(value, str):
            entries.append((f"uncertainties[{index}]", value))

    issues: list[dict[str, str]] = []
    for path, statement in entries:
        if not _SPATIAL_ALIGNMENT_RELATION_PATTERN.search(statement):
            continue
        for mention in operational_region_sector_mentions(statement, name_by_id):
            region_id = mention["region_id"]
            actual = mention["sector"]
            expected = sector_by_region.get(region_id)
            if status != "localized":
                problem = "localization_indeterminate"
            elif expected is None:
                problem = "region_not_localized"
            elif expected == "unspecified" or actual != expected:
                problem = "sector_mismatch"
            else:
                continue
            issues.append({
                "field_path": path,
                "problem": problem,
                "region_id": region_id,
                "region_name": mention["region_name"],
                "mentioned_sector": actual,
                "declared_sector": expected or "not_localized",
                "localization_status": status or "missing",
            })
            if len(issues) >= 24:
                return issues
    return issues

# Original func/step07/checks.py:5136
def _spatial_localization_retry_diagnostics(
    root: Path,
    group: dict[str, Any],
    specification: dict[str, Any],
    assessment: dict[str, Any],
    check_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bounded, source-linked localization facts; never include free-form AI prose.

    The provider assessment does not include Step06 evidence_complete/status;
    for a rejected provider response use its trusted Check metadata instead.
    """
    context = _candidate_operational_localization_context(root, group, specification)
    loc = assessment.get("spatial_localization")
    if not isinstance(loc, dict):
        return {"structure": "missing_or_invalid", "allowed_regions": [
            {"region_id": item["region_id"], "name_ko": item["name_ko"]}
            for item in context.get("allowed_regions", [])[:30]
        ]}
    allowed = {str(item["region_id"]) for item in context.get("allowed_regions", [])}
    allowed_sectors = set(SPATIAL_LOCALIZATION_SECTORS)
    regions = loc.get("regions")
    compact_regions = []
    for item in regions[:24] if isinstance(regions, list) else []:
        if not isinstance(item, dict):
            compact_regions.append({"structure": "not_object"})
            continue
        rid = item.get("region_id")
        sec = item.get("relative_sector")
        compact_regions.append({
            "region_id": rid if isinstance(rid, str) and rid in allowed else "outside_allowed_regions",
            "relative_sector": sec if isinstance(sec, str) and sec in allowed_sectors else "invalid",
        })
    hour = loc.get("forecast_hour")
    supported_hours = _positive_finding_supported_forecast_hours(assessment)
    diagnostics = _positive_image_diagnostics_at_hour(assessment, hour) if isinstance(hour, int) and not isinstance(hour, bool) else set()
    required = _required_spatial_localization_diagnostics(specification)
    check_evidence = check_evidence if isinstance(check_evidence, dict) else {}
    evidence_status = assessment.get("evidence_status", check_evidence.get("evidence_status"))
    evidence_complete = assessment.get("evidence_complete", check_evidence.get("evidence_complete"))
    eligibility = {
        "observed": assessment.get("assessment") == "observed",
        "complete_expected_scope": assessment.get("scope_coverage") == "complete_expected_scope",
        "evidence_complete_status": evidence_status == "complete",
        "evidence_complete": evidence_complete is True,
        "usable_for_synthesis": assessment.get("usable_for_synthesis") is not False,
        "no_contradictions": not bool(assessment.get("contradictory_findings")),
        "no_uncertainties": not bool(assessment.get("uncertainties")),
    }
    return {
        "status": loc.get("status") if isinstance(loc.get("status"), str) and loc.get("status") in {"not_applicable", "indeterminate", "localized"} else "invalid",
        "forecast_hour": hour if isinstance(hour, int) and not isinstance(hour, bool) and 0 <= hour <= 1000 else None,
        "confidence": loc.get("confidence") if isinstance(loc.get("confidence"), str) and loc.get("confidence") in SPATIAL_LOCALIZATION_CONFIDENCE else None,
        "regions": compact_regions,
        "region_count": len(regions) if isinstance(regions, list) else None,
        "allowed_regions": [
            {"region_id": item["region_id"], "name_ko": item["name_ko"]}
            for item in context.get("allowed_regions", [])[:30]
        ],
        "required_image_diagnostics": sorted(required),
        "observed_image_diagnostics_at_hour": sorted(diagnostics),
        "missing_image_diagnostics_at_hour": sorted(required - diagnostics),
        "positive_finding_hours": sorted(supported_hours),
        "eligibility": eligibility,
    }

# Original func/step07/checks.py:5203
def _validate_spatial_localization_narrative_grounding(
    root: Path,
    group: dict[str, Any],
    check: dict[str, Any],
    specification: dict[str, Any],
    assessment: dict[str, Any],
) -> None:
    """Fail closed when asserted alignment-region sectors differ from metadata.

    Negative/uncertain statements remain checked where they explicitly assert
    a spatial overlap relation: without the original rejected VLM responses,
    automatically exempting negation could hide a real location conflict.
    """
    issues = _spatial_narrative_grounding_issues(root, group, specification, assessment)
    if not issues:
        return
    messages = []
    for issue in issues[:12]:
        if issue["problem"] == "localization_indeterminate":
            detail = f"spatial_localization.status={issue['localization_status']}인데 운영권역 내 세부 방향을 특정했습니다"
        elif issue["problem"] == "region_not_localized":
            detail = "구조화 localization에 없는 운영권역의 세부 위치를 서술했습니다"
        else:
            detail = f"방위 {issue['mentioned_sector']}가 구조화 relative_sector={issue['declared_sector']}와 일치하지 않습니다"
        messages.append(f"{issue['field_path']}: {issue['region_name']}({issue['region_id']}) {detail}")
    raise Pass2PipelineError("SPATIAL_NARRATIVE_GROUNDING_INVALID", "; ".join(messages))

# Original func/step07/checks.py:5271
def _validate_moisture_transport_direction_reasoning(
    entries: list[dict[str, Any]], assessment: dict[str, Any],
) -> None:
    """Reject an unsupported *direction-alone* negation of moisture inflow.

    The direction of an 850-hPa wind vector is not, by itself, a test of
    advection from a source toward a geographically bounded candidate.
    This guard does NOT decide whether the moisture flow is connected and
    never changes the model's assessment. It only forces the provider to
    explain source/target geometry rather than infer non-inflow from NE wind.
    """
    if not any(
        image.get("physical_diagnostic") == "QL_MOISTURE_FLUX850_WIND850"
        for entry in entries if isinstance(entry, dict)
        for image in (entry.get("step05") or {}).get("generated_images", [])
        if isinstance(image, dict)
    ):
        return
    paragraphs: list[str] = []
    for field in ("findings", "contradictory_findings"):
        for row in assessment.get(field, []):
            if isinstance(row, dict):
                paragraphs.append(str(row.get("statement") or ""))
    paragraphs.extend(str(u) for u in assessment.get("uncertainties", []) if isinstance(u, str))
    # Narrow to the observed failure: direction used as the *sole causal
    # premise* for asserting absence of transport toward the target.
    cause = r"(?:북동향|북동쪽(?:으로)?|북동방향)(?:이므로|이라서|이어서|이기\s*때문에)"
    no_inflow = r"(?:후보|남부|북쪽|해안)[^.!?\n]{0,75}?(?:향하지\s*않|이어지지\s*않|유입되지\s*않|연결되지\s*않|흐름으로\s*확인되지\s*않|흐름이\s*아니)"
    for value in paragraphs:
        for sentence in re.split(r"(?<=[.!?。])\s*", value):
            directional_denial = re.search(cause + r"[^.!?\n]{0,120}" + no_inflow, sentence)
            # Actual CAND_005 failure also used "북동향이며 남쪽에서
            # 북쪽에 있는 후보로 향하지 않는다". The two stated geometrical
            # facts cannot logically support that categorical conclusion.
            explicit_south_to_north_denial = re.search(
                r"(?:북동향|북동쪽(?:으로)?)[^.!?\n]{0,95}"
                r"남쪽에서\s*북쪽[^.!?\n]{0,70}향하지\s*않",
                sentence,
            )
            # Soft negative statements are legitimate when grounded in a
            # separate spatial comparison. However a bare 'NE wind, hence no
            # inflow observed' without source-to-target geometry is the
            # original 2026101500 failure and must be regenerated.
            direction_only_non_observation = (
                re.search(
                    r"(?:북동향|북동쪽(?:을\s*향해|으로)?)[^.!?\n]{0,90}"
                    r"(?:후보|남부)[^.!?\n]{0,75}"
                    r"(?:흐름으로|유입으로)\s*확인되지\s*않",
                    sentence,
                )
                and not re.search(
                    r"(?:후보\s*경계|공간적\s*연결|실제\s*유입\s*경로|벡터.*(?:경계|교차|이어지)|공급원.*(?:상대\s*위치|연결))",
                    sentence,
                )
            )
            if directional_denial or explicit_south_to_north_denial or direction_only_non_observation:
                raise Pass2PipelineError(
                    "MOISTURE_TRANSPORT_DIRECTION_REASONING_INVALID",
                    "850 hPa 북동향이라는 방향만으로 후보 유입 부재를 결론지을 수 없습니다. "
                    "수증기 플럭스가 큰 공급원 위치, 후보 상대위치, 실제 바람벡터의 연결 여부를 "
                    "독립적으로 판독하고 불확실하면 명확히 남기십시오.",
                )

# Original func/step07/checks.py:5335
def _validate_image_diagnostic_narrative_identity(
    entries: list[dict[str, Any]],
    assessment: dict[str, Any],
) -> None:
    """Reject prose that assigns an image diagnostic to the wrong physical field."""
    diagnostics: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        stored = ((entry.get("step05") or {}).get("result")) if isinstance(entry.get("step05"), dict) else None
        if not isinstance(stored, dict):
            continue
        for image in stored.get("generated_images", []):
            if isinstance(image, dict) and isinstance(image.get("physical_diagnostic"), str):
                diagnostics.add(str(image["physical_diagnostic"]))
    if not diagnostics:
        return
    texts: list[str] = []
    for field in ("findings", "contradictory_findings"):
        for item in assessment.get(field, []):
            if isinstance(item, dict) and isinstance(item.get("statement"), str):
                texts.append(item["statement"])
    texts.extend(str(item) for item in assessment.get("uncertainties", []) if isinstance(item, str))
    joined = "\n".join(texts)
    errors: list[str] = []
    shade_prefix = r"(?:음영|색상|채색|그림의\s*음영|영상의\s*음영)"
    if "QL_MOISTURE_FLUX850_WIND850" in diagnostics and "QL_TCW_WIND850" not in diagnostics:
        if re.search(shade_prefix + r"[^.\n]{0,60}(?:대기\s*전층\s*수분량|TCW)", joined, flags=re.IGNORECASE):
            errors.append(
                "QL_MOISTURE_FLUX850_WIND850 image shading was described as TCW; "
                "the shading is 850 hPa moisture-flux magnitude and vectors are 850 hPa wind"
            )
    if "QL_TCW_WIND850" in diagnostics and "QL_MOISTURE_FLUX850_WIND850" not in diagnostics:
        if re.search(shade_prefix + r"[^.\n]{0,60}(?:수증기\s*플럭스|수분\s*플럭스)", joined, flags=re.IGNORECASE):
            errors.append(
                "QL_TCW_WIND850 image shading was described as moisture flux; "
                "the shading is total column water and vectors are 850 hPa wind"
            )
    if errors:
        raise Pass2PipelineError("EVIDENCE_DIAGNOSTIC_IDENTITY_INVALID", "; ".join(errors))

# Original func/step07/checks.py:5615
def _quantitative_selector_has_duplicates(selectors: Any) -> bool:
    """하나의 VLM 응답에서 동일한 정량 selector가 반복되는지 확인합니다."""
    if not isinstance(selectors, list):
        return False
    seen: set[tuple[str, str, int, str, str]] = set()
    for selector in selectors:
        if not isinstance(selector, dict):
            return False
        request_id = selector.get("request_id")
        feature_id = selector.get("feature_id")
        forecast_hour = selector.get("forecast_hour")
        region_id = selector.get("region_id")
        statistic = selector.get("statistic")
        if not all(isinstance(value, str) and value for value in (request_id, feature_id, region_id)):
            return False
        if not isinstance(forecast_hour, int) or isinstance(forecast_hour, bool) or statistic not in QUANTITATIVE_STATISTICS:
            return False
        selector_key = (request_id, feature_id, forecast_hour, region_id, statistic)
        if selector_key in seen:
            return True
        seen.add(selector_key)
    return False

# Original func/step07/checks.py:5639
def _is_grounding_validation_error(
    error: Pass2PipelineError, *, response: dict[str, Any], rows: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str]],
) -> bool:
    """VLM grounding 재시도가 가능한 검증 오류인지 확인합니다."""
    if error.code == "EVIDENCE_REF_INVALID":
        return True
    if error.code in {
        "DIRECT_PRECIPITATION_EVALUATION_UNAVAILABLE_ASSESSMENT_INVALID",
        "QUANTITATIVE_FORECAST_HOUR_OUT_OF_SCOPE",
        "QUANTITATIVE_RELATION_INVALID",
        "QUANTITATIVE_RELATION_MISMATCH",
        "QUANTITATIVE_RELATION_REQUIRED",
        "QUANTITATIVE_FEATURE_USE_INVALID",
        "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
        "QUANTITATIVE_EXPECTED_STATE_MISMATCH",
        "EVIDENCE_COMPLETENESS_ASSESSMENT_INVALID",
        "ASSESSMENT_SCOPE_COVERAGE_INVALID",
        "SPATIAL_LOCALIZATION_INVALID",
        "SPATIAL_LOCALIZATION_EVIDENCE_INVALID",
        "SPATIAL_LOCALIZATION_REGION_INVALID",
        "PRECIPITATION_MOTION_INVALID",
        "REGION_NARRATIVE_INVALID",
        "SPATIAL_NARRATIVE_GROUNDING_INVALID",
        "FORECAST_TIME_NARRATIVE_INVALID",
        "EVIDENCE_DIAGNOSTIC_IDENTITY_INVALID",
        "MOISTURE_TRANSPORT_DIRECTION_REASONING_INVALID",
    }:
        return True
    if error.code != "QUANTITATIVE_CLAIM_INVALID":
        return False
    assessments = {str(item.get("check_id")): item for item in response.get("assessments", []) if isinstance(item, dict)}
    for _group, check, entries, _fingerprint in rows:
        assessment = assessments.get(str(check["check_id"]), {})
        claims = assessment.get("quantitative_claims") if isinstance(assessment, dict) else None
        if (
            _quantitative_selector_has_namespace_mismatch(check, entries, claims)
            or _quantitative_selector_has_duplicates(claims)
        ):
            return True
        relations = assessment.get("quantitative_relations") if isinstance(assessment, dict) else None
        if not isinstance(relations, list):
            continue
        for relation in relations:
            selectors = relation.get("selectors") if isinstance(relation, dict) else None
            if (
                _quantitative_selector_has_namespace_mismatch(check, entries, selectors)
                or _quantitative_selector_has_duplicates(selectors)
            ):
                # Each relation must contain distinct quantitative cells.  Do
                # not silently drop/replace a duplicate: the intended temporal
                # comparison and its assessment must be regenerated together.
                return True
    return False

# Original func/step07/checks.py:5694
def _grounding_retry_context(
    error: Pass2PipelineError, *, checks: list[dict[str, Any]], entries_by_id: dict[str, dict[str, Any]],
    response: dict[str, Any] | None = None,
    specifications_by_check: dict[str, dict[str, Any]] | None = None,
    root: Path | None = None,
    group: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create source-grounded corrective context without changing the response schema."""
    check_contexts: list[dict[str, Any]] = []
    for check in checks:
        entries = [entries_by_id[request_id] for request_id in check["available_request_ids"]]
        row: dict[str, Any] = {
            "check_id": check["check_id"],
            "allowed_quantitative_forecast_hours": check.get("allowed_quantitative_forecast_hours", []),
            "allowed_evidence_refs": _allowed_evidence_reference_rows(check, entries),
            "allowed_quantitative_selector_namespace": _allowed_quantitative_selector_rows(check, entries),
        }
        if specifications_by_check is not None:
            specification = specifications_by_check.get(str(check["check_id"]), {})
            required = _required_specific_quantitative_selectors(specification, check, entries)
            if required is not None:
                row["specific_quantitative_selector_context"] = required
        if (root is not None and group is not None and response is not None
                and specifications_by_check is not None and error.code in {
                    "SPATIAL_LOCALIZATION_INVALID", "SPATIAL_LOCALIZATION_EVIDENCE_INVALID",
                    "SPATIAL_LOCALIZATION_REGION_INVALID", "SPATIAL_NARRATIVE_GROUNDING_INVALID",
                }):
            prior_assessment = next((
                item for item in response.get("assessments", [])
                if isinstance(item, dict) and str(item.get("check_id")) == str(check["check_id"])
            ), None)
            if isinstance(prior_assessment, dict):
                spec = specifications_by_check.get(str(check["check_id"]), {})
                row["previous_spatial_localization_diagnostics"] = _spatial_localization_retry_diagnostics(
                    root, group, spec, prior_assessment, check,
                )
                row["previous_spatial_narrative_issues"] = _spatial_narrative_grounding_issues(
                    root, group, spec, prior_assessment,
                )
                if error.code == "SPATIAL_NARRATIVE_GROUNDING_INVALID":
                    # Model-readable exact-name contrasts.  Preserve both the
                    # rejected mention and structured localization: this is
                    # guidance for fresh image interpretation, NOT an automatic
                    # substitution or endorsement of either weather assertion.
                    name_by_id = {
                        str(item.get("region_id")): str(item.get("name_ko"))
                        for item in _candidate_operational_region_naming_context(root, group).get("regions", [])
                        if isinstance(item, dict)
                    }
                    localization = prior_assessment.get("spatial_localization")
                    verified_locations = []
                    if isinstance(localization, dict) and localization.get("status") == "localized":
                        for item in localization.get("regions", []):
                            if not isinstance(item, dict):
                                continue
                            rid = str(item.get("region_id") or "")
                            if rid in name_by_id:
                                verified_locations.append({
                                    "region_id": rid,
                                    "region_name": name_by_id[rid],
                                    "relative_sector": item.get("relative_sector"),
                                })
                    row["explicit_region_name_disambiguation"] = {
                        "rejected_mentions": [
                            {
                                "field_path": issue["field_path"],
                                "region_id": issue["region_id"],
                                "region_name": issue["region_name"],
                                "mentioned_sector": issue["mentioned_sector"],
                                "problem": issue["problem"],
                            }
                            for issue in row["previous_spatial_narrative_issues"]
                        ],
                        "previous_structured_localization": verified_locations,
                        "requirement": (
                            "표준 지명과 ID를 함께 확인하십시오. 예: MARINE_WEST_SOUTH=서해남부 광역, "
                            "MARINE_SOUTH_WEST=남해서부 광역은 서로 다른 권역입니다. "
                            "원문에서 잘못 지정한 위치를 단순 추가 주석으로 가리지 마십시오. "
                            "원래 영상 근거를 재판독하여 문장과 spatial_localization을 일치시키고, "
                            "위치를 확인할 수 없으면 근거 없는 위치를 주장하지 마십시오."
                        ),
                    }
        if response is not None and error.code in {"QUANTITATIVE_RELATION_INVALID", "QUANTITATIVE_RELATION_MISMATCH"}:
            assessments = {
                str(item.get("check_id")): item
                for item in response.get("assessments", []) if isinstance(item, dict)
            }
            assessment = assessments.get(str(check["check_id"]))
            if isinstance(assessment, dict):
                row["previous_relation_diagnostics"] = _quantitative_relation_diagnostics(
                    check, entries, assessment,
                )
        if response is not None and error.code == "QUANTITATIVE_CLAIM_INVALID":
            assessments = {
                str(item.get("check_id")): item
                for item in response.get("assessments", []) if isinstance(item, dict)
            }
            assessment = assessments.get(str(check["check_id"]))
            if isinstance(assessment, dict):
                row["rejected_quantitative_selectors"] = _quantitative_response_namespace_issues(
                    check, entries, assessment,
                )[:20]
                # QUANTITATIVE_CLAIM_INVALID can also originate inside a
                # relation's selectors even when the claims and every selector
                # namespace are valid.  Give the model the precise offending
                # relation instead of only an empty namespace-issues list.
                row["duplicate_quantitative_relation_diagnostics"] = [
                    detail for detail in _quantitative_relation_diagnostics(check, entries, assessment)
                    if "duplicate quantitative selector" in str(detail.get("validation_error") or "")
                ][:12]
        if response is not None and error.code == "QUANTITATIVE_RELATION_MISMATCH":
            assessments = {
                str(item.get("check_id")): item
                for item in response.get("assessments", [])
                if isinstance(item, dict)
            }
            assessment = assessments.get(str(check["check_id"]), {})
            verified_relations: list[dict[str, Any]] = []
            for relation in assessment.get("quantitative_relations", []) if isinstance(assessment, dict) else []:
                if not isinstance(relation, dict):
                    continue
                try:
                    points = resolve_quantitative_claims(check, entries, relation.get("selectors"))
                    computed = compute_quantitative_relation(relation, points)
                except (Pass2PipelineError, QuantitativeConsistencyError):
                    continue
                if computed.get("consistent"):
                    continue
                verified_relations.append({
                    "relation_id": relation.get("relation_id"),
                    "claimed_relation": relation.get("claimed_relation"),
                    "computed_relation": computed.get("computed_relation"),
                    "claimed_forecast_hour": relation.get("claimed_forecast_hour"),
                    "computed_forecast_hour": computed.get("computed_forecast_hour"),
                    "verified_points": [
                        {
                            "feature_id": point.get("physical_diagnostic"),
                            "forecast_hour": point.get("forecast_hour"),
                            "region_id": point.get("region_id"),
                            "region_name": point.get("region_name"),
                            "statistic": point.get("statistic"),
                            "value": point.get("resolved_value"),
                            "units": point.get("units"),
                        }
                        for point in points
                    ],
                })
            if verified_relations:
                row["verified_quantitative_relation_corrections"] = verified_relations
        check_contexts.append(row)
    corrective_instructions = [
        "The previous response was discarded. Return the complete corrected response for this exact bundle.",
        "Copy only identifier combinations from the allowed namespaces below; do not infer, shorten, rewrite, or generate identifiers.",
        "Within each check, return each exact (request_id, feature_id, forecast_hour, region_id, statistic) quantitative selector at most once.",
        "If verified_quantitative_relation_corrections is present, correct only the contradicted quantitative relation and any directly dependent wording; do not change unrelated image interpretation, identifiers, or evidence links.",
    ]
    if error.code == "DIRECT_PRECIPITATION_EVALUATION_UNAVAILABLE_ASSESSMENT_INVALID":
        corrective_instructions.extend([
            "The SYSTEM direct-precipitation threshold could not be evaluated completely because the required operational-scope overlap, scope metadata, or valid precipitation values are unavailable.",
            "For this Check return exactly assessment=insufficient_evidence and scope_coverage=indeterminate. Do not report not_observed or opposite_observed, and do not treat unavailable scope/data as evidence that rain is absent.",
            "Describe the specific unavailable forecast hours and reason from system_direct_precipitation_anchor_context; preserve unrelated findings and evidence links.",
        ])
    elif error.code == "QUANTITATIVE_CLAIM_INVALID":
        corrective_instructions.extend([
            "For quantitative_claims and every quantitative_relations[].selectors item, copy an exact five-field selector tuple from allowed_quantitative_selector_namespace.",
            "Check duplicate_quantitative_relation_diagnostics. Within EACH quantitative_relations[].selectors list, never repeat the exact same five-field selector. For a temporal comparison, select the two distinct forecast hours required by the original Step04 expected_signal from one comparable region/feature/statistic; never use F048 versus F048 to represent F018 versus F048.",
            "Regenerate the whole Check including assessment and narrative against the Step05 values. Do not delete a required regional comparison merely to pass validation, assume a trend, or invent any replacement cell. If the evidence cannot resolve a claim, explicitly reflect that uncertainty.",
            "Do not swap field roles: feature_id must be an exact Step05 feature key and region_id must be an exact operational region identifier from the same allowed namespace row.",
            "If the needed selector tuple is absent from allowed_quantitative_selector_namespace, do not invent or approximate it; remove the unsupported quantitative claim/relation and reassess from the remaining valid evidence.",
        ])
    elif error.code == "QUANTITATIVE_FORECAST_HOUR_OUT_OF_SCOPE":
        corrective_instructions.extend([
            "For each check, use quantitative forecast_hour values only from that check's allowed_quantitative_forecast_hours.",
            "The allowed_quantitative_selector_namespace is already restricted to those phase hours. Copy selectors only from that namespace.",
            "Remove any out-of-scope quantitative selector and any relation point that depends on it. Do not shift the check phase or invent a nearby forecast hour.",
            "If a quantitative relation is still needed, rebuild it only from selectors that remain inside the allowed phase hours.",
        ])
    elif error.code == "SPATIAL_NARRATIVE_GROUNDING_INVALID":
        corrective_instructions.extend([
            "For precipitation/environment multivariable spatial-relation prose, spatial_localization is authoritative for operational-region subareas.",
            "If spatial_localization.status is indeterminate, do not attach north/south/east/west/diagonal/central qualifiers to an operational-region name in findings, contradictory_findings, or uncertainties. Use a generic candidate-area description and explicitly state that the operational-region location is unresolved.",
            "If spatial_localization.status is localized, any directional qualifier attached to a region name must exactly match that region's relative_sector. relative_sector=unspecified means do not add a directional qualifier.",
            "Review previous_spatial_narrative_issues and explicit_region_name_disambiguation. Compare both the rejected Korean region name and the structured localization's Korean region name with their exact IDs. MARINE_WEST_SOUTH is 서해남부 광역 (West Sea), but MARINE_SOUTH_WEST is 남해서부 광역 (South Sea); they are NOT synonyms.",
            "If a spatial overlap finding names a different region from structured localization, do not merely append an approved-region annotation while leaving the rejected region-sector claim in the same statement. Re-read the supplied images, regenerate the entire Check coherently, and resolve the conflict explicitly.",
            "Do not blindly replace an unsupported location by the prior structured location; revisit the actual image. If source evidence cannot resolve the location, use indeterminate spatial localization and an appropriately qualified meteorological conclusion.",
            "Review previous_spatial_localization_diagnostics to check whether localization itself is supported by the indicated forecast hour and image diagnostics.",
            "Do not change the assessment merely to make a location phrase legal; rewrite the prose to match the existing structured localization result.",
        ])
    elif error.code == "QUANTITATIVE_RELATION_INVALID":
        corrective_instructions.extend([
            "For all selectors in EACH quantitative relation, feature_id, statistic, and Step05 units MUST match; never compare different features/statistics/units in one relation.",
            "Inspect previous_relation_diagnostics.validation_error and resolved_points to identify the exact incompatible selectors before regenerating the complete Check.",
            "For a specific Check, use only the exact required_quantitative_selectors from specific_quantitative_selector_context for the deciding comparison; do not substitute another region to repair a relation.",
            "For every quantitative_relations[].selectors item, the exact same selector tuple must already be declared in quantitative_claims of the same check.",
            "Selector identity is the exact five-field tuple (request_id, feature_id, forecast_hour, region_id, statistic). Do not treat similar selectors as equivalent.",
            "If a relation selector is necessary, add that exact selector to quantitative_claims. If it is not necessary, remove it from the relation. Do not invent a replacement selector.",
            "The extremum relation is temporal only: every selector must use one common region_id and different forecast hours. A single-hour maximum/minimum location across multiple regions cannot be represented as extremum; keep the supporting quantitative_claims but remove that quantitative_relation instead of inventing a common region.",
            "Keep relation_id unique and non-empty, use only the allowed relation_type/claimed_relation combinations, and preserve the response schema exactly.",
        ])
    elif error.code == "QUANTITATIVE_RELATION_MISMATCH":
        corrective_instructions.extend([
            "The claimed quantitative direction disagreed with Python's Step05 recomputation. Check previous_relation_diagnostics computed_relation and resolved_points; do not repeat the rejected direction.",
            "For a specific Check, compare only specific_quantitative_selector_context.required_quantitative_selectors, even if other regions have stronger signals.",
            "Regenerate the assessment and all dependent findings/contradictions coherently; expected_state is not ground truth.",
        ])
    elif error.code == "QUANTITATIVE_RELATION_REQUIRED":
        corrective_instructions.append(
            "When quantitative_claims selects two or more forecast hours from the same quantitative series, declare quantitative_relations that cover those selected hours so Python can verify the temporal comparison."
        )
    elif error.code == "QUANTITATIVE_FEATURE_USE_INVALID":
        corrective_instructions.append(
            "For temporal precipitation intensity/peak checks, use tp_6h_increment_mm in quantitative_claims and include at least one quantitative relation composed only of tp_6h_increment_mm points."
        )
    elif error.code in {"QUANTITATIVE_SCOPE_CONTRACT_INVALID", "QUANTITATIVE_EXPECTED_STATE_MISMATCH"}:
        corrective_instructions.extend([
            "For specific Checks with available source cells, use only specific_quantitative_selector_context.required_quantitative_selectors for the deciding comparison; do not replace any region, feature, hour, statistic, or request.",
            "If intent_context.atomic_claims contains a non-null quantitative_scope for one specific claim, use only its exact feature_id, single region_id, forecast_hours, comparison_forecast_hours (when present), and statistic for the deciding relation.",
            "Re-evaluate assessment and scope_coverage from the verified quantitative direction. Do not use a different region to turn an opposite specific claim into partially_observed.",
            "For convergence925_1e5_s, positive values mean convergence and negative values mean divergence. A negative value becoming less negative is divergence weakening, not convergence strengthening. If a majority not_strengthen contract is present, more than half of the candidate-overlap regions must satisfy not-strengthen; exactly half is not a majority.",
        ])
    elif error.code in {"SPATIAL_LOCALIZATION_INVALID", "SPATIAL_LOCALIZATION_EVIDENCE_INVALID", "SPATIAL_LOCALIZATION_REGION_INVALID"}:
        corrective_instructions.extend([
            "Rebuild spatial_localization from the spatial_localization_context supplied with the check.",
            "Use previous_spatial_localization_diagnostics: eligibility, positive_finding_hours, missing_image_diagnostics_at_hour, allowed_regions, and previously declared sectors identify the failing contract.",
            "If localization is not evidence-eligible, return indeterminate with null hour/confidence and no regions; do not preserve localized merely to satisfy a phrase.",
            "Use only exact region_id values from allowed_regions and exactly one allowed relative_sector value per selected region.",
            "localized may be used only for a forecast hour supported by positive image findings and ALL diagnostics listed in previous_spatial_localization_diagnostics.required_image_diagnostics (the required set is Check-specific, not always four). If the location cannot be assigned confidently, return indeterminate with null forecast_hour/confidence and an empty regions array.",
        ])
    elif error.code == "MOISTURE_TRANSPORT_DIRECTION_REASONING_INVALID":
        corrective_instructions.extend([
            "Discard the rejected direction-alone inference. A northeastward wind does not by itself mean that moisture cannot enter a candidate to the north or northeast of a source.",
            "Using the supplied moisture-flux magnitude shading, source position, candidate bounds and 850 hPa wind arrows, determine whether a coherent source-to-candidate path is directly visible.",
            "Do not turn this error into an automatic positive moisture-supply verdict. Reassess the evidence independently; if the geometry is unresolved, describe that limit and use the appropriate uncertain/partial/not-observed assessment.",
            "Write the explanation in Korean with spatial reasoning; do not infer quantitative boundary moisture flux from magnitude shading.",
        ])
    elif error.code == "PRECIPITATION_MOTION_INVALID":
        corrective_instructions.extend([
            "Rebuild precipitation_motion only from the precipitation_motion_context supplied with the check.",
            "If precipitation_motion_context.enabled is false, return classification=not_applicable with null origin_region_id, destination_region_id, and confidence.",
            "If enabled is true, coherent_movement means the same identifiable primary precipitation area or center can be followed continuously through the intermediate forecast hours. Different endpoints, a shifted maximum, expansion/contraction, or weakening/disappearance followed by re-formation elsewhere are not sufficient for coherent_movement.",
            "For a pure direct-precipitation move Check, assessment=observed is allowed only with classification=coherent_movement and scope_coverage=complete_expected_scope. Otherwise lower the assessment and classify the evolution as reorganization, stationary, or indeterminate as supported by the images.",
            "Use origin_region_id and destination_region_id only when both endpoints can be assigned confidently to exact IDs in precipitation_motion_context.allowed_regions. Return both IDs or neither; for non-coherent classes both IDs must be null.",
        ])
    elif error.code == "FORECAST_TIME_NARRATIVE_INVALID":
        corrective_instructions.extend([
            "Every YYYY-MM-DD HH:MM KST timestamp adjacent to Fxxx must equal init UTC plus that forecast hour converted to KST (UTC+9).",
            "Do not label a UTC clock value as KST. Prefer the provided Fxxx lead if you are not certain about the absolute clock time.",
            "Regenerate the complete Check assessment so assessment, findings, contradictions, uncertainties, and quantitative relations remain mutually consistent.",
        ])
    elif error.code == "REGION_NARRATIVE_INVALID":
        corrective_instructions.extend([
            "Rewrite the public finding so the sentence itself names the supported standard operational region whenever structured location evidence is available.",
            "For the SYSTEM direct-precipitation presence Check, use only region_name values from system_direct_precipitation_anchor_context.threshold_met_regions_by_forecast_hour.",
            "For localized multivariable alignment, use the name_ko corresponding to spatial_localization.regions[].region_id from operational_region_naming_context and preserve any validated relative_sector as a suffix such as 경북권 남서부.",
            "Do not use only generic location phrases such as 후보영역, 운영 예보권역, 일부 권역, or 해당 영역 when a supported standard region name is available, and do not invent a region absent from the supplied context.",
        ])
    return {
        "grounding_retry": {
            "previous_validation_error": {"code": error.code, "message": str(error)},
            "instruction": " ".join(corrective_instructions),
            "checks": check_contexts,
        }
    }

# Original func/step07/checks.py:5959
def _can_second_grounding_retry(error: Pass2PipelineError) -> bool:
    """Allow one final LLM repair only for quantitative grounding-contract failures."""
    return error.code in SECOND_GROUNDING_RETRY_CODES



# Validate the received LLM JSON without invoking any provider.
def _decode_validate_checks_provider_response(root: Path, response, canonical, api_family: str) -> dict[str, Any]:
    content = normalize_openai_transport_json(response.content, canonical) if api_family == "responses" else response.content
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise Pass2PipelineError("PROVIDER_RESULT_INVALID", "Step07 provider response is not JSON.") from exc
    if not isinstance(value, dict):
        raise Pass2PipelineError("PROVIDER_RESULT_INVALID", "Step07 provider response root must be an object.")
    validate_schema(root, CHECK_RESPONSE_SCHEMA, value, code="PROVIDER_RESULT_INVALID")
    return value
