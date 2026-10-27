"""Step07 Pass2 check assessment and resumable checkpoint handling."""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta, timezone
from numbers import Real
from pathlib import Path
from typing import Any, Callable

from func.shared.ai_provider import AIProvider, build_openai_transport_schema, normalize_openai_transport_json
from func.shared.majority_narrative import (
    CONTRACT_VERSION as MAJORITY_NARRATIVE_CONTRACT_VERSION,
    MajorityNarrativeError, assert_majority_narrative_current,
    canonicalize_majority_result,
)
from func.shared.pass2_common import (
    Pass2PipelineError,
    active_candidate_groups,
    image_data_url,
    load_json_object,
    load_schema,
    template_text_and_hash,
    validate_schema,
    write_validated_atomic,
)
from func.shared.pipeline_common import canonical_hash, sha256sum
from func.shared.quantitative_contract_guard import (
    absolute_threshold_in_hour_comparison, inconsistent_pairwise_hour_order, opposite_majority_not_strengthen_wording,
)
from func.shared.quantitative_consistency import (
    EXTREMUM_RELATIONS,
    PAIRWISE_RELATIONS,
    TREND_RELATIONS,
    QuantitativeConsistencyError,
    compute_physical_transition,
    compute_quantitative_relation,
    quantitative_feature_semantics,
    relation_selector_keys,
)
from func.shared.korean_narrative import (
    normalize_step07_response_directions,
    validate_korean_text,
    validate_step07_response_narrative,
    validate_step07_result_narrative,
)
from func.shared.operational_regions import bbox_intersection, load_operational_regions, selectable_pass1_regions
from func.shared.runtime_paths import resolve_logical_ref, runtime_path
from func.shared.signal_validation import (
    DIRECT_PRECIPITATION_MINIMUM_MM_6H,
    alignment_requires_spatial_localization,
    operational_region_sector_mentions,
)
from func.step06.package import Pass2PackageError, validate_pass2_package


CHECK_RESULTS_SCHEMA = "pass2_check_results.schema.json"
CHECK_RESPONSE_SCHEMA = "pass2_check_assessment_response.schema.json"
CHECK_PROMPT = "ai_pass2_check_assessment_prompt.ko.md"
CHECK_RESULTS_VERSION = "step07-pass2-check-results-v13"
ENSEMBLE_CONTRACT_VERSION = "step07-ensemble-consensus-v3"
ENSEMBLE_RESULT_SELECTION_VERSION = "step07-ensemble-result-selection-v2"
ENSEMBLE_MEMBER_COUNT = 3
MINORITY_REPORT_SCHEMA = "step07_minority_report.schema.json"
MINORITY_REPORT_VERSION = "step07-llm-wiki-minority-report-v1"
QUANTITATIVE_VALIDATION_CONTRACT_VERSION = "specific-v3+convergence-majority-v2"
# Step04 on deployed runs still writes v2. The v2 specific and majority
# schemas have the fields understood by the Step07 deterministic validators.
SUPPORTED_QUANTITATIVE_ENFORCEMENT_VERSIONS = frozenset({
    "specific-v2+convergence-majority-v1",
    QUANTITATIVE_VALIDATION_CONTRACT_VERSION,
})
SPECIFIC_SELECTOR_GUIDANCE_VERSION = "specific-required-selectors-v1"
MAJORITY_QUANTITATIVE_BINDING_VERSION = "convergence-majority-source-binding-v1"
QUANTITATIVE_STATISTICS = frozenset({"min", "max", "mean", "p95"})
_TEMPORAL_PRECIPITATION_DIAGNOSTICS = frozenset({"accumulated_precipitation", "temporal_evolution"})
_TEMPORAL_PRECIPITATION_ROLES = frozenset({"hazard_magnitude", "uncertainty"})
_TEMPORAL_PRECIPITATION_SPATIAL_METHODS = frozenset({"regional_statistics", "regional_ranking", "regional_max_location"})
QUANTITATIVE_FAILURE_REASONS = frozenset({
    "QUANTITATIVE_RELATION_MISMATCH",
    "QUANTITATIVE_RELATION_REQUIRED",
    "QUANTITATIVE_FEATURE_USE_INVALID",
    "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
    "QUANTITATIVE_EXPECTED_STATE_MISMATCH",
})
EXCLUDED_FROM_SYNTHESIS_REASONS = frozenset({"INSUFFICIENT_EVIDENCE", "ENSEMBLE_NO_CONSENSUS", *QUANTITATIVE_FAILURE_REASONS})
SPATIAL_LOCALIZATION_DIAGNOSTIC_BY_COMPONENT = {
    "direct_precipitation": "QL_PRECIP_6H",
    "moisture_supply": "QL_TCW_WIND850",
    "low_level_forcing": "QL_CONVERGENCE925_WIND925",
    "mid_upper_forcing": "QL_OMEGA700_Z500",
}
SPATIAL_LOCALIZATION_SECTORS = frozenset({
    "unspecified",
    "north",
    "northeast",
    "east",
    "southeast",
    "south",
    "southwest",
    "west",
    "northwest",
    "central",
})
SPATIAL_LOCALIZATION_CONFIDENCE = frozenset({"high", "medium"})

PRECIPITATION_MOTION_CLASSIFICATIONS = frozenset({
    "coherent_movement",
    "reorganization",
    "stationary",
    "indeterminate",
    "not_applicable",
})
SPATIAL_LOCALIZATION_REGION_CATALOG = Path("daba") / "ai_pass1" / "discovery_operational_regions.yaml"
SPATIAL_LOCALIZATION_SECTOR_LABEL_KO = {
    "unspecified": "",
    "north": "북부",
    "northeast": "북동부",
    "east": "동부",
    "southeast": "남동부",
    "south": "남부",
    "southwest": "남서부",
    "west": "서부",
    "northwest": "북서부",
    "central": "중부",
}
REGION_NARRATIVE_DIRECT_ANNOTATION = "1.0 mm 이상 확인 운영권역"
REGION_NARRATIVE_ALIGNMENT_ANNOTATION = "검증된 결합 위치"
MAX_GROUNDING_RETRY_ATTEMPTS = 2
SECOND_GROUNDING_RETRY_CODES = frozenset({
    "QUANTITATIVE_FORECAST_HOUR_OUT_OF_SCOPE",
    "QUANTITATIVE_RELATION_INVALID",
    "QUANTITATIVE_RELATION_MISMATCH",
    "QUANTITATIVE_RELATION_REQUIRED",
    "QUANTITATIVE_FEATURE_USE_INVALID",
    "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
    "QUANTITATIVE_EXPECTED_STATE_MISMATCH",
    "ASSESSMENT_SCOPE_COVERAGE_INVALID",
    "SPATIAL_LOCALIZATION_INVALID",
    "SPATIAL_LOCALIZATION_EVIDENCE_INVALID",
    "SPATIAL_LOCALIZATION_REGION_INVALID",
    "PRECIPITATION_MOTION_INVALID",
    "FORECAST_TIME_NARRATIVE_INVALID",
    "EVIDENCE_DIAGNOSTIC_IDENTITY_INVALID",
})

_SCOPE_COVERAGE_BY_ASSESSMENT = {
    "observed": {"complete_expected_scope"},
    "partially_observed": {"partial_expected_scope"},
    "not_observed": {"complete_expected_scope"},
    "opposite_observed": {"opposite_expected_scope"},
    "uncertain": {"indeterminate"},
    "insufficient_evidence": {"indeterminate"},
}


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


SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY = "SYS_DIRECT_PRECIPITATION_PRESENCE"


def _is_system_direct_precipitation_anchor_check(specification: dict[str, Any]) -> bool:
    intent = specification.get("intent_contract")
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


def _normalized_bbox(value: Any) -> dict[str, float] | None:
    signature = _bbox_signature(value)
    if signature is None:
        return None
    return dict(zip(("west", "east", "south", "north"), signature))


def _spatial_scope_context(
    group: dict[str, Any],
    check: dict[str, Any],
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build neutral, Check-local spatial metadata for independent VLM reading."""
    candidate_context = group.get("candidate_context")
    candidate = candidate_context.get("candidate") if isinstance(candidate_context, dict) else None
    plan = candidate_context.get("analysis_plan") if isinstance(candidate_context, dict) else None
    candidate_area = _normalized_bbox(candidate.get("candidate_area")) if isinstance(candidate, dict) else None
    if candidate_area is None or not isinstance(plan, dict):
        raise Pass2PipelineError(
            "SPATIAL_SCOPE_CONTEXT_INVALID",
            f"{group.get('candidate_id')} check={check.get('check_id')} lacks valid candidate spatial context.",
        )
    targets = plan.get("target_areas")
    if not isinstance(targets, list):
        raise Pass2PipelineError(
            "SPATIAL_SCOPE_CONTEXT_INVALID",
            f"{group.get('candidate_id')} has no target_areas for spatial context.",
        )
    target_by_id = {
        str(target.get("target_id")): target
        for target in targets
        if isinstance(target, dict) and target.get("target_id")
    }
    evidence_targets: list[dict[str, Any]] = []
    for entry in sorted(entries, key=lambda item: str(item.get("request", {}).get("request_id") or "")):
        request = entry.get("request") if isinstance(entry, dict) else None
        step05 = entry.get("step05") if isinstance(entry, dict) else None
        if not isinstance(request, dict):
            continue
        request_id = str(request.get("request_id") or "")
        target_id = str(request.get("target_id") or "")
        target = target_by_id.get(target_id)
        if target is None:
            raise Pass2PipelineError(
                "SPATIAL_SCOPE_CONTEXT_INVALID",
                f"{group.get('candidate_id')} check={check.get('check_id')} request={request_id} has unknown target_id={target_id!r}.",
            )
        target_type = str(target.get("target_type") or "")
        resolution_method = str(target.get("resolution_method") or "")
        if target_type == "candidate_domain":
            relation = "exact_candidate_area"
        elif target_type == "context_domain":
            relation = "contains_candidate_area"
        elif target_type == "operational_regions" and resolution_method == "candidate_area_overlap":
            relation = "candidate_area_overlap"
        elif target_type == "operational_regions" and resolution_method == "explicit_region_ids":
            relation = "explicit_operational_regions"
        else:
            raise Pass2PipelineError(
                "SPATIAL_SCOPE_CONTEXT_INVALID",
                f"{group.get('candidate_id')} check={check.get('check_id')} target={target_id} has unsupported spatial contract.",
            )
        result = step05.get("result") if isinstance(step05, dict) else None
        selected_domain = (
            result.get("selected_domain")
            if isinstance(result, dict) and result.get("selected_domain") is not None
            else request.get("requested_domain")
        )
        evidence_targets.append({
            "request_id": request_id,
            "target_id": target_id,
            "target_type": target_type,
            "resolution_method": resolution_method,
            "selected_domain": _normalized_bbox(selected_domain),
            "relation_to_candidate_area": relation,
        })
    return {
        "coordinate_system": "EPSG:4326",
        "candidate_area": candidate_area,
        "candidate_area_is_spatial_metadata_only": True,
        "evidence_targets": evidence_targets,
    }


def _spatial_localization_enabled(specification: dict[str, Any] | None) -> bool:
    """Return whether a positive precip/environment overlap must name a place."""
    return alignment_requires_spatial_localization(specification)


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


def _candidate_operational_region_naming_context(
    root: Path,
    group: dict[str, Any],
) -> dict[str, Any]:
    """Expose standardized region names as vocabulary, never as evidence.

    The candidate-overlap catalog tells the VLM which operational names may be
    used when an observed feature can actually be located there from image or
    quantitative evidence.  Mere bbox overlap must never create a weather fact.
    """
    candidate_context = group.get("candidate_context")
    candidate = candidate_context.get("candidate") if isinstance(candidate_context, dict) else None
    candidate_area = _normalized_bbox(candidate.get("candidate_area")) if isinstance(candidate, dict) else None
    if candidate_area is None:
        raise Pass2PipelineError(
            "REGION_NAMING_CONTEXT_INVALID",
            f"{group.get('candidate_id')} has no valid candidate area for region naming.",
        )
    catalog_path = runtime_path(root, *SPATIAL_LOCALIZATION_REGION_CATALOG.parts)
    try:
        regions = load_operational_regions(catalog_path)
    except (OSError, TypeError, ValueError) as exc:
        raise Pass2PipelineError(
            "REGION_NAMING_CONTEXT_INVALID",
            f"Unable to load operational-region catalog for region naming: {catalog_path}",
        ) from exc
    selected = selectable_pass1_regions(regions=regions, candidate_bbox=candidate_area)
    rows: list[dict[str, Any]] = []
    for region in selected:
        bbox = region.get("bbox") if isinstance(region, dict) else None
        if not isinstance(bbox, dict):
            continue
        region_bbox = {
            "west": float(bbox["lon_min"]),
            "east": float(bbox["lon_max"]),
            "south": float(bbox["lat_min"]),
            "north": float(bbox["lat_max"]),
        }
        overlap = bbox_intersection(candidate_area, region_bbox)
        if overlap is None:
            continue
        rows.append({
            "region_id": str(region.get("region_id") or ""),
            "name_ko": str(region.get("name_ko") or ""),
            "region_role": str(region.get("region_role") or ""),
            "candidate_overlap_bbox": overlap,
        })
    return {
        "scope": "candidate_overlap_operational_forecast_regions",
        "usage": "naming_vocabulary_only",
        "regions": rows,
        "instruction": (
            "이 목록은 사용자 문장에서 사용할 표준 운영권역명과 위치 lookup 정보일 뿐 기상현상 위치의 근거가 아니다. "
            "이미지·정량값·구조화 위치판독으로 실제 위치가 확인된 경우에만 해당 name_ko를 사용한다. "
            "구조화 근거가 있는데도 '후보영역', '운영 예보권역', '일부 권역', '해당 영역'만으로 위치 설명을 끝내지 않는다. "
            "운영권역으로 안정적으로 대응할 수 없으면 지명을 추정하지 말고 운영권역 단위 위치가 미확정임을 명시한다."
        ),
    }


def _candidate_operational_localization_context(
    root: Path,
    group: dict[str, Any],
    specification: dict[str, Any],
) -> dict[str, Any]:
    """Build the closed operational-region namespace available to the VLM.

    Region bboxes are geographic lookup metadata only.  The VLM still has to
    read the supplied Step05 weather fields to decide whether the overlap is
    present.  Source-corridor regions are intentionally excluded from the
    localization namespace because the public card describes operational
    forecast regions, not upstream context.
    """
    if not _spatial_localization_enabled(specification):
        return {
            "enabled": False,
            "selection_scope": "not_applicable",
            "allowed_regions": [],
            "relative_sector_values": sorted(SPATIAL_LOCALIZATION_SECTORS),
        }
    candidate_context = group.get("candidate_context")
    candidate = candidate_context.get("candidate") if isinstance(candidate_context, dict) else None
    candidate_area = _normalized_bbox(candidate.get("candidate_area")) if isinstance(candidate, dict) else None
    if candidate_area is None:
        raise Pass2PipelineError(
            "SPATIAL_LOCALIZATION_CONTEXT_INVALID",
            f"{group.get('candidate_id')} has no valid candidate area for spatial localization.",
        )
    catalog_path = runtime_path(root, *SPATIAL_LOCALIZATION_REGION_CATALOG.parts)
    try:
        regions = load_operational_regions(catalog_path)
    except (OSError, TypeError, ValueError) as exc:
        raise Pass2PipelineError(
            "SPATIAL_LOCALIZATION_CONTEXT_INVALID",
            f"Unable to load operational-region catalog for spatial localization: {catalog_path}",
        ) from exc
    selected = selectable_pass1_regions(regions=regions, candidate_bbox=candidate_area)
    allowed_regions: list[dict[str, Any]] = []
    for region in selected:
        bbox = region.get("bbox") if isinstance(region, dict) else None
        if not isinstance(bbox, dict):
            continue
        region_bbox = {
            "west": float(bbox["lon_min"]),
            "east": float(bbox["lon_max"]),
            "south": float(bbox["lat_min"]),
            "north": float(bbox["lat_max"]),
        }
        overlap = bbox_intersection(candidate_area, region_bbox)
        if overlap is None:
            continue
        allowed_regions.append({
            "region_id": str(region["region_id"]),
            "name_ko": str(region["name_ko"]),
            "region_role": str(region["region_role"]),
            "candidate_overlap_bbox": overlap,
        })
    return {
        "enabled": True,
        "selection_scope": "candidate_overlap_operational_forecast_regions",
        "required_signal_components": [
            component
            for component in SPATIAL_LOCALIZATION_DIAGNOSTIC_BY_COMPONENT
            if component in {
                str(value)
                for value in specification.get("required_signal_components", [])
                if isinstance(value, str) and value
            }
        ],
        "required_diagnostics": sorted(_required_spatial_localization_diagnostics(specification)),
        "allowed_regions": allowed_regions,
        "relative_sector_values": sorted(SPATIAL_LOCALIZATION_SECTORS),
        "instruction": (
            "기상장 중첩이 실제로 확인된 운영 예보권역만 선택합니다. 권역 bbox는 위치 식별용이며 "
            "해당 권역 전체에서 중첩이 성립한다는 의미가 아닙니다."
        ),
    }


def _empty_spatial_localization(*, enabled: bool) -> dict[str, Any]:
    return {
        "status": "indeterminate" if enabled else "not_applicable",
        "forecast_hour": None,
        "confidence": None,
        "regions": [],
    }


def _pure_direct_precipitation_move_check(specification: dict[str, Any] | None) -> bool:
    """Return whether a Check is a pure direct-precipitation movement test.

    Candidate titles may use the strong wording ``이동 강수`` only for this
    narrowly defined contract.  Mixed spatial-evolution claims (for example,
    move + reorganize) are intentionally excluded from movement naming.
    """
    if not isinstance(specification, dict):
        return False
    if (
        specification.get("variable_role") != "hazard_magnitude"
        or specification.get("check_scope") != "temporal_evolution"
        or specification.get("required_signal_components") != ["direct_precipitation"]
    ):
        return False
    intent = specification.get("intent_contract")
    if not isinstance(intent, dict) or intent.get("verification_mode") != "spatial_evolution":
        return False
    atomic = specification.get("atomic_claim_contract")
    claims = atomic.get("canonical_claims") if isinstance(atomic, dict) else None
    if not isinstance(claims, list) or len(claims) != 1:
        return False
    claim = claims[0]
    return bool(
        isinstance(claim, dict)
        and claim.get("expected_state") == "move"
        and claim.get("claim_components") == ["direct_precipitation"]
        and claim.get("scope_quantifier") == "specific"
    )


def _empty_precipitation_motion(*, enabled: bool) -> dict[str, Any]:
    return {
        "classification": "indeterminate" if enabled else "not_applicable",
        "origin_region_id": None,
        "destination_region_id": None,
        "confidence": None,
    }


def _precipitation_motion_context(
    root: Path,
    group: dict[str, Any],
    specification: dict[str, Any],
) -> dict[str, Any]:
    """Return the closed naming namespace for a pure precipitation-move Check."""
    enabled = _pure_direct_precipitation_move_check(specification)
    if not enabled:
        return {
            "enabled": False,
            "allowed_regions": [],
            "instruction": (
                "이 Check는 사용자 표시용 '이동 강수' 명명의 직접 근거로 사용하지 않습니다. "
                "precipitation_motion.classification=not_applicable로 반환합니다."
            ),
        }
    naming = _candidate_operational_region_naming_context(root, group)
    return {
        "enabled": True,
        "allowed_regions": copy.deepcopy(naming.get("regions", [])),
        "instruction": (
            "동일하게 식별 가능한 주강수영역 또는 강수 중심이 중간 시각을 포함해 끊김 없이 추적될 때만 "
            "coherent_movement로 판정합니다. 시작·종료 위치의 단순 차이, 최대강수 위치 변화, 영역 확대, "
            "약화·분산 뒤 다른 위치의 재형성, 분리·병합·재조직은 coherent_movement가 아닙니다. "
            "origin_region_id와 destination_region_id는 실제 공간영상에서 안정적으로 대응 가능한 경우에만 "
            "allowed_regions의 region_id를 그대로 사용합니다."
        ),
    }


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


def _precipitation_motion_vote_counts(member_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for result in member_results:
        motion = result.get("precipitation_motion") if isinstance(result.get("precipitation_motion"), dict) else {}
        classification = str(motion.get("classification") or "indeterminate")
        counts[classification] = counts.get(classification, 0) + 1
    return [
        {"classification": classification, "count": counts[classification]}
        for classification in sorted(counts)
    ]


def _consensus_precipitation_motion(
    member_results: list[dict[str, Any]],
) -> tuple[dict[str, Any], str, int | None, list[dict[str, Any]]]:
    """Aggregate motion class independently from operational-region naming.

    The class is voted across all three members.  A start/end region pair is
    retained only when all three members unanimously classify coherent movement
    and independently return the same non-null operational-region pair.
    """
    votes = _precipitation_motion_vote_counts(member_results)
    if not votes:
        return _empty_precipitation_motion(enabled=False), "not_applicable", None, []
    if len(votes) == 1 and votes[0]["classification"] == "not_applicable":
        return _empty_precipitation_motion(enabled=False), "not_applicable", None, votes

    max_count = max(int(row["count"]) for row in votes)
    winners = [row for row in votes if int(row["count"]) == max_count]
    if max_count < 2 or len(winners) != 1:
        return _empty_precipitation_motion(enabled=True), "unresolved", None, votes

    classification = str(winners[0]["classification"])
    selected = next(
        index
        for index, result in enumerate(member_results, start=1)
        if isinstance(result.get("precipitation_motion"), dict)
        and result["precipitation_motion"].get("classification") == classification
    )
    agreement = "unanimous" if max_count == ENSEMBLE_MEMBER_COUNT else "majority"
    winner_motions = [
        result["precipitation_motion"]
        for result in member_results
        if isinstance(result.get("precipitation_motion"), dict)
        and result["precipitation_motion"].get("classification") == classification
    ]

    if classification == "indeterminate":
        return _empty_precipitation_motion(enabled=True), agreement, selected, votes
    if classification == "not_applicable":
        return _empty_precipitation_motion(enabled=False), agreement, selected, votes

    confidence = (
        "high"
        if winner_motions and all(item.get("confidence") == "high" for item in winner_motions)
        else "medium"
    )
    consensus = {
        "classification": classification,
        "origin_region_id": None,
        "destination_region_id": None,
        "confidence": confidence,
    }
    if classification == "coherent_movement" and agreement == "unanimous":
        pairs = [
            (item.get("origin_region_id"), item.get("destination_region_id"))
            for item in winner_motions
        ]
        if (
            len(pairs) == ENSEMBLE_MEMBER_COUNT
            and all(
                isinstance(origin, str)
                and origin
                and isinstance(destination, str)
                and destination
                and origin != destination
                for origin, destination in pairs
            )
            and len(set(pairs)) == 1
        ):
            consensus["origin_region_id"], consensus["destination_region_id"] = pairs[0]
    return consensus, agreement, selected, votes


def step07_minority_report_path(root: Path, init: str) -> Path:
    return runtime_path(root, "daio", init, "step07", "llm_wiki_minority_report.json")


def _assessment_vote_counts(member_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for result in member_results:
        assessment = str(result.get("assessment") or "")
        counts[assessment] = counts.get(assessment, 0) + 1
    return [
        {"assessment": assessment, "count": counts[assessment]}
        for assessment in sorted(counts)
    ]


def _localization_key(localization: Any) -> tuple[Any, ...] | None:
    if not isinstance(localization, dict) or localization.get("status") != "localized":
        return None
    hour = localization.get("forecast_hour")
    if not isinstance(hour, int) or isinstance(hour, bool):
        return None
    regions = localization.get("regions")
    if not isinstance(regions, list) or not regions:
        return None
    rows: list[tuple[str, str]] = []
    for region in regions:
        if not isinstance(region, dict):
            return None
        region_id = region.get("region_id")
        sector = region.get("relative_sector")
        if not isinstance(region_id, str) or not region_id or not isinstance(sector, str) or not sector:
            return None
        rows.append((region_id, sector))
    return (hour, tuple(sorted(rows)))


def _consensus_spatial_localization(
    member_results: list[dict[str, Any]],
    *,
    consensus_assessment: str,
) -> tuple[dict[str, Any], str]:
    """Build a conservative location consensus from the observed members.

    Assessment and localization are separate ensemble axes.  For an observed
    Check, forecast hour and operational region are voted independently so that
    {A, B}, {A}, {C} preserves the real 2/3 agreement on A instead of treating
    the three complete region sets as wholly different locations.
    """
    eligible = [result for result in member_results if result.get("assessment") == consensus_assessment]
    if consensus_assessment != "observed":
        enabled = any(
            isinstance(result.get("spatial_localization"), dict)
            and result["spatial_localization"].get("status") != "not_applicable"
            for result in member_results
        )
        return _empty_spatial_localization(enabled=enabled), "not_applicable"

    localized_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    statuses: list[str] = []
    for result in eligible:
        localization = result.get("spatial_localization")
        if not isinstance(localization, dict):
            continue
        status = str(localization.get("status") or "")
        statuses.append(status)
        if _localization_key(localization) is not None:
            localized_rows.append((result, localization))

    if not localized_rows:
        enabled = any(status != "not_applicable" for status in statuses)
        if statuses and len(statuses) == len(eligible) and len(set(statuses)) == 1:
            status = statuses[0]
            if status == "indeterminate":
                return _empty_spatial_localization(enabled=True), "unanimous"
            if status == "not_applicable":
                return _empty_spatial_localization(enabled=False), "not_applicable"
        return _empty_spatial_localization(enabled=enabled), "unresolved" if enabled else "not_applicable"

    hour_votes: dict[int, int] = {}
    for _, localization in localized_rows:
        hour = int(localization["forecast_hour"])
        hour_votes[hour] = hour_votes.get(hour, 0) + 1
    winning_hour, winning_hour_count = max(hour_votes.items(), key=lambda item: (item[1], -item[0]))
    if winning_hour_count < 2:
        return _empty_spatial_localization(enabled=True), "unresolved"

    hour_rows = [
        (result, localization)
        for result, localization in localized_rows
        if localization.get("forecast_hour") == winning_hour
    ]
    region_votes: dict[str, list[dict[str, Any]]] = {}
    for _, localization in hour_rows:
        for region in localization.get("regions", []):
            if not isinstance(region, dict):
                continue
            region_id = str(region.get("region_id") or "")
            if region_id:
                region_votes.setdefault(region_id, []).append(region)

    majority_region_ids = sorted(
        region_id for region_id, rows in region_votes.items() if len(rows) >= 2
    )
    if not majority_region_ids:
        return _empty_spatial_localization(enabled=True), "unresolved"

    consensus_regions: list[dict[str, str]] = []
    for region_id in majority_region_ids:
        rows = region_votes[region_id]
        sector_counts: dict[str, int] = {}
        for row in rows:
            sector = str(row.get("relative_sector") or "")
            if sector:
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
        sector = "unspecified"
        if sector_counts:
            best_sector, best_sector_count = max(
                sector_counts.items(), key=lambda item: (item[1], item[0])
            )
            if best_sector_count >= 2:
                sector = best_sector
        consensus_regions.append({
            "region_id": region_id,
            "relative_sector": sector,
        })

    consensus_region_key = tuple(
        sorted((row["region_id"], row["relative_sector"]) for row in consensus_regions)
    )
    member_region_keys = []
    confidences = []
    for _, localization in hour_rows:
        member_region_keys.append(
            tuple(sorted(
                (str(row.get("region_id") or ""), str(row.get("relative_sector") or ""))
                for row in localization.get("regions", [])
                if isinstance(row, dict)
            ))
        )
        confidences.append(str(localization.get("confidence") or ""))

    unanimous = (
        len(hour_rows) == len(eligible)
        and all(key == consensus_region_key for key in member_region_keys)
    )
    confidence = "high" if unanimous and confidences and all(value == "high" for value in confidences) else "medium"
    return {
        "status": "localized",
        "forecast_hour": winning_hour,
        "confidence": confidence,
        "regions": consensus_regions,
    }, "unanimous" if unanimous else "majority"


def _consensus_localization_member_index(
    member_results: list[dict[str, Any]],
    *,
    consensus_assessment: str,
    localization: dict[str, Any],
) -> int | None:
    """Return a member whose validated localization exactly matches consensus.

    The ensemble may vote assessment and localization on separate axes.  A
    final localized result must nevertheless inherit its prose, uncertainties,
    and evidence bookkeeping from a member that actually authored the same
    localization.  Otherwise a non-localized assessment member can be copied
    and then have only its structured location overwritten, leaving stale
    location uncertainty or contradictory prose in the final result.

    When per-region voting synthesizes a localization that no single member
    authored exactly, return ``None`` so the caller can fail closed to an
    indeterminate localization rather than publish a synthetic location with no
    matching validated narrative.
    """
    consensus_key = _localization_key(localization)
    if consensus_key is None:
        return None
    for index, result in enumerate(member_results, start=1):
        if result.get("assessment") != consensus_assessment:
            continue
        if _localization_key(result.get("spatial_localization")) == consensus_key:
            return index
    return None


def guard_unresolved_localization_narrative(result: dict[str, Any]) -> None:
    """Remove selected-member location claims when ensemble location has no consensus.

    The assessment vote and the localization vote are independent.  When the
    former succeeds but the latter does not, copying the selected member's
    region-specific finding would turn a minority location into a public
    consensus claim.  Preserve the evidence references, but replace that prose
    with an explicit location-unresolved statement.  Per-member locations remain
    available in the minority report.
    """
    assessment = str(result.get("assessment") or "")
    if assessment not in {"observed", "partially_observed"}:
        return

    evidence_refs: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    for finding in result.get("findings", []):
        if not isinstance(finding, dict):
            continue
        for ref in finding.get("evidence_refs", []):
            if not isinstance(ref, dict):
                continue
            key = json.dumps(ref, ensure_ascii=False, sort_keys=True)
            if key in seen_refs:
                continue
            seen_refs.add(key)
            evidence_refs.append(copy.deepcopy(ref))

    state = "확인되었으나" if assessment == "observed" else "부분적으로 확인되었으나"
    result["findings"] = [{
        "statement": (
            f"요청한 다변수 공간 결합 자체는 {state} "
            "운영권역 단위 결합 위치는 독립 판독 간 합의가 성립하지 않았습니다."
        ),
        "evidence_refs": evidence_refs,
    }]
    # A selected member's location-specific contradiction is equally unsafe to
    # promote as consensus prose.  The full member narratives remain in the
    # minority report.
    result["contradictory_findings"] = []
    uncertainties = [
        str(value).strip()
        for value in result.get("uncertainties", [])
        if isinstance(value, str) and str(value).strip()
    ]
    location_uncertainty = "독립 판독 3회에서 운영권역 단위 결합 위치 합의가 성립하지 않았습니다."
    if location_uncertainty not in uncertainties:
        uncertainties.append(location_uncertainty)
    result["uncertainties"] = uncertainties


def normalize_step07_result_for_synthesis(result: dict[str, Any]) -> dict[str, Any]:
    """Return the publication/synthesis view of one Step07 result.

    Historical Step07 products may contain a selected-member region narrative
    even when ensemble localization_agreement is unresolved.  Step08 and Step09
    must consume the exact same normalized view so provenance fingerprints and
    deterministic validation remain symmetric.  The stored Step07 file and the
    minority report are not modified.
    """
    normalized = copy.deepcopy(result)
    consensus = normalized.get("ensemble_consensus")
    if isinstance(consensus, dict) and consensus.get("localization_agreement") == "unresolved":
        guard_unresolved_localization_narrative(normalized)
    return normalized


def _minority_member_report(member_index: int, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "member_index": member_index,
        "assessment": result.get("assessment"),
        "scope_coverage": result.get("scope_coverage"),
        "spatial_localization": copy.deepcopy(result.get("spatial_localization") or {}),
        "findings": copy.deepcopy(result.get("findings") or []),
        "contradictory_findings": copy.deepcopy(result.get("contradictory_findings") or []),
        "uncertainties": copy.deepcopy(result.get("uncertainties") or []),
    }


def build_step07_ensemble_consensus(
    member_results: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return one conservative 3-member consensus and an optional minority record.

    The model-authored structured assessment, spatial localization, and precipitation-motion
    classification are voted. Quantitative relations, evidence availability, and source integrity remain governed
    by the existing deterministic validators for every member before this function runs.
    """
    if len(member_results) != ENSEMBLE_MEMBER_COUNT:
        raise Pass2PipelineError(
            "STEP07_ENSEMBLE_INVALID",
            f"Step07 ensemble requires exactly {ENSEMBLE_MEMBER_COUNT} validated members.",
        )
    identities = {
        (str(row.get("candidate_id") or ""), str(row.get("check_id") or ""), str(row.get("input_fingerprint") or ""))
        for row in member_results
    }
    if len(identities) != 1:
        raise Pass2PipelineError("STEP07_ENSEMBLE_INVALID", "Step07 ensemble members do not share one Check identity/fingerprint.")

    vote_counts = _assessment_vote_counts(member_results)
    max_count = max(int(row["count"]) for row in vote_counts)
    winners = [str(row["assessment"]) for row in vote_counts if int(row["count"]) == max_count]
    if max_count >= 2 and len(winners) == 1:
        consensus_assessment = winners[0]
        assessment_agreement = "unanimous" if max_count == ENSEMBLE_MEMBER_COUNT else "majority"
        assessment_selected_member_index = next(
            index for index, row in enumerate(member_results, start=1)
            if row.get("assessment") == consensus_assessment
        )
        localization, localization_agreement = _consensus_spatial_localization(
            member_results, consensus_assessment=consensus_assessment
        )
        localization_selected_member_index = None
        if isinstance(localization, dict) and localization.get("status") == "localized":
            localization_selected_member_index = _consensus_localization_member_index(
                member_results,
                consensus_assessment=consensus_assessment,
                localization=localization,
            )
            if localization_selected_member_index is None:
                # Per-region voting can create a synthetic subset that no single
                # member authored exactly (for example {A,B}, {A,C}, {A,D} -> A).
                # Such a location has no matching fully validated narrative, so
                # preserve the assessment consensus but fail closed on location.
                localization = _empty_spatial_localization(enabled=True)
                localization_agreement = "unresolved"

        selected_member_index = (
            localization_selected_member_index
            if localization_selected_member_index is not None
            else assessment_selected_member_index
        )
        result = copy.deepcopy(member_results[selected_member_index - 1])
        result["assessment"] = consensus_assessment
        result["spatial_localization"] = localization
        if localization_agreement == "unresolved":
            guard_unresolved_localization_narrative(result)
        disagreement_dimensions: list[str] = []
        if assessment_agreement != "unanimous":
            disagreement_dimensions.append("assessment")
        localized_keys = [_localization_key(row.get("spatial_localization")) for row in member_results]
        applicable_localized_keys = [key for key in localized_keys if key is not None]
        if localization_agreement in {"majority", "unresolved"} or len(set(applicable_localized_keys)) > 1:
            disagreement_dimensions.append("localization")
        disagreement_dimensions = list(dict.fromkeys(disagreement_dimensions))
        disagreement_severity = "none" if not disagreement_dimensions else "minor"
        minority_indices = [
            index for index, row in enumerate(member_results, start=1)
            if row.get("assessment") != consensus_assessment
        ]
        if "localization" in disagreement_dimensions:
            consensus_key = _localization_key(localization)
            localization_minority_indices = [
                index for index, row in enumerate(member_results, start=1)
                if row.get("assessment") == consensus_assessment
                and (consensus_key is None or _localization_key(row.get("spatial_localization")) != consensus_key)
            ]
            minority_indices = sorted(set(minority_indices) | set(localization_minority_indices))
    else:
        consensus_assessment = "uncertain"
        assessment_agreement = "unresolved"
        selected_member_index = None
        result = copy.deepcopy(member_results[0])
        result["assessment"] = "uncertain"
        result["scope_coverage"] = "indeterminate"
        localization_enabled = any(
            isinstance(row.get("spatial_localization"), dict)
            and row["spatial_localization"].get("status") != "not_applicable"
            for row in member_results
        )
        result["spatial_localization"] = _empty_spatial_localization(enabled=localization_enabled)
        result["findings"] = []
        result["contradictory_findings"] = []
        result["quantitative_claims"] = []
        result["quantitative_relations"] = []
        result["quantitative_consistency_status"] = "not_applicable"
        result["quantitative_consistency_failure_reasons"] = []
        result["quantitative_consistency_failure_details"] = []
        result["usable_for_synthesis"] = False
        result["excluded_from_synthesis_reasons"] = ["ENSEMBLE_NO_CONSENSUS"]
        result["uncertainties"] = ["독립 판독 3회에서 구조화 판정 합의가 성립하지 않았습니다."]
        localization_agreement = "unresolved" if localization_enabled else "not_applicable"
        disagreement_dimensions = ["assessment"]
        if localization_enabled:
            disagreement_dimensions.append("localization")
        disagreement_severity = "material"
        minority_indices = [1, 2, 3]

    consensus_motion, motion_agreement, motion_selected_member_index, motion_vote_counts = (
        _consensus_precipitation_motion(member_results)
    )
    result["precipitation_motion"] = consensus_motion
    result["ensemble_consensus"] = {
        "contract_version": ENSEMBLE_CONTRACT_VERSION,
        "mode": "llm_ensemble",
        "member_count": ENSEMBLE_MEMBER_COUNT,
        "assessment_agreement": assessment_agreement,
        "consensus_assessment": consensus_assessment,
        "selected_member_index": selected_member_index,
        "vote_counts": vote_counts,
        "localization_agreement": localization_agreement,
        "precipitation_motion_agreement": motion_agreement,
        "consensus_precipitation_motion_classification": consensus_motion.get("classification"),
        "precipitation_motion_selected_member_index": motion_selected_member_index,
        "precipitation_motion_vote_counts": motion_vote_counts,
        "disagreement_severity": disagreement_severity,
        "disagreement_dimensions": disagreement_dimensions,
    }

    report_entry = None
    if disagreement_dimensions:
        candidate_id = str(result.get("candidate_id") or "")
        check_id = str(result.get("check_id") or "")
        report_entry = {
            "candidate_id": candidate_id,
            "check_id": check_id,
            "consensus_assessment": consensus_assessment,
            "assessment_agreement": assessment_agreement,
            "selected_member_index": selected_member_index,
            "vote_counts": copy.deepcopy(vote_counts),
            "localization_agreement": localization_agreement,
            "disagreement_severity": disagreement_severity,
            "disagreement_dimensions": copy.deepcopy(disagreement_dimensions),
            "minority_member_indices": minority_indices,
            "member_reports": [
                _minority_member_report(index, member_results[index - 1])
                for index in minority_indices
            ],
        }
    return result, report_entry


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


def _system_direct_precipitation_anchor_context(
    group: dict[str, Any],
    check: dict[str, Any],
    specification: dict[str, Any],
    entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Expose only deterministic scope metadata for the SYSTEM presence Check."""
    if not _is_system_direct_precipitation_anchor_check(specification):
        return None
    candidate_context = group.get("candidate_context")
    candidate = candidate_context.get("candidate") if isinstance(candidate_context, dict) else None
    if not isinstance(candidate, dict):
        raise Pass2PipelineError(
            "STEP06_PACKAGE_INVALID",
            f"{group.get('candidate_id')} SYSTEM anchor has no candidate context.",
        )
    peak_hours = sorted(
        int(value)
        for value in candidate.get("peak_forecast_hours", [])
        if isinstance(value, int) and not isinstance(value, bool)
    )
    candidate_area = candidate.get("candidate_area")
    selected_domains: list[Any] = []
    threshold_by_hour: dict[int, dict[str, Any]] = {}
    for entry in entries:
        request = entry.get("request") if isinstance(entry, dict) else None
        step05 = entry.get("step05") if isinstance(entry, dict) else None
        if not isinstance(request, dict):
            continue
        result = step05.get("result") if isinstance(step05, dict) else None
        selected_domains.append(
            result.get("selected_domain") if isinstance(result, dict) else request.get("requested_domain")
        )
        generated_images = result.get("generated_images") if isinstance(result, dict) else None
        if isinstance(generated_images, list):
            for image in generated_images:
                if not isinstance(image, dict) or image.get("physical_diagnostic") != "QL_PRECIP_6H":
                    continue
                hour = image.get("forecast_hour")
                if not isinstance(hour, int) or isinstance(hour, bool) or hour not in peak_hours:
                    continue
                scope_summary = image.get("operational_scope_field_value_summary")
                region_summaries = image.get("operational_scope_region_summaries")
                if not isinstance(scope_summary, dict) or not isinstance(region_summaries, list):
                    raise Pass2PipelineError(
                        "DIRECT_PRECIPITATION_SCOPE_METADATA_MISSING",
                        f"{group.get('candidate_id')} F{hour:03d} SYSTEM anchor requires Step05 operational-scope metadata; rerun Step05.",
                    )
                maximum = scope_summary.get("maximum")
                maximum_value = float(maximum) if isinstance(maximum, (int, float)) and not isinstance(maximum, bool) else None
                normalized_regions: list[dict[str, Any]] = []
                for region in region_summaries:
                    if not isinstance(region, dict):
                        continue
                    region_id = str(region.get("region_id") or "")
                    region_name = str(region.get("region_name") or "")
                    region_max = region.get("maximum_6h_mm")
                    if not region_id or not region_name or not isinstance(region_max, (int, float)) or isinstance(region_max, bool):
                        continue
                    region_max_value = float(region_max)
                    if region_max_value >= DIRECT_PRECIPITATION_MINIMUM_MM_6H:
                        normalized_regions.append({
                            "region_id": region_id,
                            "region_name": region_name,
                            "maximum_6h_mm": region_max_value,
                        })
                threshold_by_hour[hour] = {
                    "maximum_6h_mm": maximum_value,
                    "meets_minimum": bool(maximum_value is not None and maximum_value >= DIRECT_PRECIPITATION_MINIMUM_MM_6H),
                    "threshold_met_regions": normalized_regions,
                }
    selected_domain_is_candidate_area = bool(selected_domains) and all(
        _bbox_signature(value) == _bbox_signature(candidate_area)
        for value in selected_domains
    )
    threshold_hours = sorted(hour for hour, row in threshold_by_hour.items() if row["meets_minimum"])
    return {
        "direct_precipitation_anchor": True,
        "candidate_area": candidate_area,
        "threshold_evaluation_scope": "candidate_intersection_operational_forecast_regions",
        "threshold_evaluation_scope_ko": "후보영역과 겹치는 운영 예보권역(육상·해상)",
        "peak_forecast_hours": peak_hours,
        "selected_domain_is_candidate_area": selected_domain_is_candidate_area,
        "presence_scope_quantifier": "at_least_one",
        "minimum_6h_precipitation_mm": DIRECT_PRECIPITATION_MINIMUM_MM_6H,
        "threshold_evaluation_by_forecast_hour": {
            f"F{hour:03d}": {
                "maximum_6h_mm": threshold_by_hour[hour]["maximum_6h_mm"],
                "meets_minimum": threshold_by_hour[hour]["meets_minimum"],
            }
            for hour in sorted(threshold_by_hour)
        },
        "threshold_met_regions_by_forecast_hour": {
            f"F{hour:03d}": threshold_by_hour[hour].get("threshold_met_regions", [])
            for hour in sorted(threshold_by_hour)
        },
        "threshold_met_forecast_hours": threshold_hours,
        "threshold_met": bool(threshold_hours),
    }


def _public_intent_context(specification: dict[str, Any]) -> dict[str, Any]:
    """Return only Check-local intent needed for an independent Step07 reading.

    Deliberately omit Step04 evidence_role/coverage_roles and hypotheses so the
    VLM understands WHAT/HOW/WHERE/WHEN/WHY without being told whether the Check
    was intended as supporting or opposing evidence.
    """
    intent = specification.get("intent_contract")
    intent = intent if isinstance(intent, dict) else {}
    capability = specification.get("capability_profile")
    capability = capability if isinstance(capability, dict) else {}
    return {
        "verification_mode": intent.get("verification_mode"),
        "domain_scope": intent.get("domain_scope"),
        "spatial_relation_scope": intent.get("spatial_relation_scope", "not_applicable"),
        "phase_ids": intent.get("phase_ids", []),
        "representative_spatial_hours": intent.get("representative_spatial_hours", []),
        "purpose": intent.get("purpose"),
        "source_intent_hash": specification.get("intent_hash"),
        "atomic_claims": copy.deepcopy(
            specification.get("atomic_claim_contract", {}).get("canonical_claims", [])
        ) if isinstance(specification.get("atomic_claim_contract"), dict) else [],
        "capability": {
            "execution_product_id": capability.get("execution_product_id"),
            "signal_components": capability.get("signal_components", []),
            "observable_ids": capability.get("observable_ids", []),
        },
    }


def step07_output_path(root: Path, init: str) -> Path:
    return runtime_path(root, "daio", init, "step07", "pass2_check_results.json")


def load_step06_package(root: Path, init: str) -> dict[str, Any]:
    path = runtime_path(root, "daio", init, "step06", "pass2_input_package.json")
    package = load_json_object(path, code="STEP06_PACKAGE_MISSING")
    try:
        validate_pass2_package(root, package)
    except Pass2PackageError as exc:
        raise Pass2PipelineError(exc.code, str(exc)) from exc
    return package


def load_step07_results(root: Path, init: str) -> dict[str, Any]:
    """Load the single validated Step07 checkpoint for Step08/09 consumers."""
    value = load_json_object(step07_output_path(root, init), code="STEP07_RESULTS_MISSING")
    validate_schema(root, CHECK_RESULTS_SCHEMA, value)
    return value


def _provider_metadata(config: dict[str, str], prompt_hash: str, schema_hash: str) -> dict[str, str]:
    return {
        "provider": config["provider"],
        "model": config["model"],
        "reasoning_effort": config["reasoning_effort"],
        "image_detail": config["image_detail"],
        "prompt_sha256": prompt_hash,
        "schema_sha256": schema_hash,
    }


def _entry_index(group: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = group.get("analysis_data_requests")
    if not isinstance(entries, list):
        raise Pass2PipelineError("STEP06_PACKAGE_INVALID", "candidate has no analysis_data_requests.")
    return {str(entry["request"]["request_id"]): entry for entry in entries if isinstance(entry, dict) and isinstance(entry.get("request"), dict)}


def _check_specifications(group: dict[str, Any]) -> dict[str, dict[str, Any]]:
    context = group.get("candidate_context")
    plan = context.get("analysis_plan") if isinstance(context, dict) else None
    values = plan.get("variable_checks") if isinstance(plan, dict) else None
    if not isinstance(values, list):
        raise Pass2PipelineError("VARIABLE_CHECKS_INVALID", f"{group.get('candidate_id')} has no variable_checks.")
    return {
        str(item["check_id"]): item
        for item in values
        if isinstance(item, dict) and isinstance(item.get("check_id"), str) and item["check_id"]
    }


def requires_temporal_precipitation_increment_claim(check_specification: dict[str, Any]) -> bool:
    """Identify temporal precipitation intensity checks from Step04 metadata only."""
    methods = check_specification.get("analysis_methods")
    method_set = {item for item in methods if isinstance(item, str)} if isinstance(methods, list) else set()
    temporal_scope = (
        check_specification.get("check_scope") == "temporal_evolution"
        or "temporal_evolution" in method_set
    )
    return (
        check_specification.get("product_id") == "QL_PRECIP_6H"
        and check_specification.get("diagnostic_id") in _TEMPORAL_PRECIPITATION_DIAGNOSTICS
        and check_specification.get("variable_role") in _TEMPORAL_PRECIPITATION_ROLES
        and temporal_scope
        and bool(method_set & _TEMPORAL_PRECIPITATION_SPATIAL_METHODS)
    )


def _check_fingerprint(
    root: Path, group: dict[str, Any],
    check: dict[str, Any], check_specification: dict[str, Any], entries: list[dict[str, Any]],
    config: dict[str, str], prompt_hash: str, schema_hash: str,
    *, include_majority_narrative_contract: bool = True,
) -> str:
    request_state: list[dict[str, Any]] = []
    for entry in entries:
        step05 = entry["step05"]
        request_state.append({
            "request": entry["request"],
            "step05_result": step05.get("result"),
            "step05_status": step05.get("status"),
            "images": [
                {"path": image.get("path"), "sha256": image.get("sha256")}
                for image in step05.get("generated_images", [])
            ],
        })
    fingerprint_payload = {
        "spatial_scope_context": _spatial_scope_context(group, check, entries),
        "operational_region_naming_context": _candidate_operational_region_naming_context(root, group),
        "spatial_localization_context": _candidate_operational_localization_context(root, group, check_specification),
        "precipitation_motion_context": _precipitation_motion_context(root, group, check_specification),
        "check_spec": {
            **{key: check.get(key) for key in (
                "check_id", "diagnostic_id", "expected_signal", "decision_relevance",
                "request_ids", "available_request_ids", "missing_request_ids", "evidence_complete",
                "check_scope", "required_signal_components", "available_signal_components",
                "missing_signal_components", "allowed_quantitative_forecast_hours",
                "source_intent_hash", "intent_contract_version", "source_atomic_claim_hash",
                "atomic_claim_contract_version", "atomic_claim_validation_status",
            )},
            **{key: check_specification.get(key) for key in (
                "product_id", "diagnostic_id", "variable_role", "analysis_methods",
                "check_scope", "required_signal_components",
            )},
        },
        "quantitative_validation_contract_version": QUANTITATIVE_VALIDATION_CONTRACT_VERSION,
        "resolved_requests": request_state,
        "prompt_sha256": prompt_hash,
        "output_schema_sha256": schema_hash,
        "provider": config,
        "ensemble_contract_version": ENSEMBLE_CONTRACT_VERSION,
        "ensemble_result_selection_version": ENSEMBLE_RESULT_SELECTION_VERSION,
        "ensemble_member_count": ENSEMBLE_MEMBER_COUNT,
    }
    # Only re-evaluate affected specific checks; unrelated completed results
    # keep their original fingerprint and can be resumed unchanged.
    if _single_specific_quantitative_atomic_claim(check_specification) is not None:
        fingerprint_payload["specific_selector_guidance_version"] = SPECIFIC_SELECTOR_GUIDANCE_VERSION
    if _single_majority_convergence_not_strengthen_claim(check_specification) is not None:
        # Re-evaluate only Checks whose post-model quantitative binding changed.
        fingerprint_payload["majority_quantitative_binding_version"] = MAJORITY_QUANTITATIVE_BINDING_VERSION
        if include_majority_narrative_contract:
            fingerprint_payload["majority_narrative_contract_version"] = MAJORITY_NARRATIVE_CONTRACT_VERSION
    anchor_context = _system_direct_precipitation_anchor_context(
        group, check, check_specification, entries
    )
    if anchor_context is not None:
        fingerprint_payload["system_direct_precipitation_anchor_context"] = anchor_context
    return canonical_hash(fingerprint_payload)



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


def _allowed_evidence_reference_rows(check: dict[str, Any], entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Serialize the exact response-namespace references that a Check may cite."""
    rows: list[dict[str, Any]] = []
    for request_id, evidence_type, namespace_value, forecast_hour in sorted(
        _allowed_refs(check, entries), key=lambda item: (item[0], item[1], item[2] or "", item[3] if item[3] is not None else -1)
    ):
        rows.append({
            "request_id": request_id,
            "evidence_type": evidence_type,
            "physical_diagnostic": namespace_value if evidence_type == "image" else None,
            "feature_id": namespace_value if evidence_type == "quantitative" else None,
            "forecast_hour": forecast_hour,
        })
    return rows


def _allowed_quantitative_selector_rows(check: dict[str, Any], entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the exact Step05 selector namespace allowed for this Check."""
    available_request_ids = {str(value) for value in check.get("available_request_ids", [])}
    allowed_hours_raw = check.get("allowed_quantitative_forecast_hours")
    allowed_hours = (
        {
            value
            for value in allowed_hours_raw
            if isinstance(value, int) and not isinstance(value, bool)
        }
        if isinstance(allowed_hours_raw, list) and allowed_hours_raw
        else None
    )
    rows: list[dict[str, Any]] = []
    for entry in entries:
        request = entry.get("request")
        step05 = entry.get("step05")
        if not isinstance(request, dict) or not isinstance(step05, dict):
            continue
        request_id = request.get("request_id")
        if (
            not isinstance(request_id, str)
            or request_id not in available_request_ids
            or request.get("data_type") != "quantitative_excerpt"
            or step05.get("status") != "success"
        ):
            continue
        result = step05.get("result")
        features = result.get("features") if isinstance(result, dict) else None
        if not isinstance(features, dict):
            continue
        for feature_id, feature in features.items():
            hourly = feature.get("per_forecast_hour") if isinstance(feature, dict) else None
            if not isinstance(feature_id, str) or not feature_id or not isinstance(hourly, list):
                continue
            for hour_row in hourly:
                forecast_hour = hour_row.get("forecast_hour") if isinstance(hour_row, dict) else None
                regional_statistics = hour_row.get("regional_statistics") if isinstance(hour_row, dict) else None
                if not isinstance(forecast_hour, int) or isinstance(forecast_hour, bool) or not isinstance(regional_statistics, list):
                    continue
                if allowed_hours is not None and forecast_hour not in allowed_hours:
                    continue
                for region in regional_statistics:
                    region_id = region.get("region_id") if isinstance(region, dict) else None
                    statistics = region.get("statistics") if isinstance(region, dict) else None
                    allowed_statistics = sorted(
                        name for name in QUANTITATIVE_STATISTICS
                        if isinstance(statistics, dict) and isinstance(statistics.get(name), Real) and not isinstance(statistics.get(name), bool)
                    )
                    if isinstance(region_id, str) and region_id and allowed_statistics:
                        rows.append({
                            "request_id": request_id,
                            "feature_id": feature_id,
                            "forecast_hour": forecast_hour,
                            "region_id": region_id,
                            "statistics": allowed_statistics,
                        })
    return sorted(rows, key=lambda item: (item["request_id"], item["feature_id"], item["forecast_hour"], item["region_id"]))


def _required_specific_quantitative_selectors(
    specification: dict[str, Any], check: dict[str, Any], entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Offer source-backed *identities*, never values/directions, for one specific Check.

    A single request must supply all required forecast hours. This avoids
    combining unrelated request series. Other Check classes keep the full
    original selector namespace. If the required cells are absent we explicitly
    record that fact; we do not fabricate selectors or silently change region.
    """
    claim = _single_specific_quantitative_atomic_claim(specification)
    if claim is None:
        return None
    scope = claim["quantitative_scope"]
    target_hours = (
        list(scope["comparison_forecast_hours"])
        if claim["expected_state"] in {"greater_than", "less_than"}
        else list(scope["forecast_hours"])
    )
    namespace = _allowed_quantitative_selector_rows(check, entries)
    request_order = [str(req) for req in check.get("available_request_ids", [])]
    valid_by_request: dict[str, set[int]] = {}
    for row in namespace:
        if (
            row["feature_id"] == scope["feature_id"]
            and row["region_id"] == scope["region_ids"][0]
            and scope["statistic"] in row["statistics"]
            and row["forecast_hour"] in target_hours
        ):
            valid_by_request.setdefault(row["request_id"], set()).add(row["forecast_hour"])
    matches = [req for req in request_order if set(target_hours).issubset(valid_by_request.get(req, set()))]
    source = matches[0] if matches else None
    selectors = [{
        "request_id": source,
        "feature_id": scope["feature_id"],
        "forecast_hour": hour,
        "region_id": scope["region_ids"][0],
        "statistic": scope["statistic"],
    } for hour in target_hours] if source else []
    return {
        "status": "available" if source else "missing_source_cells",
        "required_quantitative_selectors": selectors,
        "required_identity": {
            "feature_id": scope["feature_id"],
            "region_id": scope["region_ids"][0],
            "forecast_hours": target_hours,
            "allowed_forecast_hours": list(scope["forecast_hours"]),
            "statistic": scope["statistic"],
        },
        "candidate_request_ids": matches,
    }


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


def _allowed_quantitative_feature_ids(entry: dict[str, Any]) -> list[str]:
    """Return the exact Step05 feature keys that an AI selector may use."""
    request = entry.get("request")
    step05 = entry.get("step05")
    if not isinstance(request, dict) or request.get("data_type") != "quantitative_excerpt":
        return []
    if not isinstance(step05, dict) or step05.get("status") != "success":
        return []
    result = step05.get("result")
    features = result.get("features") if isinstance(result, dict) else None
    if not isinstance(features, dict):
        return []
    return sorted(feature_id for feature_id, feature in features.items() if isinstance(feature_id, str) and feature_id and isinstance(feature, dict))


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


def _single_specific_quantitative_atomic_claim(check_specification: dict[str, Any]) -> dict[str, Any] | None:
    """Return the narrow specific quantitative claim class enforced by Step07 v6.

    Legacy 0.2 scopes without feature/comparison identity are intentionally not
    enforced.  They remain readable but cannot trigger false exclusions.
    """
    contract = check_specification.get("atomic_claim_contract")
    claims = contract.get("canonical_claims") if isinstance(contract, dict) else None
    if not isinstance(claims, list) or len(claims) != 1 or not isinstance(claims[0], dict):
        return None
    claim = claims[0]
    if claim.get("scope_quantifier") != "specific":
        return None
    expected_state = str(claim.get("expected_state") or "")
    if expected_state not in {"greater_than", "less_than", "increase", "decrease", "strengthen", "weaken"}:
        return None
    scope = claim.get("quantitative_scope")
    if not isinstance(scope, dict):
        return None
    feature_id = scope.get("feature_id")
    region_scope = scope.get("region_scope")
    region_ids = scope.get("region_ids")
    hours = scope.get("forecast_hours")
    comparison_hours = scope.get("comparison_forecast_hours")
    statistic = scope.get("statistic")
    if (
        not isinstance(feature_id, str) or not feature_id
        or region_scope != "explicit_regions"
        or not isinstance(region_ids, list) or len(region_ids) != 1
        or not isinstance(region_ids[0], str) or not region_ids[0]
        or not isinstance(hours, list) or len(hours) < 2
        or any(isinstance(hour, bool) or not isinstance(hour, int) or hour < 0 for hour in hours)
        or hours != sorted(set(hours))
        or statistic not in QUANTITATIVE_STATISTICS
    ):
        return None
    if expected_state in {"greater_than", "less_than"}:
        if (
            not isinstance(comparison_hours, list) or len(comparison_hours) != 2
            or any(isinstance(hour, bool) or not isinstance(hour, int) for hour in comparison_hours)
            or len(set(comparison_hours)) != 2
            or any(hour not in hours for hour in comparison_hours)
        ):
            return None
    elif comparison_hours is not None:
        return None
    return claim



def _single_majority_convergence_not_strengthen_claim(
    check_specification: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the one majority physical-state contract currently enforced."""
    contract = check_specification.get("atomic_claim_contract")
    claims = contract.get("canonical_claims") if isinstance(contract, dict) else None
    if not isinstance(claims, list) or len(claims) != 1 or not isinstance(claims[0], dict):
        return None
    claim = claims[0]
    if claim.get("scope_quantifier") != "majority" or claim.get("expected_state") != "not_strengthen":
        return None
    scope = claim.get("quantitative_scope")
    if not isinstance(scope, dict):
        return None
    hours = scope.get("forecast_hours")
    if (
        scope.get("feature_id") != "convergence925_1e5_s"
        or scope.get("region_scope") != "candidate_overlap"
        or scope.get("region_ids") != []
        or not isinstance(hours, list) or len(hours) != 2
        or any(isinstance(hour, bool) or not isinstance(hour, int) or hour < 0 for hour in hours)
        or hours != sorted(set(hours))
        or scope.get("comparison_forecast_hours") is not None
        or scope.get("statistic") != "mean"
    ):
        return None
    return claim



_STRENGTHENING_CONVERGENCE_TRANSITIONS = frozenset({
    "convergence_strengthening",
    "divergence_to_convergence",
    "neutral_to_convergence",
})




def _bind_majority_convergence_quantitative_evidence(
    check_specification: dict[str, Any],
    check: dict[str, Any],
    entries: list[dict[str, Any]],
    assessment: dict[str, Any],
) -> dict[str, int] | None:
    """Bind numeric fields for the exact Step04 convergence-majority contract.

    This runs AFTER the independent model assessment. The hypothesis selects
    the feature, two forecast hours and candidate-overlap region scope; Step05
    selects the actual regions and numbers. The model remains responsible for
    assessment and meteorological prose, which existing validators compare
    with the deterministic evidence. Never repair identifiers by guessing.

    For this exact contract only, numerical claims and pairwise relations are
    *system-generated*, not AI-authored. The result schema is unchanged.
    """
    claim = _single_majority_convergence_not_strengthen_claim(check_specification)
    if claim is None:
        return None
    scope = claim["quantitative_scope"]
    feature_id = str(scope["feature_id"])
    statistic = str(scope["statistic"])
    hours = list(scope["forecast_hours"])
    namespace = _allowed_quantitative_selector_rows(check, entries)
    # Build the union of eligible cells. A full two-hour series must come from
    # one concrete Step05 request; otherwise source identity is ambiguous.
    cells_by_request: dict[str, set[tuple[str, int]]] = {}
    all_regions: set[str] = set()
    for item in namespace:
        if (
            item["feature_id"] != feature_id
            or item["forecast_hour"] not in hours
            or statistic not in item["statistics"]
        ):
            continue
        request_id = str(item["request_id"])
        region_id = str(item["region_id"])
        all_regions.add(region_id)
        cells_by_request.setdefault(request_id, set()).add((region_id, int(item["forecast_hour"])))
    if not all_regions:
        raise Pass2PipelineError(
            "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
            f"{check.get('check_id')} majority contract has no allowed Step05 convergence values.",
        )
    expected_cells = {(region_id, hour) for region_id in all_regions for hour in hours}
    complete_requests = sorted(
        request_id for request_id, cells in cells_by_request.items()
        if expected_cells.issubset(cells)
    )
    if len(complete_requests) != 1:
        raise Pass2PipelineError(
            "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
            f"{check.get('check_id')} majority contract requires exactly one Step05 request "
            f"covering all {len(all_regions)} regions at F{hours[0]:03d}/F{hours[1]:03d}; "
            f"complete_request_ids={complete_requests}.",
        )
    request_id = complete_requests[0]
    selectors = [
        {
            "request_id": request_id,
            "feature_id": feature_id,
            "forecast_hour": hour,
            "region_id": region_id,
            "statistic": statistic,
        }
        for region_id in sorted(all_regions) for hour in hours
    ]
    # Preserve the original strict selector and arithmetic resolvers. No
    # independently obtained values and no trusted AI arithmetic are used.
    points = resolve_quantitative_claims(check, entries, selectors)
    if len(points) != len(selectors):
        raise Pass2PipelineError(
            "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
            f"{check.get('check_id')} Step05 resolved an incomplete majority selector set.",
        )
    relations: list[dict[str, Any]] = []
    for index, region_id in enumerate(sorted(all_regions), 1):
        pair = selectors[(index - 1) * 2:index * 2]
        values = points[(index - 1) * 2:index * 2]
        probe = {
            "relation_id": f"SYS_CONV_{index:03d}",
            "relation_type": "pairwise_comparison",
            "selectors": pair,
            "claimed_relation": "approximately_equal",  # overwritten from Step05
            "claimed_forecast_hour": None,
        }
        try:
            computed = compute_quantitative_relation(probe, values)
        except QuantitativeConsistencyError as exc:
            raise Pass2PipelineError(
                "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
                f"{check.get('check_id')} cannot compute region={region_id}: {exc}",
            ) from exc
        probe["claimed_relation"] = computed["computed_relation"]
        relations.append(probe)
    original_claim_count = len(assessment.get("quantitative_claims", [])) if isinstance(assessment.get("quantitative_claims"), list) else 0
    original_relation_count = len(assessment.get("quantitative_relations", [])) if isinstance(assessment.get("quantitative_relations"), list) else 0
    assessment["quantitative_claims"] = selectors
    assessment["quantitative_relations"] = relations
    return {
        "regions": len(all_regions),
        "source_claims": len(selectors),
        "source_relations": len(relations),
        "model_claims_replaced": original_claim_count,
        "model_relations_replaced": original_relation_count,
    }


def _majority_convergence_aggregation_from_result(
    check_specification: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any] | None:
    """Return deterministic per-region aggregation for the supported majority Check.

    The helper does not compose meteorological prose.  It only converts the
    already-resolved quantitative claims into explicit region counts and
    physical-state transitions so downstream UI and validators do not have to
    trust LLM-authored aggregate numbers.
    """
    claim = _single_majority_convergence_not_strengthen_claim(check_specification)
    if claim is None:
        return None
    scope = claim["quantitative_scope"]
    feature_id = str(scope["feature_id"])
    hours = [int(hour) for hour in scope["forecast_hours"]]
    statistic = str(scope["statistic"])
    claims = result.get("quantitative_claims")
    if not isinstance(claims, list):
        return None

    rows_by_region: dict[str, dict[int, dict[str, Any]]] = {}
    region_names: dict[str, str] = {}
    region_order: list[str] = []
    for point in claims:
        if not isinstance(point, dict):
            continue
        if (
            point.get("physical_diagnostic") != feature_id
            or point.get("forecast_hour") not in hours
            or point.get("statistic") != statistic
        ):
            continue
        region_id = str(point.get("region_id") or "")
        hour = point.get("forecast_hour")
        value = point.get("resolved_value")
        if (
            not region_id
            or isinstance(hour, bool) or not isinstance(hour, int)
            or isinstance(value, bool) or not isinstance(value, Real)
        ):
            continue
        if region_id not in rows_by_region:
            rows_by_region[region_id] = {}
            region_order.append(region_id)
        # Ambiguous duplicate values are rejected elsewhere.  Refuse to build
        # a summary here rather than silently selecting one.
        if hour in rows_by_region[region_id]:
            return None
        rows_by_region[region_id][hour] = point
        region_names[region_id] = str(point.get("region_name") or region_id)

    complete_regions = [
        region_id for region_id in region_order
        if all(hour in rows_by_region.get(region_id, {}) for hour in hours)
    ]
    if not complete_regions:
        return None

    regions: list[dict[str, Any]] = []
    strengthening_regions: list[str] = []
    not_strengthening_regions: list[str] = []
    for region_id in complete_regions:
        points = [rows_by_region[region_id][hour] for hour in hours]
        values = [float(point["resolved_value"]) for point in points]
        transition = compute_physical_transition(feature_id, values)
        strengthening = transition in _STRENGTHENING_CONVERGENCE_TRANSITIONS
        region_name = region_names.get(region_id, region_id)
        (strengthening_regions if strengthening else not_strengthening_regions).append(region_name)
        regions.append({
            "region_id": region_id,
            "region_name": region_name,
            "physical_transition": transition,
            "strengthening": strengthening,
            "values": [
                {
                    "forecast_hour": int(point["forecast_hour"]),
                    "resolved_value": float(point["resolved_value"]),
                    "units": str(point.get("units") or ""),
                }
                for point in points
            ],
        })

    return {
        "contract_version": "candidate-overlap-majority-v1",
        "feature_id": feature_id,
        "statistic": statistic,
        "forecast_hours": hours,
        "total_regions": len(regions),
        "not_strengthening_count": len(not_strengthening_regions),
        "strengthening_count": len(strengthening_regions),
        "not_strengthening_regions": not_strengthening_regions,
        "strengthening_regions": strengthening_regions,
        "regions": regions,
    }


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



def quantitative_failure_uncertainty(reasons: list[str]) -> str:
    """Describe the failure type without conflating missing relations with a mismatch."""
    reason_set = set(reasons)
    if reason_set == {"QUANTITATIVE_RELATION_REQUIRED"}:
        return "필요한 정량 비교 관계가 충분히 검증되지 않아 해당 정량 근거는 종합판단에서 제외됩니다."
    if reason_set == {"QUANTITATIVE_RELATION_MISMATCH"}:
        return "정량자료의 비교 관계와 판독 결과가 일치하지 않아 해당 정량 근거는 종합판단에서 제외됩니다."
    if reason_set == {"QUANTITATIVE_FEATURE_USE_INVALID"}:
        return "시간 변화 강수 판독에 필요한 정량 비교 관계가 없어 해당 정량 근거는 종합판단에서 제외됩니다."
    return "정량 근거의 검증 조건을 충족하지 않아 해당 정량 근거는 종합판단에서 제외됩니다."


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


def _missing_incremental_precipitation_relation_series(
    result: dict[str, Any],
) -> list[tuple[str, str, str, tuple[int, ...]]]:
    return _missing_temporal_quantitative_relation_series(
        result,
        feature_filter="tp_6h_increment_mm",
    )


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


def _insufficient_result(
    candidate_id: str,
    check: dict[str, Any],
    specification: dict[str, Any],
    fingerprint: str,
    metadata: dict[str, str],
) -> dict[str, Any]:
    missing_components = [str(value) for value in check.get("missing_signal_components", [])]
    labels = {
        "direct_precipitation": "강수 분포",
        "moisture_supply": "수분 공급",
        "low_level_forcing": "하층 수렴",
        "mid_upper_forcing": "중층 상승운동",
        "thermodynamic_support": "열역학적 환경",
        "organization_persistence": "바람의 연직구조",
        "synoptic_support": "종관 환경",
        "uncertainty": "판독 보조자료",
    }
    if missing_components:
        missing_text = ", ".join(labels.get(value, value) for value in missing_components)
        reason = f"확인에 필요한 상세 예측자료 중 {missing_text} 자료가 부족하여 해당 특징을 판정하지 않습니다."
    else:
        reason = "사용 가능한 상세 예측자료가 없어 해당 특징을 판정할 수 없습니다."
    return {
        "candidate_id": candidate_id,
        "check_id": check["check_id"],
        "source_intent_hash": check.get("source_intent_hash"),
        "intent_contract_version": check.get("intent_contract_version"),
        "expected_signal": check["expected_signal"],
        "decision_relevance": check["decision_relevance"],
        "request_ids": check["request_ids"],
        "available_request_ids": check["available_request_ids"],
        "missing_request_ids": check["missing_request_ids"],
        "evidence_complete": False,
        "evidence_status": str(check.get("evidence_status") or "none"),
        "input_fingerprint": fingerprint,
        "assessment": "insufficient_evidence",
        "scope_coverage": "indeterminate",
        "spatial_localization": _empty_spatial_localization(
            enabled=_spatial_localization_enabled(check)
        ),
        "precipitation_motion": _empty_precipitation_motion(enabled=False),
        "ensemble_consensus": {
            "contract_version": ENSEMBLE_CONTRACT_VERSION,
            "mode": "deterministic_insufficient_evidence",
            "member_count": 0,
            "assessment_agreement": "not_applicable",
            "consensus_assessment": "insufficient_evidence",
            "selected_member_index": None,
            "vote_counts": [],
            "localization_agreement": "not_applicable",
            "precipitation_motion_agreement": "not_applicable",
            "consensus_precipitation_motion_classification": "not_applicable",
            "precipitation_motion_selected_member_index": None,
            "precipitation_motion_vote_counts": [],
            "disagreement_severity": "none",
            "disagreement_dimensions": [],
        },
        "findings": [],
        "contradictory_findings": [],
        "quantitative_claims": [],
        "quantitative_relations": [],
        "quantitative_consistency_status": "not_applicable",
        "quantitative_consistency_failure_reasons": [],
        "quantitative_consistency_failure_details": [],
        "usable_for_synthesis": False,
        "excluded_from_synthesis_reasons": ["INSUFFICIENT_EVIDENCE"],
        "uncertainties": [reason],
        "provider_metadata": metadata,
    }


def _bundle_messages(
    root: Path, init: str, group: dict[str, Any], checks: list[dict[str, Any]], entries: list[dict[str, Any]], prompt_text: str,
    grounding_retry_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    specifications = _check_specifications(group)
    entries_by_id = {str(entry["request"]["request_id"]): entry for entry in entries}
    check_rows = []
    for check in checks:
        specification = specifications.get(str(check.get("check_id")))
        if specification is None:
            raise Pass2PipelineError("VARIABLE_CHECKS_INVALID", f"{group.get('candidate_id')} has no Step04 definition for {check.get('check_id')}.")
        check_entries = [
            entries_by_id[request_id]
            for request_id in check.get("request_ids", [])
            if request_id in entries_by_id
        ]
        check_row = {
            **{key: check.get(key) for key in ("check_id", "diagnostic_id", "expected_signal", "decision_relevance", "evidence_complete", "evidence_status", "request_ids", "cross_section_context", "time_height_context", "check_scope", "required_signal_components", "available_signal_components", "missing_signal_components", "allowed_quantitative_forecast_hours")},
            **{key: specification.get(key) for key in ("product_id", "variable_role", "analysis_methods")},
            "intent_context": _public_intent_context(specification),
            "spatial_scope_context": _spatial_scope_context(group, check, check_entries),
            "operational_region_naming_context": _candidate_operational_region_naming_context(root, group),
            "spatial_localization_context": _candidate_operational_localization_context(root, group, specification),
            "precipitation_motion_context": _precipitation_motion_context(root, group, specification),
            "temporal_precipitation_primary_feature": requires_temporal_precipitation_increment_claim(specification),
        }
        required_selector_context = _required_specific_quantitative_selectors(
            specification, check, check_entries,
        )
        if required_selector_context is not None:
            check_row["specific_quantitative_selector_context"] = required_selector_context
        anchor_context = _system_direct_precipitation_anchor_context(
            group, check, specification, check_entries,
        )
        if anchor_context is not None:
            check_row["system_direct_precipitation_anchor_context"] = anchor_context
        check_rows.append(check_row)
    allowed_evidence_namespace = [
        {
            "check_id": check["check_id"],
            "allowed_evidence_refs": _allowed_evidence_reference_rows(
                check, [entries_by_id[request_id] for request_id in check["available_request_ids"] if request_id in entries_by_id]
            ),
            "allowed_quantitative_selector_namespace": _allowed_quantitative_selector_rows(
                check, [entries_by_id[request_id] for request_id in check["available_request_ids"] if request_id in entries_by_id]
            ),
        }
        for check in checks
    ]
    signed_feature_semantics = {}
    for namespace in allowed_evidence_namespace:
        for row in namespace.get("allowed_quantitative_selector_namespace", []):
            feature_id = row.get("feature_id") if isinstance(row, dict) else None
            if isinstance(feature_id, str) and feature_id not in signed_feature_semantics:
                semantics = quantitative_feature_semantics(feature_id)
                if semantics is not None:
                    signed_feature_semantics[feature_id] = semantics
    bundle_context = {
        "init": init,
        "candidate_id": group["candidate_id"],
        "checks": check_rows,
        "allowed_evidence_namespace": allowed_evidence_namespace,
        "quantitative_feature_semantics": signed_feature_semantics,
        "instruction": (
            "Evaluate only whether each expected_signal is observed in the supplied Step05 evidence. "
            "expected_signal and decision_relevance are not ground truth. The initial assessment is independent: "
            "no Python-computed comparison result is supplied. If you make a quantitative comparison, trend, or extremum claim, "
            "declare the exact cells in quantitative_claims and the relation in quantitative_relations so Python can verify it afterwards."
        ),
    }
    if any(
        image.get("physical_diagnostic") == "QL_MOISTURE_FLUX850_WIND850"
        for entry in entries
        for image in (entry.get("step05") or {}).get("generated_images", [])
        if isinstance(image, dict)
    ):
        bundle_context["moisture_transport_geometry_instruction"] = (
            "850 hPa 수증기 플럭스 크기 음영(q|V|)는 수송 방향을 나타내지 않으며, 화살표는 실제 850 hPa 바람벡터입니다. "
            "북동향·북향 등의 절대 바람 방향만으로 후보영역으로의 유입 여부를 긍정 또는 부정하지 마십시오. "
            "수증기 플럭스가 큰 공급원의 좌표·분포, 후보영역의 상대 위치와 경계, 바람벡터가 그 사이를 실제로 연결하는지를 영상에서 대조하십시오. "
            "방향과 공간적 연결이 불분명하면 유입 여부는 확인되지 않았다고 기술하되, '북동향이므로 후보로 향하지 않는다'처럼 "
            "방향 하나만을 근거로 부재를 단정하지 마십시오. 정량적인 경계 수증기 유입량은 주어진 자료로 계산하지 마십시오."
        )
    if any("specific_quantitative_selector_context" in row for row in check_rows):
        bundle_context["specific_quantitative_selector_instruction"] = (
            "For each specific Check with status=available, build its deciding quantitative relation "
            "using exactly the selectors in specific_quantitative_selector_context.required_quantitative_selectors. "
            "Declare the same selectors in quantitative_claims; do not substitute another region, feature, "
            "statistic, forecast hour, or request. For status=missing_source_cells, never invent values or "
            "selectors; report missing evidence consistently with the schema. "
            "This context specifies WHAT to compare, not the outcome. Determine the relation and assessment "
            "independently from Step05 evidence. expected_state is a hypothesis, not an answer. "
            "For majority/non-specific Checks, keep the existing full selector namespace."
        )
    if any("system_direct_precipitation_anchor_context" in row for row in check_rows):
        threshold = f"{DIRECT_PRECIPITATION_MINIMUM_MM_6H:g}"
        bundle_context["system_direct_precipitation_anchor_instruction"] = (
            "SYSTEM 직접강수 존재 Anchor는 이동, 지속성, 조직화, 최대강도 시각, 시각 간 강도 비교를 판정하지 않는다. "
            f"모델 강수 예측의 운영상 최소기준은 6시간 강수량 {threshold} mm이다. system_direct_precipitation_anchor_context의 "
            "threshold_evaluation_scope는 후보영역과 겹치는 운영 예보권역(육상·해상)으로 제한된다. "
            "threshold_met=false이면 이미지에 극미량 강수값이 있거나 후보영역의 운영 예보권역 밖에 강수가 있더라도 assessment=not_observed, scope_coverage=complete_expected_scope로 판정한다. "
            "threshold_met=true이면 threshold_met_forecast_hours에 포함된 핵심시각의 상세 6시간 강수 이미지에서 운영 예보권역에 "
            f"{threshold} mm 이상을 나타내는 유색 강수영역이 식별 가능한 공간범위로 확인될 때 assessment=observed, "
            "scope_coverage=complete_expected_scope로 판정한다. 임계값 이상 값이 한 격자 또는 영상 잡음처럼 고립되어 식별 가능한 강수영역으로 "
            "판단하기 어려우면 partially_observed 또는 uncertain을 근거에 맞게 사용한다. 다른 핵심시각에 강수가 약하거나 없다는 이유만으로 "
            "observed를 partially_observed로 낮추지 않는다. selected_domain_is_candidate_area=true이면 제공된 공간영상 전체 범위가 후보영역이므로 "
            "별도 후보경계선이 없다는 이유로 uncertain을 선택하지 않는다. "
            "threshold_met_regions_by_forecast_hour는 운영권역 마스크 안에서 최소기준 이상 값이 존재한 표준 권역명이다. "
            "강수 존재 finding을 작성할 때 영상에서 강수영역이 확인된 시각에는 이 목록의 region_name을 사용해 어느 운영권역인지 문장 안에 직접 명시한다. "
            "이 목록에 없는 지역명을 추정하지 않는다."
        )
    parts: list[dict[str, Any]] = [{"type": "text", "text": json.dumps(bundle_context, ensure_ascii=False)},]
    if grounding_retry_context is not None:
        parts.append({"type": "text", "text": json.dumps(grounding_retry_context, ensure_ascii=False)})
    for entry in entries:
        request = entry["request"]
        step05 = entry["step05"]
        result = step05.get("result")
        parts.append({"type": "text", "text": json.dumps({
            "request_id": request["request_id"], "diagnostic_id": request.get("diagnostic_id"), "data_type": request.get("data_type"),
            "forecast_hours": request.get("forecast_hours"), "selected_domain": (result or {}).get("selected_domain") if isinstance(result, dict) else request.get("requested_domain"),
            "step05_status": step05.get("status"), "quantitative_features": (result or {}).get("features") if isinstance(result, dict) else None,
            "allowed_feature_ids": _allowed_quantitative_feature_ids(entry),
        }, ensure_ascii=False)})
        for image in step05.get("generated_images", []):
            parts.append({"type": "text", "text": json.dumps({
                "image_metadata": {key: image.get(key) for key in ("request_id", "diagnostic_id", "physical_diagnostic", "forecast_hour", "valid_time_utc", "request_forecast_hours", "request_valid_times_utc", "units", "selected_domain")}
            }, ensure_ascii=False)})
            parts.append({"type": "image_url", "image_url": image_data_url(resolve_logical_ref(root, image["path"]))})
    return [{"role": "system", "content": prompt_text}, {"role": "user", "content": parts}]


def _call_bundle(provider: AIProvider, root: Path, messages: list[dict[str, Any]], config: dict[str, str]) -> dict[str, Any]:
    canonical = load_schema(root, CHECK_RESPONSE_SCHEMA)
    schema = build_openai_transport_schema(canonical) if provider.api_family == "responses" else canonical
    response = provider.complete(
        messages=messages, model=config["model"], max_tokens=8000, schema=schema, schema_name="AIFSPass2CheckAssessment",
        structured_output_mode="auto", image_detail=config["image_detail"], reasoning_effort=config["reasoning_effort"],
    )
    content = normalize_openai_transport_json(response.content, canonical) if provider.api_family == "responses" else response.content
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise Pass2PipelineError("PROVIDER_RESULT_INVALID", "Step07 provider response is not JSON.") from exc
    if not isinstance(value, dict):
        raise Pass2PipelineError("PROVIDER_RESULT_INVALID", "Step07 provider response root must be an object.")
    validate_schema(root, CHECK_RESPONSE_SCHEMA, value, code="PROVIDER_RESULT_INVALID")
    return value


def _step07_language_error_check_ids(
    response: dict[str, Any], language_errors: list[str],
) -> list[str]:
    """Return Check IDs whose complete assessment must be regenerated."""
    assessments = response.get("assessments")
    if not isinstance(assessments, list):
        raise Pass2PipelineError("NARRATIVE_REGENERATION_INVALID", "Step07 assessment structure is invalid.")
    targets: list[str] = []
    seen: set[str] = set()
    for error in language_errors:
        path = str(error).split(":", 1)[0].strip()
        match = re.match(r"assessments\[(\d+)\]", path)
        if not match:
            continue
        index = int(match.group(1))
        if index >= len(assessments) or not isinstance(assessments[index], dict):
            continue
        check_id = str(assessments[index].get("check_id") or "")
        if check_id and check_id not in seen:
            seen.add(check_id)
            targets.append(check_id)
    if not targets:
        raise Pass2PipelineError(
            "NARRATIVE_REGENERATION_INVALID",
            "Step07 language error could not be mapped to a complete Check assessment.",
        )
    return targets


def _replace_step07_assessment_unit(
    response: dict[str, Any], check_id: str, replacement: dict[str, Any],
) -> dict[str, Any]:
    """Replace one complete Step07 assessment object; never merge individual fields."""
    if str(replacement.get("check_id") or "") != check_id:
        raise Pass2PipelineError(
            "ASSESSMENT_REGENERATION_INVALID",
            f"Step07 regenerated assessment changed check_id expected={check_id} actual={replacement.get('check_id')}",
        )
    merged = copy.deepcopy(response)
    rows = merged.get("assessments")
    if not isinstance(rows, list):
        raise Pass2PipelineError("ASSESSMENT_REGENERATION_INVALID", "Step07 assessment array is invalid.")
    found = False
    for index, item in enumerate(rows):
        if isinstance(item, dict) and str(item.get("check_id")) == check_id:
            rows[index] = copy.deepcopy(replacement)
            found = True
            break
    if not found:
        raise Pass2PipelineError(
            "ASSESSMENT_REGENERATION_INVALID",
            f"Step07 regeneration target is absent from bundle: {check_id}",
        )
    return merged


def _regenerate_step07_assessment_units(
    provider: AIProvider,
    root: Path,
    init: str,
    group: dict[str, Any],
    rows: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str]],
    entries_by_id: dict[str, dict[str, Any]],
    prompt_text: str,
    config: dict[str, str],
    response: dict[str, Any],
    target_check_ids: list[str],
    error: Pass2PipelineError,
) -> dict[str, Any]:
    """Regenerate each failed Check assessment as one atomic semantic unit.

    The previous assessment object is discarded.  The provider receives the
    original Step04 Check definition and Step05 evidence again and returns the
    complete assessment object (assessment, findings, contradictions, uncertainty,
    quantitative claims and relations) in one response.
    """
    row_by_check_id = {str(row[1]["check_id"]): row for row in rows}
    current = copy.deepcopy(response)
    for check_id in target_check_ids:
        row = row_by_check_id.get(check_id)
        if row is None:
            raise Pass2PipelineError(
                "ASSESSMENT_REGENERATION_INVALID",
                f"Step07 regeneration target is not part of the active bundle: {check_id}",
            )
        _group, check, _entries, _fingerprint = row
        available_entries = [
            entries_by_id[request_id]
            for request_id in check.get("available_request_ids", [])
            if request_id in entries_by_id
        ]
        retry_context = _grounding_retry_context(
            error, checks=[check], entries_by_id=entries_by_id, response=current,
            specifications_by_check=_check_specifications(group), root=root, group=group,
        )
        grounding = retry_context.setdefault("grounding_retry", {})
        grounding["regeneration_unit"] = "complete_check_assessment"
        grounding["instruction"] = (
            "이전 assessment 객체는 폐기되었습니다. 일부 statement, relation 또는 assessment 필드만 수정하지 마십시오. "
            "원래 Step04 Check와 제공된 Step05 근거를 다시 보고 이 Check의 assessment, findings, contradictory_findings, "
            "uncertainties, quantitative_claims, quantitative_relations 전체를 하나의 일관된 판단으로 처음부터 다시 생성하십시오. "
            + str(grounding.get("instruction") or "")
        )
        if error.code == "NARRATIVE_LANGUAGE_INVALID":
            grounding["instruction"] += (
                " 사람이 읽는 모든 서술은 한국어로 작성하고 방위 약어 N/S/E/W 같은 허용되지 않은 영문 토큰을 사용하지 마십시오."
            )
        messages = _bundle_messages(
            root, init, group, [check], available_entries, prompt_text,
            grounding_retry_context=retry_context,
        )
        regenerated = _call_bundle(provider, root, messages, config)
        assessments = regenerated.get("assessments")
        if not isinstance(assessments, list) or len(assessments) != 1 or not isinstance(assessments[0], dict):
            raise Pass2PipelineError(
                "ASSESSMENT_REGENERATION_INVALID",
                f"Step07 complete Check regeneration must return exactly one assessment: {check_id}",
            )
        current = _replace_step07_assessment_unit(current, check_id, assessments[0])
    return current


def _step07_failing_check_ids(
    root: Path, init: str, response: dict[str, Any],
    rows: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str]],
    metadata: dict[str, str],
    specifications_by_candidate: dict[str, dict[str, dict[str, Any]]],
) -> list[str]:
    """Localize a bundle validation failure to the smallest complete Check units."""
    assessments = {
        str(item.get("check_id")): item
        for item in response.get("assessments", [])
        if isinstance(item, dict)
    }
    targets: list[str] = []
    for row in rows:
        check_id = str(row[1]["check_id"])
        assessment = assessments.get(check_id)
        if assessment is None:
            targets.append(check_id)
            continue
        try:
            _bundle_result_values(
                root=root,
                init=init,
                response={"assessments": [assessment]},
                rows=[row],
                metadata=metadata,
                specifications_by_candidate=specifications_by_candidate,
            )
        except Pass2PipelineError:
            targets.append(check_id)
    return targets or [str(row[1]["check_id"]) for row in rows]

# LEGACY_DISABLED: the active ``run_step07`` path regenerates a complete Check
# assessment via ``_regenerate_step07_assessment_units``.  The field-scoped
# rewrite helpers remain only for backward import compatibility and are not an
# execution or retry route.
def _step07_narrative_rewrite_targets(
    response: dict[str, Any], language_errors: list[str],
) -> list[dict[str, Any]]:
    """Resolve language-validator paths to immutable narrative slots.

    Language repair is intentionally narrower than the full Step07 response.  The
    provider may rewrite only strings that the Korean narrative validator flagged;
    identifiers, arrays, evidence links, assessments, and quantitative structures
    never enter the rewrite output contract.
    """
    by_path: dict[str, dict[str, Any]] = {}
    assessments = response.get("assessments")
    if not isinstance(assessments, list):
        raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", "Step07 한글 재작성 원본의 확인 항목 구조가 유효하지 않습니다.")
    for assessment_index, assessment in enumerate(assessments):
        if not isinstance(assessment, dict):
            continue
        check_id = assessment.get("check_id")
        if not isinstance(check_id, str) or not check_id:
            continue
        for field in ("findings", "contradictory_findings"):
            rows = assessment.get(field)
            if not isinstance(rows, list):
                continue
            for index, row in enumerate(rows):
                if not isinstance(row, dict) or not isinstance(row.get("statement"), str):
                    continue
                path = f"assessments[{assessment_index}].{field}[{index}].statement"
                by_path[path] = {
                    "check_id": check_id,
                    "field": field,
                    "index": index,
                    "current_text": row["statement"],
                    "path": path,
                }
        uncertainties = assessment.get("uncertainties")
        if isinstance(uncertainties, list):
            for index, value in enumerate(uncertainties):
                if not isinstance(value, str):
                    continue
                path = f"assessments[{assessment_index}].uncertainties[{index}]"
                by_path[path] = {
                    "check_id": check_id,
                    "field": "uncertainties",
                    "index": index,
                    "current_text": value,
                    "path": path,
                }

    targets: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for error in language_errors:
        path = str(error).split(":", 1)[0].strip()
        if path in seen_paths:
            continue
        target = by_path.get(path)
        if target is None:
            raise Pass2PipelineError(
                "NARRATIVE_REWRITE_INVALID",
                f"Step07 한글 재작성 대상 경로를 원본 응답에서 찾을 수 없습니다: {path}",
            )
        targets.append(target)
        seen_paths.add(path)
    if not targets:
        raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", "Step07 한글 재작성 대상이 비어 있습니다.")
    return targets


def _step07_narrative_patch_schema(max_items: int) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "AIFSHeavyRain Step07 narrative text patch",
        "type": "object",
        "additionalProperties": False,
        "required": ["rewrites"],
        "properties": {
            "rewrites": {
                "type": "array",
                "minItems": 1,
                "maxItems": max_items,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["check_id", "field", "index", "text"],
                    "properties": {
                        "check_id": {"type": "string", "minLength": 1},
                        "field": {"type": "string", "enum": ["findings", "contradictory_findings", "uncertainties"]},
                        "index": {"type": "integer", "minimum": 0},
                        "text": {"type": "string", "minLength": 1},
                    },
                },
            }
        },
    }


def _apply_step07_narrative_patch(
    original: dict[str, Any], patch: dict[str, Any], targets: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply only explicitly permitted text replacements to a Step07 response."""
    if set(patch) != {"rewrites"} or not isinstance(patch.get("rewrites"), list):
        raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", "Step07 한글 재작성 패치 구조가 유효하지 않습니다.")
    rewrites = patch["rewrites"]
    if len(rewrites) != len(targets):
        raise Pass2PipelineError(
            "NARRATIVE_REWRITE_INVALID",
            f"Step07 한글 재작성 패치 수가 대상 수와 다릅니다: expected={len(targets)} actual={len(rewrites)}",
        )

    allowed = {
        (str(target["check_id"]), str(target["field"]), int(target["index"])): target
        for target in targets
    }
    seen: set[tuple[str, str, int]] = set()
    normalized: dict[tuple[str, str, int], str] = {}
    for item in rewrites:
        if not isinstance(item, dict) or set(item) != {"check_id", "field", "index", "text"}:
            raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", "Step07 한글 재작성 패치 항목이 유효하지 않습니다.")
        check_id = item.get("check_id")
        field = item.get("field")
        index = item.get("index")
        text = item.get("text")
        if (
            not isinstance(check_id, str)
            or field not in {"findings", "contradictory_findings", "uncertainties"}
            or isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or not isinstance(text, str)
            or not text.strip()
        ):
            raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", "Step07 한글 재작성 패치 값이 유효하지 않습니다.")
        key = (check_id, str(field), index)
        if key not in allowed:
            raise Pass2PipelineError(
                "NARRATIVE_REWRITE_INVALID",
                f"Step07 한글 재작성 패치가 허용되지 않은 위치를 요청했습니다: {check_id} {field}[{index}]",
            )
        if key in seen:
            raise Pass2PipelineError(
                "NARRATIVE_REWRITE_INVALID",
                f"Step07 한글 재작성 패치가 같은 위치를 중복 요청했습니다: {check_id} {field}[{index}]",
            )
        seen.add(key)
        normalized[key] = text.strip()
    if seen != set(allowed):
        missing = sorted(set(allowed) - seen)
        raise Pass2PipelineError(
            "NARRATIVE_REWRITE_INVALID",
            f"Step07 한글 재작성 패치에 필수 대상이 누락되었습니다: {missing[:4]}",
        )

    merged = copy.deepcopy(original)
    rows_by_check_id = {
        str(item.get("check_id")): item
        for item in merged.get("assessments", [])
        if isinstance(item, dict) and isinstance(item.get("check_id"), str)
    }
    for (check_id, field, index), text in normalized.items():
        assessment = rows_by_check_id.get(check_id)
        if assessment is None:
            raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", f"Step07 한글 재작성 대상 확인 항목이 없습니다: {check_id}")
        values = assessment.get(field)
        if not isinstance(values, list) or index >= len(values):
            raise Pass2PipelineError(
                "NARRATIVE_REWRITE_INVALID",
                f"Step07 한글 재작성 대상 위치가 유효하지 않습니다: {check_id} {field}[{index}]",
            )
        if field == "uncertainties":
            if not isinstance(values[index], str):
                raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", f"Step07 {check_id} uncertainties[{index}]가 문자열이 아닙니다.")
            values[index] = text
        else:
            row = values[index]
            if not isinstance(row, dict) or not isinstance(row.get("statement"), str):
                raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", f"Step07 {check_id} {field}[{index}]가 유효하지 않습니다.")
            row["statement"] = text
    return merged


def _rewrite_step07_narrative(
    provider: AIProvider,
    root: Path,
    config: dict[str, str],
    response: dict[str, Any],
    language_errors: list[str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Repair only language-invalid Step07 narrative strings.

    A first repair can itself reintroduce an unapproved Latin token (for example
    a compass abbreviation such as ``E`` in a coordinate).  Retry the narrow
    text-only repair once more against only the still-invalid strings instead of
    failing the whole Step07 bundle.  The meteorological assessment, evidence
    references, quantitative selectors, and JSON structure remain immutable.
    """
    current_response = copy.deepcopy(response)
    current_errors = list(language_errors)
    rewrite_usages: list[dict[str, Any] | None] = []
    max_rewrite_attempts = 2

    for rewrite_attempt in range(1, max_rewrite_attempts + 1):
        targets = _step07_narrative_rewrite_targets(current_response, current_errors)
        canonical = _step07_narrative_patch_schema(len(targets))
        schema = build_openai_transport_schema(canonical) if provider.api_family == "responses" else canonical
        messages = [
            {
                "role": "system",
                "content": (
                    "기상학적 판단이나 JSON 구조를 다시 만들지 않습니다. 지정된 자연어 문자열만 한글 기상용어로 고쳐 "
                    "rewrites 배열의 텍스트 패치만 반환하십시오. 대상 식별자, 필드, 순서는 입력과 정확히 같아야 합니다. "
                    "사용자 표시 문장에는 허용 목록 밖의 영문 알파벳을 남기지 마십시오. "
                    "특히 방위 약어 N/S/E/W와 35N, 127E 같은 좌표 약어는 사용하지 말고, 원문 의미가 좌표인 경우 "
                    "북위·남위·동경·서경과 숫자·도를 사용해 같은 위치를 표현하십시오."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "rewrite_targets의 문자열만 재작성하십시오. 각 대상마다 정확히 하나의 패치를 반환하십시오.",
                        "repair_attempt": rewrite_attempt,
                        "violations": current_errors,
                        "rewrite_targets": targets,
                        "rules": [
                            "check_id, field, index는 rewrite_targets의 값을 그대로 복사하십시오.",
                            "text만 수정하고 새로운 대상이나 배열 항목을 만들거나 삭제하지 마십시오.",
                            "수치, 시각, 위치, 기상학적 판단의 강도와 방향을 변경하지 마십시오.",
                            "Step 번호와 내부 영문 용어를 제거하고 실제 기상요소 중심의 한글 문장으로 작성하십시오.",
                            "자연어의 영문은 AIFS, UTC, KST, F084 같은 예측시간, hPa·mm·m/s·Pa/s·g/kg 등 허용된 운영 약어와 물리단위만 유지하십시오.",
                            "N, S, E, W 같은 영문 방위 약어는 남기지 마십시오. 좌표 의미라면 북위·남위·동경·서경으로 표현하고 원래 숫자는 유지하십시오.",
                            "반환 전에 각 text의 영문 알파벳을 다시 확인하여 허용 목록 밖 토큰이 없도록 하십시오.",
                            "rewrites 외의 키는 반환하지 마십시오.",
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        provider_response = provider.complete(
            messages=messages,
            model=config["model"],
            max_tokens=4000,
            schema=schema,
            schema_name="AIFSStep07NarrativeTextPatch",
            structured_output_mode="auto",
            image_detail=config["image_detail"],
            reasoning_effort="low",
        )
        rewrite_usages.append(provider_response.usage if isinstance(provider_response.usage, dict) else None)
        content = normalize_openai_transport_json(provider_response.content, canonical) if provider.api_family == "responses" else provider_response.content
        try:
            patch = json.loads(content)
        except json.JSONDecodeError as exc:
            raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", "Step07 한글 재작성 패치가 JSON이 아닙니다.") from exc
        if not isinstance(patch, dict):
            raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", "Step07 한글 재작성 패치의 최상위가 객체가 아닙니다.")

        current_response = _apply_step07_narrative_patch(current_response, patch, targets)
        remaining = validate_step07_response_narrative(current_response)
        if not remaining:
            if len(rewrite_usages) == 1:
                return current_response, rewrite_usages[0]
            return current_response, {
                "rewrite_attempts": len(rewrite_usages),
                "attempts": rewrite_usages,
            }
        current_errors = remaining

    raise Pass2PipelineError("NARRATIVE_LANGUAGE_INVALID", "; ".join(current_errors[:12]))

def _deduplicate_quantitative_selectors(selectors: Any) -> tuple[Any, int]:
    """Remove only exact, structurally valid duplicate quantitative selectors.

    Malformed selectors are intentionally preserved so the existing strict resolver
    can reject them with the original validation error.
    """
    if not isinstance(selectors, list):
        return selectors, 0
    seen: set[tuple[str, str, int, str, str]] = set()
    normalized: list[Any] = []
    removed = 0
    for selector in selectors:
        if not isinstance(selector, dict):
            normalized.append(selector)
            continue
        request_id = selector.get("request_id")
        feature_id = selector.get("feature_id")
        forecast_hour = selector.get("forecast_hour")
        region_id = selector.get("region_id")
        statistic = selector.get("statistic")
        valid_key = (
            all(isinstance(value, str) and value for value in (request_id, feature_id, region_id))
            and isinstance(forecast_hour, int)
            and not isinstance(forecast_hour, bool)
            and forecast_hour >= 0
            and statistic in QUANTITATIVE_STATISTICS
        )
        if not valid_key:
            normalized.append(selector)
            continue
        selector_key = (request_id, feature_id, forecast_hour, region_id, statistic)
        if selector_key in seen:
            removed += 1
            continue
        seen.add(selector_key)
        normalized.append(selector)
    return normalized, removed



def _ensure_relation_selectors_declared_in_claims(
    claims: Any, relations: Any, *, check: dict[str, Any], entries: list[dict[str, Any]],
) -> tuple[Any, int]:
    """Add exact quantitative relation selectors missing from quantitative_claims.

    This is reference bookkeeping, not a meteorological or arithmetic decision.
    The LLM has already selected every relation point and claimed relation. Python
    only ensures that the same exact selector tuples are also declared in the
    companion quantitative_claims array, as required by the Step07 contract.
    Invalid or malformed selectors are not repaired here and remain subject to the
    existing strict resolver and namespace validation.
    """
    if not isinstance(claims, list) or not isinstance(relations, list):
        return claims, 0

    normalized = list(claims)
    existing: set[tuple[str, str, int, str, str]] = set()
    for selector in claims:
        if not isinstance(selector, dict):
            continue
        request_id = selector.get("request_id")
        feature_id = selector.get("feature_id")
        forecast_hour = selector.get("forecast_hour")
        region_id = selector.get("region_id")
        statistic = selector.get("statistic")
        if (
            all(isinstance(value, str) and value for value in (request_id, feature_id, region_id))
            and isinstance(forecast_hour, int)
            and not isinstance(forecast_hour, bool)
            and forecast_hour >= 0
            and statistic in QUANTITATIVE_STATISTICS
        ):
            existing.add((request_id, feature_id, forecast_hour, region_id, statistic))

    added = 0
    for relation in relations:
        selectors = relation.get("selectors") if isinstance(relation, dict) else None
        if not isinstance(selectors, list):
            continue
        for selector in selectors:
            if not isinstance(selector, dict):
                continue
            request_id = selector.get("request_id")
            feature_id = selector.get("feature_id")
            forecast_hour = selector.get("forecast_hour")
            region_id = selector.get("region_id")
            statistic = selector.get("statistic")
            valid = (
                all(isinstance(value, str) and value for value in (request_id, feature_id, region_id))
                and isinstance(forecast_hour, int)
                and not isinstance(forecast_hour, bool)
                and forecast_hour >= 0
                and statistic in QUANTITATIVE_STATISTICS
            )
            if not valid:
                continue
            if _quantitative_selector_namespace_issues(check, entries, [selector], source="relation_selector"):
                raise Pass2PipelineError(
                    "QUANTITATIVE_CLAIM_INVALID",
                    f"{check.get('check_id')} relation selector is not an exact Step05 quantitative tuple: {selector}",
                )
            key = (request_id, feature_id, forecast_hour, region_id, statistic)
            if key in existing:
                continue
            normalized.append({
                "request_id": request_id,
                "feature_id": feature_id,
                "forecast_hour": forecast_hour,
                "region_id": region_id,
                "statistic": statistic,
            })
            existing.add(key)
            added += 1
    return normalized, added



_FORECAST_KST_PREFIX_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<hour>\d{2}):(?P<minute>\d{2})\s*KST\s*\(F(?P<fh>\d{3})\)"
)
_FORECAST_KST_SUFFIX_RE = re.compile(
    r"F(?P<fh>\d{3})\)?\s*\((?P<date>\d{4}-\d{2}-\d{2})\s+(?P<hour>\d{2}):(?P<minute>\d{2})\s*KST\)"
)


def _expected_kst_for_forecast_hour(init: str, forecast_hour: int) -> datetime:
    init_utc = datetime.strptime(str(init), "%Y%m%d%H").replace(tzinfo=timezone.utc)
    return (init_utc + timedelta(hours=int(forecast_hour))).astimezone(timezone(timedelta(hours=9)))


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


def _infer_pairwise_relation_from_korean_text(text: str, first_hour: int, second_hour: int) -> str | None:
    """Infer only explicit F-hour pairwise wording; return None when ambiguous.

    This is intentionally narrow. It is not a meteorological parser and is used
    only to decide whether a structured relation mismatch can be arithmetically
    normalized without hiding a directly contradictory user-facing sentence.
    """
    compact = re.sub(r"\s+", " ", str(text))
    first = f"F{first_hour:03d}"
    second = f"F{second_hour:03d}"
    if first not in compact or second not in compact:
        return None

    equality_patterns = (
        rf"{re.escape(first)}[^.!?]{{0,90}}{re.escape(second)}[^.!?]{{0,90}}(?:동일|같(?:은|다|게)|비슷|유사|차이(?:가)?\s*없|모두\s*0)",
        rf"{re.escape(second)}[^.!?]{{0,90}}{re.escape(first)}[^.!?]{{0,90}}(?:동일|같(?:은|다|게)|비슷|유사|차이(?:가)?\s*없|모두\s*0)",
    )
    if any(re.search(pattern, compact) for pattern in equality_patterns):
        return "approximately_equal"

    greater_words = r"(?:높|강(?:해|하|한|하게|했|해졌)|커|크(?:다|고|게|며|기)|많(?:아|은|다|게)|증가|강화|우세|상회)"
    lesser_words = r"(?:낮|약(?:해|하|한|하게|했|해졌)|작(?:아|은|다|게)|적(?:어|은|다|게)|감소|약화|하회)"
    comparison_pairs = (
        (first, second, "first_less_than_second", "first_greater_than_second"),
        (second, first, "first_greater_than_second", "first_less_than_second"),
    )
    for reference, target, target_greater_relation, target_lesser_relation in comparison_pairs:
        prefix = rf"{re.escape(reference)}\s*(?:보다|에\s*비해|대비)\s*{re.escape(target)}"
        tail_match = re.search(prefix + r"([^.!?]{0,100})", compact)
        if not tail_match:
            continue
        tail = tail_match.group(1)
        has_greater = re.search(greater_words, tail) is not None
        has_lesser = re.search(lesser_words, tail) is not None
        if has_greater and not has_lesser:
            return target_greater_relation
        if has_lesser and not has_greater:
            return target_lesser_relation
    return None


def _safe_pairwise_relation_autocorrect_ids(
    check: dict[str, Any],
    entries: list[dict[str, Any]],
    assessment: dict[str, Any],
) -> set[str]:
    """Select structural pairwise mismatches safe for deterministic normalization.

    Autocorrection is deliberately limited to the reported failure class:
    same-region comparisons of two different forecast hours. If user-facing
    prose explicitly states the opposite relation, the mismatch is *not*
    autocorrected and the existing full-Check LLM repair path remains active.
    Cross-region, trend, and extremum mismatches continue to require LLM repair.
    """
    relations = assessment.get("quantitative_relations")
    if not isinstance(relations, list):
        return set()
    narrative_texts = _step07_narrative_texts(assessment)
    safe: set[str] = set()
    for relation in relations:
        if not isinstance(relation, dict) or relation.get("relation_type") != "pairwise_comparison":
            continue
        relation_id = str(relation.get("relation_id") or "").strip()
        if not relation_id:
            continue
        try:
            points = resolve_quantitative_claims(check, entries, relation.get("selectors"))
            computed = compute_quantitative_relation(relation, points)
        except (Pass2PipelineError, QuantitativeConsistencyError):
            continue
        if computed.get("consistent") or computed.get("computed_relation") not in PAIRWISE_RELATIONS:
            continue
        if len(points) != 2:
            continue
        first, second = points
        if first.get("region_id") != second.get("region_id"):
            continue
        first_hour = first.get("forecast_hour")
        second_hour = second.get("forecast_hour")
        if (
            isinstance(first_hour, bool) or not isinstance(first_hour, int)
            or isinstance(second_hour, bool) or not isinstance(second_hour, int)
            or first_hour == second_hour
        ):
            continue
        computed_relation = str(computed["computed_relation"])
        narrative_conflict = False
        for text in narrative_texts:
            inferred = _infer_pairwise_relation_from_korean_text(text, first_hour, second_hour)
            if inferred is not None and inferred != computed_relation:
                narrative_conflict = True
                break
        if not narrative_conflict:
            safe.add(relation_id)
    return safe


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



def _append_narrative_annotation(statement: str, label: str, values: list[str]) -> str:
    """Append a deterministic location annotation without rewriting meteorology."""
    clean = [str(value).strip() for value in values if str(value).strip()]
    if not clean:
        return statement
    if label in statement:
        return statement
    base = str(statement or "").strip()
    if not base:
        return base
    if base.endswith("."):
        base = base[:-1].rstrip()
    return f"{base} ({label}: {', '.join(clean)})."


def _direct_precipitation_regions_for_statement(
    context: dict[str, Any], statement: str,
) -> list[str]:
    """Resolve the exact supported direct-precipitation region names for a finding.

    Prefer an explicitly mentioned F-hour in the sentence.  If the sentence
    summarizes multiple/no peak hours, use the stable union in forecast-hour
    order.  The values all come from deterministic Step05 operational-scope
    statistics and are therefore naming evidence, not an LLM spatial guess.
    """
    by_hour = context.get("threshold_met_regions_by_forecast_hour", {})
    if not isinstance(by_hour, dict):
        return []
    mentioned = {int(token) for token in re.findall(r"F(\d{3})", str(statement or ""))}
    rows: list[dict[str, Any]] = []
    for hour_key, regions in by_hour.items():
        if not isinstance(regions, list):
            continue
        match = re.fullmatch(r"F(\d{3})", str(hour_key))
        hour = int(match.group(1)) if match else None
        if mentioned and hour not in mentioned:
            continue
        rows.extend(region for region in regions if isinstance(region, dict))
    if mentioned and not rows:
        # Do not silently attach regions from a different forecast hour.
        return []
    if not mentioned:
        for hour_key in sorted(by_hour):
            regions = by_hour.get(hour_key)
            if isinstance(regions, list):
                rows.extend(region for region in regions if isinstance(region, dict))
    names: list[str] = []
    for row in rows:
        name = str(row.get("region_name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _enrich_system_anchor_region_narrative(
    group: dict[str, Any],
    check: dict[str, Any],
    specification: dict[str, Any],
    entries: list[dict[str, Any]],
    assessment: dict[str, Any],
) -> int:
    """Deterministically add supported region names to positive anchor facts.

    This function intentionally does *not* change assessment, evidence refs,
    quantitative claims, or any meteorological conclusion.  It only appends the
    exact operational regions already established by Step05 scope statistics.
    """
    if not _is_system_direct_precipitation_anchor_check(specification):
        return 0
    if str(assessment.get("assessment") or "") not in {"observed", "partially_observed"}:
        return 0
    context = _system_direct_precipitation_anchor_context(group, check, specification, entries)
    if not isinstance(context, dict) or not context.get("threshold_met"):
        return 0
    findings = assessment.get("findings")
    if not isinstance(findings, list):
        return 0
    changed = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        statement = str(finding.get("statement") or "").strip()
        if "강수" not in statement or REGION_NARRATIVE_DIRECT_ANNOTATION in statement:
            continue
        names = _direct_precipitation_regions_for_statement(context, statement)
        if not names:
            continue
        updated = _append_narrative_annotation(statement, REGION_NARRATIVE_DIRECT_ANNOTATION, names)
        if updated != statement:
            finding["statement"] = updated
            changed += 1
    return changed


def _localized_region_labels(
    root: Path, group: dict[str, Any], localization: dict[str, Any],
) -> list[str]:
    naming = _candidate_operational_region_naming_context(root, group)
    name_by_id = {
        str(row.get("region_id") or ""): str(row.get("name_ko") or "").strip()
        for row in naming.get("regions", [])
        if isinstance(row, dict)
    }
    labels: list[str] = []
    for row in localization.get("regions", []):
        if not isinstance(row, dict):
            continue
        name = name_by_id.get(str(row.get("region_id") or ""), "")
        if not name:
            continue
        sector = str(row.get("relative_sector") or "unspecified")
        sector_ko = SPATIAL_LOCALIZATION_SECTOR_LABEL_KO.get(sector, "")
        label = f"{name} {sector_ko}".strip()
        if label not in labels:
            labels.append(label)
    return labels


def _enrich_localized_alignment_region_narrative(
    root: Path,
    group: dict[str, Any],
    check: dict[str, Any],
    specification: dict[str, Any],
    assessment: dict[str, Any],
) -> int:
    """Append validated alignment locations to overlap findings.

    The annotation is added only after ``spatial_localization`` itself has been
    model-authored.  Contract validation later verifies that localization
    against the actual image evidence, so this step does not invent a place.
    """
    localization = assessment.get("spatial_localization")
    if not isinstance(localization, dict) or localization.get("status") != "localized":
        return 0
    labels = _localized_region_labels(root, group, localization)
    if not labels:
        return 0
    findings = assessment.get("findings")
    if not isinstance(findings, list):
        return 0
    relation_tokens = ("겹", "결합", "중첩", "함께 나타", "같은 위치")
    changed = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        statement = str(finding.get("statement") or "").strip()
        if not statement or REGION_NARRATIVE_ALIGNMENT_ANNOTATION in statement:
            continue
        if not any(token in statement for token in relation_tokens):
            continue
        updated = _append_narrative_annotation(statement, REGION_NARRATIVE_ALIGNMENT_ANNOTATION, labels)
        if updated != statement:
            finding["statement"] = updated
            changed += 1
    return changed


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


_SPATIAL_ALIGNMENT_RELATION_PATTERN = re.compile(
    r"(?:결합|중첩|겹(?:치|쳐|친|침|칩|쳤)[가-힣]*|같은\s*(?:위치|영역)|동일\s*(?:위치|영역)|시공간(?:적)?\s*일치|위치\s*관계|공간\s*관계)"
)


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


def _drop_unrepresentable_extremum_relations(relations: Any) -> tuple[Any, list[str]]:
    """Drop extremum relations that the Step07 relation schema cannot represent.

    ``extremum`` is a temporal relation: all selectors must refer to one
    operational region and differ by forecast hour.  A single-hour comparison
    across several regions is a spatial ranking/max-location claim and has no
    ``claimed_region_id`` field in the current schema.  Keeping such a relation
    guarantees a deterministic ``extremum requires one common region`` failure.

    The quantitative claims themselves are preserved.  If a temporal relation
    is contractually required, downstream quantitative-consistency validation
    will still raise ``QUANTITATIVE_RELATION_REQUIRED`` and fail closed for that
    Check; this normalization therefore does not weaken required quantitative
    validation.
    """
    if not isinstance(relations, list):
        return relations, []
    kept: list[Any] = []
    dropped: list[str] = []
    for relation in relations:
        if not isinstance(relation, dict) or relation.get("relation_type") != "extremum":
            kept.append(relation)
            continue
        selectors = relation.get("selectors")
        if not isinstance(selectors, list):
            kept.append(relation)
            continue
        region_ids = {
            str(selector.get("region_id") or "")
            for selector in selectors
            if isinstance(selector, dict) and str(selector.get("region_id") or "")
        }
        if len(region_ids) > 1:
            dropped.append(str(relation.get("relation_id") or "<unknown>"))
            continue
        kept.append(relation)
    return kept, dropped



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


def _bundle_result_values(
    *, root: Path, init: str, response: dict[str, Any], rows: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str]],
    metadata: dict[str, str], specifications_by_candidate: dict[str, dict[str, dict[str, Any]]],
    on_quantitative_selector_dedup: Callable[[str, str, str, int], None] | None = None,
    on_quantitative_relation_selector_autofix: Callable[[str, str, str, int], None] | None = None,
    on_quantitative_relation_autocorrect: Callable[[str, str, str, str, str, str], None] | None = None,
    quantitative_relation_pass_status: str = "passed",
    allow_quantitative_relation_mismatch: bool = False,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Build and fully validate a bundle response before it reaches the checkpoint."""
    assessments = {str(item.get("check_id")): item for item in response["assessments"] if isinstance(item, dict)}
    expected = {str(check["check_id"]) for _group, check, _entries, _fingerprint in rows}
    if set(assessments) != expected:
        raise Pass2PipelineError("PROVIDER_RESULT_INVALID", f"Bundle check IDs differ expected={sorted(expected)} actual={sorted(assessments)}")
    values: dict[tuple[str, str], dict[str, Any]] = {}
    for group_value, check, entries, fingerprint in rows:
        ai = assessments[str(check["check_id"])]
        specification = specifications_by_candidate[str(group_value["candidate_id"])][str(check["check_id"])]
        _validate_image_diagnostic_narrative_identity(entries, ai)
        _validate_moisture_transport_direction_reasoning(entries, ai)
        # Validate the model-authored spatial claim before appending any
        # deterministic region labels.  An invalid response must not be
        # mutated with a correct-looking annotation that leaves its original
        # unsupported place claim intact (and can contaminate retry context).
        _validate_spatial_localization_narrative_grounding(
            root, group_value, check, specification, ai
        )
        direct_region_enrichments = _enrich_system_anchor_region_narrative(
            group_value, check, specification, entries, ai
        )
        alignment_region_enrichments = _enrich_localized_alignment_region_narrative(
            root, group_value, check, specification, ai
        )
        if direct_region_enrichments or alignment_region_enrichments:
            print(
                f"STEP07_REGION_NARRATIVE_ENRICHED candidate={group_value['candidate_id']} "
                f"check={check['check_id']} direct={direct_region_enrichments} "
                f"alignment={alignment_region_enrichments}"
            )
        _validate_system_anchor_region_narrative(group_value, check, specification, entries, ai)
        _validate_localized_alignment_region_narrative(root, group_value, check, specification, ai)
        bound_majority = _bind_majority_convergence_quantitative_evidence(
            specification, check, entries, ai,
        )
        if bound_majority is not None:
            print(
                f"STEP07_SYSTEM_QUANTITATIVE_BINDING candidate={group_value['candidate_id']} "
                f"bundle={check['bundle_id']} check={check['check_id']} "
                f"version={MAJORITY_QUANTITATIVE_BINDING_VERSION} "
                f"regions={bound_majority['regions']} source_claims={bound_majority['source_claims']} "
                f"source_relations={bound_majority['source_relations']} "
                f"model_claims_replaced={bound_majority['model_claims_replaced']} "
                f"model_relations_replaced={bound_majority['model_relations_replaced']}"
            )
        order_fixes = _normalize_specific_quantitative_relation_order(ai, specification)
        if order_fixes:
            print(
                f"STEP07_RELATION_ORDER_NORMALIZED candidate={group_value['candidate_id']} "
                f"bundle={check['bundle_id']} check={check['check_id']} fixes={order_fixes}"
            )
        relations_for_namespace_check, dropped_extremum_relations = _drop_unrepresentable_extremum_relations(
            ai.get("quantitative_relations", [])
        )
        if dropped_extremum_relations:
            ai["quantitative_relations"] = relations_for_namespace_check
            print(
                f"STEP07_SPATIAL_EXTREMUM_RELATION_DROPPED candidate={group_value['candidate_id']} "
                f"bundle={check['bundle_id']} check={check['check_id']} "
                f"relations={','.join(dropped_extremum_relations)}"
            )
        # Fail before AUTOFIX: never copy a relation's invented selector into claims.
        namespace_issues = _quantitative_response_namespace_issues(check, entries, {
            "quantitative_claims": ai.get("quantitative_claims"),
            "quantitative_relations": relations_for_namespace_check,
        })
        if namespace_issues:
            first = namespace_issues[0]
            raise Pass2PipelineError(
                "QUANTITATIVE_CLAIM_INVALID",
                f"{check.get('check_id')} unavailable Step05 quantitative selector at "
                f"{first['source']}: {first['selector']}",
            )
        completed_selectors, relation_selector_added_count = _ensure_relation_selectors_declared_in_claims(
            ai["quantitative_claims"], relations_for_namespace_check, check=check, entries=entries
        )
        normalized_selectors, removed_count = _deduplicate_quantitative_selectors(completed_selectors)
        if relation_selector_added_count:
            candidate_value = str(group_value["candidate_id"])
            bundle_value = str(check["bundle_id"])
            check_value = str(check["check_id"])
            print(
                f"STEP07_RELATION_SELECTOR_AUTOFIX candidate={candidate_value} "
                f"bundle={bundle_value} check={check_value} added={relation_selector_added_count}"
            )
            if on_quantitative_relation_selector_autofix is not None:
                try:
                    on_quantitative_relation_selector_autofix(
                        candidate_value, bundle_value, check_value, relation_selector_added_count
                    )
                except Exception:
                    pass
        if removed_count:
            candidate_value = str(group_value["candidate_id"])
            bundle_value = str(check["bundle_id"])
            check_value = str(check["check_id"])
            print(
                f"STEP07_QUANTITATIVE_SELECTOR_DEDUP candidate={candidate_value} "
                f"bundle={bundle_value} check={check_value} removed={removed_count}"
            )
            if on_quantitative_selector_dedup is not None:
                try:
                    on_quantitative_selector_dedup(candidate_value, bundle_value, check_value, removed_count)
                except Exception:
                    # Operational logging must not change an already-valid normalization path.
                    pass
        # Validate the Step04 specific-scope selector identities before
        # comparing relations; otherwise a wrong region may trigger a less
        # actionable arithmetic mismatch first.
        _validate_specific_quantitative_selector_preflight(
            specification, check, entries, normalized_selectors,
        )
        quantitative_claims = resolve_quantitative_claims(check, entries, normalized_selectors)
        # Quantitative relation mismatches must regenerate the complete Check assessment.
        # Do not silently autocorrect a relation while preserving an assessment/narrative
        # that may have been derived from the opposite relation.
        autocorrect_relation_ids: set[str] = set()
        quantitative_relations, quantitative_consistency_status, relations_consistent, relation_corrections = resolve_quantitative_relations(
            check,
            entries,
            ai.get("quantitative_relations", []),
            normalized_selectors,
            pass_status=quantitative_relation_pass_status,
            allow_mismatch=allow_quantitative_relation_mismatch,
            autocorrect_relation_ids=autocorrect_relation_ids,
        )
        if relation_corrections:
            candidate_value = str(group_value["candidate_id"])
            bundle_value = str(check["bundle_id"])
            check_value = str(check["check_id"])
            for correction in relation_corrections:
                relation_id = str(correction.get("relation_id") or "")
                claimed = str(correction.get("claimed_relation") or "")
                computed = str(correction.get("computed_relation") or "")
                print(
                    f"STEP07_RELATION_AUTOCORRECT candidate={candidate_value} bundle={bundle_value} "
                    f"check={check_value} relation={relation_id} claimed={claimed} computed={computed}"
                )
                if on_quantitative_relation_autocorrect is not None:
                    try:
                        on_quantitative_relation_autocorrect(
                            candidate_value, bundle_value, check_value, relation_id, claimed, computed
                        )
                    except Exception:
                        pass
        quantitative_probe = {
            "assessment": ai.get("assessment"),
            "findings": ai.get("findings", []),
            "contradictory_findings": ai.get("contradictory_findings", []),
            "quantitative_claims": quantitative_claims,
            "quantitative_relations": quantitative_relations,
        }
        quantitative_failure_details = quantitative_consistency_failure_details(
            specification, quantitative_probe, check=check, entries=entries
        )
        quantitative_failure_reasons = list(dict.fromkeys(
            str(detail["code"]) for detail in quantitative_failure_details
        ))
        if quantitative_failure_reasons:
            quantitative_consistency_status = "failed"
            relations_consistent = False
            if not allow_quantitative_relation_mismatch:
                primary = quantitative_failure_details[0]
                raise Pass2PipelineError(str(primary["code"]), str(primary["detail"]))
        findings = _normalize_ai_evidence_references(check, entries, ai["findings"])
        contradictory_findings = _normalize_ai_evidence_references(check, entries, ai["contradictory_findings"])
        uncertainties = list(ai["uncertainties"])
        if check["missing_request_ids"]:
            uncertainties.append(
                "일부 상세 예측자료가 생성되지 않아 해당 확인 결과의 완결성이 제한됩니다."
            )
        if not relations_consistent:
            uncertainties.append(
                quantitative_failure_uncertainty(quantitative_failure_reasons)
            )
        usable_for_synthesis = bool(relations_consistent)
        exclusion_reasons = [] if usable_for_synthesis else list(quantitative_failure_reasons)
        majority_aggregation = _majority_convergence_aggregation_from_result(
            specification, {"quantitative_claims": quantitative_claims}
        )
        value = {
            "candidate_id": group_value["candidate_id"], "check_id": check["check_id"], "source_intent_hash": check.get("source_intent_hash"), "intent_contract_version": check.get("intent_contract_version"), "expected_signal": check["expected_signal"], "decision_relevance": check["decision_relevance"],
            "request_ids": check["request_ids"], "available_request_ids": check["available_request_ids"], "missing_request_ids": check["missing_request_ids"],
            "evidence_complete": check["evidence_complete"], "evidence_status": check["evidence_status"], "input_fingerprint": fingerprint,
            "assessment": ai["assessment"], "scope_coverage": ai["scope_coverage"],
            "spatial_localization": ai["spatial_localization"],
            "precipitation_motion": ai["precipitation_motion"],
            "findings": findings, "contradictory_findings": contradictory_findings,
            "quantitative_claims": quantitative_claims, "quantitative_relations": quantitative_relations,
            **({"majority_aggregation": majority_aggregation} if majority_aggregation is not None else {}),
            "quantitative_consistency_status": quantitative_consistency_status,
            "quantitative_consistency_failure_reasons": quantitative_failure_reasons,
            "quantitative_consistency_failure_details": quantitative_failure_details,
            "usable_for_synthesis": usable_for_synthesis,
            "excluded_from_synthesis_reasons": exclusion_reasons,
            "uncertainties": uncertainties, "provider_metadata": metadata,
        }
        if _single_majority_convergence_not_strengthen_claim(specification) is not None:
            if majority_aggregation is None:
                raise Pass2PipelineError("QUANTITATIVE_SCOPE_CONTRACT_INVALID", "수렴 다수권역 정량 집계가 누락되었습니다.")
            try:
                value = canonicalize_majority_result(value)
            except MajorityNarrativeError as exc:
                raise Pass2PipelineError("QUANTITATIVE_EXPECTED_STATE_MISMATCH", str(exc)) from exc
        validate_scope_coverage_contract(value)
        validate_check_evidence_references(
            check,
            entries,
            value,
            check_specification=specification,
        )
        validate_spatial_localization_contract(root, group_value, specification, value)
        validate_precipitation_motion_contract(root, group_value, specification, value)
        time_errors = _step07_forecast_time_narrative_errors(init, value)
        if time_errors:
            raise Pass2PipelineError("FORECAST_TIME_NARRATIVE_INVALID", "; ".join(time_errors[:12]))
        language_errors = validate_step07_result_narrative(value)
        if language_errors:
            raise Pass2PipelineError("NARRATIVE_LANGUAGE_INVALID", "; ".join(language_errors[:12]))
        values[(str(group_value["candidate_id"]), str(check["check_id"]))] = value
    return values


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


def _is_grounding_validation_error(
    error: Pass2PipelineError, *, response: dict[str, Any], rows: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str]],
) -> bool:
    """VLM grounding 재시도가 가능한 검증 오류인지 확인합니다."""
    if error.code == "EVIDENCE_REF_INVALID":
        return True
    if error.code in {
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
    if error.code == "QUANTITATIVE_CLAIM_INVALID":
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


def _can_second_grounding_retry(error: Pass2PipelineError) -> bool:
    """Allow one final LLM repair only for quantitative grounding-contract failures."""
    return error.code in SECOND_GROUNDING_RETRY_CODES


def dry_run_summary(package: dict[str, Any], *, candidate_id: str | None, config: dict[str, str], root: Path) -> dict[str, Any]:
    prompt_hash = template_text_and_hash(root, CHECK_PROMPT)[1]
    schema_hash = sha256sum(runtime_path(root, "prompts", "schemas", CHECK_RESPONSE_SCHEMA))
    checks: list[dict[str, Any]] = []
    bundles: dict[tuple[str, str], dict[str, Any]] = {}
    for group in active_candidate_groups(package, candidate_id):
        indexed = _entry_index(group)
        specifications = _check_specifications(group)
        for check in group.get("check_evidence", []):
            entries = [indexed[request_id] for request_id in check["request_ids"]]
            specification = specifications.get(str(check.get("check_id")))
            if specification is None:
                raise Pass2PipelineError("VARIABLE_CHECKS_INVALID", f"{group.get('candidate_id')} has no Step04 definition for {check.get('check_id')}.")
            fingerprint = _check_fingerprint(root, group, check, specification, entries, config, prompt_hash, schema_hash)
            checks.append({"candidate_id": group["candidate_id"], "check_id": check["check_id"], "request_ids": check["request_ids"], "evidence_complete": check["evidence_complete"], "evidence_status": check["evidence_status"], "check_scope": check.get("check_scope"), "missing_signal_components": check.get("missing_signal_components", []), "bundle_id": check["bundle_id"], "fingerprint": fingerprint, "cross_section_context": check.get("cross_section_context"), "time_height_context": check.get("time_height_context"), "temporal_precipitation_primary_feature": requires_temporal_precipitation_increment_claim(specification)})
            if check["available_request_ids"] and not check.get("missing_signal_components"):
                key = (str(group["candidate_id"]), str(check["bundle_id"]))
                bundle = bundles.setdefault(key, {"candidate_id": key[0], "bundle_id": key[1], "check_ids": [], "request_ids": check["request_ids"], "png_count": 0, "quantitative_request_count": 0})
                bundle["check_ids"].append(check["check_id"])
                available_entries = [entry for entry in entries if entry["request"]["request_id"] in check["available_request_ids"]]
                bundle["png_count"] = sum(len(entry["step05"].get("generated_images", [])) for entry in available_entries)
                bundle["quantitative_request_count"] = sum(1 for entry in available_entries if entry["request"].get("data_type") == "quantitative_excerpt")
    validate_schema(root, CHECK_RESULTS_SCHEMA, {"schema_version": CHECK_RESULTS_VERSION, "init": package["init"], "results": []})
    return {"checks": checks, "bundles": list(bundles.values()), "expected_api_calls": len(bundles) * ENSEMBLE_MEMBER_COUNT, "output_schema_valid": True}


def run_step07(
    *, root: Path, init: str, package: dict[str, Any], config: dict[str, str], provider: AIProvider | None,
    candidate_id: str | None = None, overwrite: bool = False,
    on_grounding_retry: Callable[[str, str, str, int], None] | None = None,
    on_quantitative_selector_dedup: Callable[[str, str, str, int], None] | None = None,
    on_quantitative_relation_selector_autofix: Callable[[str, str, str, int], None] | None = None,
    on_quantitative_relation_autocorrect: Callable[[str, str, str, str, str, str], None] | None = None,
    on_language_retry: Callable[[str, str, list[str], dict[str, Any] | None], None] | None = None,
    on_quantitative_relation_failure: Callable[[str, str, str], None] | None = None,
) -> dict[str, Any]:
    """Run/reuse checks and atomically checkpoint after every successful bundle."""
    output_path = step07_output_path(root, init)
    prompt_text, prompt_hash = template_text_and_hash(root, CHECK_PROMPT)
    schema_hash = sha256sum(runtime_path(root, "prompts", "schemas", CHECK_RESPONSE_SCHEMA))
    metadata = _provider_metadata(config, prompt_hash, schema_hash)
    active: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str]] = {}
    specifications_by_candidate: dict[str, dict[str, dict[str, Any]]] = {}
    selected_candidate_ids = {str(group["candidate_id"]) for group in active_candidate_groups(package, candidate_id)}
    for group in active_candidate_groups(package):
        indexed = _entry_index(group)
        specifications = _check_specifications(group)
        specifications_by_candidate[str(group["candidate_id"])] = specifications
        for check in group.get("check_evidence", []):
            entries = [indexed[request_id] for request_id in check["request_ids"]]
            specification = specifications.get(str(check.get("check_id")))
            if specification is None:
                raise Pass2PipelineError("VARIABLE_CHECKS_INVALID", f"{group.get('candidate_id')} has no Step04 definition for {check.get('check_id')}.")
            source_language_errors = [
                *validate_korean_text(check.get("expected_signal"), path=f"{group.get('candidate_id')}.{check.get('check_id')}.expected_signal"),
                *validate_korean_text(check.get("decision_relevance"), path=f"{group.get('candidate_id')}.{check.get('check_id')}.decision_relevance"),
            ]
            if source_language_errors:
                raise Pass2PipelineError(
                    "NARRATIVE_LANGUAGE_INVALID",
                    "Step04 확인 항목 자연어가 새 한글 기준을 충족하지 않습니다. Step04부터 다시 생성해야 합니다. "
                    + "; ".join(source_language_errors[:8]),
                )
            active[(str(group["candidate_id"]), str(check["check_id"]))] = (group, check, entries, _check_fingerprint(root, group, check, specification, entries, config, prompt_hash, schema_hash))
    existing_on_disk = load_json_object(output_path, code="STEP07_RESULT_INVALID") if output_path.is_file() else None
    if candidate_id is not None and (
        not isinstance(existing_on_disk, dict)
        or existing_on_disk.get("init") != init
        or existing_on_disk.get("schema_version") != CHECK_RESULTS_VERSION
    ):
        raise Pass2PipelineError(
            "STEP07_ENSEMBLE_BASELINE_REQUIRED",
            "Step07 ensemble v8 최초 전환 시에는 --candidate-id 없이 전체 후보를 한 번 재수행해야 합니다.",
        )
    existing = (
        existing_on_disk
        if isinstance(existing_on_disk, dict) and (not overwrite or candidate_id is not None)
        else {"schema_version": CHECK_RESULTS_VERSION, "init": init, "results": []}
    )
    if existing.get("init") != init or existing.get("schema_version") != CHECK_RESULTS_VERSION:
        existing = {"schema_version": CHECK_RESULTS_VERSION, "init": init, "results": []}
    else:
        validate_schema(root, CHECK_RESULTS_SCHEMA, existing, code="STEP07_RESULT_INVALID")
    old = {(str(item.get("candidate_id")), str(item.get("check_id"))): item for item in existing.get("results", []) if isinstance(item, dict)}
    report_path = step07_minority_report_path(root, init)
    report_existing = (
        load_json_object(report_path, code="STEP07_MINORITY_REPORT_INVALID")
        if report_path.is_file() and (not overwrite or candidate_id is not None)
        else {
            "schema_version": MINORITY_REPORT_VERSION,
            "init": init,
            "ensemble_contract_version": ENSEMBLE_CONTRACT_VERSION,
            "member_count": ENSEMBLE_MEMBER_COUNT,
            "entries": [],
        }
    )
    if (
        report_existing.get("schema_version") != MINORITY_REPORT_VERSION
        or report_existing.get("init") != init
    ):
        report_existing = {
            "schema_version": MINORITY_REPORT_VERSION,
            "init": init,
            "ensemble_contract_version": ENSEMBLE_CONTRACT_VERSION,
            "member_count": ENSEMBLE_MEMBER_COUNT,
            "entries": [],
        }
    else:
        validate_schema(root, MINORITY_REPORT_SCHEMA, report_existing, code="STEP07_MINORITY_REPORT_INVALID")
    minority_entries: dict[tuple[str, str], dict[str, Any]] = {
        (str(item.get("candidate_id")), str(item.get("check_id"))): item
        for item in report_existing.get("entries", [])
        if isinstance(item, dict)
        and (str(item.get("candidate_id")), str(item.get("check_id"))) in active
    }

    def commit_minority_report() -> dict[str, Any]:
        report = {
            "schema_version": MINORITY_REPORT_VERSION,
            "init": init,
            "ensemble_contract_version": ENSEMBLE_CONTRACT_VERSION,
            "member_count": ENSEMBLE_MEMBER_COUNT,
            "entries": [minority_entries[key] for key in sorted(minority_entries) if key in current],
        }
        step07_document = {
            "schema_version": CHECK_RESULTS_VERSION,
            "init": init,
            "results": [current[key] for key in sorted(current)],
        }
        validate_step07_minority_report_consistency(step07_document, report)
        write_validated_atomic(root, report_path, report, schema_name=MINORITY_REPORT_SCHEMA)
        return report
    # Drop only no-longer-active checks. A selected-candidate run must preserve
    # reusable results belonging to other active candidates.
    current: dict[tuple[str, str], dict[str, Any]] = {
        key: value for key, value in old.items()
        if key in active and key[0] not in selected_candidate_ids
    }
    pending_by_bundle: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str]]] = {}
    for key, values in active.items():
        group, check, entries, fingerprint = values
        if key[0] not in selected_candidate_ids:
            continue
        previous = old.get(key)
        previous_language_valid = (
            isinstance(previous, dict)
            and not validate_step07_result_narrative(previous)
        )
        previous_quantitative_contract_valid = (
            isinstance(previous, dict)
            and previous.get("quantitative_consistency_status") in {"not_applicable", "passed", "corrected", "failed"}
            and isinstance(previous.get("usable_for_synthesis"), bool)
            and isinstance(previous.get("quantitative_relations"), list)
        )
        specification = specifications_by_candidate[str(group["candidate_id"])][str(check["check_id"])]
        majority_check = _single_majority_convergence_not_strengthen_claim(specification) is not None
        # Prior v1 checkpoints can be migrated without regenerating three AI
        # members, but ONLY when their former fingerprint still matches every
        # Step05 input and the resolved quantitative cells pass validation.
        if (not overwrite and majority_check and isinstance(previous, dict)
                and previous.get("input_fingerprint") != fingerprint
                and previous_language_valid and previous_quantitative_contract_valid):
            legacy_fingerprint = _check_fingerprint(
                root, group, check, specification, entries, config, prompt_hash, schema_hash,
                include_majority_narrative_contract=False,
            )
            if previous.get("input_fingerprint") == legacy_fingerprint:
                validate_check_evidence_references(check, entries, previous, check_specification=specification)
                reference_aggregation = _majority_convergence_aggregation_from_result(specification, previous)
                if reference_aggregation != previous.get("majority_aggregation"):
                    raise Pass2PipelineError(
                        "QUANTITATIVE_EXPECTED_STATE_MISMATCH",
                        f"{key} 기존 정량 집계가 Step07 원자료와 일치하지 않습니다.",
                    )
                try:
                    migrated = canonicalize_majority_result(previous)
                except MajorityNarrativeError as exc:
                    raise Pass2PipelineError("QUANTITATIVE_EXPECTED_STATE_MISMATCH", str(exc)) from exc
                migrated["input_fingerprint"] = fingerprint
                previous = migrated
                print(f"STEP07_MAJORITY_NARRATIVE_MIGRATED candidate={key[0]} check={key[1]}")
        if (
            not overwrite
            and isinstance(previous, dict)
            and previous.get("input_fingerprint") == fingerprint
            and previous_language_valid
            and previous_quantitative_contract_valid
        ):
            if majority_check:
                try:
                    assert_majority_narrative_current(previous)
                except MajorityNarrativeError as exc:
                    raise Pass2PipelineError("QUANTITATIVE_EXPECTED_STATE_MISMATCH", str(exc)) from exc
            current[key] = previous
        elif not check["available_request_ids"] or check.get("missing_signal_components"):
            specification = specifications_by_candidate[str(group["candidate_id"])][str(check["check_id"])]
            current[key] = _insufficient_result(key[0], check, specification, fingerprint, metadata)
        else:
            pending_by_bundle.setdefault((key[0], str(check["bundle_id"])), []).append(values)
    def commit() -> dict[str, Any]:
        document = {"schema_version": CHECK_RESULTS_VERSION, "init": init, "results": [current[key] for key in sorted(current)]}
        for key, value in current.items():
            group, check, entries, _fingerprint = active[key]
            specification = specifications_by_candidate[str(group["candidate_id"])][str(check["check_id"])]
            validate_check_evidence_references(check, entries, value, check_specification=specification)
            validate_spatial_localization_contract(root, group, specification, value)
            validate_precipitation_motion_contract(root, group, specification, value)
            validate_step07_ensemble_consensus(value)
        write_validated_atomic(root, output_path, document, schema_name=CHECK_RESULTS_SCHEMA)
        return document
    if current:
        commit()
        commit_minority_report()
    for (_candidate, _bundle), rows in sorted(pending_by_bundle.items()):
        if provider is None:
            raise Pass2PipelineError("PROVIDER_REQUIRED", "Step07 requires a provider when complete evidence is pending.")
        group = rows[0][0]
        checks = [row[1] for row in rows]
        entries_by_id = _entry_index(group)
        available_request_ids = rows[0][1]["available_request_ids"]
        def execute_ensemble_member(member_index: int) -> dict[tuple[str, str], dict[str, Any]]:
            print(
                f"STEP07_ENSEMBLE_MEMBER_START candidate={_candidate} bundle={_bundle} "
                f"member={member_index}/{ENSEMBLE_MEMBER_COUNT}"
            )
            messages = _bundle_messages(root, init, group, checks, [entries_by_id[item] for item in available_request_ids], prompt_text)
            response = _call_bundle(provider, root, messages, config)
            direction_fixes = normalize_step07_response_directions(response)
            if direction_fixes:
                print(
                    f"STEP07_DIRECTION_NORMALIZED candidate={_candidate} bundle={_bundle} "
                    f"member={member_index}/{ENSEMBLE_MEMBER_COUNT} fixes={direction_fixes} stage=initial"
                )
            language_errors = validate_step07_response_narrative(response)
            if language_errors:
                target_check_ids = _step07_language_error_check_ids(response, language_errors)
                print(
                    f"STEP07_LANGUAGE_RETRY candidate={_candidate} bundle={_bundle} "
                    f"violations={len(language_errors)} attempt=1 unit=full_check checks={','.join(target_check_ids)}"
                )
                language_error = Pass2PipelineError(
                    "NARRATIVE_LANGUAGE_INVALID", "; ".join(language_errors[:12])
                )
                response = _regenerate_step07_assessment_units(
                    provider, root, init, group, rows, entries_by_id, prompt_text, config,
                    response, target_check_ids, language_error,
                )
                direction_fixes = normalize_step07_response_directions(response)
                if direction_fixes:
                    print(
                        f"STEP07_DIRECTION_NORMALIZED candidate={_candidate} bundle={_bundle} "
                        f"member={member_index}/{ENSEMBLE_MEMBER_COUNT} fixes={direction_fixes} stage=language_retry_1"
                    )
                remaining_language_errors = validate_step07_response_narrative(response)
                if remaining_language_errors:
                    second_targets = _step07_language_error_check_ids(response, remaining_language_errors)
                    print(
                        f"STEP07_LANGUAGE_RETRY candidate={_candidate} bundle={_bundle} "
                        f"violations={len(remaining_language_errors)} attempt=2 unit=full_check checks={','.join(second_targets)}"
                    )
                    response = _regenerate_step07_assessment_units(
                        provider, root, init, group, rows, entries_by_id, prompt_text, config,
                        response, second_targets,
                        Pass2PipelineError("NARRATIVE_LANGUAGE_INVALID", "; ".join(remaining_language_errors[:12])),
                    )
                    direction_fixes = normalize_step07_response_directions(response)
                    if direction_fixes:
                        print(
                            f"STEP07_DIRECTION_NORMALIZED candidate={_candidate} bundle={_bundle} "
                            f"member={member_index}/{ENSEMBLE_MEMBER_COUNT} fixes={direction_fixes} stage=language_retry_2"
                        )
                    final_language_errors = validate_step07_response_narrative(response)
                    if final_language_errors:
                        raise Pass2PipelineError(
                            "NARRATIVE_LANGUAGE_INVALID", "; ".join(final_language_errors[:12])
                        )
                if on_language_retry is not None:
                    try:
                        on_language_retry(_candidate, _bundle, language_errors, None)
                    except Exception:
                        pass
            try:
                bundle_values = _bundle_result_values(
                    root=root,
                    init=init,
                    response=response,
                    rows=rows,
                    metadata=metadata,
                    specifications_by_candidate=specifications_by_candidate,
                    on_quantitative_selector_dedup=on_quantitative_selector_dedup,
                    on_quantitative_relation_selector_autofix=on_quantitative_relation_selector_autofix,
                    on_quantitative_relation_autocorrect=on_quantitative_relation_autocorrect,
                )
            except Pass2PipelineError as exc:
                _write_step07_grounding_failure(
                    root, init, candidate=_candidate, bundle=_bundle, member=member_index,
                    attempt=0, error=exc, response=response, rows=rows,
                )
                if not _is_grounding_validation_error(exc, response=response, rows=rows):
                    raise
                grounding_error = exc
                grounding_response = response
                bundle_values = None
                for grounding_attempt in range(1, MAX_GROUNDING_RETRY_ATTEMPTS + 1):
                    target_check_ids = _step07_failing_check_ids(
                        root, init, grounding_response, rows, metadata, specifications_by_candidate
                    )
                    print(
                        f"STEP07_GROUNDING_RETRY candidate={_candidate} bundle={_bundle} "
                        f"reason={grounding_error.code} attempt={grounding_attempt} "
                        f"unit=full_check checks={','.join(target_check_ids)}"
                    )
                    if on_grounding_retry is not None:
                        try:
                            on_grounding_retry(_candidate, _bundle, grounding_error.code, grounding_attempt)
                        except Exception:
                            pass
                    retry_response = _regenerate_step07_assessment_units(
                        provider, root, init, group, rows, entries_by_id, prompt_text, config,
                        grounding_response, target_check_ids, grounding_error,
                    )
                    direction_fixes = normalize_step07_response_directions(retry_response)
                    if direction_fixes:
                        print(
                            f"STEP07_DIRECTION_NORMALIZED candidate={_candidate} bundle={_bundle} "
                            f"member={member_index}/{ENSEMBLE_MEMBER_COUNT} fixes={direction_fixes} "
                            f"stage=grounding_retry_{grounding_attempt}"
                        )

                    language_errors = validate_step07_response_narrative(retry_response)
                    if language_errors:
                        language_targets = _step07_language_error_check_ids(retry_response, language_errors)
                        print(
                            f"STEP07_LANGUAGE_RETRY candidate={_candidate} bundle={_bundle} "
                            f"violations={len(language_errors)} attempt={grounding_attempt} "
                            f"unit=full_check checks={','.join(language_targets)}"
                        )
                        retry_response = _regenerate_step07_assessment_units(
                            provider, root, init, group, rows, entries_by_id, prompt_text, config,
                            retry_response, language_targets,
                            Pass2PipelineError("NARRATIVE_LANGUAGE_INVALID", "; ".join(language_errors[:12])),
                        )
                        direction_fixes = normalize_step07_response_directions(retry_response)
                        if direction_fixes:
                            print(
                                f"STEP07_DIRECTION_NORMALIZED candidate={_candidate} bundle={_bundle} "
                                f"member={member_index}/{ENSEMBLE_MEMBER_COUNT} fixes={direction_fixes} "
                                f"stage=grounding_language_retry_{grounding_attempt}"
                            )
                        remaining_language_errors = validate_step07_response_narrative(retry_response)
                        if remaining_language_errors:
                            raise Pass2PipelineError(
                                "NARRATIVE_LANGUAGE_INVALID", "; ".join(remaining_language_errors[:12])
                            )
                    try:
                        bundle_values = _bundle_result_values(
                            root=root,
                            init=init,
                            response=retry_response,
                            rows=rows,
                            metadata=metadata,
                            specifications_by_candidate=specifications_by_candidate,
                            on_quantitative_selector_dedup=on_quantitative_selector_dedup,
                            on_quantitative_relation_selector_autofix=on_quantitative_relation_selector_autofix,
                            on_quantitative_relation_autocorrect=on_quantitative_relation_autocorrect,
                            quantitative_relation_pass_status=(
                                "corrected" if grounding_error.code == "QUANTITATIVE_RELATION_MISMATCH" else "passed"
                            ),
                        )
                        break
                    except Pass2PipelineError as retry_exc:
                        _write_step07_grounding_failure(
                            root, init, candidate=_candidate, bundle=_bundle, member=member_index,
                            attempt=grounding_attempt, error=retry_exc,
                            response=retry_response, rows=rows,
                        )
                        if (
                            grounding_attempt < MAX_GROUNDING_RETRY_ATTEMPTS
                            and _is_grounding_validation_error(retry_exc, response=retry_response, rows=rows)
                        ):
                            grounding_error = retry_exc
                            grounding_response = retry_response
                            continue
                        if retry_exc.code not in QUANTITATIVE_FAILURE_REASONS:
                            raise
                        print(
                            f"STEP07_QUANTITATIVE_RELATION_FAILED candidate={_candidate} bundle={_bundle} "
                            f"reason={retry_exc.code}"
                        )
                        if on_quantitative_relation_failure is not None:
                            try:
                                on_quantitative_relation_failure(_candidate, _bundle, str(retry_exc))
                            except Exception:
                                pass
                        bundle_values = _bundle_result_values(
                            root=root,
                            init=init,
                            response=retry_response,
                            rows=rows,
                            metadata=metadata,
                            specifications_by_candidate=specifications_by_candidate,
                            on_quantitative_selector_dedup=on_quantitative_selector_dedup,
                            on_quantitative_relation_selector_autofix=on_quantitative_relation_selector_autofix,
                            on_quantitative_relation_autocorrect=on_quantitative_relation_autocorrect,
                            quantitative_relation_pass_status="corrected",
                            allow_quantitative_relation_mismatch=True,
                        )
                        break
                if bundle_values is None:
                    raise Pass2PipelineError(
                        "GROUNDING_RETRY_EXHAUSTED",
                        f"candidate={_candidate} bundle={_bundle} grounding retry ended without a validated result.",
                    )
            print(
                f"STEP07_ENSEMBLE_MEMBER_OK candidate={_candidate} bundle={_bundle} "
                f"member={member_index}/{ENSEMBLE_MEMBER_COUNT}"
            )
            return bundle_values

        member_bundle_values = [
            execute_ensemble_member(member_index)
            for member_index in range(1, ENSEMBLE_MEMBER_COUNT + 1)
        ]
        consensus_values: dict[tuple[str, str], dict[str, Any]] = {}
        fresh_report_entries: dict[tuple[str, str], dict[str, Any]] = {}
        expected_keys = set(member_bundle_values[0])
        if any(set(values) != expected_keys for values in member_bundle_values[1:]):
            raise Pass2PipelineError(
                "STEP07_ENSEMBLE_INVALID",
                f"candidate={_candidate} bundle={_bundle} ensemble members returned different Check sets.",
            )
        for result_key in sorted(expected_keys):
            consensus_result, report_entry = build_step07_ensemble_consensus(
                [values[result_key] for values in member_bundle_values]
            )
            try:
                consensus_result = canonicalize_majority_result(consensus_result)
            except MajorityNarrativeError as exc:
                raise Pass2PipelineError("QUANTITATIVE_EXPECTED_STATE_MISMATCH", str(exc)) from exc
            validate_step07_ensemble_consensus(consensus_result)
            consensus_values[result_key] = consensus_result
            minority_entries.pop(result_key, None)
            if report_entry is not None:
                fresh_report_entries[result_key] = report_entry
        minority_entries.update(fresh_report_entries)
        current.update(consensus_values)
        commit()
        commit_minority_report()
    final_document = commit()
    commit_minority_report()
    return final_document
