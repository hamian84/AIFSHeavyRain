"""step05: 구성된 LLM provider로 Pass 1 후보 탐색을 다단계 수행한다.

실행 흐름
---------
1. Step04 입력 패키지와 overview 첨부파일을 사전 검증한다.
2. 같은 시간그룹의 overview를 최대 4장씩 묶어 독립적인 영상 관찰을 수행한다.
3. 각 배치의 구조화 관찰 JSON을 저장하고 검증한다.
4. 검증된 그룹 요약으로 후보를 탐색하고 후보 확정 순서를 결정한다.
5. 후보마다 독립적인 API 요청으로 Pass2 분석계획과 자료요청을 생성한다.
6. 검증된 후보·계획을 Python에서 결정론적으로 병합하고 최종 계약을 검증한다.

API key는 소스코드에 저장하지 않는다. 운영체제 환경변수를 우선 사용하고,
프로젝트 루트의 envi/api_keys.env 파일을 보조 설정원으로 읽는다.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import logging
import math
import os
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable

PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_FOR_IMPORT))

from func.shared.output_policy import (  # noqa: E402
    OutputPolicy,
    add_output_mode_argument,
    resolve_output_mode,
    sanitize_failure_text,
)
from func.shared.ai_provider import (  # noqa: E402
    AIProvider,
    IMAGE_DETAILS,
    OFFICIAL_OPENAI_BASE_URL,
    ProviderConfigurationError,
    ProviderError as SharedProviderError,
    ProviderHTTPError as SharedProviderHTTPError,
    ProviderIncompleteError as SharedProviderIncompleteError,
    ProviderResponse,
    build_chat_completions_http_payload,
    build_provider,
    normalized_endpoint_id,
    normalize_openai_transport_json,
    provider_defaults,
    resolve_ai_settings,
    validate_openai_capabilities,
    validate_openai_transport_schema,
)
from func.shared.env_loader import ProjectEnvLoad, load_project_env  # noqa: E402
from func.shared.runtime_paths import logical_ref, resolve_logical_ref, runtime_path  # noqa: E402
from func.shared.operational_regions import selectable_pass1_regions  # noqa: E402
from func.shared.korean_narrative import (  # noqa: E402
    validate_step04_compact_plan_narrative,
    validate_step04_discovery_narrative,
    validate_step04_narrative as validate_forecaster_step04_narrative,
    validate_step04_plan_narrative,
)
from func.shared.quantitative_contract_guard import (
    absolute_threshold_in_hour_comparison, inconsistent_pairwise_hour_order, opposite_majority_not_strengthen_wording,
)
from func.shared.signal_validation import (  # noqa: E402
    DIRECT_PRECIPITATION_MINIMUM_MM_6H,
    NON_POSITIVE_PRECURSOR_EXPECTED_STATES,
    has_independent_precursor_support,
    infer_legacy_candidate_signal_contract,
    validate_candidate_signal_contract,
)


DEFAULT_PROVIDER = "openai"
SUPPORTED_IMAGE_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}
SYNTHESIS_MODES = ("direct", "hierarchical")
GROUP_SUMMARY_SCHEMA_VERSION = "1.1.0"
GROUP_SUMMARY_CONTRACT_VERSION = "1.1.0"
GROUP_SUMMARY_TARGET_BYTES = 4 * 1024
GROUP_SUMMARY_MAX_BYTES = 8 * 1024
HIERARCHICAL_PAYLOAD_WARN_BYTES = 100 * 1024
HIERARCHICAL_FINAL_MAX_BYTES = 256 * 1024
CANDIDATE_DISCOVERY_INPUT_PROJECTION_VERSION = "1.0.0"
CANDIDATE_DISCOVERY_GROUP_SUMMARY_COMPACTION_TRIGGER_BYTES = 3 * 1024
SPLIT_SYNTHESIS_CONTRACT_VERSION = "3.1.0"
CANDIDATE_DISCOVERY_METEOROLOGICAL_QC_VERSION = "1.1.0"
CANDIDATE_DISCOVERY_OPERATIONAL_SCOPE_CONTRACT_VERSION = "1.0.1"
SYSTEM_DIRECT_PRECIPITATION_COVERAGE_AUDIT_VERSION = "1.1.0"
DIRECT_PRECIPITATION_COVERAGE_RECONCILIATION_VERSION = "1.5.0"
COMPACT_CANDIDATE_PLAN_CONTRACT_VERSION = "3.12.3"
COMPACT_CANDIDATE_PLAN_NORMALIZATION_VERSION = "1.0.0"
CHECK_INTENT_CONTRACT_VERSION = "1.5.0"
ATOMIC_CLAIM_CONTRACT_VERSION = "0.4.0"
ATOMIC_CLAIM_VALIDATION_MODE = "shadow"
CAPABILITY_VALIDATION_MODE = "shadow"
COMPACT_CANDIDATE_PLAN_EXPANDER_VERSION = "3.7.1"
COMPACT_CANDIDATE_PLAN_COVERAGE_VERSION = "1.1.0"
CANONICAL_PLAN_MAX_HYPOTHESES = 4
CANONICAL_PLAN_MAX_FORECASTER_QUESTIONS = 6
NARRATIVE_LANGUAGE = "ko"
# High-reasoning multimodal observations can consume more combined reasoning
# and visible-output budget than the previous 3600-token recovery ceiling.
COMPACT_OBSERVATION_RECOVERY_MAX_TOKENS = 8000

# Across-band vertical sections are diagnostic cuts through the candidate core,
# not synoptic-scale transects. Keep enough margin to sample both sides of the
# precipitation band while preventing a context-domain bbox from stretching a
# section across an unnecessarily large part of East Asia.
ACROSS_SECTION_MAX_LENGTH_KM = 450.0
ACROSS_SECTION_MARGIN_FRACTION = 0.10
ACROSS_SECTION_MARGIN_MIN_DEGREES = 0.20
ACROSS_SECTION_MARGIN_MAX_DEGREES = 0.60

SPATIAL_ANALYSIS_METHODS = {"detailed_map", "variable_cross_check"}
REGIONAL_ANALYSIS_METHODS = {
    "regional_statistics",
    "regional_ranking",
    "regional_max_location",
    "temporal_evolution",
}
OPERATIONAL_DIAGNOSTIC_IDS = {
    "detailed_spatial_map",
    "accumulated_precipitation",
    "multivariable_composite",
    "vertical_cross_section",
    "time_height_section",
    "moisture_flux",
    "horizontal_convergence",
    "vertical_wind_shear",
    "temporal_evolution",
}

# These diagnostics currently use dedicated spatial/section renderers and do not
# expose the operational-regions quantitative-excerpt contract. Regional statistics,
# ranking, max-location, and separate time-series requests must come from supported
# component-variable checks. Section methods are canonicalized before validation.
SPATIAL_ONLY_DIAGNOSTIC_IDS = {"multivariable_composite", "vertical_cross_section", "time_height_section", "vertical_wind_shear"}
SECTION_DIAGNOSTIC_IDS = {"vertical_cross_section", "time_height_section"}
CANDIDATE_SCOPE_ONLY_DIAGNOSTIC_IDS = {"time_height_section"}
# Temporal intent is a contract of the Check, not merely a property of one
# renderer.  A spatial map Check may therefore legitimately use the temporal
# scope when it compares two or more forecast times.
MINIMUM_FORECAST_HOURS_BY_DIAGNOSTIC = {
    "temporal_evolution": 2,
    "time_height_section": 2,
}

# Only the diagnostic/role pairs that are unambiguous are constrained here.
# Flexible map/profile diagnostics retain the model-selected explanatory role.
DIAGNOSTIC_REQUIRED_VARIABLE_ROLES: dict[str, set[str]] = {
    "accumulated_precipitation": {"hazard_magnitude"},
    "moisture_flux": {"moisture_supply"},
    "horizontal_convergence": {"low_level_forcing"},
    "vertical_wind_shear": {"organization_persistence"},
    "multivariable_composite": {"synoptic_support", "uncertainty"},
}

# Step04 AI describes meteorological verification intent only.  The execution
# contract below is owned by Python so the model never has to compose internal
# diagnostic_id/check_scope/analysis_methods combinations.
VERIFICATION_SIGNAL_COMPONENTS = (
    "direct_precipitation",
    "moisture_supply",
    "low_level_forcing",
    "mid_upper_forcing",
    "organization_persistence",
)
VERIFICATION_MODES = (
    "spatial_distribution",
    "regional_intensity",
    "temporal_change",
    "spatial_evolution",
    "multivariable_alignment",
    "vertical_structure",
    "vertical_time_evolution",
    "precipitation_amount",
)
REGIONAL_VERIFICATION_MODES = {
    "regional_intensity",
    "temporal_change",
    "precipitation_amount",
}
SPATIAL_RELATION_SCOPES = (
    "candidate_interior",
    "candidate_boundary_crossing",
    "candidate_external_context",
    "not_applicable",
)
VERIFICATION_SIGNAL_TO_ROLE = {
    "direct_precipitation": "hazard_magnitude",
    "moisture_supply": "moisture_supply",
    "low_level_forcing": "low_level_forcing",
    "mid_upper_forcing": "midlevel_forcing",
    "organization_persistence": "organization_persistence",
}
VERIFICATION_SIGNAL_TO_SPATIAL_DIAGNOSTIC = {
    "direct_precipitation": "detailed_spatial_map",
    "moisture_supply": "moisture_flux",
    "low_level_forcing": "horizontal_convergence",
    "mid_upper_forcing": "detailed_spatial_map",
    "organization_persistence": "vertical_wind_shear",
}


def _verification_role_for_components(components: list[str]) -> str:
    for component in ("moisture_supply", "mid_upper_forcing", "organization_persistence"):
        if component in components:
            return VERIFICATION_SIGNAL_TO_ROLE[component]
    if components:
        return VERIFICATION_SIGNAL_TO_ROLE.get(components[0], "synoptic_support")
    return "synoptic_support"


def _derive_diagnostic_check_from_verification_request(row: dict[str, Any]) -> dict[str, Any]:
    """Translate one AI meteorological verification request into an executable Check.

    AI owns WHEN/WHERE/WHAT/WHY. Python owns the internal execution grammar.
    This boundary prevents model output from failing on combinations such as a
    spatial-only diagnostic paired with regional/time-series analysis methods.
    """
    components = list(dict.fromkeys(
        str(value) for value in row.get("signal_components", []) if str(value)
    ))
    unknown = sorted(set(components) - set(VERIFICATION_SIGNAL_COMPONENTS))
    if not components or unknown:
        raise ValueError(
            f"verification request check_key={row.get('check_key')!r} has invalid "
            f"signal_components={components} unknown={unknown}"
        )
    mode = str(row.get("verification_mode") or "")
    if mode not in VERIFICATION_MODES:
        raise ValueError(
            f"verification request check_key={row.get('check_key')!r} has unsupported "
            f"verification_mode={mode!r}"
        )

    domain_scope = str(row.get("domain_scope") or "candidate")
    spatial_relation_scope = str(row.get("spatial_relation_scope") or "not_applicable")
    if spatial_relation_scope not in SPATIAL_RELATION_SCOPES:
        raise ValueError(
            f"verification request check_key={row.get('check_key')!r} has unsupported "
            f"spatial_relation_scope={spatial_relation_scope!r}"
        )
    if spatial_relation_scope in {"candidate_boundary_crossing", "candidate_external_context"} and domain_scope != "context":
        raise ValueError(
            "SPATIAL_RELATION_DOMAIN_SCOPE_MISMATCH "
            f"check_key={row.get('check_key')} spatial_relation_scope={spatial_relation_scope} "
            f"domain_scope={domain_scope}; repair=claims that require the candidate boundary, "
            "outside-candidate location, approach/exit, or surrounding external source context "
            "must use domain_scope=context with a context_domain that contains candidate_area"
        )
    cross_section_line = None
    cross_section_strategy = None
    reference_band_axis = None

    if mode == "multivariable_alignment":
        allowed = {
            "direct_precipitation", "moisture_supply",
            "low_level_forcing", "mid_upper_forcing",
        }
        if len(components) < 2 or not set(components).issubset(allowed):
            raise ValueError(
                f"verification request check_key={row.get('check_key')!r} "
                "multivariable_alignment requires at least two of direct_precipitation, "
                "moisture_supply, low_level_forcing, mid_upper_forcing"
            )
        diagnostic_id = "multivariable_composite"
        variable_role = "synoptic_support"
        check_scope = "multivariable_alignment"
        analysis_methods = ["detailed_map", "variable_cross_check"]
    elif mode in {"vertical_structure", "vertical_time_evolution"}:
        allowed = {"moisture_supply", "mid_upper_forcing", "organization_persistence"}
        if not set(components).issubset(allowed):
            raise ValueError(
                f"verification request check_key={row.get('check_key')!r} {mode} only supports "
                "moisture_supply, mid_upper_forcing, organization_persistence"
            )
        variable_role = _verification_role_for_components(components)
        check_scope = "vertical_structure"
        analysis_methods = ["detailed_map"]
        if mode == "vertical_structure":
            diagnostic_id = "vertical_cross_section"
            cross_section_line = _cross_section_line_signature(row.get("cross_section_line"))
            cross_section_strategy = _normalized_cross_section_strategy(
                row.get("cross_section_strategy")
            )
            reference_band_axis = _reference_band_axis_signature(
                row.get("reference_band_axis")
            )
            if cross_section_line is None or cross_section_strategy is None or reference_band_axis is None:
                raise ValueError(
                    f"verification request check_key={row.get('check_key')!r} vertical_structure "
                    "requires cross_section_line, cross_section_strategy, and reference_band_axis"
                )
        else:
            diagnostic_id = "time_height_section"
            domain_scope = "candidate"
    else:
        if len(components) != 1:
            raise ValueError(
                f"verification request check_key={row.get('check_key')!r} mode={mode!r} "
                f"requires exactly one signal component; actual={components}"
            )
        component = components[0]
        variable_role = VERIFICATION_SIGNAL_TO_ROLE[component]
        if mode == "precipitation_amount":
            if component != "direct_precipitation":
                raise ValueError(
                    f"verification request check_key={row.get('check_key')!r} "
                    "precipitation_amount requires direct_precipitation"
                )
            diagnostic_id = "accumulated_precipitation"
            check_scope = "single_variable"
            analysis_methods = ["detailed_map", "regional_statistics", "regional_max_location"]
        elif mode == "temporal_change":
            if component == "organization_persistence":
                raise ValueError(
                    f"verification request check_key={row.get('check_key')!r} temporal_change "
                    "does not support organization_persistence; use spatial_evolution"
                )
            diagnostic_id = "temporal_evolution"
            check_scope = "temporal_evolution"
            # Temporal-change checks keep the complete operational-region time
            # series and also request AI-selected representative spatial maps.
            # Python never chooses those map hours; it only executes the hours
            # explicitly selected by the Step04 AI.
            analysis_methods = ["detailed_map", "temporal_evolution"]
            domain_scope = "candidate"
        else:
            diagnostic_id = VERIFICATION_SIGNAL_TO_SPATIAL_DIAGNOSTIC[component]
            if mode == "spatial_distribution":
                check_scope = "single_variable"
                analysis_methods = ["detailed_map"]
            elif mode == "regional_intensity":
                if component == "organization_persistence":
                    raise ValueError(
                        f"verification request check_key={row.get('check_key')!r} regional_intensity "
                        "does not support organization_persistence; use spatial_distribution"
                    )
                check_scope = "single_variable"
                analysis_methods = [
                    "detailed_map", "regional_statistics",
                    "regional_ranking", "regional_max_location",
                ]
            elif mode == "spatial_evolution":
                check_scope = "temporal_evolution"
                analysis_methods = ["detailed_map"]
            else:
                raise ValueError(
                    f"verification request check_key={row.get('check_key')!r} has unsupported "
                    f"verification_mode={mode!r}"
                )

    check = {
        "check_key": row.get("check_key"),
        "verification_mode": mode,
        "variable_role": variable_role,
        "diagnostic_id": diagnostic_id,
        "domain_scope": domain_scope,
        "spatial_relation_scope": spatial_relation_scope,
        "phase_keys": list(row.get("phase_keys", [])),
        "representative_spatial_hours": [
            int(value)
            for value in row.get("representative_spatial_hours", [])
            if isinstance(value, int) and not isinstance(value, bool)
        ],
        "analysis_methods": analysis_methods,
        "expected_signal": row.get("expected_signal"),
        "decision_relevance": row.get("decision_relevance"),
        "priority": row.get("priority"),
        "evidence_role": row.get("evidence_role"),
        "coverage_roles": list(row.get("coverage_roles", [])),
        "purpose": row.get("purpose"),
        "atomic_claims": copy.deepcopy(row.get("atomic_claims", [])) if isinstance(row.get("atomic_claims"), list) else [],
        "cross_section_line": cross_section_line,
        "cross_section_strategy": cross_section_strategy,
        "reference_band_axis": reference_band_axis,
        "check_scope": check_scope,
        "required_signal_components": components,
    }
    check["coverage_roles"] = _effective_compact_check_coverage_roles(check)
    return check


_STEP04_FORECAST_HOUR_TOKEN = re.compile(
    r"(?i)(?<![A-Za-z0-9_])F\s*(\d{1,3})(?![A-Za-z0-9_])"
)


def _canonicalize_step04_forecast_hour_text(
    value: Any,
    *,
    allowed_hours: set[int],
) -> tuple[Any, list[tuple[str, str]]]:
    """Canonicalize recoverable Step04 F-hour tokens without changing meaning.

    Only forecast hours that are already available for the active candidate are
    rewritten.  For example, ``F96`` becomes ``F096`` when 96 is an available
    forecast hour.  Invalid or unavailable hours such as ``F97`` are left
    untouched so the existing validators can reject them.
    """
    if not isinstance(value, str) or not value:
        return value, []

    replacements: list[tuple[str, str]] = []

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        hour = int(match.group(1))
        if hour not in allowed_hours:
            return raw
        canonical = f"F{hour:03d}"
        if raw != canonical:
            replacements.append((raw, canonical))
        return canonical

    return _STEP04_FORECAST_HOUR_TOKEN.sub(replace, value), replacements


def _canonicalize_step04_candidate_plan_forecast_hours(
    value: dict[str, Any],
    *,
    candidate: dict[str, Any],
    attachment_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Normalize forecast-hour spelling in Step04 human-readable plan text.

    This is formatting canonicalization only.  It never invents a forecast hour
    and never changes the meteorological claim selected by the model.
    """
    normalized = copy.deepcopy(value)
    allowed_hours = set(_available_candidate_forecast_hours(candidate, attachment_manifest))
    if not allowed_hours:
        return normalized

    changed: list[tuple[str, str, str]] = []

    def rewrite(container: dict[str, Any], field: str, path: str) -> None:
        if field not in container:
            return
        rewritten, replacements = _canonicalize_step04_forecast_hour_text(
            container.get(field), allowed_hours=allowed_hours
        )
        container[field] = rewritten
        changed.extend((path, before, after) for before, after in replacements)

    rewrite(normalized, "analysis_objective", "analysis_objective")
    rewrite(normalized, "context_domain_reason", "context_domain_reason")

    for i, hypothesis in enumerate(normalized.get("hypotheses", [])):
        if isinstance(hypothesis, dict):
            rewrite(hypothesis, "statement", f"hypotheses[{i}].statement")

    for i, phase in enumerate(normalized.get("analysis_phases", [])):
        if isinstance(phase, dict):
            rewrite(phase, "purpose", f"analysis_phases[{i}].purpose")

    for i, request in enumerate(normalized.get("verification_requests", [])):
        if not isinstance(request, dict):
            continue
        for field in ("purpose", "expected_signal", "decision_relevance"):
            rewrite(request, field, f"verification_requests[{i}].{field}")
        line = request.get("cross_section_line")
        if isinstance(line, dict):
            rewrite(
                line,
                "selection_reason",
                f"verification_requests[{i}].cross_section_line.selection_reason",
            )
        for j, claim in enumerate(request.get("atomic_claims", [])):
            if not isinstance(claim, dict):
                continue
            for field in ("statement", "scope_description"):
                rewrite(
                    claim,
                    field,
                    f"verification_requests[{i}].atomic_claims[{j}].{field}",
                )

    questions = normalized.get("forecaster_questions")
    if isinstance(questions, list):
        for i, question in enumerate(questions):
            rewritten, replacements = _canonicalize_step04_forecast_hour_text(
                question, allowed_hours=allowed_hours
            )
            questions[i] = rewritten
            changed.extend(
                (f"forecaster_questions[{i}]", before, after)
                for before, after in replacements
            )

    if changed:
        logging.info(
            "step04 forecast-hour tokens canonicalized candidate_id=%s count=%s changes=%s",
            candidate.get("candidate_id"),
            len(changed),
            ", ".join(
                f"{path}:{before}->{after}" for path, before, after in changed[:12]
            ),
        )
    return normalized


def _normalize_ai_verification_chunk_for_validation(
    value: dict[str, Any],
    previous_chunks: list[dict[str, Any]],
    *,
    chunk_index: int,
    transport_schema: dict[str, Any],
    candidate: dict[str, Any],
    attachment_manifest: dict[str, Any],
) -> dict[str, Any]:
    # Apply the same representation-only normalizer before provider-schema
    # validation so harmless duplicates/order issues do not fail before reaching
    # the central semantic normalization layer. No meteorological intent is
    # changed here.
    normalized = copy.deepcopy(value)
    transport_changes = _normalize_candidate_plan_representation_fields(normalized)
    if transport_changes:
        _log_step04_advisories(
            "candidate_plan.contract_normalization.transport",
            list(dict.fromkeys(transport_changes)),
        )
    schema_errors = _validate_jsonschema(normalized, transport_schema)
    if schema_errors:
        raise ValueError(
            "candidate-plan verification transport schema invalid: "
            + " | ".join(schema_errors[:20])
        )
    normalized = _canonicalize_step04_candidate_plan_forecast_hours(
        normalized,
        candidate=candidate,
        attachment_manifest=attachment_manifest,
    )
    requests = normalized.pop("verification_requests", None)
    if not isinstance(requests, list):
        raise ValueError("candidate-plan verification_requests must be an object array")
    for row in requests:
        _validate_ai_verification_request_intent(row)
    normalized["diagnostic_checks"] = [
        _derive_diagnostic_check_from_verification_request(row)
        for row in requests
    ]
    return _normalize_compact_chunk_for_validation(
        normalized, previous_chunks, chunk_index=chunk_index
    )


def _verification_mode_from_diagnostic_check(row: dict[str, Any]) -> str:
    diagnostic_id = str(row.get("diagnostic_id") or "")
    scope = str(row.get("check_scope") or "")
    methods = {str(value) for value in row.get("analysis_methods", [])}
    if diagnostic_id == "multivariable_composite":
        return "multivariable_alignment"
    if diagnostic_id == "vertical_cross_section":
        return "vertical_structure"
    if diagnostic_id == "time_height_section":
        return "vertical_time_evolution"
    if diagnostic_id == "accumulated_precipitation":
        return "precipitation_amount"
    if diagnostic_id == "temporal_evolution" or "temporal_evolution" in methods:
        return "temporal_change"
    if scope == "temporal_evolution":
        return "spatial_evolution"
    if methods & {"regional_statistics", "regional_ranking", "regional_max_location"}:
        return "regional_intensity"
    return "spatial_distribution"


def _canonical_variable_role_for_diagnostic(diagnostic_id: str, variable_role: str) -> str:
    """Normalize composite-role labels without replacing component checks.

    A multivariable composite compares precipitation, moisture, low-level
    convergence, and midlevel ascent in one spatial product. The model may
    label that check with one component role, but the composite should remain
    a cross-variable support check so dedicated component checks continue to
    provide coverage and quantitative evidence.
    """
    if diagnostic_id == "multivariable_composite" and variable_role in {
        "hazard_magnitude",
        "moisture_supply",
        "low_level_forcing",
        "midlevel_forcing",
    }:
        return "synoptic_support"
    return variable_role
COMPACT_COVERAGE_ROLES = (
    "hazard_magnitude",
    "moisture_supply",
    "low_level_forcing",
    "mid_level_forcing",
    "upper_level_forcing",
    "instability_or_stability",
    "temporal_evolution",
    "spatial_evolution",
    "terrain_or_regional_effect",
    "supporting_evidence",
    "opposing_evidence",
    "forecast_uncertainty",
)

# Coverage roles that are direct consequences of the structured verification
# contract are owned by Python, not by the model.  GPT models may still emit
# coverage_roles for planning/provenance, but these deterministic roles are
# recomputed from signal_components, verification_mode, and evidence_role.
# This prevents continuation loops where a valid temporal/spatial Check exists
# but the model omitted only the corresponding coverage tag.
_DETERMINISTIC_COMPACT_COVERAGE_ROLES = {
    "hazard_magnitude",
    "moisture_supply",
    "low_level_forcing",
    "mid_level_forcing",
    "temporal_evolution",
    "spatial_evolution",
    "supporting_evidence",
    "opposing_evidence",
}

_VERIFICATION_COMPONENT_TO_COVERAGE_ROLE = {
    "direct_precipitation": "hazard_magnitude",
    "moisture_supply": "moisture_supply",
    "low_level_forcing": "low_level_forcing",
    "mid_upper_forcing": "mid_level_forcing",
}

def _effective_compact_check_coverage_roles(row: dict[str, Any]) -> list[str]:
    """Return canonical coverage roles implied by one verification Check.

    Structural coverage must follow the executable verification contract rather
    than a free model-authored tag.  Non-structural roles (for example
    forecast_uncertainty or terrain_or_regional_effect) remain model-planned.
    """
    declared = [
        str(value)
        for value in row.get("coverage_roles", [])
        if str(value) in COMPACT_COVERAGE_ROLES
        and str(value) not in _DETERMINISTIC_COMPACT_COVERAGE_ROLES
    ]

    components_raw = row.get("required_signal_components")
    if not isinstance(components_raw, list):
        components_raw = row.get("signal_components", [])
    components = [str(value) for value in components_raw if str(value)]

    inferred: list[str] = []
    for component in components:
        role = _VERIFICATION_COMPONENT_TO_COVERAGE_ROLE.get(component)
        if role:
            inferred.append(role)

    mode = str(row.get("verification_mode") or "")
    if not mode and (row.get("diagnostic_id") or row.get("check_scope") or row.get("analysis_methods")):
        mode = _verification_mode_from_diagnostic_check(row)

    # A spatial-evolution diagnosis necessarily compares multiple times and
    # therefore satisfies both time-evolution and spatial-evolution coverage.
    if mode in {"temporal_change", "spatial_evolution", "vertical_time_evolution"}:
        inferred.append("temporal_evolution")
    if mode == "spatial_evolution":
        inferred.append("spatial_evolution")

    evidence_role = str(row.get("evidence_role") or "context")
    if evidence_role == "supporting":
        inferred.append("supporting_evidence")
    elif evidence_role == "opposing":
        inferred.append("opposing_evidence")

    return list(dict.fromkeys([*declared, *inferred]))

CHECK_SCOPES = {
    "single_variable",
    "multivariable_alignment",
    "temporal_evolution",
    "vertical_structure",
}
SIGNAL_COMPONENTS = {
    "direct_precipitation",
    "moisture_supply",
    "low_level_forcing",
    "mid_upper_forcing",
    "thermodynamic_support",
    "organization_persistence",
    "synoptic_support",
    "uncertainty",
}

def _product_signal_components_from_support(
    product_id: str, support_catalog: dict[str, Any],
) -> set[str]:
    """Return product capability copied from diagnostic_catalog.yaml by Step03."""
    for product in support_catalog.get("spatial_field_products", []):
        if isinstance(product, dict) and str(product.get("product_id")) == str(product_id):
            values = product.get("signal_components")
            if isinstance(values, list):
                return {str(value) for value in values if str(value) in SIGNAL_COMPONENTS}
    return set()


def _derived_check_scope(diagnostic_id: str, analysis_methods: list[str] | tuple[str, ...] | set[str]) -> str:
    """Return a deterministic fallback scope for legacy/non-compact planning paths."""
    methods = {str(value) for value in analysis_methods}
    if diagnostic_id == "multivariable_composite":
        return "multivariable_alignment"
    if diagnostic_id in {"vertical_cross_section", "time_height_section"}:
        return "vertical_structure"
    if diagnostic_id == "temporal_evolution" or "temporal_evolution" in methods:
        return "temporal_evolution"
    return "single_variable"


def _check_scope_contract_errors(
    *,
    check_key: str,
    diagnostic_id: str,
    analysis_methods: list[str] | tuple[str, ...] | set[str],
    check_scope: str,
    required_signal_components: list[str] | tuple[str, ...] | set[str],
) -> list[str]:
    """Return common Step04 Check scope-contract errors.

    This is shared by compact planning, canonical expansion, and final-plan
    validation.  It enforces only necessary implications: diagnostics or methods
    that intrinsically require a scope must declare it, and every declared scope
    must be executable with its declared components.  It deliberately does *not*
    require the inverse relation.  For example, a detailed spatial-map Check can
    validly use ``temporal_evolution`` to compare maps at multiple times.
    """
    methods = _canonical_analysis_methods_for_diagnostic(
        diagnostic_id,
        analysis_methods,
    )
    errors: list[str] = []
    if check_scope not in CHECK_SCOPES:
        return [
            f"check_key={check_key} has unsupported check_scope={check_scope!r}"
        ]
    if diagnostic_id == "temporal_evolution" or "temporal_evolution" in methods:
        if check_scope != "temporal_evolution":
            errors.append(
                f"check_key={check_key} temporal diagnostic/method must declare "
                "check_scope=temporal_evolution"
            )
    if diagnostic_id == "multivariable_composite":
        if check_scope != "multivariable_alignment":
            errors.append(
                f"check_key={check_key} multivariable_composite must declare "
                "check_scope=multivariable_alignment"
            )
    if diagnostic_id in SECTION_DIAGNOSTIC_IDS:
        if check_scope != "vertical_structure":
            errors.append(
                f"check_key={check_key} section diagnostic must declare "
                "check_scope=vertical_structure"
            )
    if diagnostic_id in SPATIAL_ONLY_DIAGNOSTIC_IDS and set(methods) & REGIONAL_ANALYSIS_METHODS:
        errors.append(
            f"check_key={check_key} {diagnostic_id} is spatial-only; use component-variable checks for "
            "regional statistics, ranking, max location, and time series"
        )

    components = [
        str(value)
        for value in required_signal_components
        if str(value)
    ]
    if not components or any(value not in SIGNAL_COMPONENTS for value in components):
        errors.append(
            f"check_key={check_key} has invalid required_signal_components={components}"
        )
        return errors
    unique_components = set(components)
    if check_scope == "multivariable_alignment":
        if diagnostic_id != "multivariable_composite" or len(unique_components) < 2:
            errors.append(
                f"check_key={check_key} multivariable_alignment requires "
                "multivariable_composite and at least two signal components"
            )
    elif check_scope == "vertical_structure":
        # A section diagnostic is a single analysis object, but it can expose
        # several physical signal components at once (for example moisture,
        # ascent, and wind structure in VERTICAL_CROSS_SECTION_CORE).  Component
        # capability is validated separately against the active Step05 product
        # catalog, so do not collapse a valid section check to one component.
        if diagnostic_id not in SECTION_DIAGNOSTIC_IDS:
            errors.append(
                f"check_key={check_key} vertical_structure requires a section diagnostic"
            )
    elif len(unique_components) != 1:
        errors.append(
            f"check_key={check_key} {check_scope} requires exactly one signal component; "
            f"actual={components}"
        )
    return errors


def _validate_check_scope_contract(
    *,
    check_key: str,
    diagnostic_id: str,
    analysis_methods: list[str] | tuple[str, ...] | set[str],
    check_scope: str,
    required_signal_components: list[str] | tuple[str, ...] | set[str],
) -> None:
    """Raise the first shared Step04 Check scope-contract error."""
    errors = _check_scope_contract_errors(
        check_key=check_key,
        diagnostic_id=diagnostic_id,
        analysis_methods=analysis_methods,
        check_scope=check_scope,
        required_signal_components=required_signal_components,
    )
    if errors:
        raise ValueError(errors[0])


def _minimum_forecast_hours_for_check(
    diagnostic_id: str,
    check_scope: str,
) -> int:
    """Return the minimum distinct forecast hours required by one Check."""
    return max(
        MINIMUM_FORECAST_HOURS_BY_DIAGNOSTIC.get(diagnostic_id, 1),
        2 if check_scope == "temporal_evolution" else 1,
    )


def _normalized_required_signal_components(
    values: Any,
    *,
    variable_role: str,
    diagnostic_id: str,
) -> list[str]:
    """Normalize model-declared required signal components without inferring outcomes."""
    if isinstance(values, list):
        result = list(dict.fromkeys(str(value) for value in values if str(value) in SIGNAL_COMPONENTS))
    else:
        result = []
    if not result:
        raise ValueError(
            "candidate-plan check must explicitly declare required_signal_components "
            f"diagnostic_id={diagnostic_id!r} variable_role={variable_role!r}"
        )
    return result


def _executable_products_for_required_components(
    *,
    diagnostic_id: str,
    required_signal_components: list[str] | tuple[str, ...] | set[str],
    support_catalog: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Return executable products whose catalog capability covers one Check.

    Step03's diagnostic_execution_support is authoritative for executable
    diagnostic/product pairs. Product signal-components are copied from the
    same diagnostic catalog. If an older fixture does not carry component
    metadata, compatibility alone is retained for backward-compatible tests.
    """
    execution_support = support_catalog.get("diagnostic_execution_support", {})
    contract = (
        execution_support.get(diagnostic_id, {})
        if isinstance(execution_support, dict)
        else {}
    )
    if not isinstance(contract, dict):
        return [], []
    available_product_ids = {
        str(item.get("product_id"))
        for item in support_catalog.get("spatial_field_products", [])
        if isinstance(item, dict) and item.get("product_id")
    }
    compatible = [
        str(value)
        for value in contract.get("compatible_product_ids", [])
        if str(value) in available_product_ids
    ]
    required = {str(value) for value in required_signal_components if str(value)}
    component_metadata_seen = False
    executable: list[str] = []
    for product_id in compatible:
        components = _product_signal_components_from_support(product_id, support_catalog)
        if components:
            component_metadata_seen = True
            if required.issubset(components):
                executable.append(product_id)
    if not component_metadata_seen:
        return list(dict.fromkeys(compatible)), list(dict.fromkeys(compatible))
    return list(dict.fromkeys(executable)), list(dict.fromkeys(compatible))


def _validate_compact_check_execution_contract(
    *,
    check_key: str,
    diagnostic_id: str,
    required_signal_components: list[str] | tuple[str, ...] | set[str],
    support_catalog: dict[str, Any],
) -> None:
    """Fail inside the repairable compact-plan stage if Step05 cannot execute a Check."""
    executable, compatible = _executable_products_for_required_components(
        diagnostic_id=diagnostic_id,
        required_signal_components=required_signal_components,
        support_catalog=support_catalog,
    )
    if executable:
        return
    raise ValueError(
        "CHECK_EXECUTION_COMPONENT_UNAVAILABLE "
        f"check_key={check_key} diagnostic_id={diagnostic_id!r} "
        f"required_signal_components={sorted({str(value) for value in required_signal_components if str(value)})} "
        f"compatible_product_ids={compatible}; "
        "the active Step05 contract has no executable product that provides all required components"
    )


class Pass1Error(RuntimeError):
    """Pass 1 execution failure."""


class ResponseValidationError(Pass1Error):
    """A provider response was received but did not satisfy the local contract."""


class CandidatePlanError(Pass1Error):
    """A candidate-specific plan failed before its checkpoint could be written."""

    def __init__(self, *, candidate_id: str, cause: Exception) -> None:
        self.candidate_id = candidate_id
        diagnostics = (
            dict(cause.diagnostics)
            if isinstance(getattr(cause, "diagnostics", None), dict)
            else {}
        )
        # Failed candidates do not have a reusable plan checkpoint, so retain
        # their bounded atomic-repair history in the sanitized failure summary.
        # This is metadata only; it never becomes a substitute for a valid
        # candidate plan or an opportunity to patch individual fields.
        diagnostics.update({"stage": "candidate_plan", "candidate_id": candidate_id})
        self.diagnostics = diagnostics
        super().__init__(f"candidate plan failed candidate_id={candidate_id}: {cause}")


class ProviderIncompleteError(Pass1Error):
    """Pass through sanitized OpenAI incomplete-generation diagnostics."""

    def __init__(self, message: str, *, diagnostics: dict[str, Any]) -> None:
        self.diagnostics = dict(diagnostics)
        super().__init__(message)


class ProviderHTTPError(Pass1Error):
    def __init__(self, status: int, body: str, message: str | None = None) -> None:
        self.status = int(status)
        self.body = body
        self.body_bytes = len(body.encode("utf-8", errors="replace"))
        self.body_sha256_prefix = hashlib.sha256(
            body.encode("utf-8", errors="replace")
        ).hexdigest()[:12]
        super().__init__(
            message
            or (
                f"provider HTTP status={status} body_bytes={self.body_bytes} "
                f"body_sha256_prefix={self.body_sha256_prefix}"
            )
        )


@dataclass(frozen=True)
class ProviderResult:
    content: str
    response_envelope: dict[str, Any]
    structured_output_used: bool
    provider_extensions_used: bool
    http_retry_count: int = 0
    structured_output_fallback_count: int = 0
    usage_attempts: tuple[dict[str, Any] | None, ...] = ()


def _canonical_analysis_methods_for_diagnostic(
    diagnostic_id: str,
    methods: Iterable[Any],
) -> list[str]:
    """Return executable analysis methods for one diagnostic.

    ``time_height_section`` and ``vertical_cross_section`` already contain their
    own spatial/temporal axes and produce dedicated PNG plus structured JSON.
    Regional statistics, ranking, maximum-location, and a separate temporal
    evolution request are therefore not executable for these section products.
    Structured-output models may still emit those generic methods; normalize such
    responses to the dedicated section renderer instead of failing the whole plan.
    """
    ordered = list(dict.fromkeys(str(item) for item in methods))
    if diagnostic_id in SECTION_DIAGNOSTIC_IDS:
        return ["detailed_map"]
    if diagnostic_id == "multivariable_composite":
        retained = [item for item in ordered if item in SPATIAL_ANALYSIS_METHODS]
        if "detailed_map" not in retained:
            retained.insert(0, "detailed_map")
        return retained
    return ordered


def _candidate_unit_type(candidate: dict[str, Any]) -> str:
    """Return the deterministic Step04 candidate unit semantics.

    Direct-precipitation and mixed candidates are peak-centred monitoring
    episodes. A qualified precursor-only candidate has no precipitation peak,
    so it is represented as a precursor episode rather than being mislabeled
    as a peak episode.
    """
    return (
        "precursor_episode"
        if str(candidate.get("detection_basis")) == "precursor_environment"
        else "peak_episode"
    )


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rel(root: Path, path: Path) -> str:
    return logical_ref(root, path)


def setup_logging(root: Path, init: str) -> Path:
    path = runtime_path(root, "logs", init, "05_run_ai_pass1.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(path, mode="w", encoding="utf-8"), logging.StreamHandler(sys.stdout)],
        force=True,
    )
    return path


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return value


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def report_call_metadata(call_meta: dict[str, Any], output_policy: OutputPolicy) -> dict[str, Any]:
    """Retain operational repair/canonicalization facts without provider detail."""
    result = {
        key: call_meta.get(key, 0)
        for key in (
            "structured_output_fallback_count",
            "source_grounded_retry_attempt_count",
            "repair_attempt_count",
            "http_retry_count",
        )
    }
    canonicalized = call_meta.get("canonicalized_fields", [])
    if output_policy.should_write("provider_metadata"):
        result["canonicalized_fields"] = canonicalized
        result["fixed_field_mismatches"] = call_meta.get("fixed_field_mismatches", [])
    else:
        result["canonicalized_fields"] = [
            {
                key: item[key]
                for key in ("field", "reason", "source_request_id", "added_values")
                if key in item
            }
            for item in canonicalized
            if isinstance(item, dict)
        ]
        reasons = sorted(
            {
                str(item.get("reason", "fixed_field"))
                for item in canonicalized
                if isinstance(item, dict)
            }
        )
        result["canonicalization_summary"] = {
            "count": len(canonicalized),
            "reasons": reasons,
        }
    return result


def checkpoint_retry_summary(call_meta: dict[str, Any]) -> dict[str, int]:
    """Keep only non-sensitive retry counters in resumable checkpoints."""
    return {
        key: int(call_meta.get(key, 0))
        for key in (
            "structured_output_fallback_count",
            "source_grounded_retry_attempt_count",
            "repair_attempt_count",
            "http_retry_count",
        )
    }


def checkpoint_repair_history(call_meta: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Retain sanitized Step04 regeneration history in resumable checkpoints.

    Provider bodies and free-form plan text stay out of operational checkpoints.
    The retained rows are limited to the candidate/check identity, bounded retry
    accounting, local validation errors, and regeneration outcome.
    """
    keys = (
        "candidate_id",
        "stage",
        "check_key",
        "repair_unit",
        "repair_attempt",
        "repair_attempt_limit",
        "validation_errors",
        "next_validation_errors",
        "result",
    )

    def sanitize(rows: Any) -> list[dict[str, Any]]:
        if not isinstance(rows, list):
            return []
        return [
            {key: row[key] for key in keys if key in row}
            for row in rows
            if isinstance(row, dict)
        ]

    return {
        "atomic_check_regenerations": sanitize(call_meta.get("atomic_repair_history")),
        "candidate_chunk_regenerations": sanitize(call_meta.get("candidate_chunk_repair_history")),
    }


def checkpoint_usage_summary(call_meta: dict[str, Any]) -> dict[str, Any]:
    """Retain normalized token accounting without provider bodies or request text."""
    usage = call_meta.get("usage")
    return usage if isinstance(usage, dict) else {"attempts": [], "stage_total": None}


def _client_checkpoint_configuration(
    client: Any,
    *,
    stage: str | None = None,
) -> dict[str, Any]:
    """Keep unit-test adapters compatible while production adapters are explicit."""
    if stage is not None:
        scoped = getattr(client, "checkpoint_configuration_for_stage", None)
        if callable(scoped):
            value = scoped(stage)
            return value if isinstance(value, dict) else {}
    value = getattr(client, "checkpoint_configuration", None)
    return value if isinstance(value, dict) else {}


def _summarize_usage_attempts(attempts: list[dict[str, Any] | None]) -> dict[str, Any]:
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
    )
    totals: dict[str, int | None] = {}
    for field in fields:
        values = [item.get(field) if isinstance(item, dict) else None for item in attempts]
        totals[field] = (
            sum(values)
            if values and all(isinstance(value, int) for value in values)
            else None
        )
    return {"attempts": attempts, "stage_total": totals}


def write_transient_failure_summary(
    *,
    pass1_dir: Path,
    root: Path,
    stage: str,
    exc: Exception,
) -> None:
    """Write a sanitized transient summary for the compatibility engine workspace."""
    safe_stage = re.sub(r"[^A-Za-z0-9_.-]+", "_", stage)
    summary_path = pass1_dir / "failures" / safe_stage / "failure_summary.json"
    write_json(
        summary_path,
        {
            "failed_step": safe_stage,
            "failed_candidate_id": getattr(exc, "candidate_id", None),
            "failed_request_id": None,
            "error_type": type(exc).__name__,
            "sanitized_message": sanitize_failure_text(exc, project_root=str(root)),
            "resume_available": True,
            **(
                {"incomplete_diagnostics": dict(exc.diagnostics)}
                if isinstance(getattr(exc, "diagnostics", None), dict)
                else {}
            ),
        },
    )


def write_final_response_artifacts(
    *,
    pass1_dir: Path,
    final_response: dict[str, Any],
    final_raw: str,
    output_policy: OutputPolicy,
) -> dict[str, Path]:
    """Materialize only the final artifacts permitted by the output policy."""
    raw_path = pass1_dir / "ai_candidate_response.raw.txt"
    parsed_path = pass1_dir / "ai_candidate_response.json"
    validated_path = pass1_dir / "ai_candidate_response.validated.json"
    if output_policy.should_write("provider_raw"):
        atomic_write_text(raw_path, final_raw + "\n")
    if output_policy.should_write("intermediate_parsed_json"):
        write_json(parsed_path, final_response)
    write_json(validated_path, final_response)
    return {
        "raw": raw_path,
        "parsed": parsed_path,
        "validated": validated_path,
    }


def write_observation_artifacts(
    *,
    observation_path: Path,
    raw_path: Path,
    provider_path: Path,
    observation: dict[str, Any],
    raw: str,
    provider_summary: dict[str, Any],
    call_meta: dict[str, Any],
    elapsed_seconds: float,
    output_policy: OutputPolicy,
) -> None:
    """Persist the resume checkpoint and only the allowed observation diagnostics."""
    if output_policy.should_write("provider_raw"):
        atomic_write_text(raw_path, raw + "\n")
    write_json(observation_path, observation)
    if output_policy.should_write("provider_metadata"):
        write_json(
            provider_path,
            {**provider_summary, **call_meta, "elapsed_seconds": elapsed_seconds},
        )


def sha256sum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def json_utf8_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _safe_identity_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 256 else value[:256] + "...<truncated>"
    return f"<{type(value).__name__}>"


def _nested_value(value: dict[str, Any], field: str) -> tuple[bool, Any]:
    current: Any = value
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def inspect_fixed_fields(value: dict[str, Any], expected: dict[str, Any]) -> list[dict[str, Any]]:
    """Record provider identity mismatches before canonical system repair."""
    if not isinstance(value, dict):
        return [{"field": "$", "expected": "object", "actual": _safe_identity_value(value)}]
    mismatches: list[dict[str, Any]] = []
    for field, expected_value in expected.items():
        present, actual_value = _nested_value(value, field)
        if not present:
            continue
        if actual_value != expected_value:
            mismatches.append(
                {
                    "field": field,
                    "expected": _safe_identity_value(expected_value),
                    "actual": _safe_identity_value(actual_value),
                    "provider_value": _safe_identity_value(actual_value),
                    "canonical_value": _safe_identity_value(expected_value),
                }
            )
    return mismatches


def _validate_jsonschema(instance: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    try:
        import jsonschema  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit("jsonschema is required. Install: pip install jsonschema") from exc
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    rendered: list[str] = []
    for error in errors:
        location = ".".join(str(value) for value in error.absolute_path) or "$"
        validator_name = str(error.validator)
        if validator_name == "maxItems":
            rendered.append(f"{location}: item count exceeds maximum {error.validator_value}")
        elif validator_name == "minItems":
            rendered.append(f"{location}: item count is below minimum {error.validator_value}")
        elif validator_name == "required":
            rendered.append(f"{location}: required property is missing")
        elif validator_name == "additionalProperties":
            unexpected: list[str] = []
            if isinstance(error.instance, dict) and isinstance(error.schema, dict):
                allowed = error.schema.get("properties")
                if isinstance(allowed, dict):
                    unexpected = sorted(str(key) for key in set(error.instance) - set(allowed))
            if unexpected:
                rendered.append(
                    f"{location}: unsupported properties are present: {unexpected}"
                )
            else:
                rendered.append(f"{location}: unsupported property is present")
        elif validator_name == "enum":
            rendered.append(f"{location}: value is outside the allowed enum")
        elif validator_name == "type":
            rendered.append(f"{location}: value has an invalid type")
        elif validator_name == "pattern":
            rendered.append(f"{location}: value does not match the required pattern")
        elif validator_name == "const":
            rendered.append(f"{location}: value does not match the required constant")
        else:
            rendered.append(f"{location}: schema {validator_name} constraint failed")
    return rendered


def _normalize_message_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        texts: list[str] = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                texts.append(item["text"])
            elif isinstance(item, str):
                texts.append(item)
        return "".join(texts)
    if value is None:
        return ""
    return str(value)


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip().lstrip("\ufeff")
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    candidates: list[tuple[int, dict[str, Any]]] = []
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, end = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append((end, value))
    if not candidates:
        raise ValueError("provider response does not contain a valid JSON object")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _retry_after_seconds(headers: Any) -> float | None:
    try:
        value = headers.get("Retry-After")
    except Exception:  # noqa: BLE001
        return None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _error_mentions(body: str, terms: Iterable[str]) -> bool:
    lowered = body.lower()
    return any(term.lower() in lowered for term in terms)


def build_http_payload(
    *,
    model: str,
    messages: list[dict[str, Any]],
    response_schema: dict[str, Any] | None,
    schema_name: str,
    structured_output: bool,
    enable_thinking: bool,
    extensions: bool,
    max_tokens: int,
    temperature: float,
    top_p: float,
) -> dict[str, Any]:
    return build_chat_completions_http_payload(
        model=model,
        messages=messages,
        response_schema=response_schema,
        schema_name=schema_name,
        structured_output=structured_output,
        enable_thinking=enable_thinking,
        extensions=extensions,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
    )


class Pass1ProviderClient:
    """Compatibility adapter that keeps Pass1 repair flow provider-neutral."""

    _TRANSPORT_STAGE_BY_SCHEMA = {
        "AIFSPass1BatchObservation": "batch_observation",
        "AIFSPass1BatchObservationCompactRecovery": "batch_observation",
        "AIFSPass1SingleImageObservation": "batch_observation",
        "AIFSPass1GroupSummary": "group_summary",
        "AIFSPass1GroupSummaryCompaction": "group_summary",
        "AIFSPass1CandidateDiscovery": "candidate_discovery",
        "AIFSPass1CandidatePlan": "candidate_plan",
        "AIFSPass1CandidateResponse": "candidate_response",
    }

    _REASONING_STAGE_BY_SCHEMA = {
        "AIFSPass1BatchObservation": "observation",
        "AIFSPass1BatchObservationCompactRecovery": "observation",
        "AIFSPass1SingleImageObservation": "observation",
        "AIFSPass1GroupSummary": "group_summary",
        "AIFSPass1GroupSummaryCompaction": "group_summary",
        "AIFSPass1CandidateDiscovery": "candidate_discovery",
        "AIFSPass1CandidatePlan": "candidate_plan",
        "AIFSPass1CandidateResponse": "candidate_discovery",
    }

    def __init__(
        self,
        *,
        provider: AIProvider,
        image_detail: str,
        reasoning_efforts: dict[str, str],
        transport_schemas: dict[str, dict[str, Any]],
    ) -> None:
        self.provider = provider
        self.image_detail = image_detail
        self.reasoning_efforts = dict(reasoning_efforts)
        self.transport_schemas = transport_schemas
        if self.provider.api_family == "responses":
            for stage, schema in sorted(self.transport_schemas.items()):
                try:
                    validate_openai_transport_schema(schema)
                except ProviderConfigurationError as exc:
                    raise ProviderConfigurationError(
                        f"invalid OpenAI transport schema stage={stage}: {exc}"
                    ) from exc
        self.transport_schema_hashes = {
            stage: hashlib.sha256(
                json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            for stage, schema in transport_schemas.items()
        }
        self.last_result: ProviderResult | None = None

    @property
    def checkpoint_configuration(self) -> dict[str, Any]:
        return {
            "provider": self.provider.provider_name,
            "api_family": self.provider.api_family,
            "endpoint_id": self.provider.endpoint_id,
            "image_detail": self.image_detail if self.provider.api_family == "responses" else None,
            "reasoning_efforts": self.reasoning_efforts if self.provider.api_family == "responses" else None,
            "structured_output_mode": "strict" if self.provider.api_family == "responses" else "legacy",
            "transport_schema_sha256": self.transport_schema_hashes if self.provider.api_family == "responses" else None,
        }

    def checkpoint_configuration_for_stage(self, stage: str) -> dict[str, Any]:
        configuration = self.checkpoint_configuration
        if configuration.get("api_family") != "responses":
            return configuration
        reasoning_stage = {
            "batch_observation": "observation",
            "group_summary": "group_summary",
            "candidate_discovery": "candidate_discovery",
            "candidate_plan": "candidate_plan",
        }[stage]
        reasoning_efforts = configuration.get("reasoning_efforts")
        if isinstance(reasoning_efforts, dict):
            configuration["reasoning_efforts"] = {
                reasoning_stage: reasoning_efforts.get(reasoning_stage)
            }
        transport_hashes = configuration.get("transport_schema_sha256")
        if isinstance(transport_hashes, dict):
            configuration["transport_schema_sha256"] = {
                stage: transport_hashes.get(stage)
            }
        return configuration

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        response_schema: dict[str, Any] | None,
        schema_name: str,
        structured_output_mode: str,
        enable_thinking: bool,
        max_tokens: int,
        temperature: float,
        top_p: float,
    ) -> ProviderResult:
        stage = self._TRANSPORT_STAGE_BY_SCHEMA.get(schema_name)
        canonical_schema = response_schema
        dynamic_candidate_plan_transport = schema_name in {
            "AIFSPass1CandidatePlanDiagnosticCheckRepair",
            "AIFSPass1CandidatePlanContinuationSpatialEvolutionGapRepair",
        }
        if self.provider.api_family == "responses":
            if dynamic_candidate_plan_transport:
                if not isinstance(response_schema, dict):
                    raise Pass1Error(
                        f"OpenAI dynamic transport schema is unavailable for stage schema {schema_name}"
                    )
                try:
                    validate_openai_transport_schema(response_schema)
                except ProviderConfigurationError as exc:
                    raise Pass1Error(
                        f"invalid OpenAI dynamic transport schema for stage schema {schema_name}: {exc}"
                    ) from exc
            else:
                if stage is None or stage not in self.transport_schemas:
                    raise Pass1Error(f"OpenAI transport schema is unavailable for stage schema {schema_name}")
                response_schema = self.transport_schemas[stage]
        reasoning_stage = (
            "candidate_plan"
            if dynamic_candidate_plan_transport
            else self._REASONING_STAGE_BY_SCHEMA.get(schema_name, "candidate_discovery")
        )
        reasoning_effort = self.reasoning_efforts.get(reasoning_stage)
        try:
            response: ProviderResponse = self.provider.complete(
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                schema=response_schema,
                schema_name=schema_name,
                structured_output_mode=structured_output_mode,
                enable_thinking=enable_thinking,
                temperature=temperature,
                top_p=top_p,
                image_detail=self.image_detail,
                reasoning_effort=reasoning_effort,
            )
        except SharedProviderIncompleteError as exc:
            raise ProviderIncompleteError(str(exc), diagnostics=exc.diagnostics) from exc
        except SharedProviderHTTPError as exc:
            raise ProviderHTTPError(exc.status, "", message=str(exc)) from exc
        except SharedProviderError as exc:
            raise Pass1Error(str(exc)) from exc
        content = response.content
        if self.provider.api_family == "responses" and canonical_schema is not None:
            # ``response_schema`` is transport schema here; canonicalization needs the
            # original caller schema saved before its OpenAI substitution.
            content = normalize_openai_transport_json(content, canonical_schema)
        result = ProviderResult(
            content=content,
            response_envelope=response.compact_envelope(),
            structured_output_used=response.structured_output_used,
            provider_extensions_used=response.provider_extensions_used,
            http_retry_count=response.retry_count,
            structured_output_fallback_count=response.structured_output_fallback_count,
            usage_attempts=response.attempt_usage,
        )
        if response.finish_reason == "length":
            logging.warning(
                "provider response reached output length limit provider=%s api_family=%s schema=%s",
                self.provider.provider_name,
                self.provider.api_family,
                schema_name,
            )
        self.last_result = result
        return result


def image_data_url(path: Path) -> str:
    mime = SUPPORTED_IMAGE_SUFFIXES.get(path.suffix.lower())
    if mime is None:
        raise ValueError(f"unsupported image format: {path}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _attachment_by_id(attachment_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    attachments = attachment_manifest.get("attachments")
    if not isinstance(attachments, list):
        raise ValueError("attachment manifest attachments must be an array")
    result: dict[str, dict[str, Any]] = {}
    for item in attachments:
        if not isinstance(item, dict):
            raise ValueError("attachment manifest item must be an object")
        asset_id = str(item.get("asset_id", ""))
        if not asset_id:
            raise ValueError("attachment asset_id is missing")
        if asset_id in result:
            raise ValueError(f"duplicate attachment asset_id: {asset_id}")
        result[asset_id] = item
    return result


def verify_attachments(root: Path, attachment_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_id = _attachment_by_id(attachment_manifest)
    declared_count = int(attachment_manifest.get("attachment_count", -1))
    if declared_count != len(by_id):
        raise ValueError(f"attachment_count mismatch declared={declared_count} actual={len(by_id)}")
    for asset_id, item in by_id.items():
        file_path = resolve_logical_ref(root, str(item.get("file_path", "")))
        if not file_path.is_file():
            raise FileNotFoundError(f"attachment missing asset_id={asset_id}: {file_path}")
        expected_size = item.get("size_bytes")
        if expected_size is not None and int(expected_size) != file_path.stat().st_size:
            raise ValueError(f"attachment size mismatch asset_id={asset_id}")
        expected_hash = str(item.get("sha256", ""))
        if expected_hash and sha256sum(file_path) != expected_hash:
            raise ValueError(f"attachment sha256 mismatch asset_id={asset_id}")
    return by_id


def derive_stable_batches(
    attachment_manifest: dict[str, Any],
    *,
    max_images_per_request: int,
) -> list[dict[str, Any]]:
    if max_images_per_request < 1 or max_images_per_request > 4:
        raise ValueError("max_images_per_request must be between 1 and 4")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in attachment_manifest.get("attachments", []):
        tier = str(item.get("analysis_tier", "unknown"))
        group_id = str(item.get("group_id", "ungrouped"))
        groups[(tier, group_id)].append(item)

    tier_order = {"temporal_core": 0, "synoptic_context": 1}
    batches: list[dict[str, Any]] = []
    for (tier, group_id), items in sorted(
        groups.items(), key=lambda pair: (tier_order.get(pair[0][0], 99), pair[0][0], pair[0][1])
    ):
        items.sort(key=lambda item: (str(item.get("product_id")), int(item.get("forecast_hour_start", 0))))
        for chunk_index in range(0, len(items), max_images_per_request):
            chunk = items[chunk_index : chunk_index + max_images_per_request]
            suffix = "" if len(items) <= max_images_per_request else f"_P{chunk_index // max_images_per_request + 1:02d}"
            batch_id = f"PASS1_OBS_{tier}_{group_id}{suffix}".upper()
            batches.append(
                {
                    "batch_id": batch_id,
                    "stage": "image_observation",
                    "analysis_tier": tier,
                    "group_id": group_id,
                    "attachment_count": len(chunk),
                    "attachment_ids": [str(item["asset_id"]) for item in chunk],
                    "forecast_hours": sorted(
                        {int(hour) for item in chunk for hour in item.get("forecast_hours", [])}
                    ),
                    "source_panel_count": sum(len(item.get("source_panels", [])) for item in chunk),
                }
            )
    return batches


def normalize_batch_plan(
    batch_plan: dict[str, Any],
    attachment_manifest: dict[str, Any],
    *,
    max_images_per_request: int,
) -> list[dict[str, Any]]:
    planned = batch_plan.get("batches")
    use_planned = isinstance(planned, list) and planned
    batches: list[dict[str, Any]] = []
    if use_planned:
        for item in planned:
            if not isinstance(item, dict):
                use_planned = False
                break
            ids = [str(value) for value in item.get("attachment_ids", [])]
            if not ids or len(ids) > max_images_per_request or str(item.get("stage", "")) not in {
                "image_observation",
                "pass1_image_observation",
            }:
                use_planned = False
                break
            row = dict(item)
            row["attachment_ids"] = ids
            row["attachment_count"] = len(ids)
            batches.append(row)
    if not use_planned:
        batches = derive_stable_batches(
            attachment_manifest,
            max_images_per_request=max_images_per_request,
        )

    declared_ids = [str(item["asset_id"]) for item in attachment_manifest.get("attachments", [])]
    planned_ids = [asset_id for batch in batches for asset_id in batch["attachment_ids"]]
    if Counter(planned_ids) != Counter(declared_ids):
        missing = sorted(set(declared_ids) - set(planned_ids))
        extra = sorted(set(planned_ids) - set(declared_ids))
        duplicates = sorted(asset_id for asset_id, count in Counter(planned_ids).items() if count > 1)
        raise ValueError(
            f"batch attachment coverage mismatch missing={missing} extra={extra} duplicates={duplicates}"
        )
    return batches


def _batch_attachment_descriptor(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "asset_id": item.get("asset_id"),
            "product_id": item.get("product_id"),
            "analysis_tier": item.get("analysis_tier"),
            "group_id": item.get("group_id"),
            "forecast_hours": item.get("forecast_hours"),
            "valid_times_utc": item.get("valid_times_utc"),
            "source_panels": item.get("source_panels"),
            "render_domain_id": item.get("render_domain_id"),
            "render_domain_bbox": item.get("render_domain_bbox"),
            "unit_label": item.get("unit_label"),
        }
        for item in items
    ]


def build_batch_messages(
    *,
    package_id: str,
    batch: dict[str, Any],
    attachments: list[dict[str, Any]],
    root: Path,
    prompt_template: str,
) -> list[dict[str, Any]]:
    descriptor = _batch_attachment_descriptor(attachments)
    content: list[dict[str, Any]] = []
    # Gemma 4 multimodal guidance recommends placing image content before text.
    for item in attachments:
        path = resolve_logical_ref(root, str(item["file_path"]))
        content.append({"type": "image_url", "image_url": {"url": image_data_url(path)}})
    prompt = (
        prompt_template.strip()
        + "\n\n## 현재 배치\n"
        + json.dumps(
            {
                "package_id": package_id,
                "batch_id": batch["batch_id"],
                "analysis_tier": batch.get("analysis_tier"),
                "group_id": batch.get("group_id"),
                "attachment_count": len(attachments),
                "attachments_in_payload_order": descriptor,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n\n## Response contract\n"
        + json.dumps(
            {
                "attachment_observations_order": "same order as images in the payload",
                "system_assigned_fields": [
                    "package_id",
                    "batch_id",
                    "analysis_tier",
                    "group_id",
                    "observed_attachment_count",
                    "asset_id",
                    "product_id",
                    "forecast_hours",
                ],
                "rules": [
                    "이미지 payload 순서대로 이미지마다 관찰 항목을 하나씩 반환합니다.",
                    "응답 스키마가 요구하는 이미지 기반 분석 필드만 반환합니다.",
                    "식별자와 시간 메타데이터는 시스템이 결정하므로 추가하지 않습니다.",
                    "사람이 읽는 모든 관찰·설명 문장은 한국어로 작성합니다.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    content.append({"type": "text", "text": prompt})
    return [
        {
            "role": "system",
            "content": (
                "당신은 AIFS 강수 감시용 개요장을 관찰하는 멀티모달 분석기입니다. "
                "현재 단계에서는 후보를 생성하지 말고, 제공된 원본 asset_id에 연결된 관찰 사실만 JSON으로 기록하십시오. "
                "JSON 키와 기계 식별자는 계약값을 유지하고, 사람이 읽는 모든 설명 문장은 한국어로 작성하십시오."
            ),
        },
        {"role": "user", "content": content},
    ]


def _normalize_direct_precipitation_signal(value: Any) -> dict[str, Any]:
    allowed = {"not_applicable", "absent", "weak", "present", "uncertain"}
    if not isinstance(value, dict):
        return {"status": "uncertain", "significant_forecast_hours": [], "rationale": "모델 강수 예측 신호 분류가 누락되었습니다."}
    status = str(value.get("status", "uncertain")).strip()
    if status not in allowed:
        status = "uncertain"
    hours: list[int] = []
    for raw in value.get("significant_forecast_hours", []):
        try:
            hours.append(int(raw))
        except (TypeError, ValueError):
            continue
    rationale = re.sub(r"\s+", " ", str(value.get("rationale", "")).strip())
    return {
        "status": status,
        "significant_forecast_hours": sorted(set(hours)),
        "rationale": rationale[:220],
    }


def _direct_precipitation_audit_from_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    precip_rows = [
        row for row in rows
        if isinstance(row, dict) and str(row.get("product_id")) == "QL_PRECIP_6H"
    ]
    if not precip_rows:
        return {
            "status": "not_applicable",
            "source_asset_ids": [],
            "significant_forecast_hours": [],
            "rationale": "이 그룹에는 모델 강수 예측 overview가 없습니다.",
        }
    priority = {"present": 4, "weak": 3, "uncertain": 2, "absent": 1, "not_applicable": 0}
    signals = [
        _normalize_direct_precipitation_signal(row.get("direct_precipitation_signal"))
        for row in precip_rows
    ]
    status = max((signal["status"] for signal in signals), key=lambda item: priority[item])
    hours = sorted({
        hour
        for signal in signals
        for hour in signal["significant_forecast_hours"]
    })
    if status in {"absent", "not_applicable"}:
        hours = []
    rationales = _unique_nonempty_strings(signal.get("rationale") for signal in signals)
    return {
        "status": status,
        "source_asset_ids": [str(row.get("asset_id")) for row in precip_rows],
        "significant_forecast_hours": hours,
        "rationale": _truncate_utf8_text(" ".join(rationales), max_bytes=220) if rationales else "모델 강수 예측 신호를 구조화하여 통합했습니다.",
    }


def _validate_direct_precipitation_signal_for_asset(
    signal: Any,
    *,
    product_id: str,
    allowed_hours: Iterable[int],
    path: str,
) -> list[str]:
    errors: list[str] = []
    normalized = _normalize_direct_precipitation_signal(signal)
    status = normalized["status"]
    hours = set(normalized["significant_forecast_hours"])
    allowed = {int(value) for value in allowed_hours}
    if hours - allowed:
        errors.append(f"{path}.significant_forecast_hours contains values outside the asset: {sorted(hours - allowed)}")
    if product_id == "QL_PRECIP_6H":
        if status == "not_applicable":
            errors.append(f"{path}.status cannot be not_applicable for QL_PRECIP_6H")
        if status in {"present", "weak"} and not hours:
            errors.append(f"{path}.significant_forecast_hours is required when status={status}")
        if status == "absent" and hours:
            errors.append(f"{path}.significant_forecast_hours must be empty when status=absent")
    else:
        if status != "not_applicable":
            errors.append(f"{path}.status must be not_applicable for non-precipitation product")
        if hours:
            errors.append(f"{path}.significant_forecast_hours must be empty for non-precipitation product")
    if not normalized["rationale"]:
        errors.append(f"{path}.rationale must not be empty")
    return errors


def validate_batch_observation(
    observation: dict[str, Any],
    *,
    schema: dict[str, Any],
    package_id: str,
    batch: dict[str, Any],
    attachment_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    errors = _validate_jsonschema(observation, schema)
    expected_ids = [str(value) for value in batch["attachment_ids"]]
    if observation.get("package_id") != package_id:
        errors.append("package_id does not match Step04 input package")
    if observation.get("batch_id") != batch["batch_id"]:
        errors.append("batch_id does not match batch plan")
    if observation.get("analysis_tier") != batch.get("analysis_tier"):
        errors.append("analysis_tier does not match batch plan")
    if observation.get("group_id") != batch.get("group_id"):
        errors.append("group_id does not match batch plan")
    if int(observation.get("observed_attachment_count", -1)) != len(expected_ids):
        errors.append("observed_attachment_count does not match batch attachment count")
    rows = observation.get("attachment_observations", [])
    if isinstance(rows, list):
        observed_ids = [str(item.get("asset_id")) for item in rows if isinstance(item, dict)]
        if Counter(observed_ids) != Counter(expected_ids):
            errors.append(
                f"attachment_observations coverage mismatch expected={expected_ids} actual={observed_ids}"
            )
        for item in rows:
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get("asset_id"))
            source = attachment_by_id.get(asset_id)
            if source is None:
                errors.append(f"unknown observed asset_id: {asset_id}")
                continue
            if item.get("product_id") != source.get("product_id"):
                errors.append(f"product_id mismatch for {asset_id}")
            actual_hours = [int(value) for value in item.get("forecast_hours", [])]
            expected_hours = [int(value) for value in source.get("forecast_hours", [])]
            if actual_hours != expected_hours:
                errors.append(f"forecast_hours mismatch for {asset_id}")
            errors.extend(
                _validate_direct_precipitation_signal_for_asset(
                    item.get("direct_precipitation_signal"),
                    product_id=str(source.get("product_id")),
                    allowed_hours=expected_hours,
                    path=f"attachment_observations[{asset_id}].direct_precipitation_signal",
                )
            )
    return errors



def normalize_batch_observation_contract(
    value: dict[str, Any],
    *,
    package_id: str,
    batch: dict[str, Any],
    attachments_in_payload_order: list[dict[str, Any]],
    include_batch_synthesis: bool = True,
) -> dict[str, Any]:
    """Bind VLM observations to immutable manifest metadata by payload order."""
    if not isinstance(value, dict):
        return value

    normalized = dict(value)
    normalized["package_id"] = package_id
    normalized["batch_id"] = batch["batch_id"]
    normalized["analysis_tier"] = batch.get("analysis_tier")
    normalized["group_id"] = batch.get("group_id")
    normalized["observed_attachment_count"] = len(attachments_in_payload_order)
    rows = normalized.get("attachment_observations")
    if isinstance(rows, list):
        normalized_rows: list[Any] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or index >= len(attachments_in_payload_order):
                normalized_rows.append(row)
                continue
            source = attachments_in_payload_order[index]
            normalized_rows.append(
                {
                    **row,
                    "asset_id": source["asset_id"],
                    "product_id": source["product_id"],
                    "forecast_hours": [int(item) for item in source["forecast_hours"]],
                    "direct_precipitation_signal": _normalize_direct_precipitation_signal(
                        row.get("direct_precipitation_signal")
                    ),
                }
            )
        normalized["attachment_observations"] = normalized_rows
    if not include_batch_synthesis:
        normalized["cross_product_synthesis"] = []
        normalized["batch_uncertainties"] = []
    return normalized


def normalize_final_response_contract(
    value: dict[str, Any],
    *,
    package_id: str,
) -> dict[str, Any]:
    """Inject the deterministic Step04 package ID before final validation."""
    if not isinstance(value, dict):
        return value

    normalized = dict(value)
    normalized["package_id"] = package_id
    return normalized


def validate_candidate_discovery_narrative_language(response: dict[str, Any]) -> list[str]:
    """Strictly validate forecaster-facing discovery text as Korean narrative."""
    errors = validate_step04_discovery_narrative(response)
    for candidate_index, candidate in enumerate(response.get("candidates", [])):
        if not isinstance(candidate, dict):
            continue
        separation_reason = candidate.get("separation_reason")
        if separation_reason is not None and len(str(separation_reason)) > 200:
            errors.append(
                f"candidates[{candidate_index}].separation_reason must be concise (<=200 characters)"
            )
    return errors


def validate_candidate_plan_narrative_language(response: dict[str, Any]) -> list[str]:
    """Strictly validate forecaster-facing candidate-plan text as Korean narrative."""
    return validate_step04_plan_narrative(response)


def validate_step04_narrative_language(response: dict[str, Any]) -> list[str]:
    """Validate all Step04 forecaster-facing narrative fields."""
    return validate_forecaster_step04_narrative(response)


def build_source_grounded_retry_messages(
    messages: list[dict[str, Any]],
    *,
    validation_errors: list[str],
    retry_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Re-send the original multimodal request with a strict completeness correction.

    The original messages include the source images. This retry is therefore able
    to recover omitted attachment observations, unlike a text-only JSON repair.
    """

    correction = {
        "task": (
            "Re-read every image in the original multimodal request and return a "
            "complete JSON object that satisfies the required attachment coverage."
        ),
        "validation_errors": validation_errors,
        "required_attachment_count": retry_context.get("required_attachment_count"),
        "payload_order": "same order as the original image payload",
        "rules": [
            "The original source images are included earlier in this same request.",
            "Return JSON only. Do not use Markdown or explanatory text.",
            "Return exactly one attachment_observations item for every source image.",
            "Keep attachment_observations in the original image payload order.",
            "Do not output package, batch, asset, product, or forecast metadata fields.",
            "Keep observations grounded only in the corresponding source image.",
        ],
    }
    return [
        *messages,
        {
            "role": "user",
            "content": json.dumps(correction, ensure_ascii=False),
        },
    ]


def build_compact_observation_recovery_schema(
    schema: dict[str, Any],
    *,
    batch: dict[str, Any],
) -> dict[str, Any]:
    """Constrain a length-recovery response without changing the canonical schema."""
    compact = copy.deepcopy(schema)
    expected_asset_ids = [str(item) for item in batch["attachment_ids"]]
    properties = compact["properties"]
    observations = properties["attachment_observations"]
    observations["minItems"] = len(expected_asset_ids)
    observations["maxItems"] = len(expected_asset_ids)
    item_properties = observations["items"]["properties"]
    item_properties["asset_id"]["enum"] = expected_asset_ids
    item_properties["observed_signals"]["minItems"] = 1
    item_properties["observed_signals"]["maxItems"] = 4
    item_properties["observed_signals"]["items"]["maxLength"] = 160
    item_properties["spatial_pattern"]["maxLength"] = 220
    item_properties["temporal_evolution"]["maxLength"] = 220
    item_properties["limitations"]["maxItems"] = 2
    item_properties["limitations"]["items"]["maxLength"] = 160
    direct_signal = item_properties.get("direct_precipitation_signal", {}).get("properties", {})
    if direct_signal:
        direct_signal["significant_forecast_hours"]["maxItems"] = 4
        direct_signal["rationale"]["maxLength"] = 160
    properties["cross_product_synthesis"]["maxItems"] = 3
    properties["cross_product_synthesis"]["items"]["maxLength"] = 180
    properties["batch_uncertainties"]["maxItems"] = 3
    properties["batch_uncertainties"]["items"]["maxLength"] = 160
    return compact


def build_provider_observation_schema(
    schema: dict[str, Any],
    *,
    batch: dict[str, Any],
    include_batch_synthesis: bool = True,
) -> dict[str, Any]:
    """Limit VLM output to observations; immutable manifest fields are injected locally."""
    provider_schema = build_compact_observation_recovery_schema(schema, batch=batch)
    properties = provider_schema["properties"]
    required = provider_schema["required"]
    for field in (
        "package_id",
        "batch_id",
        "analysis_tier",
        "group_id",
        "observed_attachment_count",
    ):
        properties.pop(field, None)
        if field in required:
            required.remove(field)
    if not include_batch_synthesis:
        for field in ("cross_product_synthesis", "batch_uncertainties"):
            properties.pop(field, None)
            if field in required:
                required.remove(field)
    observation_item = properties["attachment_observations"]["items"]
    item_properties = observation_item["properties"]
    item_required = observation_item["required"]
    for field in ("asset_id", "product_id", "forecast_hours"):
        item_properties.pop(field, None)
        if field in item_required:
            item_required.remove(field)
    return provider_schema


def build_compact_observation_recovery_messages(
    messages: list[dict[str, Any]],
    *,
    batch: dict[str, Any],
) -> list[dict[str, Any]]:
    """Request the same observations with bounded factual prose after a length stop."""
    correction = {
        "task": "직전 응답은 길이 제한으로 JSON을 완성하지 못했습니다. 원본 이미지 4장을 다시 판독해 완전한 JSON만 반환하십시오.",
        "required_asset_ids": [str(item) for item in batch["attachment_ids"]],
        "rules": [
            "각 required_asset_id에 대해 attachment_observations 항목을 정확히 하나씩 작성하십시오.",
            "이미지에서 확인되는 사실만 짧게 작성하고 반복, 서론, 결론, 후보 판단을 넣지 마십시오.",
            "observed_signals는 시간 변화를 대표하는 짧은 문장 1~4개로 제한하십시오.",
            "spatial_pattern과 temporal_evolution은 각각 한 문장으로 작성하십시오.",
            "limitations, cross_product_synthesis, batch_uncertainties는 필요한 경우에만 짧게 작성하십시오.",
            "각 이미지의 direct_precipitation_signal을 반드시 유지하고 QL_PRECIP_6H 이외에는 not_applicable로 작성하십시오.",
            "JSON 외의 설명, Markdown, 들여쓰기용 공백을 출력하지 말고 마지막 } 직후 즉시 종료하십시오.",
        ],
    }
    return [
        *messages,
        {"role": "user", "content": json.dumps(correction, ensure_ascii=False)},
    ]


def _compact_provider_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    choice = None
    choices = envelope.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice = choices[0]
    return {
        "id": envelope.get("id"),
        "model": envelope.get("model"),
        "created": envelope.get("created"),
        "usage": envelope.get("usage"),
        "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
    }



SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY = "SYS_DIRECT_PRECIPITATION_PRESENCE"
SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CLAIM_KEY = "SYS_CLM_DIRECT_PRECIPITATION_PRESENCE"
SYSTEM_DIRECT_PRECIPITATION_ANCHOR_EXECUTION_CONTRACT_VERSION = "2.3.0"


def _is_system_direct_precipitation_anchor_row(row: dict[str, Any]) -> bool:
    """Return whether an internal row is the system-owned precipitation anchor."""
    return bool(row.get("_system_direct_precipitation_anchor") is True)


def _is_system_direct_precipitation_anchor_check(check: dict[str, Any]) -> bool:
    """Return whether a canonical Check is the system-owned precipitation anchor."""
    intent = check.get("intent_contract")
    return bool(
        isinstance(intent, dict)
        and intent.get("direct_precipitation_anchor") is True
        and str(intent.get("source_check_key") or "")
        == SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY
    )



def _unreferenced_compact_phase_keys(chunks: list[dict[str, Any]]) -> list[str]:
    """Return every declared compact phase that no Check references."""
    declared_phase_keys = {
        str(phase.get("phase_key", "")).strip()
        for chunk in chunks
        for phase in _compact_plan_rows(chunk, "analysis_phases")
        if str(phase.get("phase_key", "")).strip()
    }
    referenced_phase_keys = {
        str(phase_key).strip()
        for chunk in chunks
        for check in _compact_plan_rows(chunk, "diagnostic_checks")
        for phase_key in check.get("phase_keys", [])
        if str(phase_key).strip()
    }
    return sorted(declared_phase_keys - referenced_phase_keys)


def _compact_phase_reference_failure_context(
    value: dict[str, Any],
    validation_errors: list[str],
    *,
    previous_chunks: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return bounded structural context for compact-plan phase graph failures.

    Whole-chunk regeneration intentionally does not send the discarded provider
    response back to the model. For unreferenced phases or unresolved quantitative
    forecast-hour ownership, the validator can safely expose a compact structural
    summary of declared phases and references. This is diagnostic context only;
    it must never be merged into or patched onto the regenerated plan.
    """
    has_unreferenced_phase_error = any(
        "COMPACT_PLAN_UNREFERENCED_PHASE" in str(error) for error in validation_errors
    )
    has_quantitative_phase_error = any(
        "QUANTITATIVE_SCOPE_PHASE_MISMATCH" in str(error) for error in validation_errors
    )
    has_hour_coverage_error = any(
        "insufficient forecast-hour coverage" in str(error) for error in validation_errors
    )
    if not (has_unreferenced_phase_error or has_quantitative_phase_error or has_hour_coverage_error):
        return None
    if not isinstance(value, dict):
        return None

    chunks = [*(previous_chunks or []), value]
    phases = [
        phase
        for chunk in chunks
        for phase in _compact_plan_rows(chunk, "analysis_phases")
    ]
    checks = [
        check
        for chunk in chunks
        for check in _compact_plan_rows(chunk, "diagnostic_checks")
    ]
    if not phases:
        return None

    def local_key(raw: Any) -> str:
        key = str(raw or "").strip()
        match = re.fullmatch(r"chunk\d+:(.+)", key)
        return match.group(1) if match else key

    declared_keys = {
        str(row.get("phase_key", "")).strip()
        for row in phases
        if str(row.get("phase_key", "")).strip()
    }
    referenced_keys = {
        str(phase_key).strip()
        for check in checks
        for phase_key in check.get("phase_keys", [])
        if str(phase_key).strip()
    }
    unreferenced_keys = _unreferenced_compact_phase_keys(chunks)
    phase_hours_by_key = {
        str(row.get("phase_key", "")).strip(): set(_compact_phase_hours(row))
        for row in phases
        if str(row.get("phase_key", "")).strip()
    }
    quantitative_phase_mismatches: list[dict[str, Any]] = []
    for check in checks:
        request_phase_keys = [
            str(key).strip() for key in check.get("phase_keys", []) if str(key).strip()
        ]
        request_hours = {
            hour
            for phase_key in request_phase_keys
            for hour in phase_hours_by_key.get(phase_key, set())
        }
        for claim in check.get("atomic_claims", []):
            if not isinstance(claim, dict):
                continue
            scope = claim.get("quantitative_scope")
            if not isinstance(scope, dict):
                continue
            raw_hours = scope.get("forecast_hours")
            if not isinstance(raw_hours, list) or any(
                isinstance(hour, bool) or not isinstance(hour, int) or hour < 0
                for hour in raw_hours
            ):
                continue
            quantitative_hours = set(int(hour) for hour in raw_hours)
            claim_phase_keys = [
                str(key).strip() for key in claim.get("phase_keys", []) if str(key).strip()
            ]
            claim_hours = {
                hour
                for phase_key in claim_phase_keys
                for hour in phase_hours_by_key.get(phase_key, set())
            }
            missing_request_hours = sorted(quantitative_hours - request_hours)
            missing_claim_hours = sorted(quantitative_hours - claim_hours)
            if not missing_request_hours and not missing_claim_hours:
                continue
            missing_hours = sorted(set(missing_request_hours) | set(missing_claim_hours))
            quantitative_phase_mismatches.append(
                {
                    "check_key": local_key(check.get("check_key")),
                    "claim_key": str(claim.get("claim_key") or ""),
                    "quantitative_forecast_hours": sorted(quantitative_hours),
                    "request_phase_keys": [local_key(key) for key in request_phase_keys],
                    "claim_phase_keys": [local_key(key) for key in claim_phase_keys],
                    "missing_request_hours": missing_request_hours,
                    "missing_claim_hours": missing_claim_hours,
                    "declared_covering_phase_keys": {
                        str(hour): [
                            local_key(phase_key)
                            for phase_key, phase_hours in phase_hours_by_key.items()
                            if hour in phase_hours
                        ]
                        for hour in missing_hours
                    },
                }
            )

    return {
        "declared_phases": [
            {
                "phase_key": local_key(row.get("phase_key")),
                "normalized_phase_key": str(row.get("phase_key", "")),
                "phase_type": str(row.get("phase_type", "")),
                "forecast_hours": sorted(_compact_phase_hours(row)),
            }
            for row in phases
        ],
        "check_phase_references": [
            {
                "check_key": local_key(check.get("check_key")),
                "phase_keys": [local_key(key) for key in check.get("phase_keys", [])],
                "normalized_phase_keys": [str(key) for key in check.get("phase_keys", [])],
                "verification_mode": _verification_mode_from_diagnostic_check(check),
                "signal_components": list(check.get("required_signal_components", [])),
            }
            for check in checks
        ],
        "unreferenced_phase_keys": [local_key(key) for key in unreferenced_keys],
        "normalized_unreferenced_phase_keys": unreferenced_keys,
        "quantitative_scope_phase_mismatches": quantitative_phase_mismatches,
        "forecast_hour_coverage_failures": [
            {
                "check_key": local_key(check.get("check_key")),
                "phase_keys": [local_key(key) for key in check.get("phase_keys", [])],
                "referenced_hours": sorted({
                    hour for key in check.get("phase_keys", [])
                    for hour in phase_hours_by_key.get(str(key), set())
                }),
                "required_hours": _minimum_forecast_hours_for_check(
                    str(check.get("diagnostic_id") or ""),
                    str(check.get("check_scope") or ""),
                ),
            }
            for check in checks
            if len({
                hour for key in check.get("phase_keys", [])
                for hour in phase_hours_by_key.get(str(key), set())
            }) < _minimum_forecast_hours_for_check(
                str(check.get("diagnostic_id") or ""),
                str(check.get("check_scope") or ""),
            )
        ] if has_hour_coverage_error else [],
        "instruction": (
            "이 구조는 실패 원인을 설명하기 위한 참고정보입니다. 이전 객체를 부분 수정하지 말고 "
            "원래 후보 컨텍스트에서 chunk 전체를 다시 생성하십시오. 미참조 phase가 기상학적으로 필요하면 "
            "의미 있는 verification_request가 실제로 참조하도록 하고, 필요하지 않으면 새 출력에 선언하지 마십시오. "
            "정량 명제가 여러 F시각을 요구하면 각 시각이 실제 analysis phase에 포함되고 verification_request와 "
            "atomic claim이 모두 그 phase를 참조해야 합니다. 스키마 통과만을 위한 무의미한 요청이나 정량 "
            "forecast_hours 임의 삭제는 하지 마십시오."
        ),
    }


def _compact_check_reference_failure_context(
    value: dict[str, Any],
    validation_errors: list[str],
    *,
    previous_chunks: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return bounded graph context for an unresolved hypothesis Check reference.

    Candidate-plan chunk regeneration intentionally starts again from the original
    candidate context instead of patching a failed provider response.  A dangling
    hypothesis Check reference is different from a meteorological-content error:
    the repair model needs the *valid graph keys* in order to avoid inventing a
    stale continuation key again.  Expose only structural metadata (keys, modes,
    components, and phase references), never the discarded narrative content.
    """
    if not any(
        "unresolved check reference after normalization" in str(error)
        for error in validation_errors
    ):
        return None
    if not isinstance(value, dict):
        return None

    previous = list(previous_chunks or [])
    previous_checks = [
        check
        for chunk in previous
        for check in _compact_plan_rows(chunk, "diagnostic_checks")
    ]
    current_checks = _compact_plan_rows(value, "diagnostic_checks")
    hypotheses = _compact_plan_rows(value, "hypotheses")

    def local_key(raw: Any) -> str:
        key = str(raw or "").strip()
        match = re.fullmatch(r"chunk\d+:(.+)", key)
        return match.group(1) if match else key

    existing_check_keys = sorted(
        {
            str(check.get("check_key", "")).strip()
            for check in previous_checks
            if str(check.get("check_key", "")).strip()
        }
    )
    normalized_current_check_keys = sorted(
        {
            str(check.get("check_key", "")).strip()
            for check in current_checks
            if str(check.get("check_key", "")).strip()
        }
    )
    valid_normalized_keys = set(existing_check_keys) | set(normalized_current_check_keys)

    hypothesis_rows: list[dict[str, Any]] = []
    unresolved: set[str] = set()
    for hypothesis in hypotheses:
        supporting = [
            str(key).strip()
            for key in hypothesis.get("supporting_check_keys", [])
            if str(key).strip()
        ]
        opposing = [
            str(key).strip()
            for key in hypothesis.get("opposing_check_keys", [])
            if str(key).strip()
        ]
        bad = sorted((set(supporting) | set(opposing)) - valid_normalized_keys)
        unresolved.update(bad)
        hypothesis_rows.append(
            {
                "hypothesis_key": local_key(hypothesis.get("hypothesis_key")),
                "supporting_check_keys": [local_key(key) for key in supporting],
                "opposing_check_keys": [local_key(key) for key in opposing],
                "normalized_supporting_check_keys": supporting,
                "normalized_opposing_check_keys": opposing,
                "unresolved_check_keys": bad,
            }
        )

    def check_summary(check: dict[str, Any], *, include_local_key: bool) -> dict[str, Any]:
        normalized_key = str(check.get("check_key", "")).strip()
        row = {
            "check_key": local_key(normalized_key) if include_local_key else normalized_key,
            "verification_mode": _verification_mode_from_diagnostic_check(check),
            "signal_components": list(check.get("required_signal_components", [])),
            "phase_keys": [local_key(key) for key in check.get("phase_keys", [])],
        }
        if include_local_key:
            row["normalized_check_key"] = normalized_key
        return row

    return {
        "existing_check_keys": existing_check_keys,
        "existing_checks": [
            check_summary(check, include_local_key=False) for check in previous_checks
        ],
        "current_declared_check_keys": [
            local_key(key) for key in normalized_current_check_keys
        ],
        "normalized_current_declared_check_keys": normalized_current_check_keys,
        "current_declared_checks": [
            check_summary(check, include_local_key=True) for check in current_checks
        ],
        "hypothesis_check_references": hypothesis_rows,
        "unresolved_check_keys": sorted(unresolved),
        "instruction": (
            "이 구조는 hypothesis의 Check 참조 오류를 설명하기 위한 참고정보입니다. "
            "이전 chunk를 참조할 때는 existing_check_keys에 실제 존재하는 key만 그대로 사용하십시오. "
            "현재 chunk에서 새 verification_request를 선언하면 hypothesis는 그 요청의 check_key 로컬값을 "
            "정확히 참조하십시오. 번호를 추정해 존재하지 않는 chunkXX:CHKYY key를 만들지 마십시오. "
            "필요한 기상학적 근거에 대응하는 유효 Check가 없다면 의미 있는 verification_request를 현재 "
            "chunk에 새로 구성하고 그 check_key를 참조하십시오. 오류를 숨기기 위해 참조만 삭제하지 마십시오."
        ),
    }


def _repair_rules_for_validation_errors(validation_errors: list[str]) -> list[str]:
    """Return repair guidance using only the Step04 AI intent contract.

    Step04 AI owns meteorological verification intent. Python owns the
    executable diagnostic contract. Repair instructions therefore never ask
    the model to edit diagnostic_id, check_scope, analysis_methods,
    variable_role, or required_signal_components.
    """
    joined = "\n".join(str(error) for error in validation_errors)
    rules: list[str] = []

    if "SPATIAL_ANALYSIS_GRID_INSUFFICIENT" in joined:
        rules.extend([
            "공간 중첩·공간 구조의 변화는 단일 원본 격자만 보이는 지도에서 판단할 수 없습니다. 후보 candidate_area와 강수 앵커는 절대로 확대하거나 제거하지 마십시오.",
            "공간 구조 판독에 필요한 경우 candidate_area를 포함하면서 실제 주변 원본 격자를 보여주는 context_domain을 사용하고, 해당 verification_request의 domain_scope=context로 지정하십시오.",
            "context_domain도 너무 좁으면 실제 주변을 포함하도록 context_domain_reason과 함께 설정하십시오. 기존 가설을 삭제하거나 물리적 중첩을 추정해서 단정하지 마십시오.",
            "주변 맥락 영상 전체에서 강수와 환경장이 동시에 보이더라도 후보 핵심영역 안의 중첩으로 오인하지 마십시오. STEP05는 후보 핵심영역을 테두리로 따로 표시합니다.",
            "직접강수 존재 확인, 단일 격자의 정량값, SYSTEM 필수 앵커는 그대로 유지하십시오.",
        ])

    if "insufficient forecast-hour coverage" in joined:
        rules.extend([
            "시간 변화 및 공간 구조 변화 검증에는 서로 다른 실제 예측시각을 최소 2개 참조해야 합니다. required=2를 낮추지 마십시오.",
            "입력의 time_feasibility_contract.effective_hours와 available_analysis_phase_types에서 실제 비교 가능한 시각을 확인하십시오.",
            "해당 check_key의 phase_keys가 가리키는 phase들의 forecast_hours 합집합이 2개 이상인지 확인하십시오. 필요하면 phase와 Check를 같은 chunk에서 함께 재구성하십시오.",
            "두 시각이 실제 존재하지 않으면 시간을 만들지 말고 오류를 그대로 보존하십시오.",
        ])

    if "unresolved check reference after normalization" in joined:
        rules.extend(
            [
                "hypothesis의 supporting_check_keys/opposing_check_keys는 실제 존재하는 Check만 참조해야 합니다.",
                "이전 chunk의 Check를 참조할 때는 check_reference_failure_context.existing_check_keys에 있는 key만 그대로 사용하고, 번호를 추정하여 존재하지 않는 chunkXX:CHKYY key를 만들지 마십시오.",
                "현재 chunk에서 생성한 Check는 해당 verification_request에 선언한 check_key 로컬값을 hypothesis에서 정확히 참조하십시오. 필요한 근거 Check가 없다면 의미 있는 verification_request를 현재 chunk에 새로 만들고 그 key를 참조하십시오. 단순히 오류 참조를 삭제하여 가설의 의미를 훼손하지 마십시오.",
            ]
        )

    if "CANDIDATE_OPERATIONAL_SCOPE_INVALID" in joined:
        rules.extend(
            [
                "CANDIDATE_OPERATIONAL_SCOPE_INVALID는 direct_precipitation/mixed 후보의 candidate_area가 현행 운영 예보권역과 전혀 겹치지 않는다는 뜻입니다.",
                "candidate_discovery input의 candidate_operational_scope.eligible_forecast_regions를 권위 있는 경계로 사용하십시오. source_corridor에만 있는 강수는 독립 direct/mixed 후보로 만들지 말고 그룹요약의 주변 맥락 또는 선행환경 근거로 남기십시오.",
                "오류를 없애기 위해 candidate_area를 국내/해상 운영권역까지 임의로 확대·이동하지 마십시오. 실제 강수 영향 핵심영역이 운영권역과 겹치지 않으면 해당 direct/mixed 후보 자체를 제거하고 candidates와 candidate_evidence의 순서를 다시 맞추십시오.",
                "group_summaries.direct_precipitation_audit.status=present는 원본 영상 관찰 보존용이며, 운영권역 밖 신호 자체가 Candidate 생성 의무를 뜻하지 않습니다. 강제 coverage는 system_direct_precipitation_coverage_guidance.required_reference_signals만 따르십시오.",
            ]
        )
    if "SYSTEM_DIRECT_PRECIPITATION_COVERAGE_REQUIRED" in joined:
        rules.extend(
            [
                "SYSTEM_DIRECT_PRECIPITATION_COVERAGE_REQUIRED는 candidate reference threshold 이상인 운영권역 정량 강수 신호가 아직 어떤 direct/mixed 후보에도 연결되지 않았다는 뜻입니다.",
                "system_direct_precipitation_coverage_guidance.required_reference_signals의 forecast_hour, region_id, region_name_ko, max_location을 확인하고 실제 영상·그룹요약과 일치하는 후보에 포함시키십시오.",
                "동일 기상계라는 근거가 있으면 기존 후보를 사용할 수 있지만, 서로 떨어진 강수군을 coverage만 맞추기 위해 하나의 큰 bbox로 합치지 마십시오. 필요한 경우 운영권역과 실제로 겹치는 별도 후보를 생성하십시오.",
                "일치하는 QL_PRECIP_6H asset/hour를 candidate_evidence에 포함하고 정량 임계값을 낮추거나 SYSTEM 신호를 삭제하지 마십시오.",
            ]
        )

    if "OPERATIONAL_REGION_TARGET_EMPTY" in joined:
        rules.extend(
            [
                "OPERATIONAL_REGION_TARGET_EMPTY는 immutable candidate_area와 겹치는 실행 가능한 operational_land/marine_macro 권역이 없는데 운영권역 정량자료를 요구했다는 뜻입니다.",
                "candidate_operational_region_overlap.regional_verification_available=false이면 regional_intensity, temporal_change, precipitation_amount를 사용하지 마십시오. spatial_distribution, spatial_evolution, multivariable_alignment 또는 적절한 vertical mode처럼 후보/맥락 공간장에서 실행 가능한 검증으로 다시 구성하십시오.",
                "오류를 피하려고 candidate_area를 운영권역 쪽으로 확대·이동하거나 존재하지 않는 region_id를 만들지 마십시오. 선행환경/source-corridor 후보는 해당 실제 위치를 유지한 채 공간장 검증을 사용해야 합니다.",
                "quantitative_scope.region_ids가 필요한 명제도 selectable_regions가 비어 있으면 작성하지 마십시오. 정량 운영권역 비교 대신 실제 제공되는 공간장으로 판정 가능한 기상학적 expected_signal을 선택하십시오.",
            ]
        )

    if "EXPECTED_SIGNAL_PROCEDURAL_TEXT_INVALID" in joined:
        rules.extend(
            [
                "EXPECTED_SIGNAL_PROCEDURAL_TEXT_INVALID는 expected_signal이 기상학적 예상 상태가 아니라 분석 절차를 설명하는 문장이라는 뜻입니다.",
                "오류 check_key의 verification_request 전체를 다시 작성하고 expected_signal은 상세자료에서 확인/부분 확인/미확인/반대 여부를 판정할 수 있는 기상학적 명제로 작성하십시오.",
                "`확인한다`, `비교한다`, `살펴본다`, `판단한다`, `검토한다`, `분석한다`, `평가한다`로 끝내지 마십시오. 예: 단일요소 요청은 `F060 전북 부근에 925 hPa 수렴 중심이 나타난다`. 여러 요소의 중첩은 해당 요소를 모두 선언하고 multivariable_alignment를 사용하십시오.",
            ]
        )
    if "EXPECTED_SIGNAL_VAGUE_COMPARISON_INVALID" in joined:
        rules.extend(
            [
                "EXPECTED_SIGNAL_VAGUE_COMPARISON_INVALID는 `서로 다르다`, `정량적으로 구분된다`, `차이가 나타난다`, 방향 없는 `변화한다`처럼 거의 항상 참이 될 수 있는 문장이라는 뜻입니다.",
                "오류 check_key의 verification_request 전체를 다시 작성하고 어느 시각/영역이 더 강하거나 약한지, 중심이 어디에 있는지, 또는 중첩이 확인될지/약화될지처럼 지지 여부를 반증할 수 있는 방향과 상태를 명시하십시오.",
                "예: `F120 최대 6시간 강수량이 F102보다 크다`, `F120에는 강수 중심과 상승운동 중심의 중첩이 F102보다 약하다`처럼 작성하십시오.",
            ]
        )
    if "EXPECTED_SIGNAL_AMBIGUOUS_ALTERNATIVE_INVALID" in joined:
        rules.extend(
            [
                "EXPECTED_SIGNAL_AMBIGUOUS_ALTERNATIVE_INVALID는 하나의 expected_signal에 서로 반대되는 결과를 `또는/혹은`으로 함께 적었다는 뜻입니다.",
                "한 Check에는 하나의 기대 상태만 작성하십시오. 강화와 약화처럼 상반된 결과 중 하나를 가설로 선택하고, 반대 결과는 Step07에서 반대 신호로 판독할 수 있게 두십시오.",
            ]
        )
    if "EXPECTED_SIGNAL_SPATIAL_EVOLUTION_MODE_MISMATCH" in joined:
        rules.extend(
            [
                "EXPECTED_SIGNAL_SPATIAL_EVOLUTION_MODE_MISMATCH는 현재 verification_mode로는 선언된 기상요소의 이동·재배치·분리·병합·확대·축소를 검증할 수 없다는 뜻입니다.",
                "후보영역 평균이나 정량 강도의 강화·약화가 목적이면 temporal_change/regional_intensity/precipitation_amount에 그 정량 상태만 남기십시오.",
                "중심 위치의 이동이나 공간구조 재조직이 실제 목적이면 별도의 verification_request를 만들고 verification_mode=spatial_evolution을 사용하십시오. 강도 변화와 공간 이동을 한 Check에 섞지 마십시오.",
            ]
        )
    if (
        "SPATIAL_RELATION_VERTICAL_MODE_SCOPE_MISMATCH" in joined
        or "SPATIAL_RELATION_VERIFICATION_MODE_MISMATCH" in joined
    ):
        rules.extend(
            [
                "SPATIAL_RELATION 관련 오류는 verification_mode와 spatial_relation_scope가 서로 다른 종류의 공간판정을 요구한다는 뜻입니다. 오류 문자열에 포함된 check_key, verification_mode, spatial_relation_scope, domain_scope, expected_signal을 함께 확인하십시오.",
                "verification_mode=vertical_structure 또는 vertical_time_evolution이면 spatial_relation_scope는 반드시 not_applicable로 작성하십시오. 단면선이 후보영역 밖의 context_domain까지 확장되더라도 그것은 렌더링 범위일 뿐 수평적인 후보-외부 관계를 뜻하지 않습니다.",
                "expected_signal의 실제 결론이 후보 밖 공급원 위치, 후보 경계 통과, 후보영역으로의 접근·이탈 등 수평 관계라면 vertical mode를 억지로 유지하지 말고 spatial_distribution, spatial_evolution 또는 multivariable_alignment의 별도 verification_request로 구성하십시오.",
                "연직구조와 후보 밖 수평 관계가 모두 필요하면 하나의 요청에 섞지 말고 두 verification_request로 분리하십시오. 스키마 통과를 위해 의미 없이 verification_mode만 바꾸거나 spatial_relation_scope만 바꾸지 마십시오.",
            ]
        )


    if "EXPECTED_SIGNAL_CANDIDATE_AGGREGATE_UNAVAILABLE" in joined:
        rules.extend(
            [
                "EXPECTED_SIGNAL_CANDIDATE_AGGREGATE_UNAVAILABLE는 현재 수평 Step05 정량자료가 후보 bbox 전체를 하나로 합친 평균값을 제공하지 않는데 expected_signal이 `후보영역 평균` 또는 `후보지역 평균`을 요구했다는 뜻입니다.",
                "temporal_change/regional_intensity/precipitation_amount에서는 후보영역과 겹치는 운영권역별 평균·최대·순위처럼 실제 제공되는 공간 통계 단위로 다시 작성하십시오.",
                "후보영역 전체의 단일 평균을 새로 가정하거나 만들어 내지 마십시오.",
            ]
        )
    if "EXPECTED_SIGNAL_SPATIAL_EXTENT_MODE_MISMATCH" in joined:
        rules.extend(
            [
                "EXPECTED_SIGNAL_SPATIAL_EXTENT_MODE_MISMATCH는 평균·최대값 등 정량 강도를 확인하는 모드에서 실제 기상장의 면적·폭·공간범위·확대·축소까지 한 Check에 요구했다는 뜻입니다.",
                "평균·강도 변화는 temporal_change/regional_intensity/precipitation_amount에 남기십시오. 여러 운영권역의 평균값을 근거로 범지역적 강도 경향을 표현해야 한다면 `여러 운영권역에서 평균값의 뚜렷한 강화가 없다`처럼 작성할 수 있습니다.",
                "기상장 자체의 면적·폭·영역 확대·축소를 판단하려면 spatial_distribution 또는 spatial_evolution의 별도 verification_request로 분리하십시오.",
            ]
        )
    if "VERIFICATION_MOISTURE_VECTOR_SEMANTICS_MISMATCH" in joined:
        rules.extend(
            [
                "VERIFICATION_MOISTURE_VECTOR_SEMANTICS_MISMATCH는 수증기 수송 수평상품의 화살표를 수증기 플럭스 벡터로 해석했거나, 정량 시계열만으로 바람 방향을 판정하려 했다는 뜻입니다.",
                "QL_MOISTURE_FLUX850_WIND850의 음영은 850 hPa 수증기 플럭스 크기이고 화살표는 850 hPa 실제 바람입니다. `수증기 플럭스 벡터/화살표/흐름`이라고 쓰지 마십시오.",
                "방향성이 필요하면 `수증기 플럭스가 큰 영역에서 850 hPa 바람이 후보 방향으로 이어진다`처럼 수증기 플럭스 크기와 850 hPa 바람 방향을 구분해 작성하십시오. temporal_change에서는 바람 방향을 판정하지 마십시오.",
            ]
        )

    if "VERIFICATION_STANDALONE_MOISTURE_SEMANTICS_MISMATCH" in joined:
        rules.extend(
            [
                "VERIFICATION_STANDALONE_MOISTURE_SEMANTICS_MISMATCH는 독립 수분공급 수평 Check가 실제 상품에 없는 대기 전층 수분량(TCW)을 요구했다는 뜻입니다.",
                "독립 moisture_supply의 수평상품은 850 hPa 수증기 플럭스 크기 음영과 실제 850 hPa 바람입니다. spatial_distribution/spatial_evolution에서는 두 의미를 구분하고, regional_intensity/temporal_change에서는 수증기 플럭스 크기 통계만 사용하십시오.",
                "대기 전층 수분량(TCW)은 multivariable_alignment 합성자료의 수분 요소에서만 사용하십시오.",
            ]
        )
    if "VERIFICATION_VERTICAL_SECTION_MOISTURE_SEMANTICS_MISMATCH" in joined:
        rules.extend(
            [
                "VERIFICATION_VERTICAL_SECTION_MOISTURE_SEMANTICS_MISMATCH는 연직단면/시간-고도 Check가 수평 수증기 플럭스 또는 대기 전층 수분량(TCW)을 요구했다는 뜻입니다.",
                "연직 수분 구조는 비습과 고수분층의 고도·두께·연직분포로 작성하십시오. 수평 850 hPa 수증기 플럭스는 별도 moisture_supply 수평 Check로 분리하십시오.",
            ]
        )
    if "VERIFICATION_VERTICAL_SECTION_SHEAR_SEMANTICS_MISMATCH" in joined:
        rules.extend(
            [
                "VERIFICATION_VERTICAL_SECTION_SHEAR_SEMANTICS_MISMATCH는 연직단면/시간-고도 자료만으로 전용 850–500 hPa 연직바람시어를 판정하려 했다는 뜻입니다.",
                "연직단면에서는 풍속과 단면평행 바람의 연직구조만 확인하십시오. 850–500 hPa 시어가 필요하면 organization_persistence의 spatial_distribution 또는 spatial_evolution을 별도 요청으로 만드십시오.",
            ]
        )

    if "VERIFICATION_VERTICAL_SECTION_PRECIPITATION_CONTEXT_MISMATCH" in joined:
        rules.extend(
            [
                "VERIFICATION_VERTICAL_SECTION_PRECIPITATION_CONTEXT_MISMATCH는 vertical_structure/vertical_time_evolution의 expected_signal이 연직자료에서 직접 제공되지 않는 강수를 중첩·일치의 실제 검증 대상으로 포함했다는 뜻입니다.",
                "강수대·강수영역·강수중심·강수 피크는 단면 위치나 비교 시각을 정하는 종속적인 기준점으로는 언급할 수 있지만, 강수 자체가 고수분층·상승운동·바람구조와 겹치거나 일치한다는 결론은 연직 Check에서 요구하지 마십시오.",
                "연직 Check의 실제 판정 명제는 선언한 moisture_supply(비습·고수분층), mid_upper_forcing(상승·하강운동), organization_persistence(풍속·단면평행 바람)의 연직구조로 작성하십시오. vertical_structure에서는 강수 기준을 cross_section_strategy, reference_band_axis, cross_section_line.selection_reason에 우선 기록하십시오.",
                "강수와 환경장의 실제 공간적 중첩이 판단 대상이면 연직 Check에 포함하지 말고 별도의 horizontal multivariable_alignment verification_request로 분리하십시오.",
            ]
        )
    if "VERIFICATION_MULTIVARIABLE_INTENT_MISMATCH" in joined:
        rules.extend(
            [
                "VERIFICATION_MULTIVARIABLE_INTENT_MISMATCH는 expected_signal이 선언하지 않은 다른 기상요소와의 실제 중첩·일치·동반 관계를 검증 대상으로 요구한다는 뜻입니다.",
                "오류 check_key의 verification_request 전체를 다시 작성하십시오. diagnostic_id, check_scope, analysis_methods 같은 내부 실행필드는 작성하지 마십시오.",
                "한 기상요소 자체의 변화가 목적이면 signal_components는 그 요소 하나만 유지하고 expected_signal도 그 요소에서 실제 확인할 분포·강도·시간변화로 제한하십시오.",
                "강수 최대 시각 이전·이후, 강수 발생 전후처럼 다른 기상요소를 시간 기준점으로 언급하는 것은 허용됩니다. 이 경우 그 기준 요소와의 공간적 중첩·일치·동반을 결론으로 요구하지 마십시오.",
                "둘 이상의 기상요소의 공간적 중첩·일치가 실제 목적이면 signal_components에 실제 비교할 요소를 모두 포함하고 verification_mode=multivariable_alignment를 사용하십시오.",
            ]
        )
    if "VERIFICATION_WIND_STRUCTURE_SEMANTICS_MISMATCH" in joined:
        rules.extend(
            [
                "수평장의 organization_persistence 요청은 내부적으로 850-500 hPa 연직바람시어 자료로 실행됩니다. 연직바람시어 벡터는 실제 850 hPa 바람이나 수분 유입 벡터가 아닙니다.",
                "연직바람시어를 확인하려는 경우 signal_components=[organization_persistence]를 유지하고 expected_signal을 시어의 크기, 방향 또는 공간구조만 확인하도록 다시 작성하십시오.",
                "하층 유입축, 850 hPa 흐름의 방향·지속성, 수증기 수송이 실제 목적이면 signal_components=[moisture_supply]로 바꾸고 spatial_distribution 또는 spatial_evolution을 사용하십시오.",
            ]
        )
    if "requires exactly one signal component" in joined:
        rules.append(
            "해당 verification_mode는 한 기상요소만 확인합니다. 오류 check_key의 signal_components를 실제 분석 대상 하나로 제한하십시오. 둘 이상의 공간적 관계가 목적이면 verification_mode=multivariable_alignment로 구성하십시오."
        )
    if "multivariable_alignment requires at least two" in joined:
        rules.append(
            "multivariable_alignment는 실제로 공간적 중첩을 비교할 signal_components를 2개 이상 선택해야 합니다. direct_precipitation, moisture_supply, low_level_forcing, mid_upper_forcing 중 필요한 요소만 사용하십시오."
        )
    if "'6시간 누적 강수량' 대신" in joined or '"6시간 누적 강수량" 대신' in joined:
        rules.extend(
            [
                "`6시간 누적 강수량`은 6시간 증분 강수와 초기시각부터의 누적강수를 혼동시키는 금지 표현입니다.",
                "6시간 구간 강수를 뜻하면 `6시간 강수량`으로 작성하십시오. 초기시각부터 해당 예측시각까지 누적된 총량을 뜻할 때만 `초기시각부터의 누적강수량`이라고 작성하십시오.",
                "precipitation_amount의 시간별 강도·최대위치 검증은 `6시간 강수량`을 기준으로 작성하고, expected_signal·decision_relevance·purpose 전체에서 모호한 표현을 제거하십시오.",
            ]
        )

    if "precipitation_amount requires direct_precipitation" in joined:
        rules.append(
            "verification_mode=precipitation_amount는 signal_components=[direct_precipitation]에만 사용하십시오."
        )
    if "temporal_change does not support organization_persistence" in joined:
        rules.append(
            "organization_persistence의 여러 시각 바람구조를 비교하려면 verification_mode=spatial_evolution을 사용하십시오. temporal_change를 사용하지 마십시오."
        )
    if "regional_intensity does not support organization_persistence" in joined:
        rules.append(
            "organization_persistence는 regional_intensity를 사용하지 말고 spatial_distribution으로 공간구조를 확인하십시오."
        )
    if "vertical_structure" in joined and "only supports" in joined:
        rules.append(
            "vertical_structure에는 moisture_supply, mid_upper_forcing, organization_persistence 중 실제 단면에서 확인할 요소만 사용하고 단면 위치정보를 모두 유효하게 지정하십시오."
        )
    if "vertical_time_evolution" in joined and "only supports" in joined:
        rules.append(
            "vertical_time_evolution에는 moisture_supply, mid_upper_forcing, organization_persistence 중 실제 시간-고도 구조에서 확인할 요소만 사용하십시오."
        )
    if "QUANTITATIVE_SCOPE_PHASE_MISMATCH" in joined:
        rules.extend(
            [
                "QUANTITATIVE_SCOPE_PHASE_MISMATCH는 정량 명제가 요구하는 F시각과 verification_request/atomic claim이 참조하는 analysis phase가 일치하지 않는다는 뜻입니다.",
                "quantitative_scope.forecast_hours를 오류 회피 목적으로 임의 삭제하지 마십시오. 두 시각 비교가 기상학적으로 필요하면 그 시각을 실제로 포함하는 analysis phase를 선언하고 verification_request.phase_keys와 atomic_claim.phase_keys가 모두 해당 phase를 참조하도록 candidate-plan chunk 전체를 다시 구성하십시오.",
                "필요한 비교시각 또는 비교 자체가 기상학적으로 불필요하다면 원래 후보 컨텍스트에서 정량 명제와 phase 계획을 함께 다시 결정하십시오. 이전 실패 객체의 일부 필드만 patch하지 마십시오.",
            ]
        )

    if "QUANTITATIVE_ATOMICITY_INVALID" in joined:
        rules.extend([
            "QUANTITATIVE_ATOMICITY_INVALID는 한 verification_request 안에 실행 가능한 정량 명제가 둘 이상 선언된 구조적 오류입니다. 하나의 Check만 재생성해서는 별도의 요청을 추가할 수 없으므로 현재 candidate-plan chunk 전체를 재구성하십시오.",
            "서로 다른 정량 검증 명제는 각각 고유한 check_key를 가진 별도 verification_request로 작성하고, 각 요청의 atomic_claims에는 그 정량 명제 하나만 두십시오. expected_signal 및 claim_statement도 그 한 명제에 맞추십시오.",
            "오류를 피하려고 원래 기상학적 판단 기준 중 하나를 조용히 삭제하거나 quantitative_scope를 무조건 null로 바꾸지 마십시오. 기존 명제들이 실제로 별도 검증이 필요하면 모두 보존하십시오. 영상 공간구조 해석만 필요한 명제라면 실행 가능한 정량 검증인 것처럼 표시하지 마십시오.",
            "분리한 새 Check를 hypothesis의 supporting_check_keys/opposing_check_keys가 참조해야 한다면 실제 새 check_key를 참조하도록 그래프 전체를 일관되게 다시 작성하십시오. 기존 Check의 참조가 끊어지지 않게 하십시오.",
            "verification_requests가 스키마 허용 최대 개수에 도달한 경우 명제를 무단 삭제하거나 결합하지 마십시오. 한 chunk 안에서 유효한 계획을 만들 수 없다면 검증 실패를 유지하십시오.",
        ])

    if "QUANTITATIVE_SCOPE_COMPARISON_INVALID" in joined:
        rules.extend(
            [
                "QUANTITATIVE_SCOPE_COMPARISON_INVALID는 expected_state가 요구하는 시간 비교 방식과 quantitative_scope.comparison_forecast_hours가 맞지 않는다는 뜻입니다.",
                "increase/decrease/strengthen/weaken은 forecast_hours의 시간 오름차순 자체가 변화 방향을 정의하므로 comparison_forecast_hours=null로 작성하십시오. 두 시각의 좌항·우항을 명시하는 greater_than/less_than에서만 comparison_forecast_hours=[좌항 시각, 우항 시각]을 사용하십시오.",
                "스키마 통과만을 위해 decrease를 greater_than으로 바꾸거나 increase를 less_than으로 바꾸지 마십시오. 원래 기상학적 명제가 시간 변화인지 두 특정 시각의 대소 비교인지에 따라 expected_state를 유지하거나 verification_request 전체를 다시 작성하십시오.",
            ]
        )
    if (
        "QUANTITATIVE_SCOPE_CONTRACT_INVALID" in joined
        and "forecast_hours must be unique chronological forecast hours" in joined
    ):
        rules.extend(
            [
                "quantitative_scope.forecast_hours는 검증에 사용할 F시각 집합입니다. 동일 시각을 중복하지 말고, 최소 2개의 서로 다른 F시각을 시간 오름차순으로 작성하십시오.",
                "예를 들어 [18,24]는 허용되지만 [24,18], [18,24,18], [18,18]은 허용되지 않습니다. 좌항·우항의 방향은 greater_than/less_than에서만 comparison_forecast_hours로 별도 표현하십시오.",
                "오류를 피하려고 필요한 비교시각을 삭제하지 마십시오. 중복/순서 오류만 고치고, 고유 시각이 1개뿐이라면 원래 가설에 필요한 두 시각을 다시 선택하여 verification_request 전체를 재생성하십시오.",
            ]
        )

    if (
        "QUANTITATIVE_SCOPE_CONTRACT_INVALID" in joined
        and "majority not_strengthen" in joined
    ):
        rules.extend(
            [
                "QUANTITATIVE_SCOPE_CONTRACT_INVALID의 majority + not_strengthen 계약은 925 hPa 수렴(convergence925_1e5_s)의 두 시각 변화만 정량 판정합니다. quantitative_scope.forecast_hours에는 시간순으로 정확히 2개 시각만 넣으십시오.",
                "3개 이상의 시각을 하나의 majority + not_strengthen claim에 넣지 마십시오. 예를 들어 [78,84,90]은 허용되지 않습니다. 피크 전후 두 구간을 모두 확인해야 하면 [78,84]와 [84,90]처럼 각각 별도 verification_request로 분리하고, 각 요청의 atomic_claims도 해당 정량명제 1개만 포함하십시오.",
                "이 계약에서는 feature_id=convergence925_1e5_s, region_scope=candidate_overlap, region_ids=[], comparison_forecast_hours=null, statistic=mean을 유지하십시오. 오류를 피하려고 임의로 다른 feature나 expected_state로 바꾸지 마십시오.",
            ]
        )

    if "QUANTITATIVE_SCOPE_REGION_IDENTITY_MISMATCH" in joined:
        rules.extend(
            [
                "QUANTITATIVE_SCOPE_REGION_IDENTITY_MISMATCH는 자연어 명제의 운영권역명과 quantitative_scope.region_ids가 서로 다른 권역을 가리킨다는 뜻입니다.",
                "오류 문자열의 selected_region_id/selected_region_name_ko와 natural_language_regions 매핑을 권위 있는 운영권역 정보로 사용하십시오. structured region_id를 유지하는 경우 expected_signal과 atomic claim에는 selected_region_name_ko만 사용하고 다른 운영권역 이름을 섞지 마십시오.",
                "다만 원래 기상학적 명제가 natural_language_regions의 다른 권역을 실제 대상으로 한다면 자연어만 치환하지 말고 verification request 전체를 다시 생성하여 quantitative_scope.region_ids와 모든 설명 문자열을 동일한 운영권역으로 일치시키십시오.",
            ]
        )
    if "QUANTITATIVE_ABSOLUTE_THRESHOLD_CONTRACT_MISMATCH" in joined:
        rules.extend([
            "QUANTITATIVE_ABSOLUTE_THRESHOLD_CONTRACT_MISMATCH는 'F096 강수량이 1mm를 넘는다'와 같은 절대 임계값 명제에 F096 대 F090 시간 비교 계약을 잘못 붙인 오류입니다.",
            "현재 specific-v2 quantitative_scope의 greater_than/less_than은 두 F시각의 대소만 판정합니다. 고정 1mm 임계값을 비교시각으로 변환하거나 원래 명제를 임의 삭제하지 마십시오.",
            "고정 임계값 명제는 원래 검증 의미를 보존하는 독립적인 강수 존재/수치 검증 요청으로 다시 구성하십시오. 강수 존재 최소 1mm는 별도 직접강수 판정 계약으로 검증하며, 임의 수치 임계값을 현행 시간비교 정량 계약으로 표현하지 마십시오. 현재 지원되는 방법으로 검증할 수 없다면 검증 실패를 유지하십시오.",
            "해당 오류를 받은 verification_request의 expected_signal과 atomic_claim.claim_statement, expected_state 및 quantitative_scope를 같은 명제로 일치시키십시오. 다른 시각 대비 증가·감소는 필요할 경우 별도 Check로 작성하십시오.",
        ])
    if "QUANTITATIVE_SCOPE_COMPARISON_IDENTITY_MISMATCH" in joined:
        rules.extend(
            [
                "QUANTITATIVE_SCOPE_COMPARISON_IDENTITY_MISMATCH는 greater_than/less_than 자연어 명제의 좌항·우항 시각 순서와 comparison_forecast_hours 순서가 다르다는 뜻입니다.",
                "atomic claim에 F090이 F084보다 크다고 썼다면 comparison_forecast_hours=[90,84]처럼 자연어 좌항·우항과 같은 순서를 사용하십시오.",
            ]
        )
    if "QUANTITATIVE_SCOPE_REGION_UNAVAILABLE" in joined:
        rules.extend(
            [
                "QUANTITATIVE_SCOPE_REGION_UNAVAILABLE는 정량 명제의 region_id가 candidate_area와 실제로 겹치는 운영권역 목록에 없다는 뜻입니다.",
                "오류 문자열의 selectable_region_ids 중 원래 기상학적 명제와 실제 후보영역에 맞는 정확히 하나의 운영권역을 선택하십시오. 존재하지 않는 권역명을 만들거나 후보영역을 확대해 맞추지 마십시오.",
                "적절한 운영권역이 없어 해당 specific 정량 명제를 유지할 수 없다면 공간장 기반의 질적 검증으로 verification_request 전체를 다시 구성하고 quantitative_scope=null로 작성하십시오.",
            ]
        )

    if "COMPACT_PLAN_CONTINUATION_NO_PROGRESS" in joined:
        rules.extend(
            [
                "COMPACT_PLAN_CONTINUATION_NO_PROGRESS는 후속 chunk가 continuation_context.remaining_roles 중 어느 하나도 실제 실행 가능한 검증 구조로 충족하지 못했다는 뜻입니다.",
                "remaining_roles가 하나 이상이면 빈 verification_requests를 반환하지 말고, remaining_role_guidance와 existing_phases의 forecast_hours를 사용하여 최소 하나의 남은 역할을 실제로 감소시키는 verification_request를 작성하십시오.",
                "temporal_evolution과 spatial_evolution이 함께 남아 있으면 둘 이상의 실제 예측시각을 비교하는 verification_mode=spatial_evolution 요청 하나로 두 역할을 함께 충족할 수 있습니다. 단일시각 요청에 coverage_roles만 추가해서는 안 됩니다.",
                "이전 chunk의 phase와 Check를 재작성하지 말고, 필요한 기존 phase는 existing_phases의 phase_key를 그대로 참조하십시오. 새 phase가 필요할 때만 available_forecast_hours 범위에서 추가하십시오.",
            ]
        )

    if "COMPACT_PLAN_UNREFERENCED_PHASE" in joined:
        rules.extend(
            [
                "COMPACT_PLAN_UNREFERENCED_PHASE는 is_complete=true인 최종 candidate-plan chunk에 어떤 verification_request도 참조하지 않는 analysis_phase가 남았다는 뜻입니다.",
                "최종 계획의 모든 analysis_phase는 이전 chunk와 현재 chunk를 합쳐 최소 하나의 verification_request.phase_keys에서 실제로 사용되어야 합니다. 해당 시각대가 기상학적으로 필요하면 그 phase를 사용하는 의미 있는 verification_request 전체를 작성하고 가설과 연결하십시오. 필요하지 않으면 그 analysis_phase 자체를 출력하지 마십시오.",
                "오류의 chunkNN: 접두사는 검증용 namespace입니다. 출력 phase_key에는 접두사를 붙이지 마십시오. 예를 들어 chunk01:P3는 현재 chunk의 로컬 phase_key P3를 뜻합니다.",
                "candidate_peak_forecast_hours를 포함하는 peak phase는 제거하지 말고, 반드시 하나 이상의 적절한 verification_request가 참조하도록 계획 전체를 일관되게 다시 구성하십시오.",
            ]
        )

    if "POSITIVE_ALIGNMENT_MIXED_POLARITY" in joined or "POSITIVE_ALIGNMENT_NON_OVERLAP_CLAIM" in joined:
        rules.extend(
            [
                "POSITIVE_ALIGNMENT 관련 오류는 핵심시각의 강수-환경 결합과 이후 약화·분리·지속성 제한을 하나의 Check에 섞었다는 뜻입니다.",
                "핵심시각의 긍정적 결합은 evidence_role=supporting, verification_mode=multivariable_alignment인 독립 verification_request 하나로 만들고 atomic_claims에는 positive overlap 명제만 남기십시오.",
                "F018 이후 약화, decrease, weaken, not_overlap, split, reorganize 등 후속 지속성 제한은 별도 verification_request로 분리하십시오. 동일 Check 안에 결합 존재와 결합 약화를 함께 넣지 마십시오.",
            ]
        )
    if "POSITIVE_ALIGNMENT_SCOPE_TOO_BROAD" in joined:
        rules.extend(
            [
                "POSITIVE_ALIGNMENT_SCOPE_TOO_BROAD는 강수와 환경요소의 긍정적 중첩을 후보 bbox 전체(all)에 요구했다는 뜻입니다.",
                "운영상 의미 있는 결합영역이 후보 안에 하나 이상 존재하는지를 묻는 일반 결합 Check는 scope_quantifier=at_least_one을 사용하십시오. 특정 하위영역을 명시적으로 검증할 때만 specific을 사용하십시오.",
                "후보영역 전체가 동시에 중첩되어야 한다는 조건을 만들지 마십시오.",
            ]
        )
    if "POSITIVE_ALIGNMENT_DUPLICATE_ATOMIC_CLAIM" in joined:
        rules.extend(
            [
                "POSITIVE_ALIGNMENT_DUPLICATE_ATOMIC_CLAIM은 같은 시각·요소·공간범위의 긍정적 다변수 중첩을 둘 이상의 Check가 중복 검증한다는 뜻입니다.",
                "동일한 핵심시각 결합 명제는 정확히 하나의 pure multivariable_alignment verification_request에만 두십시오.",
                "후속 약화·지속성·이동을 추가로 판단해야 하면 같은 overlap Check를 다시 만들지 말고 별도 temporal/spatial verification_request로 구성하십시오.",
            ]
        )
    if "QUANTITATIVE_COMPLEMENTARY_DUPLICATE_CHECK" in joined:
        rules.extend(
            [
                "QUANTITATIVE_COMPLEMENTARY_DUPLICATE_CHECK는 동일 물리량·동일 운영권역·동일 비교시각에 대해 서로 반대 방향의 정량 명제를 별도 Check로 중복 생성했다는 뜻입니다.",
                "강화/약화 또는 증가/감소 중 하나의 검증 명제만 유지하십시오. 실제 값이 반대이면 Step07이 opposite_observed로 판정하므로 반대 명제를 별도 Check로 다시 만들지 마십시오.",
                "필요한 추가 Check는 다른 시간구간, 다른 운영권역, 다른 물리량 또는 실제로 독립적인 의사결정 질문을 검증해야 합니다.",
            ]
        )

    if "HYPOTHESIS_CHECK_ROLE_CONFLICT" in joined:
        rules.extend(
            [
                "HYPOTHESIS_CHECK_ROLE_CONFLICT는 동일한 Check를 같은 가설의 supporting과 opposing 양쪽에 동시에 연결했다는 뜻입니다.",
                "하나의 Check에는 하나의 검증 명제와 하나의 evidence_role만 유지하십시오. 지지 가설과 제한 가설이 모두 필요하면 서로 다른 verification_request로 분리하십시오.",
                "특히 강화와 약화, overlap과 not_overlap처럼 방향이 반대인 명제를 한 Check로 병합하거나 양쪽 역할에 재사용하지 마십시오.",
            ]
        )
    if "COMPACT_CHECK_MERGE_CONTRACT_CONFLICT" in joined:
        rules.extend(
            [
                "COMPACT_CHECK_MERGE_CONTRACT_CONFLICT는 의미가 다른 verification_request를 하나의 Check로 합치면 안 된다는 뜻입니다.",
                "evidence_role이 다르거나 expected_state, 비교시각, quantitative_scope가 다른 명제는 각각 독립 verification_request로 유지하십시오.",
                "강화 후 약화처럼 연속된 두 변화도 하나의 Check로 합치지 말고 각 시간구간을 별도 명제로 작성하십시오.",
            ]
        )


    internal_markers = (
        "vertical_wind_shear is spatial-only",
        "temporal diagnostic/method must declare check_scope=temporal_evolution",
        "multivariable_composite must declare check_scope=multivariable_alignment",
        "section diagnostic must declare check_scope=vertical_structure",
        "multivariable_alignment requires multivariable_composite and at least two signal components",
        "vertical_structure requires a section diagnostic",
        "has invalid required_signal_components",
        "CHECK_EXECUTION_COMPONENT_UNAVAILABLE",
    )
    if any(marker in joined for marker in internal_markers):
        rules.append(
            "이 오류는 Python이 만든 내부 실행계약의 불변조건 오류입니다. AI는 내부 실행필드를 수정하지 말고 원래 verification_request의 기상학적 의도만 일관되게 다시 생성하십시오."
        )

    return rules


def _compact_check_keys_from_validation_errors(
    validation_errors: list[str],
    *,
    parsed_plan: dict[str, Any] | None = None,
) -> list[str]:
    """Extract compact diagnostic-check keys named by semantic validation errors.

    A remaining QUANTITATIVE_SCOPE_PHASE_MISMATCH means deterministic phase
    alignment could not resolve the required hour to one existing phase. That is
    a phase-graph problem, so it must regenerate the whole candidate-plan chunk
    rather than repeatedly regenerating one Check that cannot create phases.

    Step04 repair is atomic at the diagnostic-check level.  Most semantic
    validators name ``check_key`` directly, while schema/narrative validators
    can report only an array path such as ``diagnostic_checks[3]``.  When the
    current normalized plan is available, resolve that stable array index back
    to its existing ``check_key`` so a local text error cannot force a whole
    candidate chunk regeneration.  Individual fields are never patched.
    """
    if any("QUANTITATIVE_SCOPE_PHASE_MISMATCH" in str(error) for error in validation_errors):
        return []
    if any("insufficient forecast-hour coverage" in str(error) for error in validation_errors):
        if not isinstance(parsed_plan, dict):
            return []
        phase_rows = parsed_plan.get("analysis_phases")
        if not isinstance(phase_rows, list) or not all(isinstance(x, dict) for x in phase_rows):
            return []
        # When the plan has only one distinct hour, rebuilding a Check alone
        # cannot add another phase.  Regenerate the whole phase+check graph.
        local_hours = {hour for phase in phase_rows for hour in _compact_phase_hours(phase)}
        if len(local_hours) < 2:
            return []

    keys: list[str] = []
    seen: set[str] = set()
    patterns = (
        re.compile(r"check_key=([^\s,;]+)"),
        re.compile(r"check_key['\"]?\s*[:=]\s*['\"]([^'\"\s,;]+)"),
    )
    # ``parsed_plan`` is the normalized compact chunk only when transport
    # parsing + normalization succeeded.  On parse/transport/normalization
    # failure ``call_and_validate_json`` intentionally supplies an empty dict.
    # Atomic repair cannot be localized without a valid normalized Check array,
    # so return no indexed Checks here and let the existing whole-chunk repair
    # path regenerate from the immutable candidate context.  Do not call the
    # strict ``_compact_plan_rows`` helper on a partial/empty plan because that
    # would mask the original validation error with a new shape error.
    indexed_checks: list[dict[str, Any]] = []
    if isinstance(parsed_plan, dict):
        raw_checks = parsed_plan.get("diagnostic_checks")
        if isinstance(raw_checks, list) and all(isinstance(item, dict) for item in raw_checks):
            indexed_checks = raw_checks
    available_keys = [
        str(row.get("check_key") or "").strip()
        for row in indexed_checks
        if str(row.get("check_key") or "").strip()
    ]

    def resolve_current_key(raw_key: str) -> str:
        """Resolve validator/local Check keys to the normalized chunk key.

        Compact-plan normalization namespaces declarations as
        ``chunkNN:<local_key>``.  Validators are not fully uniform: some emit
        the namespaced key while others report only the provider-authored local
        key.  Atomic repair must target the key stored in the current normalized
        plan; stripping the namespace unconditionally makes the target disappear
        and incorrectly falls back to whole-chunk regeneration.

        If a parsed plan object was supplied but does not contain a valid
        normalized ``diagnostic_checks`` array, the transport/normalization
        boundary itself failed.  In that state there is no safe Check object to
        replace, even when the error text happens to mention ``check_key``.
        Return an empty target so the bounded whole-chunk regeneration path can
        recover from the immutable candidate context.
        """
        key = raw_key.strip().strip("'\"[]()")
        if not key:
            return key
        if isinstance(parsed_plan, dict) and not available_keys:
            return ""
        if not available_keys:
            return key
        if key in available_keys:
            return key
        local_key = re.sub(r"^chunk\d+:", "", key, flags=re.IGNORECASE)
        matches = [
            candidate_key
            for candidate_key in available_keys
            if re.sub(r"^chunk\d+:", "", candidate_key, flags=re.IGNORECASE) == local_key
        ]
        return matches[0] if len(matches) == 1 else key

    index_pattern = re.compile(r"diagnostic_checks\[(\d+)\]")
    for error in validation_errors:
        text = str(error)
        for pattern in patterns:
            for match in pattern.finditer(text):
                key = resolve_current_key(match.group(1))
                if key and key not in seen:
                    seen.add(key)
                    keys.append(key)
        for match in index_pattern.finditer(text):
            index = int(match.group(1))
            if index < 0 or index >= len(indexed_checks):
                continue
            key = str(indexed_checks[index].get("check_key") or "").strip()
            if key and key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


def _compact_check_scope_constraints_from_validation_errors(
    validation_errors: list[str],
) -> dict[str, str]:
    """Return scope constraints already implied by named Check contract errors.

    These are not field patches.  They make the full-Check regeneration response
    schema reject only a scope value that the local validator has already proved
    impossible for that exact Check.
    """
    constraints: dict[str, str] = {}
    for error in validation_errors:
        text = str(error)
        scope: str | None = None
        if "temporal diagnostic/method must declare check_scope=temporal_evolution" in text:
            scope = "temporal_evolution"
        elif "multivariable_composite must declare check_scope=multivariable_alignment" in text:
            scope = "multivariable_alignment"
        elif "section diagnostic must declare check_scope=vertical_structure" in text:
            scope = "vertical_structure"
        if scope is not None:
            for check_key in _compact_check_keys_from_validation_errors([text]):
                constraints[check_key] = scope
    return constraints


def _compact_check_repair_schema(
    transport_schema: dict[str, Any],
    target_keys: list[str],
    *,
    check_scope_constraints: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build an atomic-repair schema at the provider transport boundary.

    OpenAI/Codex candidate-plan transport uses ``verification_requests``.  The
    normalized in-process representation uses ``diagnostic_checks`` after
    ``_derive_diagnostic_check_from_verification_request``.  Atomic repair must
    therefore regenerate the transport object and only then translate it back to
    the normalized Check representation.  Legacy schemas that already expose
    ``diagnostic_checks`` remain supported.
    """
    properties = transport_schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError("candidate-plan transport schema has no properties")

    transport_field: str
    item_schema_source: Any
    if isinstance(properties.get("verification_requests"), dict):
        transport_field = "verification_requests"
        item_schema_source = properties[transport_field].get("items")
    elif isinstance(properties.get("diagnostic_checks"), dict):
        transport_field = "diagnostic_checks"
        item_schema_source = properties[transport_field].get("items")
    else:
        raise ValueError(
            "candidate-plan transport schema has neither verification_requests nor diagnostic_checks item schema"
        )
    if not isinstance(item_schema_source, dict):
        raise ValueError(
            f"candidate-plan transport schema has no {transport_field} item schema"
        )

    constraints = check_scope_constraints or {}
    variants: list[dict[str, Any]] = []
    for check_key in target_keys:
        variant = copy.deepcopy(item_schema_source)
        variant_properties = variant.get("properties")
        if not isinstance(variant_properties, dict):
            raise ValueError(
                f"candidate-plan {transport_field} item schema has no properties"
            )
        variant_properties["check_key"] = {"type": "string", "enum": [check_key]}
        # ``check_scope`` is Python-owned for OpenAI/Codex transport requests and
        # is derived from verification_mode.  Apply a direct constraint only to
        # legacy schemas that actually expose the field.
        if check_key in constraints and "check_scope" in variant_properties:
            variant_properties["check_scope"] = {
                "type": "string",
                "enum": [constraints[check_key]],
            }
        variants.append(variant)

    item_schema: dict[str, Any] = (
        variants[0] if len(variants) == 1 else {"anyOf": variants}
    )
    return {
        "title": "AIFS Pass1 Candidate Plan Atomic Regeneration",
        "type": "object",
        "additionalProperties": False,
        "required": [transport_field],
        "properties": {
            transport_field: {
                "type": "array",
                "minItems": len(target_keys),
                "maxItems": len(target_keys),
                "items": item_schema,
            }
        },
    }


def _compact_continuation_gap_repair_schema(
    transport_schema: dict[str, Any],
) -> dict[str, Any]:
    """Return a constrained whole-chunk schema for spatial/temporal gap repair.

    A continuation that leaves ``spatial_evolution``/``temporal_evolution``
    uncovered has already demonstrated that a free-form regeneration prompt is
    not sufficiently binding.  This schema keeps the meteorological choice with
    the model (component, times, hypothesis, expected signal) while constraining
    the repair unit to an executable ``spatial_evolution`` request.

    The response remains a normal candidate-plan transport chunk and is passed
    through the ordinary semantic validator afterwards.
    """
    schema = copy.deepcopy(transport_schema)
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("candidate-plan transport schema has no properties")
    requests = properties.get("verification_requests")
    if not isinstance(requests, dict):
        raise ValueError(
            "candidate-plan transport schema has no verification_requests for continuation gap repair"
        )
    item = requests.get("items")
    if not isinstance(item, dict):
        raise ValueError(
            "candidate-plan verification_requests schema has no item definition for continuation gap repair"
        )
    item_properties = item.get("properties")
    if not isinstance(item_properties, dict):
        raise ValueError(
            "candidate-plan verification request schema has no properties for continuation gap repair"
        )

    # At least one request must be emitted and every emitted request in this
    # specialized repair must close the spatial/temporal evolution gap.  Do not
    # let unrelated valid Checks consume the bounded chunk-regeneration budget.
    requests["minItems"] = 1
    requests["maxItems"] = min(int(requests.get("maxItems", 10)), 3)
    item_properties["verification_mode"] = {
        "type": "string",
        "enum": ["spatial_evolution"],
    }

    signal_components = item_properties.get("signal_components")
    if isinstance(signal_components, dict):
        signal_components["minItems"] = 1
        signal_components["maxItems"] = 1

    # A spatial-evolution request must contain an explicit proposition rather
    # than relying on coverage-role labels alone.  The model still chooses which
    # of these meteorological states is appropriate for the candidate.
    claims = item_properties.get("atomic_claims")
    if isinstance(claims, dict):
        claims["minItems"] = 1
        claim_item = claims.get("items")
        if isinstance(claim_item, dict):
            claim_properties = claim_item.get("properties")
            if isinstance(claim_properties, dict) and isinstance(
                claim_properties.get("expected_state"), dict
            ):
                claim_properties["expected_state"] = {
                    "type": "string",
                    "enum": [
                        "move",
                        "persist",
                        "split",
                        "merge",
                        "expand",
                        "contract",
                        "reorganize",
                    ],
                }

    title = str(schema.get("title") or "AIFS Pass1 Candidate Plan")
    schema["title"] = f"{title} Continuation Spatial Evolution Gap Repair"
    return schema


def _continuation_gap_requires_spatial_repair(
    validation_errors: list[str],
) -> bool:
    joined = "\n".join(str(error) for error in validation_errors)
    return (
        "COMPACT_PLAN_CONTINUATION_NO_PROGRESS" in joined
        and "spatial_evolution" in joined
    )


def _compact_existing_phase_context(
    chunks: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Return only the prior phase identifiers and actual hours needed for repair."""
    rows: list[dict[str, Any]] = []
    for chunk in chunks or []:
        for phase in _compact_plan_rows(chunk, "analysis_phases"):
            rows.append(
                {
                    "phase_key": str(phase.get("phase_key") or ""),
                    "phase_type": str(phase.get("phase_type") or ""),
                    "forecast_hours": list(_compact_phase_hours(phase)),
                }
            )
    return rows


def _merge_compact_check_regeneration(
    original: dict[str, Any],
    regenerated: dict[str, Any],
    target_keys: list[str],
) -> dict[str, Any]:
    """Replace complete targeted Checks and preserve every other plan unit.

    Provider transport repairs may return ``verification_requests`` while the
    normalized plan stores ``diagnostic_checks``.  Convert each repaired request
    through the same deterministic adapter used for the initial candidate plan;
    never field-patch a normalized Check.
    """
    if isinstance(regenerated.get("verification_requests"), list):
        transport_rows = _compact_plan_rows(regenerated, "verification_requests")
        replacement_rows = [
            _derive_diagnostic_check_from_verification_request(row)
            for row in transport_rows
        ]
    else:
        replacement_rows = _compact_plan_rows(regenerated, "diagnostic_checks")
    replacements = {str(row.get("check_key", "")): row for row in replacement_rows}
    if set(replacements) != set(target_keys) or len(replacement_rows) != len(target_keys):
        raise ValueError(
            "atomic diagnostic-check regeneration must return exactly the requested check_key values "
            f"expected={sorted(target_keys)} actual={sorted(replacements)}"
        )
    merged = copy.deepcopy(original)
    output_rows: list[dict[str, Any]] = []
    found: set[str] = set()
    for row in _compact_plan_rows(merged, "diagnostic_checks"):
        key = str(row.get("check_key", ""))
        if key in replacements:
            output_rows.append(copy.deepcopy(replacements[key]))
            found.add(key)
        else:
            output_rows.append(row)
    missing = set(target_keys) - found
    if missing:
        raise ValueError(f"atomic diagnostic-check regeneration target not found in source chunk: {sorted(missing)}")
    merged["diagnostic_checks"] = output_rows
    return merged

def call_and_validate_json(
    *,
    client: Pass1ProviderClient,
    model: str,
    messages: list[dict[str, Any]],
    schema: dict[str, Any],
    schema_name: str,
    structured_output_mode: str,
    enable_thinking: bool,
    max_tokens: int,
    temperature: float,
    top_p: float,
    repair_attempts: int,
    semantic_validator,
    normalizer=None,
    fixed_field_expectations: dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    source_retry_attempts: int = 0,
    source_retry_after_repair_reserve: int = 0,
    source_retry_context: dict[str, Any] | None = None,
    source_retry_schema: dict[str, Any] | None = None,
    source_retry_schema_name: str | None = None,
    source_retry_before_repair_on_parse_error: bool = False,
    repair_context_messages: list[dict[str, Any]] | None = None,
    compact_length_recovery: dict[str, Any] | None = None,
    semantic_unit_repair: str | None = None,
    repair_identity: dict[str, Any] | None = None,
    nonrepairable_error_markers: tuple[str, ...] = (),
    chunk_repair_from_original_context: bool = False,
    chunk_repair_previous_chunks: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]:
    fixed_field_mismatches: list[dict[str, Any]] = []
    atomic_repair_history: list[dict[str, Any]] = []

    def parse_normalize_validate(raw_text: str, *, stage: str) -> tuple[dict[str, Any], list[str]]:
        try:
            parsed_value = extract_json_object(raw_text)
            if fixed_field_expectations is not None:
                expected_fields = (
                    fixed_field_expectations(parsed_value)
                    if callable(fixed_field_expectations)
                    else fixed_field_expectations
                )
                mismatches = inspect_fixed_fields(parsed_value, expected_fields)
                if mismatches:
                    fixed_field_mismatches.append({"stage": stage, "mismatches": mismatches})
            if normalizer is not None:
                parsed_value = normalizer(parsed_value)
            return parsed_value, semantic_validator(parsed_value)
        except Exception as exc:  # noqa: BLE001
            return {}, [str(exc)]

    def is_json_parse_error(validation_errors: list[str]) -> bool:
        return any("valid JSON object" in error for error in validation_errors)

    def retry_with_original_images(*, retry_index: int, stage_prefix: str) -> None:
        nonlocal result, raw, envelope_summary, parsed, errors, source_retry_count, http_retry_count, usage_attempts
        logging.warning(
            "원본 이미지 기반 재시도 단계=%s 시도=%s 오류=%s",
            stage_prefix,
            retry_index,
            errors[:10],
        )
        retry_result = client.complete(
            model=model,
            messages=build_source_grounded_retry_messages(
                messages,
                validation_errors=errors,
                retry_context=source_retry_context or {},
            ),
            response_schema=source_retry_schema or schema,
            schema_name=source_retry_schema_name or schema_name,
            structured_output_mode=structured_output_mode,
            enable_thinking=False,
            max_tokens=max_tokens,
            temperature=0.0,
            top_p=1.0,
        )
        result = retry_result
        source_retry_count += 1
        http_retry_count += int(getattr(retry_result, "http_retry_count", 0))
        usage_attempts.extend(
            getattr(retry_result, "usage_attempts", ())
            or (_compact_provider_envelope(retry_result.response_envelope).get("usage"),)
        )
        raw = retry_result.content
        envelope_summary = _compact_provider_envelope(retry_result.response_envelope)
        parsed, errors = parse_normalize_validate(
            raw,
            stage=f"{stage_prefix}_{retry_index}",
        )

    result = client.complete(
        model=model,
        messages=messages,
        response_schema=schema,
        schema_name=schema_name,
        structured_output_mode=structured_output_mode,
        enable_thinking=enable_thinking,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
    )
    raw = result.content
    envelope_summary = _compact_provider_envelope(result.response_envelope)
    usage_attempts: list[dict[str, Any] | None] = list(
        getattr(result, "usage_attempts", ()) or (envelope_summary.get("usage"),)
    )
    repair_count = 0
    http_retry_count = int(getattr(result, "http_retry_count", 0))
    parsed, errors = parse_normalize_validate(raw, stage="initial")

    source_retry_count = 0
    compact_recovery_count = 0
    source_retry_after_repair_reserve = min(
        max(0, int(source_retry_after_repair_reserve)),
        source_retry_attempts,
    )
    source_retry_before_repair_attempts = (
        source_retry_attempts - source_retry_after_repair_reserve
    )
    if source_retry_context is not None and (
        not is_json_parse_error(errors) or source_retry_before_repair_on_parse_error
    ):
        for source_retry_index in range(source_retry_before_repair_attempts):
            if not errors:
                break
            retry_with_original_images(
                retry_index=source_retry_index + 1,
                stage_prefix="source_grounded_retry",
            )

    # Multiple executable quantitative claims require *additional* verification
    # requests and potentially updated hypothesis references. A full-Check
    # repair is intentionally one-to-one (same check_key), so it cannot comply
    # with the validator's split instruction. Escalate this structural case to
    # whole-chunk regeneration instead of burning three futile atomic retries.
    # A compact plan can contain independent Check failures.  Each Check gets
    # its own bounded atomic-regeneration budget; repairs for CHK_A must not
    # consume the budget available to CHK_B.
    chunk_repair_attempts = 0
    check_repair_attempts: dict[str, int] = {}
    check_last_error_signature: dict[str, str] = {}
    chunk_last_error_signature: str | None = None
    chunk_last_state_signature: str | None = None
    candidate_chunk_repair_history: list[dict[str, Any]] = []
    repair_iteration = 0
    while errors:
        joined_errors = "\n".join(str(error) for error in errors)
        matched_nonrepairable = [
            marker for marker in nonrepairable_error_markers if marker in joined_errors
        ]
        if matched_nonrepairable:
            errors = [
                "STEP04_INTERNAL_EXECUTION_MAPPING_ERROR "
                f"markers={matched_nonrepairable}; Python-owned execution contract failed after deterministic intent mapping",
                *errors,
            ]
            break

        # A Check-only repair cannot create or enlarge the chunk-level
        # context_domain.  Skip futile atomic retries for that structural
        # failure, and regenerate the whole chunk from immutable context.
        quantitative_request_split_required = any(
            "QUANTITATIVE_ATOMICITY_INVALID" in str(error) for error in errors
        )
        spatial_context_creation_required = (
            "SPATIAL_ANALYSIS_GRID_INSUFFICIENT" in joined_errors
            and "Provide a genuine context_domain" in joined_errors
        )
        atomic_check_targets = (
            _compact_check_keys_from_validation_errors(errors, parsed_plan=parsed)
            if semantic_unit_repair == "diagnostic_check"
            and not spatial_context_creation_required
            and not quantitative_request_split_required
            else []
        )
        if atomic_check_targets:
            check_error_signatures = {
                check_key: " | ".join(
                    sorted(
                        str(error)
                        for error in errors
                        if check_key in _compact_check_keys_from_validation_errors(
                            [str(error)], parsed_plan=parsed
                        )
                    )
                )
                for check_key in atomic_check_targets
            }
            # Most identical repair failures indicate true non-progress and
            # should stop immediately.  One narrow structural contract is
            # allowed to consume the remaining bounded repair budget: when
            # forecast_hours collapses to fewer than two unique times Python
            # cannot invent the missing comparison hour, so the model gets the
            # normal full-Check regeneration budget with an explicit repair
            # rule instead of being stopped after the first identical retry.
            _bounded_same_signature_marker = (
                "forecast_hours must be unique chronological forecast hours"
            )
            repeated_error_targets = [
                check_key
                for check_key, signature in check_error_signatures.items()
                if (
                    signature
                    and check_last_error_signature.get(check_key) == signature
                    and not (
                        _bounded_same_signature_marker in signature
                        and check_repair_attempts.get(check_key, 0) < repair_attempts
                    )
                )
            ]
            if repeated_error_targets:
                errors = [
                    *errors,
                    "STEP04_ATOMIC_REPAIR_NONPROGRESS "
                    f"check_keys={sorted(repeated_error_targets)}",
                ]
                break
            exhausted_targets = [
                check_key
                for check_key in atomic_check_targets
                if check_repair_attempts.get(check_key, 0) >= repair_attempts
            ]
            if exhausted_targets:
                errors = [
                    *errors,
                    "STEP04_ATOMIC_REPAIR_BUDGET_EXHAUSTED "
                    f"check_keys={sorted(exhausted_targets)} max_attempts={repair_attempts}",
                ]
                break
            # Regenerate complete Check objects only.  Do not ask the model to patch
            # individual fields, and do not accept changes to unrelated plan units.
            repair_schema = _compact_check_repair_schema(
                schema,
                atomic_check_targets,
                check_scope_constraints=_compact_check_scope_constraints_from_validation_errors(errors),
            )
            current_checks = {
                str(row.get("check_key", "")): row
                for row in _compact_plan_rows(parsed, "diagnostic_checks")
            }
            missing_targets = [key for key in atomic_check_targets if key not in current_checks]
            if missing_targets:
                # If validation named a Check that cannot be resolved to the current
                # normalized chunk, fall back to whole-chunk regeneration below.
                atomic_check_targets = []
            else:
                repair_iteration += 1
                target_attempts = {
                    check_key: check_repair_attempts.get(check_key, 0) + 1
                    for check_key in atomic_check_targets
                }
                check_repair_attempts.update(target_attempts)
                check_last_error_signature.update(check_error_signatures)
                phase_keys = [
                    str(row.get("phase_key", ""))
                    for row in _compact_plan_rows(parsed, "analysis_phases")
                ]
                phase_hours_by_key = {
                    str(row.get("phase_key", "")): sorted(_compact_phase_hours(row))
                    for row in _compact_plan_rows(parsed, "analysis_phases")
                }
                history_rows = []
                for check_key in atomic_check_targets:
                    check = current_checks[check_key]
                    history_row = {
                        **(repair_identity or {}),
                        "check_key": check_key,
                        "repair_unit": "full_check",
                        "repair_attempt": target_attempts[check_key],
                        "repair_attempt_limit": repair_attempts,
                        "validation_errors": list(errors),
                        "diagnostic_id": check.get("diagnostic_id"),
                        "check_scope": check.get("check_scope"),
                        "analysis_methods": list(check.get("analysis_methods", [])),
                        "required_signal_components": list(check.get("required_signal_components", [])),
                        "forecast_hours": sorted({
                            hour
                            for phase_key in check.get("phase_keys", [])
                            for hour in phase_hours_by_key.get(str(phase_key), [])
                        }),
                    }
                    atomic_repair_history.append(history_row)
                    history_rows.append(history_row)
                repair_transport_field = (
                    "verification_requests"
                    if "verification_requests" in repair_schema.get("properties", {})
                    else "diagnostic_checks"
                )
                repair_prompt = {
                    "task": (
                        "검증에 실패한 verification request를 필드 수정이 아니라 요청 전체 단위로 처음부터 다시 생성하십시오."
                        if repair_transport_field == "verification_requests"
                        else "검증에 실패한 diagnostic Check를 필드 수정이 아니라 Check 전체 단위로 처음부터 다시 생성하십시오."
                    ),
                    "validation_errors": errors,
                    "regenerate_check_keys": atomic_check_targets,
                    "allowed_phase_keys": phase_keys,
                    "rules": [
                        f"{repair_transport_field} 배열만 반환하십시오.",
                        "각 regenerate_check_keys에 대해 정확히 하나의 완전한 객체를 반환하십시오.",
                        "check_key는 입력값을 정확히 유지하십시오.",
                        (
                            "기존 verification request의 일부 필드만 고치지 마십시오. 기존 객체는 폐기된 참고자료이며 signal_components, verification_mode, domain_scope, spatial_relation_scope, phase_keys, representative_spatial_hours, expected_signal, decision_relevance, purpose, priority, evidence_role, coverage_roles, atomic_claims 및 단면 관련 필드를 하나의 일관된 요청으로 함께 다시 결정하십시오."
                            if repair_transport_field == "verification_requests"
                            else "기존 Check의 일부 필드만 고치지 마십시오. 기존 객체는 폐기된 참고자료이며 diagnostic_id, variable_role, required_signal_components, check_scope, phase_keys, analysis_methods, expected_signal, decision_relevance, purpose, priority, evidence_role, coverage_roles 및 단면 관련 필드를 하나의 일관된 Check로 함께 다시 결정하십시오."
                        ),
                        "원래 후보와 가설의 목적은 앞선 대화 컨텍스트에서 다시 확인하고, 현재 active catalog가 지원하는 진단만 선택하십시오.",
                        "phase_keys는 allowed_phase_keys 중에서만 선택하십시오. 새로운 phase_key를 만들지 마십시오.",
                        "지원되지 않는 product, feature, diagnostic 또는 분석방법을 만들어 내지 마십시오.",
                        "사람이 읽는 모든 설명 문자열은 한국어로 작성하십시오.",
                        *_repair_rules_for_validation_errors(errors),
                    ],
                }
                logging.warning(
                    "STEP04_ATOMIC_REPAIR unit=full_check checks=%s attempts=%s/%s errors=%s",
                    atomic_check_targets,
                    target_attempts,
                    repair_attempts,
                    errors[:10],
                )
                repair_messages = [
                    {
                        "role": "system",
                        "content": (
                            "엄격한 기계 처리 파이프라인의 Step04 검증 요청을 복구합니다. "
                            "필드 패치가 아니라 지정된 검증 객체 전체를 다시 생성하고 JSON만 반환하십시오."
                        ),
                    },
                ]
                if repair_context_messages is not None:
                    repair_messages.extend(repair_context_messages)
                repair_messages.append(
                    {"role": "user", "content": json.dumps(repair_prompt, ensure_ascii=False)}
                )
                repair_result = client.complete(
                    model=model,
                    messages=repair_messages,
                    response_schema=repair_schema,
                    schema_name=f"{schema_name}DiagnosticCheckRepair",
                    structured_output_mode=structured_output_mode,
                    enable_thinking=False,
                    max_tokens=max_tokens,
                    temperature=0.0,
                    top_p=1.0,
                )
                result = repair_result
                repair_count += 1
                http_retry_count += int(getattr(repair_result, "http_retry_count", 0))
                usage_attempts.extend(
                    getattr(repair_result, "usage_attempts", ())
                    or (_compact_provider_envelope(repair_result.response_envelope).get("usage"),)
                )
                raw = repair_result.content
                envelope_summary = _compact_provider_envelope(repair_result.response_envelope)
                try:
                    replacement_value = extract_json_object(raw)
                    schema_errors = _validate_jsonschema(replacement_value, repair_schema)
                    if schema_errors:
                        raise ValueError(" | ".join(schema_errors[:12]))
                    candidate_value = _merge_compact_check_regeneration(
                        parsed, replacement_value, atomic_check_targets
                    )
                    candidate_errors = semantic_validator(candidate_value)
                    parsed, errors = candidate_value, candidate_errors
                    for history_row in history_rows:
                        history_row["result"] = "PASS" if not candidate_errors else "FAIL"
                        history_row["next_validation_errors"] = list(candidate_errors)
                except Exception as exc:  # noqa: BLE001
                    errors = [str(exc)]
                    for history_row in history_rows:
                        history_row["result"] = "FAIL"
                        history_row["next_validation_errors"] = list(errors)
                continue

        # Errors that cannot be localized to one diagnostic Check (for example a
        # missing first-chunk precipitation Check or a phase/hypothesis contract)
        # are regenerated at the complete chunk level from the original candidate
        # context. A discarded provider response or Check snapshot is never sent
        # back to the model.
        chunk_error_signature = " | ".join(sorted(str(error) for error in errors))
        chunk_state_signature = canonical_hash(
            {
                "validation_errors": sorted(str(error) for error in errors),
                "normalized_chunk": parsed,
            }
        )
        if chunk_last_state_signature == chunk_state_signature:
            errors = [
                *errors,
                "STEP04_CANDIDATE_CHUNK_REPAIR_NONPROGRESS",
            ]
            break
        if chunk_repair_attempts >= repair_attempts:
            break
        chunk_repair_attempts += 1
        chunk_last_error_signature = chunk_error_signature
        chunk_last_state_signature = chunk_state_signature
        repair_iteration += 1
        chunk_history = {
            **(repair_identity or {}),
            "repair_unit": "candidate_plan_chunk",
            "repair_attempt": chunk_repair_attempts,
            "repair_attempt_limit": repair_attempts,
            "validation_errors": list(errors),
        }
        candidate_chunk_repair_history.append(chunk_history)
        discovery_scope_requires_fresh_regeneration = any(
            marker in str(error)
            for error in errors
            for marker in (
                "CANDIDATE_OPERATIONAL_SCOPE_INVALID",
                "SYSTEM_DIRECT_PRECIPITATION_COVERAGE_REQUIRED",
            )
        )
        regenerate_chunk_from_original = (
            semantic_unit_repair == "diagnostic_check"
            or chunk_repair_from_original_context
            or discovery_scope_requires_fresh_regeneration
        )
        continuation_spatial_gap_repair = _continuation_gap_requires_spatial_repair(errors)
        chunk_response_schema = (
            _compact_continuation_gap_repair_schema(schema)
            if continuation_spatial_gap_repair
            else schema
        )
        chunk_schema_name = (
            f"{schema_name}ContinuationSpatialEvolutionGapRepair"
            if continuation_spatial_gap_repair
            else schema_name
        )
        repair_prompt = {
            "task": (
                "이전 provider 응답은 사용하지 말고, 원래 후보 컨텍스트와 검증 오류를 기준으로 "
                "현재 candidate-plan chunk 전체를 일관되게 다시 생성하십시오."
                if regenerate_chunk_from_original
                else "이전 응답을 지원된 분석 내용만 유지하여 유효한 JSON으로 복구하십시오."
            ),
            "validation_errors": errors,
            **(
                {}
                if regenerate_chunk_from_original
                else {"previous_response": raw}
            ),
            "response_schema": schema,
            "rules": [
                "JSON만 반환하십시오.",
                "지원되지 않는 asset_id, product_id, forecast_hour, feature_id, candidate_id를 추가하지 마십시오.",
                *(
                    [
                        "이전 응답의 개별 필드를 patch하지 마십시오. 현재 chunk를 의미적으로 완결된 하나의 계획 단위로 다시 생성하십시오.",
                        "verification_request를 다시 만들 때는 signal_components, verification_mode, domain_scope, spatial_relation_scope, phase_keys, expected_signal, decision_relevance, purpose, priority, evidence_role, coverage_roles와 단면 관련 필드를 하나의 일관된 요청으로 함께 결정하십시오.",
                    ]
                    if regenerate_chunk_from_original
                    else ["유효한 관찰과 분석은 유지하고 형식 및 계약 오류만 수정하십시오."]
                ),
                "JSON 키·식별자·enum 값은 계약 범위 안에서 사용하고 사람이 읽는 설명 문자열은 모두 한국어로 작성하십시오.",
                *_repair_rules_for_validation_errors(errors),
            ],
        }
        if quantitative_request_split_required:
            # Supply bounded structural facts, never a previous provider JSON
            # response. The source-grounded chunk regeneration still owns all
            # meteorological intent and remains under the ordinary validator.
            split_targets = _compact_check_keys_from_validation_errors(
                errors, parsed_plan=parsed
            )
            failed_checks = {
                str(item.get("check_key") or ""): item
                for item in _compact_plan_rows(parsed, "diagnostic_checks")
            } if isinstance(parsed.get("diagnostic_checks"), list) else {}
            repair_prompt["quantitative_split_context"] = [
                {
                    "check_key": key,
                    "expected_signal": str(failed_checks[key].get("expected_signal") or ""),
                    "claims": [
                        {
                            "claim_statement": str(claim.get("claim_statement") or ""),
                            "expected_state": str(claim.get("expected_state") or ""),
                            "scope_quantifier": str(claim.get("scope_quantifier") or ""),
                            "has_quantitative_scope": claim.get("quantitative_scope") is not None,
                        }
                        for claim in failed_checks[key].get("atomic_claims", [])
                        if isinstance(claim, dict)
                    ][:4],
                }
                for key in split_targets
                if key in failed_checks
            ]
            logging.warning(
                "STEP04_QUANTITATIVE_ATOMICITY_SPLIT route=candidate_chunk "
                "check_keys=%s attempt=%s/%s",
                split_targets, chunk_repair_attempts, repair_attempts,
            )
        phase_reference_failure_context = _compact_phase_reference_failure_context(
            parsed,
            errors,
            previous_chunks=chunk_repair_previous_chunks,
        )
        if phase_reference_failure_context is not None:
            repair_prompt["phase_reference_failure_context"] = phase_reference_failure_context
            repair_prompt["rules"].append(
                "phase_reference_failure_context는 실패 원인을 설명하는 구조 요약일 뿐입니다. "
                "이전 응답을 patch하거나 그대로 복원하지 말고 원래 후보 컨텍스트에서 현재 chunk 전체를 다시 생성하십시오."
            )
        check_reference_failure_context = _compact_check_reference_failure_context(
            parsed,
            errors,
            previous_chunks=chunk_repair_previous_chunks,
        )
        if check_reference_failure_context is not None:
            repair_prompt["check_reference_failure_context"] = check_reference_failure_context
            repair_prompt["rules"].append(
                "check_reference_failure_context는 유효한 Check 참조 그래프를 알려주는 구조 요약입니다. "
                "이전 응답을 patch하지 말고 원래 후보 컨텍스트에서 현재 chunk 전체를 다시 생성하되, "
                "hypothesis가 이 구조에 존재하지 않는 이전 Check key를 추정해 참조하지 않도록 하십시오."
            )
        if continuation_spatial_gap_repair:
            existing_phases = _compact_existing_phase_context(chunk_repair_previous_chunks)
            repair_prompt["continuation_gap_contract"] = {
                "required_verification_mode": "spatial_evolution",
                "required_signal_component_count_per_request": 1,
                "required_minimum_distinct_forecast_hours": 2,
                "existing_phases": existing_phases,
                "allowed_spatial_states": [
                    "move", "persist", "split", "merge", "expand", "contract", "reorganize"
                ],
            }
            repair_prompt["rules"].extend(
                [
                    "이번 재생성은 temporal_evolution과 spatial_evolution의 미충족을 해소하기 위한 전용 복구입니다. verification_requests에는 verification_mode=spatial_evolution 요청을 최소 1개 반드시 포함하십시오.",
                    "각 spatial_evolution 요청은 signal_components를 정확히 1개만 사용하고, 해당 기상요소의 여러 시각 공간장을 실제로 비교해야 합니다.",
                    "phase_keys가 가리키는 기존/신규 phase의 forecast_hours 합집합에는 서로 다른 실제 예측시각이 최소 2개 있어야 합니다. continuation_gap_contract.existing_phases를 우선 재사용하십시오.",
                    "기존 phase만으로 두 시각 이상을 확보할 수 있으면 analysis_phases를 새로 만들지 마십시오. 부족할 때만 원래 입력의 available_forecast_hours 범위에서 필요한 phase를 추가하십시오.",
                    "atomic_claims를 최소 1개 작성하고 expected_state는 move/persist/split/merge/expand/contract/reorganize 중 실제 후보에 적합한 상태를 선택하십시오. coverage_roles 문자열만 추가해서는 안 됩니다.",
                    "spatial_evolution에서는 representative_spatial_hours=[]로 두십시오. 정량 강도 변화나 precipitation_amount를 이 복구 요청에 섞지 마십시오.",
                    "기상학적 결론은 강제로 긍정하지 마십시오. 이동이 없다면 persist, 구조 변화가 있다면 해당 spatial state를 선택하여 검증 가능한 가설로 작성하십시오.",
                ]
            )
        if source_retry_context is not None:
            repair_prompt["required_attachment_count"] = source_retry_context.get("required_attachment_count")
            repair_prompt["required_asset_ids"] = source_retry_context.get("required_asset_ids")
            repair_prompt["rules"].append(
                "원본 이미지 없이 누락된 asset 관찰을 추정하거나 만들어 내지 마십시오."
            )
        if continuation_spatial_gap_repair:
            logging.warning(
                "STEP04_CONTINUATION_GAP_REPAIR mode=spatial_evolution attempt=%s/%s existing_phase_count=%s",
                chunk_repair_attempts,
                repair_attempts,
                len(_compact_existing_phase_context(chunk_repair_previous_chunks)),
            )
        logging.warning(
            "STEP04_ATOMIC_REPAIR unit=candidate_chunk attempt=%s/%s errors=%s",
            chunk_repair_attempts,
            repair_attempts,
            errors[:10],
        )
        repair_messages = [
            {
                "role": "system",
                "content": (
                    "엄격한 기계 처리 파이프라인의 JSON을 재생성합니다. "
                    "부분 필드 패치를 하지 말고 요청된 의미 단위 전체를 다시 생성하여 JSON만 반환하십시오."
                ),
            },
        ]
        if repair_context_messages is not None:
            repair_messages.extend(repair_context_messages)
        repair_messages.append(
            {"role": "user", "content": json.dumps(repair_prompt, ensure_ascii=False)}
        )
        repair_result = client.complete(
            model=model,
            messages=repair_messages,
            response_schema=chunk_response_schema,
            schema_name=chunk_schema_name,
            structured_output_mode=structured_output_mode,
            enable_thinking=False,
            max_tokens=max_tokens,
            temperature=0.0,
            top_p=1.0,
        )
        result = repair_result
        repair_count += 1
        http_retry_count += int(getattr(repair_result, "http_retry_count", 0))
        usage_attempts.extend(
            getattr(repair_result, "usage_attempts", ())
            or (_compact_provider_envelope(repair_result.response_envelope).get("usage"),)
        )
        raw = repair_result.content
        envelope_summary = _compact_provider_envelope(repair_result.response_envelope)
        parsed, errors = parse_normalize_validate(raw, stage=f"semantic_unit_regeneration_{repair_iteration}")
        chunk_history["result"] = "PASS" if not errors else "FAIL"
        chunk_history["next_validation_errors"] = list(errors)

    if errors and source_retry_context is not None:
        for source_retry_index in range(source_retry_count, source_retry_attempts):
            retry_with_original_images(
                retry_index=source_retry_index + 1,
                stage_prefix="source_grounded_after_repair",
            )
            if not errors:
                break

    finish_reason = _compact_provider_envelope(result.response_envelope).get("finish_reason")
    if errors and compact_length_recovery is not None and finish_reason == "length":
        logging.warning(
            "길이 제한 JSON 복구 시도=1 max_tokens=%s 오류=%s",
            compact_length_recovery["max_tokens"],
            errors[:10],
        )
        compact_result = client.complete(
            model=model,
            messages=compact_length_recovery["messages"],
            response_schema=compact_length_recovery["schema"],
            schema_name=compact_length_recovery["schema_name"],
            structured_output_mode=structured_output_mode,
            enable_thinking=False,
            max_tokens=compact_length_recovery["max_tokens"],
            temperature=0.0,
            top_p=1.0,
        )
        result = compact_result
        compact_recovery_count = 1
        http_retry_count += int(getattr(compact_result, "http_retry_count", 0))
        usage_attempts.extend(
            getattr(compact_result, "usage_attempts", ())
            or (_compact_provider_envelope(compact_result.response_envelope).get("usage"),)
        )
        raw = compact_result.content
        envelope_summary = _compact_provider_envelope(compact_result.response_envelope)
        parsed, errors = parse_normalize_validate(raw, stage="compact_length_recovery_1")

    if errors:
        error = ResponseValidationError("JSON 검증 실패: " + " | ".join(errors[:30]))
        # Retain bounded repair evidence even when the candidate cannot be
        # checkpointed.  This makes a repeated validation failure auditable
        # without storing raw provider text or accepting an invalid plan.
        error.diagnostics = {
            "semantic_unit_repair": semantic_unit_repair,
            "repair_attempt_count": repair_count,
            "repair_attempts_by_check": check_repair_attempts,
            "check_last_error_signature": check_last_error_signature,
            "candidate_chunk_repair_attempt_count": chunk_repair_attempts,
            "chunk_last_state_signature": chunk_last_state_signature,
            "candidate_chunk_repair_history": candidate_chunk_repair_history,
            "candidate_chunk_last_error_signature": chunk_last_error_signature,
            "atomic_repair_history": atomic_repair_history,
            "final_validation_errors": list(errors),
        }
        raise error
    call_meta = {
        "structured_output_used": result.structured_output_used,
        "structured_output_fallback_count": int(
            getattr(result, "structured_output_fallback_count", 0)
        ),
        "provider_extensions_used": result.provider_extensions_used,
        "source_grounded_retry_attempt_count": source_retry_count,
        "compact_length_recovery_attempt_count": compact_recovery_count,
        "repair_attempt_count": repair_count,
        "repair_attempts_by_check": check_repair_attempts,
        "candidate_chunk_repair_attempt_count": chunk_repair_attempts,
        "candidate_chunk_repair_history": candidate_chunk_repair_history,
        "atomic_repair_history": atomic_repair_history,
        "http_retry_count": http_retry_count,
        "usage": _summarize_usage_attempts(usage_attempts),
        "fixed_field_mismatches": fixed_field_mismatches,
        "canonicalized_fields": [
            {
                "stage": item["stage"],
                "field": mismatch["field"],
                "provider_value": mismatch["provider_value"],
                "canonical_value": mismatch["canonical_value"],
            }
            for item in fixed_field_mismatches
            for mismatch in item["mismatches"]
        ],
    }
    return parsed, raw, envelope_summary, call_meta


def create_compact_candidate_plan(
    *,
    package_id: str,
    candidate: dict[str, Any],
    candidate_evidence: dict[str, Any],
    group_summaries: list[dict[str, Any]],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    feature_catalog: dict[str, Any],
    operational_region_catalog: dict[str, Any],
    related_precursor_opportunities: list[dict[str, Any]],
    canonical_schema: dict[str, Any],
    transport_schema: dict[str, Any],
    final_schema: dict[str, Any],
    prompt_template: str,
    prompt_sha256: str,
    client: Pass1ProviderClient,
    model: str,
    structured_output_mode: str,
    max_tokens: int,
    temperature: float,
    top_p: float,
    repair_attempts: int,
    continuation_attempts: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, int], dict[str, str]]:
    """Collect compact OpenAI chunks, then expand them without storing raw text."""
    candidate_id = str(candidate["candidate_id"])
    chunks: list[dict[str, Any]] = []
    call_metas: list[dict[str, Any]] = []
    payload_rows: list[dict[str, int]] = []
    provider_summary: dict[str, Any] = {}
    continuation_count = 0
    no_progress_count = 0
    while True:
        chunk_index = len(chunks) + 1
        continuation_context = (
            _compact_plan_context(chunks, candidate=candidate) if chunks else None
        )
        messages, input_value = build_candidate_plan_messages(
            prompt_template=prompt_template,
            package_id=package_id,
            candidate=candidate,
            candidate_evidence=candidate_evidence,
            group_summaries=group_summaries,
            attachment_manifest=attachment_manifest,
            support_catalog=support_catalog,
            feature_catalog=feature_catalog,
            operational_region_catalog=operational_region_catalog,
            related_precursor_opportunities=related_precursor_opportunities,
            compact_transport=True,
            plan_chunk_index=chunk_index,
            continuation_context=continuation_context,
        )
        payload_row = split_payload_diagnostics(
            model=model,
            messages=messages,
            input_value=input_value,
            response_schema=transport_schema,
            schema_name="AIFSPass1CandidatePlan",
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            enable_thinking=False,
        )
        payload_rows.append(payload_row)
        ensure_split_payload_budget(
            f"compact candidate plan {candidate_id} chunk={chunk_index}", payload_row
        )
        try:
            chunk, _raw, provider_summary, call_meta = call_and_validate_json(
                client=client,
                model=model,
                messages=messages,
                schema=transport_schema,
                schema_name="AIFSPass1CandidatePlan",
                structured_output_mode=structured_output_mode,
                enable_thinking=False,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                repair_attempts=repair_attempts,
                semantic_validator=lambda value, _chunk_index=chunk_index, _chunks=chunks: validate_compact_candidate_plan_response(
                    value,
                    schema=None,
                    candidate=candidate,
                    attachment_manifest=attachment_manifest,
                    support_catalog=support_catalog,
                    operational_region_catalog=operational_region_catalog,
                    expected_chunk_index=_chunk_index,
                    previous_chunks=_chunks,
                ),
                normalizer=lambda value, _chunks=chunks, _chunk_index=chunk_index: _normalize_ai_verification_chunk_for_validation(
                    value,
                    _chunks,
                    chunk_index=_chunk_index,
                    transport_schema=transport_schema,
                    candidate=candidate,
                    attachment_manifest=attachment_manifest,
                ),
                repair_context_messages=messages,
                semantic_unit_repair="diagnostic_check",
                repair_identity={"candidate_id": candidate_id, "stage": "step04"},
                nonrepairable_error_markers=(
                    "vertical_wind_shear is spatial-only",
                    "temporal diagnostic/method must declare check_scope=temporal_evolution",
                    "multivariable_composite must declare check_scope=multivariable_alignment",
                    "section diagnostic must declare check_scope=vertical_structure",
                    "multivariable_alignment requires multivariable_composite and at least two signal components",
                    "vertical_structure requires a section diagnostic",
                    "has invalid required_signal_components",
                    "CHECK_EXECUTION_COMPONENT_UNAVAILABLE",
                ),
                chunk_repair_from_original_context=True,
                chunk_repair_previous_chunks=list(chunks),
            )
        except ProviderIncompleteError as exc:
            diagnostics = dict(exc.diagnostics)
            diagnostics.update(
                {
                    "stage": "candidate_plan",
                    "candidate_id": candidate_id,
                    "prompt_sha256": prompt_sha256,
                    "transport_schema_sha256": canonical_hash(transport_schema),
                    "canonical_schema_sha256": canonical_hash(canonical_schema),
                    "input_sha256": canonical_hash(input_value),
                }
            )
            raise ProviderIncompleteError(str(exc), diagnostics=diagnostics) from exc
        previous_chunks = list(chunks)
        chunk = _normalize_compact_continuation_chunk(chunk, previous_chunks)
        chunks.append(chunk)
        call_metas.append(call_meta)
        (
            coverage,
            missing_roles,
            model_is_complete,
            model_remaining_roles,
        ) = normalize_compact_plan_completion_state(
            chunks,
            candidate=candidate,
        )
        if (
            model_is_complete != chunk["is_complete"]
            or model_remaining_roles != chunk["remaining_roles"]
        ):
            logging.info(
                "step04 candidate plan continuation state canonicalized "
                "candidate_id=%s model_is_complete=%s model_remaining_role_count=%s "
                "actual_remaining_role_count=%s",
                candidate_id,
                model_is_complete,
                len(model_remaining_roles),
                len(chunk["remaining_roles"]),
            )
        if not missing_roles:
            break
        if previous_chunks and not compact_candidate_plan_made_progress(
            previous_chunks,
            chunks,
            candidate=candidate,
        ):
            no_progress_count += 1
            if no_progress_count >= continuation_attempts:
                existing_contracts = [
                    {
                        "check_key": str(check.get("check_key") or ""),
                        "verification_mode": str(
                            check.get("verification_mode")
                            or _verification_mode_from_diagnostic_check(check)
                        ),
                        "coverage_roles": _effective_compact_check_coverage_roles(check),
                    }
                    for existing_chunk in chunks
                    for check in _compact_plan_rows(existing_chunk, "diagnostic_checks")
                ]
                raise ValueError(
                    "candidate plan continuation made no progress "
                    f"candidate_id={candidate_id} remaining_roles={missing_roles} "
                    f"existing_contracts={existing_contracts}"
                )
        else:
            no_progress_count = 0
        continuation_count += 1

    expanded = expand_compact_candidate_plan(
        chunks,
        package_id=package_id,
        candidate=candidate,
        attachment_manifest=attachment_manifest,
        support_catalog=support_catalog,
    )
    # Compact-plan coverage treats the compulsory direct-precipitation Check as
    # SYSTEM-owned, not AI-owned. Insert that Check before validating the
    # expanded canonical plan; otherwise the completeness gate can pass while
    # validate_final_response rejects the plan for the missing SYSTEM request.
    # The caller's later ensure call is idempotent and still post-validates.
    expanded, _ = ensure_system_direct_precipitation_anchor(
        expanded,
        candidate=candidate,
        support_catalog=support_catalog,
    )
    final_errors = validate_candidate_plan_response(
        expanded,
        schema=canonical_schema,
        final_schema=final_schema,
        package_id=package_id,
        candidate=candidate,
        candidate_evidence=candidate_evidence,
        attachment_manifest=attachment_manifest,
        support_catalog=support_catalog,
        feature_catalog=feature_catalog,
        operational_region_catalog=operational_region_catalog,
        require_system_direct_precipitation_anchor=True,
    )
    if final_errors:
        raise ValueError("compact candidate plan expansion failed: " + " | ".join(final_errors[:30]))
    usage_attempts = [
        attempt
        for call_meta in call_metas
        for attempt in call_meta.get("usage", {}).get("attempts", [])
    ]
    aggregate_meta = {
        "structured_output_used": True,
        "structured_output_fallback_count": sum(
            int(item.get("structured_output_fallback_count", 0)) for item in call_metas
        ),
        "provider_extensions_used": False,
        "source_grounded_retry_attempt_count": 0,
        "compact_length_recovery_attempt_count": 0,
        "repair_attempt_count": sum(
            int(item.get("repair_attempt_count", 0)) for item in call_metas
        ),
        "repair_attempts_by_check": {
            check_key: attempts
            for item in call_metas
            for check_key, attempts in item.get("repair_attempts_by_check", {}).items()
        },
        "candidate_chunk_repair_attempt_count": sum(
            int(item.get("candidate_chunk_repair_attempt_count", 0)) for item in call_metas
        ),
        "candidate_chunk_repair_history": [
            row
            for item in call_metas
            for row in item.get("candidate_chunk_repair_history", [])
        ],
        "atomic_repair_history": [
            row
            for item in call_metas
            for row in item.get("atomic_repair_history", [])
        ],
        "http_retry_count": sum(int(item.get("http_retry_count", 0)) for item in call_metas),
        "usage": _summarize_usage_attempts(usage_attempts),
        "fixed_field_mismatches": [],
        "canonicalized_fields": [],
        "compact_plan_chunk_count": len(chunks),
        "continuation_attempt_count": continuation_count,
        "coverage": coverage,
    }
    aggregate_payload = {
        "http_json_utf8_bytes": sum(item["http_json_utf8_bytes"] for item in payload_rows),
        "input_utf8_bytes": sum(item["input_utf8_bytes"] for item in payload_rows),
        "response_schema_utf8_bytes": max(
            (item["response_schema_utf8_bytes"] for item in payload_rows), default=0
        ),
        "chunk_count": len(payload_rows),
    }
    return expanded, provider_summary, aggregate_meta, aggregate_payload, coverage


def _single_attachment_batch(
    batch: dict[str, Any],
    attachment: dict[str, Any],
) -> dict[str, Any]:
    value = dict(batch)
    value["attachment_ids"] = [str(attachment["asset_id"])]
    value["attachment_count"] = 1
    value["source_panel_count"] = len(attachment.get("source_panels", []))
    return value


def _merge_single_image_observations(
    *,
    package_id: str,
    batch: dict[str, Any],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for observation in observations:
        items = observation.get("attachment_observations")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise ResponseValidationError(
                "single-image fallback did not return exactly one attachment observation"
            )
        rows.append(items[0])
    return {
        "package_id": package_id,
        "batch_id": batch["batch_id"],
        "analysis_tier": batch.get("analysis_tier"),
        "group_id": batch.get("group_id"),
        "observed_attachment_count": len(rows),
        "attachment_observations": rows,
        "cross_product_synthesis": [],
        "batch_uncertainties": [],
    }


def run_single_image_observation_fallback(
    *,
    client: Pass1ProviderClient,
    model: str,
    package_id: str,
    batch: dict[str, Any],
    attachments: list[dict[str, Any]],
    root: Path,
    prompt_template: str,
    canonical_schema: dict[str, Any],
    structured_output_mode: str,
    max_tokens: int,
    temperature: float,
    top_p: float,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Use bounded single-image calls only after a complete batch contract failure."""
    started = time.monotonic()
    single_observations: list[dict[str, Any]] = []
    call_metadata: list[dict[str, Any]] = []
    attachment_by_id = {str(item["asset_id"]): item for item in attachments}
    logging.warning(
        "single-image observation fallback start batch_id=%s source_asset_count=%s",
        batch["batch_id"],
        len(attachments),
    )
    for index, attachment in enumerate(attachments, start=1):
        single_batch = _single_attachment_batch(batch, attachment)
        messages = build_batch_messages(
            package_id=package_id,
            batch=single_batch,
            attachments=[attachment],
            root=root,
            prompt_template=prompt_template,
        )
        provider_schema = build_provider_observation_schema(
            canonical_schema,
            batch=single_batch,
            include_batch_synthesis=False,
        )
        logging.info(
            "single-image observation start batch_id=%s index=%s/%s",
            batch["batch_id"],
            index,
            len(attachments),
        )
        observation, _, _, call_meta = call_and_validate_json(
            client=client,
            model=model,
            messages=messages,
            schema=provider_schema,
            schema_name="AIFSPass1SingleImageObservation",
            structured_output_mode=structured_output_mode,
            enable_thinking=False,
            max_tokens=min(max_tokens, COMPACT_OBSERVATION_RECOVERY_MAX_TOKENS),
            temperature=temperature,
            top_p=top_p,
            repair_attempts=1,
            normalizer=lambda value, _batch=single_batch, _attachment=attachment: normalize_batch_observation_contract(
                value,
                package_id=package_id,
                batch=_batch,
                attachments_in_payload_order=[_attachment],
                include_batch_synthesis=False,
            ),
            semantic_validator=lambda value, _batch=single_batch: validate_batch_observation(
                value,
                schema=canonical_schema,
                package_id=package_id,
                batch=_batch,
                attachment_by_id=attachment_by_id,
            ),
            source_retry_attempts=1,
            source_retry_context={
                "required_attachment_count": 1,
                "required_asset_ids": [str(attachment["asset_id"])],
            },
            source_retry_schema=provider_schema,
            source_retry_schema_name="AIFSPass1SingleImageObservation",
            source_retry_before_repair_on_parse_error=True,
        )
        single_observations.append(observation)
        call_metadata.append(call_meta)
    merged = _merge_single_image_observations(
        package_id=package_id,
        batch=batch,
        observations=single_observations,
    )
    errors = validate_batch_observation(
        merged,
        schema=canonical_schema,
        package_id=package_id,
        batch=batch,
        attachment_by_id=attachment_by_id,
    )
    if errors:
        raise ResponseValidationError(
            "single-image fallback validation failed: " + " | ".join(errors[:30])
        )
    aggregated = {
        "structured_output_used": all(
            bool(item.get("structured_output_used")) for item in call_metadata
        ),
        "structured_output_fallback_count": sum(
            int(item.get("structured_output_fallback_count", 0)) for item in call_metadata
        ),
        "provider_extensions_used": [],
        "source_grounded_retry_attempt_count": sum(
            int(item.get("source_grounded_retry_attempt_count", 0)) for item in call_metadata
        ),
        "compact_length_recovery_attempt_count": sum(
            int(item.get("compact_length_recovery_attempt_count", 0)) for item in call_metadata
        ),
        "repair_attempt_count": sum(
            int(item.get("repair_attempt_count", 0)) for item in call_metadata
        ),
        "http_retry_count": sum(
            int(item.get("http_retry_count", 0)) for item in call_metadata
        ),
        "fixed_field_mismatches": [],
        "canonicalized_fields": [],
        "single_image_fallback_asset_count": len(attachments),
    }
    logging.info(
        "single-image observation fallback completed batch_id=%s elapsed=%.3fs",
        batch["batch_id"],
        time.monotonic() - started,
    )
    return merged, {}, aggregated


def _feature_ids(feature_catalog: dict[str, Any]) -> set[str]:
    return {
        str(item.get("feature_id"))
        for item in feature_catalog.get("features", [])
        if isinstance(item, dict) and item.get("feature_id")
    }


def _normalized_bbox_for_validation(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        bbox = {
            "west": float(value["west"]),
            "east": float(value["east"]),
            "south": float(value["south"]),
            "north": float(value["north"]),
        }
    except (KeyError, TypeError, ValueError):
        return None
    if bbox["west"] >= bbox["east"] or bbox["south"] >= bbox["north"]:
        return None
    return bbox


# Native AIFS lat/lon analysis grid used by STEP02/05 is on the quarter-degree
# coordinate lattice (0.00, 0.25, ...). Count actual *nominal grid centres*
# inside the requested box, not its geometric span. If a new source grid
# shifts its phase/resolution, these assumptions must be updated from the
# GRIB coordinate metadata; STEP05 remains authoritative for actual values.
AIFS_NATIVE_SPATIAL_INTERVAL_DEGREES = 0.25


def _native_axis_grid_count(lower: float, upper: float, *, step: float = AIFS_NATIVE_SPATIAL_INTERVAL_DEGREES) -> int:
    """Quarter-degree grid centres inside closed bbox bounds (origin at 0°)."""
    import math

    tolerance = 1.0e-9
    if not math.isfinite(lower) or not math.isfinite(upper) or lower > upper:
        return 0
    first = math.ceil((lower - tolerance) / step)
    last = math.floor((upper + tolerance) / step)
    return max(0, last - first + 1)
SPATIAL_STRUCTURE_VERIFICATION_MODES = frozenset({
    "multivariable_alignment", "spatial_evolution",
})


def _spatial_analysis_support_error(
    *, candidate_id: str, check_id: str, mode: str, scope: str,
    candidate_bbox: dict[str, float], context_bbox: dict[str, float] | None,
) -> str | None:
    if mode not in SPATIAL_STRUCTURE_VERIFICATION_MODES:
        return None
    if scope not in {"candidate", "context"}:
        return None
    domain = candidate_bbox if scope == "candidate" else context_bbox
    if domain is None:
        return None  # existing scope validation reports missing context
    lon_span = domain["east"] - domain["west"]
    lat_span = domain["north"] - domain["south"]
    step = AIFS_NATIVE_SPATIAL_INTERVAL_DEGREES
    lon_grid_count = _native_axis_grid_count(domain["west"], domain["east"])
    lat_grid_count = _native_axis_grid_count(domain["south"], domain["north"])
    if lon_grid_count >= 2 and lat_grid_count >= 2:
        return None
    context_sufficient = (
        context_bbox is not None
        and _native_axis_grid_count(context_bbox["west"], context_bbox["east"]) >= 2
        and _native_axis_grid_count(context_bbox["south"], context_bbox["north"]) >= 2
    )
    hint = (
        "Use domain_scope=context and the existing context_domain to render surrounding native cells; "
        "keep candidate_area unchanged and judge overlap only inside that candidate."
        if scope == "candidate" and context_sufficient
        else "Provide a genuine context_domain containing candidate_area with >=2 quarter-degree grid centres "
             "on each axis, then use domain_scope=context; do not alter candidate_area."
    )
    return (
        "SPATIAL_ANALYSIS_GRID_INSUFFICIENT "
        f"candidate_id={candidate_id} check_key={check_id} "
        f"verification_mode={mode} domain_scope={scope} "
        f"width_lon={lon_span:.3f} width_lat={lat_span:.3f} native_step={step:.2f} "
        f"grid_nlon={lon_grid_count} grid_nlat={lat_grid_count}; "
        "spatial structure cannot be established from a single-grid-axis map. "
        + hint
    )


def _expanded_plan_spatial_support_errors(
    plan: dict[str, Any], candidate: dict[str, Any],
) -> list[str]:
    candidate_bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
    if candidate_bbox is None:
        return []  # the canonical candidate validator owns invalid geometry
    target_index = {
        str(target.get("target_id")): target
        for target in plan.get("target_areas", [])
        if isinstance(target, dict)
    }
    context_bbox = next((
        _normalized_bbox_for_validation(target.get("requested_domain"))
        for target in target_index.values()
        if target.get("target_type") == "context_domain"
    ), None)
    errors: list[str] = []
    for check in plan.get("variable_checks", []):
        if not isinstance(check, dict):
            continue
        contract = check.get("intent_contract")
        if not isinstance(contract, dict):
            continue
        mode = str(contract.get("verification_mode") or "")
        scope = str(contract.get("domain_scope") or "")
        error = _spatial_analysis_support_error(
            candidate_id=str(candidate.get("candidate_id")),
            check_id=str(check.get("check_id")), mode=mode, scope=scope,
            candidate_bbox=candidate_bbox, context_bbox=context_bbox,
        )
        if error:
            errors.append(error)
    return errors


def _bbox_overlap_fraction_of_smaller(first: Any, second: Any) -> float:
    """Return intersection area divided by the smaller bbox area.

    The ratio is used only as a duplicate-candidate guard. A planar degree-area
    approximation is sufficient because both boxes are within the same compact
    regional domain and only a relative overlap threshold is applied.
    """
    left = _normalized_bbox_for_validation(first)
    right = _normalized_bbox_for_validation(second)
    if left is None or right is None:
        return 0.0
    width = max(0.0, min(left["east"], right["east"]) - max(left["west"], right["west"]))
    height = max(0.0, min(left["north"], right["north"]) - max(left["south"], right["south"]))
    intersection = width * height
    left_area = (left["east"] - left["west"]) * (left["north"] - left["south"])
    right_area = (right["east"] - right["west"]) * (right["north"] - right["south"])
    smaller = min(left_area, right_area)
    return intersection / smaller if smaller > 0.0 else 0.0


def _time_window_overlap_hours(first: Any, second: Any) -> int:
    if not isinstance(first, dict) or not isinstance(second, dict):
        return 0
    try:
        start = max(int(first["forecast_hour_start"]), int(second["forecast_hour_start"]))
        end = min(int(first["forecast_hour_end"]), int(second["forecast_hour_end"]))
    except (KeyError, TypeError, ValueError):
        return 0
    return max(0, end - start)


def _time_window_gap_hours(first: Any, second: Any) -> int | None:
    """Return the non-negative gap between two windows, or None for invalid input."""
    if not isinstance(first, dict) or not isinstance(second, dict):
        return None
    try:
        first_start = int(first["forecast_hour_start"])
        first_end = int(first["forecast_hour_end"])
        second_start = int(second["forecast_hour_start"])
        second_end = int(second["forecast_hour_end"])
    except (KeyError, TypeError, ValueError):
        return None
    if first_start > first_end or second_start > second_end:
        return None
    if first_end < second_start:
        return second_start - first_end
    if second_end < first_start:
        return first_start - second_end
    return 0


def _candidate_mechanism_similarity(first: dict[str, Any], second: dict[str, Any]) -> tuple[bool, list[str]]:
    first_categories = {
        str(value)
        for value in first.get("precursor_signal_categories", [])
        if str(value) != "direct_precipitation"
    }
    second_categories = {
        str(value)
        for value in second.get("precursor_signal_categories", [])
        if str(value) != "direct_precipitation"
    }
    common = sorted(first_categories & second_categories)
    same_basis = (
        str(first.get("detection_basis", ""))
        == str(second.get("detection_basis", ""))
        and bool(str(first.get("detection_basis", "")))
    )
    return same_basis or len(common) >= 2, common


def _candidate_peak_gap_hours(first: dict[str, Any], second: dict[str, Any]) -> int | None:
    first_peaks = sorted({int(value) for value in first.get("peak_forecast_hours", [])})
    second_peaks = sorted({int(value) for value in second.get("peak_forecast_hours", [])})
    if not first_peaks or not second_peaks:
        return None
    first_start = int(first.get("time_window", {}).get("forecast_hour_start", min(first_peaks)))
    second_start = int(second.get("time_window", {}).get("forecast_hour_start", min(second_peaks)))
    earlier, later = (first_peaks, second_peaks) if first_start <= second_start else (second_peaks, first_peaks)
    gap = min(later) - max(earlier)
    return max(0, gap)



def _region_bbox_for_peak_validation(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    aliases = {
        "west": ("west", "lon_min"),
        "east": ("east", "lon_max"),
        "south": ("south", "lat_min"),
        "north": ("north", "lat_max"),
    }
    normalized: dict[str, float] = {}
    try:
        for target, names in aliases.items():
            source = next(name for name in names if name in value)
            normalized[target] = float(value[source])
    except (StopIteration, TypeError, ValueError):
        return None
    return _normalized_bbox_for_validation(normalized)


def _expanded_bbox(value: dict[str, float], padding: float) -> dict[str, float]:
    return {
        "west": max(-180.0, float(value["west"]) - padding),
        "east": min(180.0, float(value["east"]) + padding),
        "south": max(-90.0, float(value["south"]) - padding),
        "north": min(90.0, float(value["north"]) + padding),
    }


def _point_in_validation_bbox(latitude: Any, longitude: Any, bbox: dict[str, float]) -> bool:
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return False
    return bbox["west"] <= lon <= bbox["east"] and bbox["south"] <= lat <= bbox["north"]


def _operational_precipitation_series(overview_data: dict[str, Any]) -> list[dict[str, Any]]:
    dataset = overview_data.get("operational_region_timeseries", {})
    rows = dataset.get("series", []) if isinstance(dataset, dict) else []
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and str(row.get("feature_id")) == "tp_6h_increment_mm"
        and bool(row.get("eligible_for_risk_rank"))
    ]


def _operational_region_bbox_index(overview_data: dict[str, Any]) -> dict[str, dict[str, float]]:
    catalog = overview_data.get("operational_region_catalog", {})
    rows = catalog.get("regions", []) if isinstance(catalog, dict) else []
    result: dict[str, dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        region_id = str(row.get("region_id", "")).strip()
        bbox = _region_bbox_for_peak_validation(row.get("bbox"))
        if region_id and bbox is not None:
            result[region_id] = bbox
    return result


def _precipitation_peak_score(maximum: Any, p95: Any, mean: Any) -> float:
    try:
        maximum_value = max(0.0, float(maximum))
    except (TypeError, ValueError):
        maximum_value = 0.0
    try:
        p95_value = max(0.0, float(p95))
    except (TypeError, ValueError):
        p95_value = 0.0
    try:
        mean_value = max(0.0, float(mean))
    except (TypeError, ValueError):
        mean_value = 0.0
    # p95 is weighted more heavily than a single-grid maximum so an isolated
    # numerical spike does not define the event anchor by itself.
    return 0.40 * maximum_value + 0.45 * p95_value + 0.15 * mean_value


def _local_peak_hours_from_timeline(timeline: list[dict[str, Any]]) -> list[int]:
    ordered = sorted(
        [row for row in timeline if isinstance(row, dict) and row.get("forecast_hour") is not None],
        key=lambda row: int(row["forecast_hour"]),
    )
    if not ordered:
        return []
    scores = [float(row.get("score", 0.0)) for row in ordered]
    hours = [int(row["forecast_hour"]) for row in ordered]
    peaks: list[int] = []
    for index, score in enumerate(scores):
        if score <= 0.0:
            continue
        previous = scores[index - 1] if index > 0 else None
        following = scores[index + 1] if index + 1 < len(scores) else None
        if previous is None and following is None:
            is_peak = True
        elif previous is None:
            is_peak = score >= float(following) * 1.10
        elif following is None:
            is_peak = score >= float(previous) * 1.10
        else:
            is_peak = (
                score >= float(previous) * 0.98
                and score >= float(following) * 0.98
                and score > min(float(previous), float(following)) * 1.02
            )
        if is_peak:
            peaks.append(hours[index])
    if not peaks:
        strongest_index = max(range(len(scores)), key=lambda idx: scores[idx])
        peaks.append(hours[strongest_index])
    return peaks


def _aggregate_precipitation_timeline(
    overview_data: dict[str, Any],
    *,
    candidate_bbox: dict[str, float] | None = None,
    overlap_group_id: str | None = None,
) -> list[dict[str, Any]]:
    series_rows = _operational_precipitation_series(overview_data)
    region_bbox_index = _operational_region_bbox_index(overview_data)
    expanded_candidate = _expanded_bbox(candidate_bbox, 0.50) if candidate_bbox is not None else None
    by_hour: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for series in series_rows:
        if overlap_group_id is not None and str(series.get("overlap_group_id")) != overlap_group_id:
            continue
        region_id = str(series.get("region_id", ""))
        region_bbox = region_bbox_index.get(region_id)
        if candidate_bbox is not None and region_bbox is not None:
            if _bbox_overlap_fraction_of_smaller(candidate_bbox, region_bbox) <= 0.0:
                continue
        hours = series.get("forecast_hours", [])
        maxima = series.get("max", [])
        p95_values = series.get("p95", [])
        means = series.get("mean", [])
        locations = series.get("max_location", [])
        for index, raw_hour in enumerate(hours):
            try:
                hour = int(raw_hour)
            except (TypeError, ValueError):
                continue
            maximum = maxima[index] if index < len(maxima) else 0.0
            p95 = p95_values[index] if index < len(p95_values) else 0.0
            mean = means[index] if index < len(means) else 0.0
            location = locations[index] if index < len(locations) and isinstance(locations[index], dict) else {}
            if expanded_candidate is not None:
                location_inside = _point_in_validation_bbox(
                    location.get("latitude"), location.get("longitude"), expanded_candidate
                )
                substantial_region_overlap = (
                    region_bbox is not None
                    and _bbox_overlap_fraction_of_smaller(candidate_bbox, region_bbox) >= 0.25
                )
                if not location_inside and not substantial_region_overlap:
                    continue
            score = _precipitation_peak_score(maximum, p95, mean)
            by_hour[hour].append(
                {
                    "forecast_hour": hour,
                    "score": score,
                    "max_mm": round(float(maximum or 0.0), 3),
                    "p95_mm": round(float(p95 or 0.0), 3),
                    "mean_mm": round(float(mean or 0.0), 3),
                    "region_id": region_id,
                    "region_name_ko": str(series.get("region_name_ko", "")),
                    "overlap_group_id": str(series.get("overlap_group_id", "")),
                    "max_location": {
                        "latitude": location.get("latitude"),
                        "longitude": location.get("longitude"),
                    },
                }
            )
    timeline: list[dict[str, Any]] = []
    for hour in sorted(by_hour):
        strongest = max(by_hour[hour], key=lambda row: float(row.get("score", 0.0)))
        timeline.append(dict(strongest))
    return timeline


def _peak_anchor_minimum_mm(support_catalog: dict[str, Any] | None) -> float:
    config = support_catalog.get("direct_precipitation_peak_anchor") if isinstance(support_catalog, dict) else None
    value = config.get("regional_maximum_6h_mm") if isinstance(config, dict) else 5.0
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        threshold = 5.0
    return max(threshold, 0.0)


def _system_direct_precipitation_signal_rows(
    overview_data: dict[str, Any],
    *,
    candidate_reference_threshold_mm: float,
) -> list[dict[str, Any]]:
    """Return deterministic >=1 mm operational-region precipitation signals.

    Candidate discovery remains an AI meteorological judgment.  This dataset is
    a SYSTEM-owned coverage ledger so an AI model cannot silently turn a
    quantitative direct-precipitation signal into ``absent``.  Signals below the
    candidate-reference threshold are recorded for audit but do not force a new
    candidate.
    """
    region_bbox_index = _operational_region_bbox_index(overview_data)
    signals: list[dict[str, Any]] = []
    for series in _operational_precipitation_series(overview_data):
        hours = series.get("forecast_hours", [])
        maxima = series.get("max", [])
        p95_values = series.get("p95", [])
        means = series.get("mean", [])
        locations = series.get("max_location", [])
        for index, raw_hour in enumerate(hours):
            try:
                hour = int(raw_hour)
                maximum = float(maxima[index]) if index < len(maxima) else 0.0
            except (TypeError, ValueError):
                continue
            if maximum < DIRECT_PRECIPITATION_MINIMUM_MM_6H:
                continue
            p95 = p95_values[index] if index < len(p95_values) else None
            mean = means[index] if index < len(means) else None
            location = (
                locations[index]
                if index < len(locations) and isinstance(locations[index], dict)
                else {}
            )
            region_id = str(series.get("region_id") or "")
            signals.append(
                {
                    "forecast_hour": hour,
                    "region_id": region_id,
                    "region_name_ko": str(series.get("region_name_ko") or ""),
                    "region_role": str(series.get("region_role") or ""),
                    "overlap_group_id": str(series.get("overlap_group_id") or ""),
                    "maximum_6h_mm": round(maximum, 6),
                    "p95_6h_mm": round(float(p95), 6) if isinstance(p95, (int, float)) and not isinstance(p95, bool) else None,
                    "mean_6h_mm": round(float(mean), 6) if isinstance(mean, (int, float)) and not isinstance(mean, bool) else None,
                    "max_location": {
                        "latitude": location.get("latitude"),
                        "longitude": location.get("longitude"),
                    },
                    "region_bbox": region_bbox_index.get(region_id),
                    "candidate_generation_required": maximum >= float(candidate_reference_threshold_mm),
                }
            )
    return sorted(
        signals,
        key=lambda row: (
            int(row["forecast_hour"]),
            -float(row["maximum_6h_mm"]),
            str(row["region_id"]),
        ),
    )


def build_system_direct_precipitation_coverage_guidance(
    overview_data: dict[str, Any],
    *,
    support_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build compact SYSTEM coverage guidance for candidate discovery."""
    reference_threshold = _peak_anchor_minimum_mm(support_catalog)
    signals = _system_direct_precipitation_signal_rows(
        overview_data,
        candidate_reference_threshold_mm=reference_threshold,
    )
    required = [row for row in signals if row["candidate_generation_required"]]
    by_hour: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in signals:
        by_hour[int(row["forecast_hour"])].append(row)
    hour_summary: list[dict[str, Any]] = []
    for hour in sorted(by_hour):
        rows = by_hour[hour]
        strongest = max(rows, key=lambda row: (float(row["maximum_6h_mm"]), str(row["region_id"])))
        hour_summary.append(
            {
                "forecast_hour": hour,
                "qualifying_region_count": len(rows),
                "strongest_region_id": strongest["region_id"],
                "strongest_region_name_ko": strongest["region_name_ko"],
                "strongest_maximum_6h_mm": strongest["maximum_6h_mm"],
                "candidate_generation_required": any(bool(row["candidate_generation_required"]) for row in rows),
            }
        )
    return {
        "contract_version": SYSTEM_DIRECT_PRECIPITATION_COVERAGE_AUDIT_VERSION,
        "source": "step02.operational_region_timeseries.tp_6h_increment_mm",
        "minimum_direct_signal_6h_mm": DIRECT_PRECIPITATION_MINIMUM_MM_6H,
        "candidate_reference_threshold_6h_mm": reference_threshold,
        "required_reference_signals": [
            {
                key: row[key]
                for key in (
                    "forecast_hour", "region_id", "region_name_ko",
                    "maximum_6h_mm", "p95_6h_mm", "mean_6h_mm", "max_location",
                )
            }
            for row in required
        ],
        "minimum_signal_hour_summary": hour_summary,
        "usage_rules": [
            "6시간 최대강수량 1.0 mm 이상 신호는 SYSTEM이 전수 감사하며 모델의 present/absent 판독과 독립적입니다.",
            f"지역 최대 6시간 강수량 {reference_threshold:g} mm 이상 신호가 어떤 후보에도 포함되지 않으면 SYSTEM 전용 coverage reconciliation에서 기존 후보 확장 또는 별도 후보 추가 여부를 재판정합니다.",
            f"1.0 mm 이상 {reference_threshold:g} mm 미만의 미커버 신호는 감사 기록에 남기되 그 값만으로 신규 후보 생성을 강제하지 않습니다.",
            "한 후보가 여러 정량 신호를 포괄할 수 있으며, 동일 기상계인지 여부와 후보 병합·분리는 AI가 영상·환경장과 함께 판단합니다.",
        ],
    }


def _precipitation_asset_ids_for_hour(
    attachment_manifest: dict[str, Any],
    forecast_hour: int,
) -> list[str]:
    result: list[str] = []
    for item in attachment_manifest.get("attachments", []):
        if not isinstance(item, dict) or str(item.get("product_id")) != "QL_PRECIP_6H":
            continue
        hours = {
            int(value)
            for value in item.get("forecast_hours", [])
            if isinstance(value, int) and not isinstance(value, bool)
        }
        asset_id = str(item.get("asset_id") or "")
        if asset_id and forecast_hour in hours:
            result.append(asset_id)
    return result


def _candidate_covers_system_direct_precipitation_signal(
    candidate: dict[str, Any],
    signal: dict[str, Any],
    *,
    attachment_manifest: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Return whether a discovery candidate deterministically covers one signal."""
    if str(candidate.get("detection_basis") or "") not in {"direct_precipitation", "mixed"}:
        return False, []
    if str(candidate.get("precipitation_signal_status") or "") != "present":
        return False, []

    hour = int(signal["forecast_hour"])
    window = candidate.get("time_window")
    if not isinstance(window, dict):
        return False, []
    try:
        start = int(window.get("forecast_hour_start"))
        end = int(window.get("forecast_hour_end"))
    except (TypeError, ValueError):
        return False, []
    if not (start <= hour <= end):
        return False, []

    candidate_bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
    if candidate_bbox is None:
        return False, []
    expanded = _expanded_bbox(candidate_bbox, 0.50)
    location = signal.get("max_location") if isinstance(signal.get("max_location"), dict) else {}
    location_inside = _point_in_validation_bbox(
        location.get("latitude"), location.get("longitude"), expanded
    )
    region_bbox = signal.get("region_bbox")
    substantial_overlap = bool(
        isinstance(region_bbox, dict)
        and _bbox_overlap_fraction_of_smaller(candidate_bbox, region_bbox) >= 0.25
    )
    if not location_inside and not substantial_overlap:
        return False, []

    allowed_assets = set(_precipitation_asset_ids_for_hour(attachment_manifest, hour))
    related_assets = {
        str(value) for value in candidate.get("related_overview_asset_ids", [])
    }
    matched_assets = sorted(allowed_assets & related_assets)
    return bool(matched_assets), matched_assets



def _system_direct_precipitation_candidate_diagnostic(
    candidate: dict[str, Any],
    signal: dict[str, Any],
    *,
    attachment_manifest: dict[str, Any],
    evidence_group: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact pass/fail reasons for one candidate against one SYSTEM signal."""
    hour = int(signal["forecast_hour"])
    reasons: list[str] = []
    if str(candidate.get("detection_basis") or "") not in {"direct_precipitation", "mixed"}:
        reasons.append("detection_basis")
    if str(candidate.get("precipitation_signal_status") or "") != "present":
        reasons.append("precipitation_status")

    window = candidate.get("time_window")
    try:
        start = int(window.get("forecast_hour_start")) if isinstance(window, dict) else -1
        end = int(window.get("forecast_hour_end")) if isinstance(window, dict) else -1
        if not (start <= hour <= end):
            reasons.append("time_window")
    except (TypeError, ValueError):
        reasons.append("time_window")

    bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
    location_inside = False
    region_overlap = 0.0
    if bbox is None:
        reasons.append("candidate_bbox")
    else:
        location = signal.get("max_location") if isinstance(signal.get("max_location"), dict) else {}
        location_inside = _point_in_validation_bbox(
            location.get("latitude"), location.get("longitude"), _expanded_bbox(bbox, 0.50)
        )
        region_bbox = signal.get("region_bbox")
        if isinstance(region_bbox, dict):
            region_overlap = _bbox_overlap_fraction_of_smaller(bbox, region_bbox)
        if not location_inside and region_overlap < 0.25:
            reasons.append("spatial_overlap")

    allowed_assets = set(_precipitation_asset_ids_for_hour(attachment_manifest, hour))
    related_assets = {str(value) for value in candidate.get("related_overview_asset_ids", [])}
    matched_assets = sorted(allowed_assets & related_assets)
    if not matched_assets:
        reasons.append("precipitation_asset")

    evidence_hours: set[int] = set()
    if isinstance(evidence_group, dict):
        for item in evidence_group.get("evidence_items", []):
            if isinstance(item, dict) and str(item.get("asset_id") or "") in matched_assets:
                evidence_hours.update(
                    int(value)
                    for value in item.get("forecast_hours", [])
                    if isinstance(value, int) and not isinstance(value, bool)
                )
    if hour not in evidence_hours:
        reasons.append("evidence_hour")

    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "failure_reasons": reasons,
        "matching_precipitation_asset_ids": matched_assets,
        "location_inside_expanded_bbox": location_inside,
        "region_bbox_overlap_fraction": round(region_overlap, 6),
        "coverage_ok": not reasons,
    }


def _bbox_contains_bbox(outer_value: Any, inner_value: Any) -> bool:
    outer = _normalized_bbox_for_validation(outer_value)
    inner = _normalized_bbox_for_validation(inner_value)
    if outer is None or inner is None:
        return False
    return (
        outer["west"] <= inner["west"]
        and outer["east"] >= inner["east"]
        and outer["south"] <= inner["south"]
        and outer["north"] >= inner["north"]
    )


def _time_window_contains_window(outer: Any, inner: Any) -> bool:
    if not isinstance(outer, dict) or not isinstance(inner, dict):
        return False
    try:
        return (
            int(outer["forecast_hour_start"]) <= int(inner["forecast_hour_start"])
            and int(outer["forecast_hour_end"]) >= int(inner["forecast_hour_end"])
        )
    except (KeyError, TypeError, ValueError):
        return False


def _canonicalize_system_direct_precipitation_evidence(
    response: dict[str, Any],
    *,
    overview_data: dict[str, Any],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any] | None = None,
) -> None:
    """Backfill a covered SYSTEM precipitation hour into existing evidence items.

    This never creates a candidate, changes a bbox/time window, or invents a new
    evidence asset.  It only repairs an omitted hour when a candidate already
    covers the quantitative signal and already cites the matching precipitation
    overview asset.
    """
    reference_threshold = _peak_anchor_minimum_mm(support_catalog)
    signals = _system_direct_precipitation_signal_rows(
        overview_data,
        candidate_reference_threshold_mm=reference_threshold,
    )
    candidates = [item for item in response.get("candidates", []) if isinstance(item, dict)]
    evidence_by_id = {
        str(group.get("candidate_id")): group
        for group in response.get("candidate_evidence", [])
        if isinstance(group, dict)
    }
    for signal in signals:
        hour = int(signal["forecast_hour"])
        for candidate in candidates:
            covered, matched_assets = _candidate_covers_system_direct_precipitation_signal(
                candidate, signal, attachment_manifest=attachment_manifest
            )
            if not covered:
                continue
            candidate_id = str(candidate.get("candidate_id") or "")
            group = evidence_by_id.get(candidate_id)
            if not isinstance(group, dict):
                continue
            for item in group.get("evidence_items", []):
                if not isinstance(item, dict) or str(item.get("asset_id") or "") not in matched_assets:
                    continue
                existing = {
                    int(value)
                    for value in item.get("forecast_hours", [])
                    if isinstance(value, int) and not isinstance(value, bool)
                }
                if hour in existing:
                    continue
                item["forecast_hours"] = sorted(existing | {hour})
                logging.info(
                    "step04 system precipitation coverage evidence canonicalized "
                    "candidate_id=%s region_id=%s forecast_hour=%s asset_id=%s",
                    candidate_id,
                    signal.get("region_id"),
                    hour,
                    item.get("asset_id"),
                )
                break


def build_system_direct_precipitation_coverage_audit(
    response: dict[str, Any],
    *,
    overview_data: dict[str, Any],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit candidate discovery against Step02 operational-region precipitation."""
    reference_threshold = _peak_anchor_minimum_mm(support_catalog)
    signals = _system_direct_precipitation_signal_rows(
        overview_data,
        candidate_reference_threshold_mm=reference_threshold,
    )
    candidates = [item for item in response.get("candidates", []) if isinstance(item, dict)]
    evidence_by_id = {
        str(group.get("candidate_id")): group
        for group in response.get("candidate_evidence", [])
        if isinstance(group, dict)
    }
    audited: list[dict[str, Any]] = []
    for signal in signals:
        covered_by: list[str] = []
        candidate_diagnostics: list[dict[str, Any]] = []
        for candidate in candidates:
            candidate_id = str(candidate.get("candidate_id") or "")
            diagnostic = _system_direct_precipitation_candidate_diagnostic(
                candidate,
                signal,
                attachment_manifest=attachment_manifest,
                evidence_group=evidence_by_id.get(candidate_id),
            )
            candidate_diagnostics.append(diagnostic)
            if diagnostic["coverage_ok"]:
                covered_by.append(candidate_id)
        row = {
            key: signal.get(key)
            for key in (
                "forecast_hour", "region_id", "region_name_ko", "region_role",
                "overlap_group_id", "maximum_6h_mm", "p95_6h_mm", "mean_6h_mm",
                "max_location", "candidate_generation_required",
            )
        }
        row["coverage_status"] = "covered" if covered_by else "uncovered"
        row["covered_candidate_ids"] = sorted(set(covered_by))
        row["coverage_reason"] = (
            "covered_by_candidate"
            if covered_by
            else (
                "candidate_reference_threshold_met"
                if signal["candidate_generation_required"]
                else "below_candidate_reference_threshold"
            )
        )
        if not covered_by and signal["candidate_generation_required"]:
            row["candidate_match_diagnostics"] = candidate_diagnostics
        audited.append(row)
    required_uncovered = [
        row for row in audited
        if row["coverage_status"] == "uncovered" and row["candidate_generation_required"]
    ]
    advisory_uncovered = [
        row for row in audited
        if row["coverage_status"] == "uncovered" and not row["candidate_generation_required"]
    ]
    return {
        "contract_version": SYSTEM_DIRECT_PRECIPITATION_COVERAGE_AUDIT_VERSION,
        "source": "step02.operational_region_timeseries.tp_6h_increment_mm",
        "minimum_direct_signal_6h_mm": DIRECT_PRECIPITATION_MINIMUM_MM_6H,
        "candidate_reference_threshold_6h_mm": reference_threshold,
        "signal_count": len(audited),
        "covered_signal_count": sum(row["coverage_status"] == "covered" for row in audited),
        "uncovered_signal_count": sum(row["coverage_status"] == "uncovered" for row in audited),
        "required_uncovered_count": len(required_uncovered),
        "advisory_uncovered_count": len(advisory_uncovered),
        "required_uncovered_forecast_hours": sorted({int(row["forecast_hour"]) for row in required_uncovered}),
        "advisory_uncovered_forecast_hours": sorted({int(row["forecast_hour"]) for row in advisory_uncovered}),
        "signals": audited,
    }


def _system_direct_precipitation_coverage_errors(audit: dict[str, Any]) -> list[str]:
    rows = [
        row for row in audit.get("signals", [])
        if isinstance(row, dict)
        and row.get("coverage_status") == "uncovered"
        and row.get("candidate_generation_required") is True
    ]
    by_hour: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_hour[int(row["forecast_hour"])].append(row)
    errors: list[str] = []
    threshold = float(audit.get("candidate_reference_threshold_6h_mm") or 0.0)
    for hour in sorted(by_hour):
        regions = ", ".join(
            f"{row.get('region_id')}({row.get('region_name_ko')}:{float(row.get('maximum_6h_mm') or 0.0):.2f}mm)"
            for row in sorted(by_hour[hour], key=lambda item: -float(item.get("maximum_6h_mm") or 0.0))
        )
        diagnostics = []
        for row in by_hour[hour]:
            failures = ";".join(
                f"{item.get('candidate_id') or '?'}:{','.join(item.get('failure_reasons', [])) or 'none'}"
                for item in row.get("candidate_match_diagnostics", [])[:5]
                if isinstance(item, dict)
            )
            if failures:
                diagnostics.append(f"{row.get('region_id')}=>{failures}")
        diagnostic_text = " | ".join(diagnostics)
        errors.append(
            "SYSTEM_DIRECT_PRECIPITATION_COVERAGE_REQUIRED "
            f"forecast_hour=F{hour:03d} uncovered_regions=[{regions}] "
            f"candidate_reference_threshold_mm={threshold:g}"
            + (f" candidate_failures=[{diagnostic_text}]" if diagnostic_text else "")
            + "; add a direct_precipitation/mixed candidate or extend an existing meteorologically identical candidate only when justified, "
            "and cite the matching QL_PRECIP_6H asset/hour in candidate evidence. "
            "Do not lower the threshold or delete the quantitative signal."
        )
    return errors


def _required_uncovered_system_direct_precipitation_signals(
    audit: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        row
        for row in audit.get("signals", [])
        if isinstance(row, dict)
        and row.get("coverage_status") == "uncovered"
        and row.get("candidate_generation_required") is True
    ]


def _required_precipitation_coverage_keys(audit: dict[str, Any]) -> set[tuple[int, str]]:
    """SYSTEM-owned keys; never use AI text as the coverage denominator."""
    return {
        (int(row["forecast_hour"]), str(row["region_id"]))
        for row in _required_uncovered_system_direct_precipitation_signals(audit)
    }


def _direct_precipitation_reconciliation_progress(
    before_audit: dict[str, Any],
    after_audit: dict[str, Any],
) -> dict[str, Any]:
    """Reject backward/no-op proposals before updating the working discovery."""
    before = _required_precipitation_coverage_keys(before_audit)
    after = _required_precipitation_coverage_keys(after_audit)
    # Coverage can only be satisfied by new links, never by deleting input signals.
    before_all = {
        (int(row["forecast_hour"]), str(row["region_id"]))
        for row in before_audit.get("signals", [])
        if isinstance(row, dict) and row.get("candidate_generation_required") is True
    }
    after_all = {
        (int(row["forecast_hour"]), str(row["region_id"]))
        for row in after_audit.get("signals", [])
        if isinstance(row, dict) and row.get("candidate_generation_required") is True
    }
    if before_all != after_all:
        raise ValueError("COVERAGE_RECONCILIATION_SIGNAL_LEDGER_CHANGED")
    regained = after - before
    improvement = before - after
    return {
        "before": len(before),
        "after": len(after),
        "newly_covered": [f"F{h:03d}:{r}" for h, r in sorted(improvement)],
        "regressed": [f"F{h:03d}:{r}" for h, r in sorted(regained)],
        "accept": bool(improvement) and not regained,
    }


def _is_sequence_subset(before: Any, after: Any) -> bool:
    if not isinstance(before, list) or not isinstance(after, list):
        return before == after
    remaining = list(after)
    for item in before:
        try:
            index = remaining.index(item)
        except ValueError:
            return False
        remaining.pop(index)
    return True


def _candidate_discovery_coverage_reconciliation_preservation_errors(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    required_uncovered_count: int,
) -> list[str]:
    """Reject reconciliation responses that rewrite unrelated discovery judgments."""
    errors: list[str] = []
    before_candidates = [item for item in before.get("candidates", []) if isinstance(item, dict)]
    after_candidates = [item for item in after.get("candidates", []) if isinstance(item, dict)]
    if len(after_candidates) < len(before_candidates):
        return ["COVERAGE_RECONCILIATION_PRESERVATION_INVALID existing candidates were deleted"]
    if len(after_candidates) - len(before_candidates) > max(1, required_uncovered_count):
        errors.append(
            "COVERAGE_RECONCILIATION_PRESERVATION_INVALID too many new candidates were added"
        )

    immutable_candidate_fields = (
        "detection_basis",
        "precipitation_signal_status",
        "precursor_signal_categories",
        "precursor_spatiotemporal_overlap",
        "analysis_envelope_request",
        "diagnostic_plan",
        "confidence",
        "separation_reason",
    )
    subset_candidate_fields = (
        "peak_forecast_hours",
        "signal_types",
        "related_product_ids",
        "related_overview_asset_ids",
        "supporting_reasons",
        "opposing_reasons",
        "uncertainties",
    )
    for index, original in enumerate(before_candidates):
        revised = after_candidates[index]
        candidate_id = str(original.get("candidate_id") or f"index={index}")
        if str(revised.get("candidate_id") or "") != str(original.get("candidate_id") or ""):
            errors.append(
                f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID candidate order/id changed candidate_id={candidate_id}"
            )
            continue
        for field in immutable_candidate_fields:
            if revised.get(field) != original.get(field):
                errors.append(
                    f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID candidate_id={candidate_id} field={field} changed"
                )
        original_hypothesis = original.get("hypothesis") if isinstance(original.get("hypothesis"), dict) else {}
        revised_hypothesis = revised.get("hypothesis") if isinstance(revised.get("hypothesis"), dict) else {}
        if revised_hypothesis.get("spatiotemporal_overlap") != original_hypothesis.get("spatiotemporal_overlap"):
            errors.append(
                f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID candidate_id={candidate_id} field=hypothesis.spatiotemporal_overlap changed"
            )
        if not _bbox_contains_bbox(revised.get("candidate_area"), original.get("candidate_area")):
            errors.append(
                f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID candidate_id={candidate_id} candidate_area shrank or moved"
            )
        if not _time_window_contains_window(revised.get("time_window"), original.get("time_window")):
            errors.append(
                f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID candidate_id={candidate_id} time_window shrank or moved"
            )
        for field in subset_candidate_fields:
            if not _is_sequence_subset(original.get(field, []), revised.get(field, [])):
                errors.append(
                    f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID candidate_id={candidate_id} field={field} lost existing values"
                )

    for candidate in after_candidates[len(before_candidates):]:
        if str(candidate.get("detection_basis") or "") not in {"direct_precipitation", "mixed"}:
            errors.append(
                "COVERAGE_RECONCILIATION_PRESERVATION_INVALID new candidate must be direct_precipitation or mixed"
            )
        if str(candidate.get("precipitation_signal_status") or "") != "present":
            errors.append(
                "COVERAGE_RECONCILIATION_PRESERVATION_INVALID new candidate precipitation_signal_status must be present"
            )

    before_evidence = {
        str(item.get("candidate_id") or ""): item
        for item in before.get("candidate_evidence", [])
        if isinstance(item, dict)
    }
    after_evidence = {
        str(item.get("candidate_id") or ""): item
        for item in after.get("candidate_evidence", [])
        if isinstance(item, dict)
    }
    for candidate_id, original_group in before_evidence.items():
        revised_group = after_evidence.get(candidate_id)
        if not isinstance(revised_group, dict):
            errors.append(
                f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID candidate_id={candidate_id} evidence group missing"
            )
            continue
        revised_items = {
            str(item.get("asset_id") or ""): item
            for item in revised_group.get("evidence_items", [])
            if isinstance(item, dict)
        }
        for original_item in original_group.get("evidence_items", []):
            if not isinstance(original_item, dict):
                continue
            asset_id = str(original_item.get("asset_id") or "")
            revised_item = revised_items.get(asset_id)
            if not isinstance(revised_item, dict):
                errors.append(
                    f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID candidate_id={candidate_id} asset_id={asset_id} evidence deleted"
                )
                continue
            if not set(original_item.get("forecast_hours", [])).issubset(
                set(revised_item.get("forecast_hours", []))
            ):
                errors.append(
                    f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID candidate_id={candidate_id} asset_id={asset_id} evidence hours deleted"
                )

    if before.get("precursor_opportunities") != after.get("precursor_opportunities"):
        errors.append("COVERAGE_RECONCILIATION_PRESERVATION_INVALID precursor_opportunities changed")
    before_assessment = before.get("precursor_candidate_assessment")
    after_assessment = after.get("precursor_candidate_assessment")
    if isinstance(before_assessment, dict) and isinstance(after_assessment, dict):
        for field in (
            "assessment_status",
            "qualified_opportunity_count",
            "source_asset_ids",
            "forecast_hours",
            "rationale",
        ):
            if before_assessment.get(field) != after_assessment.get(field):
                errors.append(
                    f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID precursor_candidate_assessment.{field} changed"
                )
    if not _is_sequence_subset(
        before.get("global_uncertainties", []), after.get("global_uncertainties", [])
    ):
        errors.append("COVERAGE_RECONCILIATION_PRESERVATION_INVALID global_uncertainties were deleted")
    return errors



CANDIDATE_DISCOVERY_INITIAL_EVIDENCE_LIMIT = 6
CANDIDATE_DISCOVERY_INITIAL_ASSET_LIMIT = 8
CANDIDATE_DISCOVERY_RECONCILIATION_ASSET_LIMIT = 16
CANDIDATE_DISCOVERY_RECONCILIATION_EVIDENCE_LIMIT = 12


def _candidate_discovery_initial_compact_limit_errors(value: dict[str, Any]) -> list[str]:
    """Retain 6/8 initial discovery limits even with repair headroom in the schema."""
    errors: list[str] = []
    for index, candidate in enumerate(value.get("candidates", [])):
        if isinstance(candidate, dict) and len(candidate.get("related_overview_asset_ids", [])) > CANDIDATE_DISCOVERY_INITIAL_ASSET_LIMIT:
            errors.append(
                f"INITIAL_DISCOVERY_ASSET_LIMIT candidate_index={index} "
                f"max={CANDIDATE_DISCOVERY_INITIAL_ASSET_LIMIT}"
            )
    for index, evidence in enumerate(value.get("candidate_evidence", [])):
        if isinstance(evidence, dict) and len(evidence.get("evidence_items", [])) > CANDIDATE_DISCOVERY_INITIAL_EVIDENCE_LIMIT:
            errors.append(
                f"INITIAL_DISCOVERY_EVIDENCE_LIMIT candidate_index={index} "
                f"max={CANDIDATE_DISCOVERY_INITIAL_EVIDENCE_LIMIT}"
            )
    return errors


def _coverage_reconciliation_capacity(
    response: dict[str, Any], *, asset_limit: int = CANDIDATE_DISCOVERY_RECONCILIATION_ASSET_LIMIT,
    evidence_limit: int = CANDIDATE_DISCOVERY_RECONCILIATION_EVIDENCE_LIMIT,
) -> list[dict[str, Any]]:
    """Expose occupied evidence slots; never infer storm identity from capacity."""
    by_id = {
        str(group.get("candidate_id")): group
        for group in response.get("candidate_evidence", [])
        if isinstance(group, dict)
    }
    rows: list[dict[str, Any]] = []
    for candidate in response.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        related = list(candidate.get("related_overview_asset_ids", []))
        existing_items = list(by_id.get(candidate_id, {}).get("evidence_items", []))
        rows.append({
            "candidate_id": candidate_id,
            "related_asset_count": len(related),
            "related_asset_limit": asset_limit,
            "available_related_asset_slots": max(0, asset_limit - len(related)),
            "evidence_item_count": len(existing_items),
            "evidence_item_limit": evidence_limit,
            "available_evidence_slots": max(0, evidence_limit - len(existing_items)),
            "precipitation_evidence_assets": [
                str(item.get("asset_id")) for item in existing_items
                if isinstance(item, dict) and str(item.get("product_id")) == "QL_PRECIP_6H"
            ],
        })
    return rows


def _canonicalize_candidate_discovery_coverage_reconciliation_preservation(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    attachment_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply coverage reconciliation as an allowlisted patch over the original response.

    The provider still returns the full candidate-discovery object because it uses the
    discovery response schema.  Reconciliation, however, is *not* allowed to regenerate
    existing meteorological judgments.  Rebuild every existing candidate from the
    pre-reconciliation object and accept only fields needed to close direct-precipitation
    coverage gaps:

    - candidate_area: expansion only
    - time_window: expansion only
    - related_product_ids / related_overview_asset_ids: additive only
    - candidate_evidence: additive assets/hours only
    - candidate_name / hypothesis.statement: optional wording update only when the
      candidate area or time window was actually expanded

    All other existing-candidate fields (including separation_reason, uncertainties,
    confidence, diagnostic_plan, precursor judgments, etc.) are preserved exactly.
    New candidates remain provider-authored and are validated separately.
    """
    if not isinstance(before, dict) or not isinstance(after, dict):
        return after

    result = copy.deepcopy(after)
    attachment_by_id = _attachment_by_id(attachment_manifest) if attachment_manifest is not None else None
    before_candidates = [item for item in before.get("candidates", []) if isinstance(item, dict)]
    provider_candidates = [item for item in result.get("candidates", []) if isinstance(item, dict)]
    before_ids = [str(item.get("candidate_id") or "") for item in before_candidates]
    provider_by_id = {
        str(item.get("candidate_id") or ""): item
        for item in provider_candidates
        if str(item.get("candidate_id") or "")
    }

    def additive_sequence(original: Any, revised: Any) -> list[Any]:
        base = list(original) if isinstance(original, list) else []
        additions = list(revised) if isinstance(revised, list) else []
        merged = copy.deepcopy(base)
        for item in additions:
            if item not in merged:
                merged.append(copy.deepcopy(item))
        return merged

    canonical_candidates: list[dict[str, Any]] = []
    restored: list[str] = []
    coverage_changes: list[str] = []

    for index, original in enumerate(before_candidates):
        candidate_id = str(original.get("candidate_id") or f"index={index}")
        revised = provider_by_id.get(str(original.get("candidate_id") or ""))
        if not isinstance(revised, dict):
            canonical_candidates.append(copy.deepcopy(original))
            restored.append(f"{candidate_id}.candidate")
            continue

        # Start from the original object so any newly introduced non-coverage field
        # is ignored by default rather than requiring one-off preservation rules.
        merged = copy.deepcopy(original)
        area_expanded = False
        time_expanded = False

        revised_area = revised.get("candidate_area")
        if _bbox_contains_bbox(revised_area, original.get("candidate_area")):
            if revised_area != original.get("candidate_area"):
                merged["candidate_area"] = copy.deepcopy(revised_area)
                area_expanded = True
                coverage_changes.append(f"{candidate_id}.candidate_area")
        elif revised_area != original.get("candidate_area"):
            restored.append(f"{candidate_id}.candidate_area")

        revised_window = revised.get("time_window")
        if _time_window_contains_window(revised_window, original.get("time_window")):
            if revised_window != original.get("time_window"):
                merged["time_window"] = copy.deepcopy(revised_window)
                time_expanded = True
                coverage_changes.append(f"{candidate_id}.time_window")
        elif revised_window != original.get("time_window"):
            restored.append(f"{candidate_id}.time_window")

        for field in ("related_product_ids", "related_overview_asset_ids"):
            merged_values = additive_sequence(original.get(field, []), revised.get(field, []))
            added_values = [item for item in merged_values if item not in original.get(field, [])]
            if field == "related_product_ids" and any(
                str(item) != "QL_PRECIP_6H" for item in added_values
            ):
                raise ValueError(
                    "COVERAGE_RECONCILIATION_NON_PRECIPITATION_PRODUCT "
                    f"candidate_id={candidate_id} additions={added_values}"
                )
            if field == "related_overview_asset_ids" and attachment_by_id is not None:
                invalid_assets = [
                    str(asset_id) for asset_id in added_values
                    if attachment_by_id.get(str(asset_id), {}).get("product_id") != "QL_PRECIP_6H"
                ]
                if invalid_assets:
                    raise ValueError(
                        "COVERAGE_RECONCILIATION_NON_PRECIPITATION_ASSET "
                        f"candidate_id={candidate_id} additions={invalid_assets}"
                    )
            if merged_values != original.get(field, []):
                coverage_changes.append(f"{candidate_id}.{field}")
            if field == "related_overview_asset_ids" and len(merged_values) > CANDIDATE_DISCOVERY_RECONCILIATION_ASSET_LIMIT:
                raise ValueError(
                    "COVERAGE_RECONCILIATION_ASSET_CAPACITY_EXHAUSTED "
                    f"candidate_id={candidate_id} count={len(merged_values)} "
                    f"limit={CANDIDATE_DISCOVERY_RECONCILIATION_ASSET_LIMIT}; "
                    "do not discard previous links or merge independent precipitation objects"
                )
            merged[field] = merged_values

        # Wording may be updated only to describe a genuinely widened coverage scope.
        if area_expanded or time_expanded:
            revised_name = str(revised.get("candidate_name") or "").strip()
            if revised_name:
                merged["candidate_name"] = revised_name
            original_hypothesis = (
                original.get("hypothesis") if isinstance(original.get("hypothesis"), dict) else {}
            )
            revised_hypothesis = (
                revised.get("hypothesis") if isinstance(revised.get("hypothesis"), dict) else {}
            )
            merged_hypothesis = copy.deepcopy(original_hypothesis)
            revised_statement = str(revised_hypothesis.get("statement") or "").strip()
            if revised_statement:
                merged_hypothesis["statement"] = revised_statement
            merged["hypothesis"] = merged_hypothesis

        # Explicitly log any provider-authored changes that were discarded.  This is
        # diagnostic only; the allowlist above already guarantees preservation.
        for field in original.keys():
            if field in {
                "candidate_area",
                "time_window",
                "related_product_ids",
                "related_overview_asset_ids",
                "candidate_name",
                "hypothesis",
            }:
                continue
            if revised.get(field) != original.get(field):
                restored.append(f"{candidate_id}.{field}")

        canonical_candidates.append(merged)

    # Keep provider-authored new candidates in their returned order.  The preservation
    # validator restricts these to direct_precipitation/mixed + precipitation present.
    for revised in provider_candidates:
        candidate_id = str(revised.get("candidate_id") or "")
        if candidate_id not in before_ids:
            canonical_candidates.append(copy.deepcopy(revised))
            coverage_changes.append(f"{candidate_id or '<new>'}.new_candidate")
    result["candidates"] = canonical_candidates
    run_summary = result.get("run_summary")
    if isinstance(run_summary, dict):
        run_summary["candidate_count"] = len(canonical_candidates)
    result["pass2_required"] = bool(canonical_candidates)

    # Candidate evidence is also patch-only.  Existing evidence items are immutable
    # except that forecast hours may be added.  New precipitation evidence assets may
    # be appended.  Provider rewrites of product_id/reason are discarded.
    before_evidence = {
        str(item.get("candidate_id") or ""): item
        for item in before.get("candidate_evidence", [])
        if isinstance(item, dict)
    }
    provider_evidence = {
        str(item.get("candidate_id") or ""): item
        for item in result.get("candidate_evidence", [])
        if isinstance(item, dict)
    }
    canonical_evidence: list[dict[str, Any]] = []
    for original_candidate in before_candidates:
        candidate_id = str(original_candidate.get("candidate_id") or "")
        original_group = before_evidence.get(candidate_id)
        revised_group = provider_evidence.get(candidate_id)
        if not isinstance(original_group, dict):
            if isinstance(revised_group, dict):
                canonical_evidence.append(copy.deepcopy(revised_group))
            continue
        merged_group = copy.deepcopy(original_group)
        original_items = [
            item for item in original_group.get("evidence_items", []) if isinstance(item, dict)
        ]
        revised_items = [
            item for item in (revised_group or {}).get("evidence_items", []) if isinstance(item, dict)
        ] if isinstance(revised_group, dict) else []
        revised_by_asset = {
            str(item.get("asset_id") or ""): item
            for item in revised_items
            if str(item.get("asset_id") or "")
        }
        merged_items: list[dict[str, Any]] = []
        original_asset_ids: set[str] = set()
        for original_item in original_items:
            asset_id = str(original_item.get("asset_id") or "")
            original_asset_ids.add(asset_id)
            merged_item = copy.deepcopy(original_item)
            revised_item = revised_by_asset.get(asset_id)
            if isinstance(revised_item, dict):
                merged_hours = additive_sequence(
                    original_item.get("forecast_hours", []),
                    revised_item.get("forecast_hours", []),
                )
                if merged_hours != original_item.get("forecast_hours", []):
                    coverage_changes.append(
                        f"{candidate_id}.candidate_evidence[{asset_id}].forecast_hours"
                    )
                merged_item["forecast_hours"] = merged_hours
                if revised_item.get("product_id") != original_item.get("product_id"):
                    restored.append(
                        f"{candidate_id}.candidate_evidence[{asset_id}].product_id"
                    )
                if revised_item.get("reason") != original_item.get("reason"):
                    restored.append(f"{candidate_id}.candidate_evidence[{asset_id}].reason")
            merged_items.append(merged_item)
        for revised_item in revised_items:
            asset_id = str(revised_item.get("asset_id") or "")
            if asset_id and asset_id not in original_asset_ids:
                if attachment_by_id is not None and (
                    attachment_by_id.get(asset_id, {}).get("product_id") != "QL_PRECIP_6H"
                ):
                    raise ValueError(
                        "COVERAGE_RECONCILIATION_NON_PRECIPITATION_EVIDENCE "
                        f"candidate_id={candidate_id} asset_id={asset_id}"
                    )
                merged_items.append(copy.deepcopy(revised_item))
                coverage_changes.append(f"{candidate_id}.candidate_evidence[{asset_id}]")
        if len(merged_items) > CANDIDATE_DISCOVERY_RECONCILIATION_EVIDENCE_LIMIT:
            raise ValueError(
                "COVERAGE_RECONCILIATION_EVIDENCE_CAPACITY_EXHAUSTED "
                f"candidate_id={candidate_id} count={len(merged_items)} "
                f"limit={CANDIDATE_DISCOVERY_RECONCILIATION_EVIDENCE_LIMIT}; "
                "do not discard previous evidence or merge independent precipitation objects"
            )
        merged_group["evidence_items"] = merged_items
        canonical_evidence.append(merged_group)

    # Evidence for genuinely new candidates is provider-authored.
    for candidate in canonical_candidates[len(before_candidates):]:
        candidate_id = str(candidate.get("candidate_id") or "")
        revised_group = provider_evidence.get(candidate_id)
        if isinstance(revised_group, dict):
            canonical_evidence.append(copy.deepcopy(revised_group))
    result["candidate_evidence"] = canonical_evidence

    # These judgments are fully outside direct-precipitation coverage reconciliation.
    for field in ("precursor_opportunities", "global_uncertainties"):
        if before.get(field) != result.get(field):
            if field in before:
                result[field] = copy.deepcopy(before[field])
            else:
                result.pop(field, None)
            restored.append(field)

    before_assessment = before.get("precursor_candidate_assessment")
    after_assessment = result.get("precursor_candidate_assessment")
    if isinstance(before_assessment, dict) and isinstance(after_assessment, dict):
        # dismissed_precursor_groups is derived from the final candidate set and may
        # legitimately change when a new direct/mixed candidate is added.
        dismissed = copy.deepcopy(after_assessment.get("dismissed_precursor_groups"))
        result["precursor_candidate_assessment"] = copy.deepcopy(before_assessment)
        if dismissed is not None:
            result["precursor_candidate_assessment"]["dismissed_precursor_groups"] = dismissed
        if before_assessment != after_assessment:
            restored.append("precursor_candidate_assessment")
    elif before_assessment != after_assessment:
        if "precursor_candidate_assessment" in before:
            result["precursor_candidate_assessment"] = copy.deepcopy(before_assessment)
        else:
            result.pop("precursor_candidate_assessment", None)
        restored.append("precursor_candidate_assessment")

    if restored:
        logging.info(
            "step04 coverage reconciliation restored/ignored non-target fields count=%s fields=%s",
            len(restored),
            ",".join(restored[:40]),
        )
    if coverage_changes:
        logging.info(
            "step04 coverage reconciliation accepted coverage patches count=%s fields=%s",
            len(coverage_changes),
            ",".join(coverage_changes[:40]),
        )
    return result


def build_system_direct_precipitation_coverage_reconciliation_messages(
    *,
    base_messages: list[dict[str, Any]],
    current_response: dict[str, Any],
    audit: dict[str, Any],
    attachment_manifest: dict[str, Any],
    worklist_offset: int = 0,
    worklist_limit: int = 4,
) -> list[dict[str, Any]]:
    all_missing = _required_uncovered_system_direct_precipitation_signals(audit)
    # Small work batches avoid enormous candidate-by-candidate error prose.
    # Sort by intensity first; grouping is a repair worklist, not storm identity.
    prioritized = sorted(
        all_missing,
        key=lambda row: (-float(row.get("maximum_6h_mm") or 0), int(row["forecast_hour"]), str(row["region_id"])),
    )
    # Rejected proposals should not repeatedly receive the same oversized worklist.
    # Rotate among unresolved signals; preserve the original SYSTEM signal ledger.
    limit = max(1, min(4, int(worklist_limit)))
    if prioritized:
        offset = int(worklist_offset) % len(prioritized)
        selected = (prioritized[offset:] + prioritized[:offset])[:limit]
    else:
        selected = []
    missing = []
    for row in selected:
        item = {key: row.get(key) for key in (
            "forecast_hour", "region_id", "region_name_ko", "maximum_6h_mm", "max_location",
        )}
        # The validator knows whether an existing candidate failed due to time,
        # space, asset, or evidence. Supply that distinction to the repair model.
        # This is diagnostic metadata, not a change in the acceptance contract.
        candidate_diagnostics = [
            {
                "candidate_id": diag.get("candidate_id"),
                "failure_reasons": diag.get("failure_reasons", []),
                "matching_precipitation_asset_ids": diag.get("matching_precipitation_asset_ids", []),
            }
            for diag in row.get("candidate_match_diagnostics", [])
            if isinstance(diag, dict)
        ]
        item["candidate_diagnostics"] = sorted(
            candidate_diagnostics,
            key=lambda diag: (len(diag["failure_reasons"]), str(diag["candidate_id"])),
        )[:4]
        missing.append(item)
    hours = sorted({int(row["forecast_hour"]) for row in missing})
    precipitation_assets_by_hour = {
        f"F{hour:03d}": _precipitation_asset_ids_for_hour(attachment_manifest, hour)
        for hour in hours
    }
    payload = {
        "task": (
            "현재 candidate discovery의 SYSTEM 직접강수 coverage 누락만 보완하십시오. "
            "후보 탐색을 처음부터 다시 하지 마십시오."
        ),
        "contract_version": DIRECT_PRECIPITATION_COVERAGE_RECONCILIATION_VERSION,
        "current_candidate_discovery": current_response,
        "required_uncovered_signals": missing,
        "total_required_uncovered_signal_count": len(all_missing),
        "worklist_size": len(missing),
        "matching_precipitation_assets_by_hour": precipitation_assets_by_hour,
        "candidate_capacity": _coverage_reconciliation_capacity(current_response),
        "rules": [
            "기존 후보와 candidate_evidence는 삭제하거나 순서를 바꾸지 마십시오.",
            "누락 신호가 기존 후보와 동일한 기상계라는 근거가 있을 때만 해당 후보의 bbox·time_window·관련 강수 asset/evidence를 바깥쪽으로 확장하십시오.",
            "기존 후보의 detection_basis, precipitation_signal_status, precursor 판단, confidence, diagnostic_plan, separation_reason, 기존 근거·제한·불확실성은 변경하거나 삭제하지 마십시오.",
            "기존 후보에서는 candidate_area, time_window, 관련 강수 asset/evidence, 그리고 확장 범위를 설명하기 위한 candidate_name·hypothesis.statement 외의 필드를 수정하지 마십시오.",
            "서로 떨어진 강수군을 coverage만 맞추기 위해 하나의 큰 bbox로 합치지 마십시오. 별도 기상계이면 direct_precipitation 또는 mixed 후보를 새로 추가하십시오.",
            "각 required_uncovered_signal은 해당 forecast_hour의 QL_PRECIP_6H asset을 related_overview_asset_ids와 candidate_evidence에 포함해야 합니다.",
            "각 후보의 candidate_capacity를 확인하십시오. 기존 QL_PRECIP_6H evidence_item이 같은 forecast_hour를 포함할 수 있으면 기존 항목의 forecast_hours만 확장하십시오. 기존 이미지·근거를 중복으로 추가하지 마십시오.",
            "초기 후보당 영상 8개·근거 6개 제한은 유지됩니다. 이 보완 단계에서만 기존 항목 보존을 위해 영상 최대 16개·근거 최대 12개까지 허용됩니다. 신규 precipitation asset이 실제로 필요할 때만 추가하십시오.",
            "보완 후에도 슬롯이 부족하면 기존 후보의 자료를 잘라내지 마십시오. 독립 기상계로 확인되는 경우에만 별도 후보를 검토하고, 독립성이 확인되지 않으면 안전한 미보완 상태를 유지하십시오.",
            "SYSTEM candidate reference threshold를 낮추거나 정량 신호를 삭제하지 마십시오.",
            "5 mm 이상이라는 이유만으로 해당 시각을 peak_forecast_hours에 추가하지 마십시오. 실제 후보 강수의 peak일 때만 추가하십시오.",
            "candidate_name과 hypothesis.statement은 기존 후보의 확장된 시간·공간 범위를 사실대로 표현하기 위해 필요한 경우에만 수정하십시오.",
            "완전한 candidate discovery JSON 객체 하나만 반환하십시오. Python은 허용된 포괄성 변경만 적용합니다.",
            "이번 작업목록에 포함된 신호를 우선 처리하십시오. 다른 포괄 신호를 잃거나 강수계를 억지로 병합하지 마십시오.",
            "candidate_diagnostics의 failure_reasons를 확인하고 공간/시간/영상/근거 중 실제 누락된 항목만 보완하십시오.",
            "10개 후보 개수 상한을 넘기거나, 그 상한을 맞추기 위해 독립적인 강수계를 무리하게 병합하지 마십시오.",
        ],
    }
    return [
        *base_messages,
        {
            "role": "user",
            "content": "## SYSTEM direct precipitation coverage reconciliation\n" + json.dumps(payload, ensure_ascii=False),
        },
    ]


def _system_direct_precipitation_coverage_advisories(audit: dict[str, Any]) -> list[str]:
    rows = [
        row for row in audit.get("signals", [])
        if isinstance(row, dict)
        and row.get("coverage_status") == "uncovered"
        and row.get("candidate_generation_required") is not True
    ]
    by_hour: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_hour[int(row["forecast_hour"])].append(row)
    threshold = float(audit.get("candidate_reference_threshold_6h_mm") or 0.0)
    notes: list[str] = []
    for hour in sorted(by_hour):
        strongest = max(by_hour[hour], key=lambda item: float(item.get("maximum_6h_mm") or 0.0))
        notes.append(
            "SYSTEM_DIRECT_PRECIPITATION_COVERAGE_ADVISORY "
            f"forecast_hour=F{hour:03d} strongest_uncovered_region={strongest.get('region_id')} "
            f"maximum_6h_mm={float(strongest.get('maximum_6h_mm') or 0.0):.2f} "
            f"below_candidate_reference_threshold_mm={threshold:g}; candidate generation is not forced."
        )
    return notes


def build_precipitation_peak_guidance(
    overview_data: dict[str, Any],
    *,
    minimum_regional_max_mm: float = 5.0,
) -> dict[str, Any]:
    """Build deterministic quantitative peak guidance from Step02 region series.

    The guidance is an anchor, not a candidate generator. The model still decides
    whether distinct peaks belong to one meteorological event after considering
    spatial continuity and the environmental mechanism.
    """
    timeline = _aggregate_precipitation_timeline(overview_data)
    primary_peak_hours = _local_peak_hours_from_timeline(timeline)
    group_ids = sorted(
        {
            str(row.get("overlap_group_id"))
            for row in _operational_precipitation_series(overview_data)
            if str(row.get("overlap_group_id", "")).strip()
        }
    )
    anchors: list[dict[str, Any]] = []
    for group_id in group_ids:
        group_timeline = _aggregate_precipitation_timeline(
            overview_data,
            overlap_group_id=group_id,
        )
        group_peaks = _local_peak_hours_from_timeline(group_timeline)
        row_by_hour = {int(row["forecast_hour"]): row for row in group_timeline}
        for hour in group_peaks:
            row = row_by_hour.get(hour)
            if row is None or float(row.get("max_mm", 0.0)) < float(minimum_regional_max_mm):
                continue
            anchors.append(
                {
                    "forecast_hour": hour,
                    "overlap_group_id": group_id,
                    "region_id": row.get("region_id"),
                    "region_name_ko": row.get("region_name_ko"),
                    "max_mm": row.get("max_mm"),
                    "p95_mm": row.get("p95_mm"),
                    "mean_mm": row.get("mean_mm"),
                    "max_location": row.get("max_location"),
                    "score": round(float(row.get("score", 0.0)), 3),
                }
            )
    # Keep the strongest anchor for one overlap group/hour and bound prompt size.
    unique: dict[tuple[int, str], dict[str, Any]] = {}
    for anchor in anchors:
        key = (int(anchor["forecast_hour"]), str(anchor["overlap_group_id"]))
        if key not in unique or float(anchor["score"]) > float(unique[key]["score"]):
            unique[key] = anchor
    bounded = sorted(unique.values(), key=lambda row: (-float(row["score"]), int(row["forecast_hour"])))[:36]
    bounded.sort(key=lambda row: (int(row["forecast_hour"]), str(row["overlap_group_id"])))
    return {
        "source": "step02.operational_region_timeseries.tp_6h_increment_mm",
        "metric": "0.40*regional_max + 0.45*regional_p95 + 0.15*regional_mean",
        "regional_maximum_6h_anchor_mm": float(minimum_regional_max_mm),
        "primary_peak_hours": primary_peak_hours,
        "operational_timeline": [
            {
                key: row.get(key)
                for key in (
                    "forecast_hour", "max_mm", "p95_mm", "mean_mm", "region_id",
                    "region_name_ko", "max_location", "score"
                )
            }
            for row in timeline
        ],
        "regional_peak_anchors": bounded,
        "usage_rules": [
            "정량 강수 peak는 direct_precipitation 및 mixed 후보의 peak 판단을 위한 참고 anchor이며 최종 peak_forecast_hours는 AI가 영상·정량 근거를 함께 해석해 판단합니다.",
            "overview 그룹의 첫 시각을 자동으로 peak로 선택하지 않습니다.",
            "정량 peak와 AI 판단 peak가 다르거나 여러 peak가 시간적으로 분리되어도 Python은 후보를 자동 merge/split하지 않으며 후속 상세분석에서 재검토할 QC 참고정보로 사용합니다.",
            "precursor_environment 후보의 peak_forecast_hours는 강수 peak가 아니라 선행환경 중첩이 가장 강한 대표시각입니다.",
        ],
    }


def _precursor_category_for_product(product_id: str) -> str | None:
    mapping = {
        "QL_TCW_WIND850": "moisture_supply",
        "QL_CONVERGENCE925_WIND925": "low_level_forcing",
        "QL_OMEGA700_Z500": "mid_upper_forcing",
    }
    return mapping.get(product_id)


_PRECURSOR_CATEGORY_ORDER = (
    "direct_precipitation",
    "moisture_supply",
    "low_level_forcing",
    "mid_upper_forcing",
    "thermodynamic_support",
    "organization_persistence",
)


def _ordered_precursor_categories(values: Iterable[Any]) -> list[str]:
    normalized = {str(value) for value in values}
    return [value for value in _PRECURSOR_CATEGORY_ORDER if value in normalized]


def _candidate_precursor_asset_ids(
    candidate: dict[str, Any],
    *,
    attachment_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    result: list[str] = []
    for raw_asset_id in candidate.get("related_overview_asset_ids", []):
        asset_id = str(raw_asset_id)
        source = attachment_by_id.get(asset_id)
        if source is None:
            continue
        category = _precursor_category_for_product(str(source.get("product_id", "")))
        if category is not None and asset_id not in result:
            result.append(asset_id)
    return result


def _normalize_candidate_precursor_metadata(
    candidates: list[dict[str, Any]],
    *,
    attachment_by_id: dict[str, dict[str, Any]] | None,
) -> None:
    """Preserve environmental evidence even when discovery began from precipitation.

    ``detection_basis`` records how a candidate was discovered. It must not erase
    moisture/forcing evidence that was explicitly attached to the same candidate.
    The overlap flag is synchronized with the model's hypothesis-level judgment,
    but only when at least one non-precipitation category is actually referenced.
    """
    for candidate in candidates:
        declared = [str(value) for value in candidate.get("precursor_signal_categories", [])]
        derived: list[str] = []
        if attachment_by_id is not None:
            for asset_id in candidate.get("related_overview_asset_ids", []):
                source = attachment_by_id.get(str(asset_id))
                if source is None:
                    continue
                category = _precursor_category_for_product(str(source.get("product_id", "")))
                if category is not None:
                    derived.append(category)

        basis = str(candidate.get("detection_basis", ""))
        precipitation_status = str(candidate.get("precipitation_signal_status", ""))
        if basis in {"direct_precipitation", "mixed"} and precipitation_status != "absent":
            declared.append("direct_precipitation")
        if basis == "precursor_environment":
            declared = [value for value in declared if value != "direct_precipitation"]

        categories = _ordered_precursor_categories([*declared, *derived])
        candidate["precursor_signal_categories"] = categories
        non_precipitation_categories = {
            value for value in categories if value != "direct_precipitation"
        }
        hypothesis = candidate.get("hypothesis")
        hypothesis_overlap = bool(
            hypothesis.get("spatiotemporal_overlap")
            if isinstance(hypothesis, dict)
            else False
        )
        overlap = bool(
            non_precipitation_categories
            and (
                candidate.get("precursor_spatiotemporal_overlap") is True
                or hypothesis_overlap
            )
        )
        candidate["precursor_spatiotemporal_overlap"] = overlap
        if isinstance(hypothesis, dict):
            hypothesis["spatiotemporal_overlap"] = overlap


def _build_dismissed_precursor_groups(
    candidates: list[dict[str, Any]],
    *,
    attachment_by_id: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Create an audit trail for precursor signals absorbed into direct/mixed candidates."""
    if attachment_by_id is None:
        return []
    groups: list[dict[str, Any]] = []
    for candidate in candidates:
        if str(candidate.get("detection_basis")) not in {"direct_precipitation", "mixed"}:
            continue
        categories = [
            str(value)
            for value in candidate.get("precursor_signal_categories", [])
            if str(value) != "direct_precipitation"
        ]
        overlap = candidate.get("precursor_spatiotemporal_overlap") is True
        if not has_independent_precursor_support(
            categories,
            spatial_temporal_overlap=overlap,
        ):
            continue
        source_asset_ids = _candidate_precursor_asset_ids(
            candidate,
            attachment_by_id=attachment_by_id,
        )
        if len(source_asset_ids) < 2:
            continue
        candidate_id = str(candidate.get("candidate_id", ""))
        forecast_hours = sorted(
            {int(value) for value in candidate.get("peak_forecast_hours", [])}
        )
        if not candidate_id or not forecast_hours:
            continue
        groups.append(
            {
                "dismissed_group_id": f"DPG_{len(groups)+1:03d}",
                "dismissal_basis": "absorbed_into_direct_or_mixed_candidate",
                "source_asset_ids": source_asset_ids[:8],
                "forecast_hours": forecast_hours[:8],
                "precursor_signal_categories": _ordered_precursor_categories(categories),
                "overlapping_candidate_ids": [candidate_id],
                "dismissal_reason": (
                    f"수분·역학 강제력 신호가 {candidate_id}의 강수 peak와 같은 시간·영역에 "
                    "중첩되어 독립 선행환경 후보로 분리하지 않았습니다."
                ),
            }
        )
    return groups[:10]


def _precursor_candidate_evidence_advisories(
    candidates: list[dict[str, Any]],
    *,
    attachment_manifest: dict[str, Any],
) -> list[str]:
    """Return non-blocking AI precursor-classification review notes."""
    errors: list[str] = []
    attachment_by_id = _attachment_by_id(attachment_manifest)
    forcing_categories = {"low_level_forcing", "mid_upper_forcing"}
    precursor_candidates = [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict)
        and str(candidate.get("detection_basis")) == "precursor_environment"
    ]
    direct_or_mixed_candidates = [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict)
        and str(candidate.get("detection_basis")) in {"direct_precipitation", "mixed"}
    ]
    for candidate in precursor_candidates:
        candidate_id = str(candidate.get("candidate_id") or candidate.get("candidate_name") or "<unknown>")
        categories = {
            str(value)
            for value in candidate.get("precursor_signal_categories", [])
            if str(value) != "direct_precipitation"
        }
        if len(categories) < 2 or not (categories & forcing_categories):
            errors.append(
                f"{candidate_id} precursor_environment requires at least two independent precursor categories "
                "including low_level_forcing or mid_upper_forcing"
            )
        if candidate.get("precursor_spatiotemporal_overlap") is not True:
            errors.append(f"{candidate_id} precursor_environment requires precursor_spatiotemporal_overlap=true")
        if str(candidate.get("precipitation_signal_status")) == "present":
            errors.append(
                f"{candidate_id} has direct precipitation present and must be mixed rather than precursor_environment"
            )
        evidence_categories = {
            category
            for asset_id in candidate.get("related_overview_asset_ids", [])
            for category in [
                _precursor_category_for_product(
                    str(attachment_by_id.get(str(asset_id), {}).get("product_id", ""))
                )
            ]
            if category is not None
        }
        if len(evidence_categories) < 2 or not (evidence_categories & forcing_categories):
            errors.append(
                f"{candidate_id} precursor_environment lacks at least two non-precipitation overview products "
                "including a dynamical forcing product"
            )

        # Similarity to a direct/mixed candidate is useful review information,
        # but the AI retains the merge/split decision.
        for other in direct_or_mixed_candidates:
            spatial_overlap = _bbox_overlap_fraction_of_smaller(
                candidate.get("candidate_area"), other.get("candidate_area")
            )
            temporal_overlap = _time_window_overlap_hours(
                candidate.get("time_window"), other.get("time_window")
            )
            other_categories = {
                str(value)
                for value in other.get("precursor_signal_categories", [])
                if str(value) != "direct_precipitation"
            }
            common_categories = categories & other_categories
            if spatial_overlap >= 0.60 and temporal_overlap > 0 and len(common_categories) >= 2:
                errors.append(
                    f"{candidate_id} duplicates {other.get('candidate_id')} in time, area, and precursor mechanism; "
                    "merge them as one mixed candidate or retain only an independent precursor event"
                )
    return errors


def _validate_precursor_opportunities(
    opportunities: Any,
    *,
    candidates: list[dict[str, Any]],
    attachment_manifest: dict[str, Any] | None,
) -> None:
    """Validate structural precursor relation invariants, never meteorology."""
    if opportunities is None:
        return  # Legacy discovery artifact: relation provenance was unavailable.
    if not isinstance(opportunities, list):
        raise ValueError("precursor_opportunities must be an array")
    candidate_by_id = {
        str(candidate.get("candidate_id")): candidate
        for candidate in candidates if isinstance(candidate, dict) and candidate.get("candidate_id")
    }
    attachment_ids = set(_attachment_by_id(attachment_manifest)) if attachment_manifest else set()
    opportunity_ids: set[str] = set()
    created_ids: set[str] = set()
    for opportunity in opportunities:
        if not isinstance(opportunity, dict):
            raise ValueError("precursor_opportunities item is not an object")
        opportunity_id = str(opportunity.get("opportunity_id", ""))
        if not opportunity_id or opportunity_id in opportunity_ids:
            raise ValueError("precursor_opportunities opportunity_id values must be unique")
        opportunity_ids.add(opportunity_id)
        qualification = str(opportunity.get("qualification", ""))
        relation = str(opportunity.get("relation_to_direct", ""))
        creation = str(opportunity.get("candidate_creation", ""))
        created_id = opportunity.get("created_candidate_id")
        related_ids = [str(value) for value in opportunity.get("related_direct_candidate_ids", [])]
        if qualification not in {"qualified", "not_qualified"}:
            raise ValueError(f"{opportunity_id} qualification is invalid")
        if relation not in {"independent", "absorbed_into_direct", "boundary_ambiguous", "not_applicable"}:
            raise ValueError(f"{opportunity_id} relation_to_direct is invalid")
        if creation not in {"created", "not_created"}:
            raise ValueError(f"{opportunity_id} candidate_creation is invalid")
        if qualification == "not_qualified" and (relation != "not_applicable" or creation != "not_created"):
            raise ValueError(f"{opportunity_id} not_qualified must be not_applicable and not_created")
        if relation in {"absorbed_into_direct", "boundary_ambiguous"} and creation != "not_created":
            raise ValueError(f"{opportunity_id} {relation} must not create a precursor candidate")
        if creation == "created":
            if qualification != "qualified" or relation != "independent":
                raise ValueError(f"{opportunity_id} created requires qualified independent relation")
            if not isinstance(created_id, str) or created_id not in candidate_by_id:
                raise ValueError(f"{opportunity_id} created_candidate_id must reference a candidate")
            if candidate_by_id[created_id].get("detection_basis") != "precursor_environment":
                raise ValueError(f"{opportunity_id} created_candidate_id must reference precursor_environment")
            created_ids.add(created_id)
        elif created_id is not None:
            raise ValueError(f"{opportunity_id} not_created must have null created_candidate_id")
        for candidate_id in related_ids:
            candidate = candidate_by_id.get(candidate_id)
            if candidate is None:
                raise ValueError(f"{opportunity_id} references unknown related_direct_candidate_id={candidate_id}")
            if candidate.get("detection_basis") not in {"direct_precipitation", "mixed"}:
                raise ValueError(f"{opportunity_id} related_direct_candidate_ids must reference direct/mixed candidates")
        if relation in {"absorbed_into_direct", "boundary_ambiguous"} and not related_ids:
            raise ValueError(f"{opportunity_id} {relation} requires related_direct_candidate_ids")
        if attachment_ids:
            unknown_assets = sorted(set(str(value) for value in opportunity.get("support_asset_ids", [])) - attachment_ids)
            if unknown_assets:
                raise ValueError(f"{opportunity_id} references unknown support_asset_ids={unknown_assets}")
    precursor_candidates = {
        candidate_id for candidate_id, candidate in candidate_by_id.items()
        if candidate.get("detection_basis") == "precursor_environment"
    }
    if opportunities is not None and precursor_candidates != created_ids:
        raise ValueError("precursor_environment candidates must exactly match created precursor opportunities")


def _derive_legacy_precursor_assessment(
    opportunities: list[dict[str, Any]],
    existing: dict[str, Any],
) -> dict[str, Any]:
    """Keep the legacy summary as a deterministic compatibility projection."""
    independent = [
        item
        for item in opportunities
        if item.get("qualification") == "qualified"
        and item.get("relation_to_direct") == "independent"
    ]
    assets = list(dict.fromkeys(str(asset) for item in independent for asset in item.get("support_asset_ids", [])))[:8]
    hours = sorted({int(hour) for item in independent for hour in item.get("representative_forecast_hours", [])})[:8]
    return {
        "assessment_status": "qualified_independent_signal" if independent else "no_qualified_independent_signal",
        "qualified_opportunity_count": len(independent),
        "source_asset_ids": assets,
        "forecast_hours": hours,
        "rationale": "선행환경 탐색 결과에서 결정론적으로 파생한 기존 형식 호환 요약입니다.",
        "dismissed_precursor_groups": existing.get("dismissed_precursor_groups", []) if isinstance(existing, dict) else [],
    }

def _validate_precursor_candidate_assessment(
    assessment: Any,
    *,
    candidates: list[dict[str, Any]],
    attachment_manifest: dict[str, Any] | None,
) -> None:
    """Validate only executable/reference integrity of precursor assessment.

    Meteorological consistency (whether an independent precursor truly exists,
    whether a direct/mixed candidate absorbed it, or whether the opportunity
    count/rationale fully agrees with the selected candidates) is an AI judgment
    and must not abort Step04.  Those conditions are reported separately as
    non-blocking advisories by ``_precursor_candidate_assessment_advisories``.
    """
    if not isinstance(assessment, dict):
        raise ValueError("candidate discovery requires precursor_candidate_assessment")
    status = str(assessment.get("assessment_status", ""))
    if status not in {"qualified_independent_signal", "no_qualified_independent_signal"}:
        raise ValueError("precursor_candidate_assessment.assessment_status is invalid")
    try:
        opportunity_count = int(assessment.get("qualified_opportunity_count", -1))
    except (TypeError, ValueError) as exc:
        raise ValueError("precursor_candidate_assessment.qualified_opportunity_count is invalid") from exc
    if opportunity_count < 0:
        raise ValueError("precursor_candidate_assessment.qualified_opportunity_count must be non-negative")

    source_asset_ids = assessment.get("source_asset_ids", [])
    if not isinstance(source_asset_ids, list):
        raise ValueError("precursor_candidate_assessment.source_asset_ids must be an array")
    source_asset_ids = [str(value) for value in source_asset_ids]
    try:
        forecast_hours = [int(value) for value in assessment.get("forecast_hours", [])]
    except (TypeError, ValueError) as exc:
        raise ValueError("precursor_candidate_assessment.forecast_hours is invalid") from exc
    if any(hour < 0 for hour in forecast_hours):
        raise ValueError("precursor_candidate_assessment.forecast_hours contains a negative value")

    dismissed_groups = assessment.get("dismissed_precursor_groups", [])
    if not isinstance(dismissed_groups, list):
        raise ValueError("precursor_candidate_assessment.dismissed_precursor_groups must be an array")

    candidate_ids = {
        str(candidate.get("candidate_id"))
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("candidate_id")
    }
    seen_group_ids: set[str] = set()
    for index, group in enumerate(dismissed_groups, start=1):
        if not isinstance(group, dict):
            raise ValueError(f"dismissed_precursor_groups item is not an object index={index}")
        group_id = str(group.get("dismissed_group_id", ""))
        if group_id in seen_group_ids:
            raise ValueError("dismissed_precursor_groups IDs must be unique")
        seen_group_ids.add(group_id)
        unknown_candidates = sorted(
            set(str(value) for value in group.get("overlapping_candidate_ids", [])) - candidate_ids
        )
        if unknown_candidates:
            raise ValueError(f"{group_id or f'dismissed_group[{index}]'} references unknown overlapping_candidate_ids={unknown_candidates}")
        try:
            group_hours = [int(value) for value in group.get("forecast_hours", [])]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{group_id or f'dismissed_group[{index}]'} forecast_hours is invalid") from exc
        if any(hour < 0 for hour in group_hours):
            raise ValueError(f"{group_id or f'dismissed_group[{index}]'} forecast_hours contains a negative value")

    if attachment_manifest is None:
        return
    attachment_by_id = _attachment_by_id(attachment_manifest)
    unknown = sorted(set(source_asset_ids) - set(attachment_by_id))
    if unknown:
        raise ValueError(f"precursor_candidate_assessment references unknown source_asset_ids={unknown}")
    for index, group in enumerate(dismissed_groups, start=1):
        group_id = str(group.get("dismissed_group_id") or f"dismissed_group[{index}]")
        group_asset_ids = [str(value) for value in group.get("source_asset_ids", [])]
        unknown_group_assets = sorted(set(group_asset_ids) - set(attachment_by_id))
        if unknown_group_assets:
            raise ValueError(f"{group_id} references unknown source_asset_ids={unknown_group_assets}")


def _precursor_candidate_assessment_advisories(
    assessment: Any,
    *,
    candidates: list[dict[str, Any]],
    attachment_manifest: dict[str, Any] | None,
) -> list[str]:
    """Return non-blocking meteorological/audit consistency notes.

    These checks are useful for Pass2 review and debugging but they do not decide
    candidate selection, precursor independence, or merge/split.  No item returned
    by this function is allowed to fail Step04.
    """
    if not isinstance(assessment, dict):
        return []
    notes: list[str] = []
    status = str(assessment.get("assessment_status", ""))
    try:
        opportunity_count = int(assessment.get("qualified_opportunity_count", 0))
    except (TypeError, ValueError):
        opportunity_count = 0
    precursor_candidates = [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict)
        and str(candidate.get("detection_basis")) == "precursor_environment"
    ]
    source_asset_ids = [str(value) for value in assessment.get("source_asset_ids", [])]
    try:
        forecast_hours = [int(value) for value in assessment.get("forecast_hours", [])]
    except (TypeError, ValueError):
        forecast_hours = []
    rationale = re.sub(r"\s+", " ", str(assessment.get("rationale", "")).strip())
    dismissed_groups = [
        group for group in assessment.get("dismissed_precursor_groups", []) if isinstance(group, dict)
    ]

    if not rationale:
        notes.append("precursor assessment rationale is empty")
    elif re.search(r"[가-힣]", rationale) is None:
        notes.append("precursor assessment rationale is not Korean narrative")

    if status == "qualified_independent_signal":
        if opportunity_count < 1:
            notes.append("qualified precursor assessment has qualified_opportunity_count < 1")
        if not precursor_candidates:
            notes.append("qualified precursor assessment emitted no precursor_environment candidate")
        if opportunity_count < len(precursor_candidates):
            notes.append("qualified_opportunity_count is smaller than emitted precursor candidate count")
        if not source_asset_ids or not forecast_hours:
            notes.append("qualified precursor assessment has incomplete source assets or representative hours")
    elif status == "no_qualified_independent_signal":
        if opportunity_count != 0:
            notes.append("no-qualified precursor assessment has nonzero qualified_opportunity_count")
        if precursor_candidates:
            notes.append("precursor_environment candidates exist while assessment says no qualified signal")
        if source_asset_ids or forecast_hours:
            notes.append("no-qualified precursor assessment retains source assets or representative hours")

    expected_absorbed_candidates = {
        str(candidate.get("candidate_id"))
        for candidate in candidates
        if isinstance(candidate, dict)
        and str(candidate.get("detection_basis")) in {"direct_precipitation", "mixed"}
        and has_independent_precursor_support(
            candidate.get("precursor_signal_categories", []),
            spatial_temporal_overlap=(candidate.get("precursor_spatiotemporal_overlap") is True),
        )
    }
    covered_candidate_ids = {
        str(value)
        for group in dismissed_groups
        for value in group.get("overlapping_candidate_ids", [])
    }
    missing_absorbed = sorted(expected_absorbed_candidates - covered_candidate_ids)
    if missing_absorbed:
        notes.append(f"dismissed precursor audit does not cover absorbed candidates={missing_absorbed}")

    candidate_by_id = {
        str(candidate.get("candidate_id")): candidate
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("candidate_id")
    }
    for index, group in enumerate(dismissed_groups, start=1):
        group_id = str(group.get("dismissed_group_id") or f"dismissed_group[{index}]")
        categories = [str(value) for value in group.get("precursor_signal_categories", [])]
        if not has_independent_precursor_support(categories, spatial_temporal_overlap=True):
            notes.append(f"{group_id} has weak precursor-category audit support")
        try:
            group_hours = [int(value) for value in group.get("forecast_hours", [])]
        except (TypeError, ValueError):
            group_hours = []
        group_candidate_ids = [str(value) for value in group.get("overlapping_candidate_ids", [])]
        if group_candidate_ids and group_hours:
            if not any(
                any(
                    int(candidate_by_id[candidate_id].get("time_window", {}).get("forecast_hour_start", -1))
                    <= hour
                    <= int(candidate_by_id[candidate_id].get("time_window", {}).get("forecast_hour_end", -1))
                    for hour in group_hours
                )
                for candidate_id in group_candidate_ids
                if candidate_id in candidate_by_id
            ):
                notes.append(f"{group_id} audit hours do not overlap referenced candidate windows")

    if attachment_manifest is not None:
        attachment_by_id = _attachment_by_id(attachment_manifest)
        for index, group in enumerate(dismissed_groups, start=1):
            group_id = str(group.get("dismissed_group_id") or f"dismissed_group[{index}]")
            group_asset_ids = [str(value) for value in group.get("source_asset_ids", [])]
            derived_categories = {
                category
                for asset_id in group_asset_ids
                for category in [
                    _precursor_category_for_product(
                        str(attachment_by_id.get(asset_id, {}).get("product_id", ""))
                    )
                ]
                if category is not None
            }
            declared_categories = {
                str(value) for value in group.get("precursor_signal_categories", [])
            }
            if not declared_categories.issubset(derived_categories):
                notes.append(f"{group_id} declared precursor categories exceed source-asset support")
        if status == "qualified_independent_signal":
            categories = {
                category
                for asset_id in source_asset_ids
                for category in [
                    _precursor_category_for_product(
                        str(attachment_by_id.get(asset_id, {}).get("product_id", ""))
                    )
                ]
                if category is not None
            }
            if len(categories) < 2 or not categories & {"low_level_forcing", "mid_upper_forcing"}:
                notes.append("qualified precursor assessment has limited independent dynamical source support")
            assessment_asset_set = set(source_asset_ids)
            assessment_hour_set = set(forecast_hours)
            for candidate in precursor_candidates:
                candidate_id = str(candidate.get("candidate_id") or candidate.get("candidate_name") or "<unknown>")
                candidate_assets = {str(value) for value in candidate.get("related_overview_asset_ids", [])}
                candidate_hours = {int(value) for value in candidate.get("peak_forecast_hours", [])}
                if assessment_asset_set and not (candidate_assets & assessment_asset_set):
                    notes.append(f"{candidate_id} is weakly traceable to precursor assessment source assets")
                if assessment_hour_set and not (candidate_hours & assessment_hour_set):
                    notes.append(f"{candidate_id} is weakly traceable to precursor assessment forecast hours")
    return list(dict.fromkeys(notes))


def _log_step04_advisories(stage: str, notes: Iterable[str]) -> None:
    for note in dict.fromkeys(str(value).strip() for value in notes if str(value).strip()):
        logging.warning("step04 advisory stage=%s note=%s", stage, note)

def _canonicalize_significant_precipitation_evidence(
    response: dict[str, Any],
    *,
    group_summaries: list[dict[str, Any]],
    attachment_manifest: dict[str, Any],
) -> None:
    """Backfill omitted in-window precipitation hours into existing evidence items.

    Candidate boundaries remain an AI meteorological judgment.  This function only
    repairs a representational omission when all of the following are already true:
    the candidate references the precipitation asset, its declared time window
    contains the audited significant hour, and its evidence group already contains
    an item for that same asset.  It never creates or extends candidates and never
    invents a new evidence asset.
    """
    attachment_by_id = _attachment_by_id(attachment_manifest)
    candidates = [
        item for item in response.get("candidates", []) if isinstance(item, dict)
    ]
    evidence_by_id = {
        str(group.get("candidate_id")): group
        for group in response.get("candidate_evidence", [])
        if isinstance(group, dict)
    }

    for summary in group_summaries:
        if not isinstance(summary, dict):
            continue
        audit = summary.get("direct_precipitation_audit")
        if not isinstance(audit, dict) or audit.get("status") != "present":
            continue
        significant_hours = sorted(
            {int(value) for value in audit.get("significant_forecast_hours", [])}
        )
        for asset_id in [str(value) for value in audit.get("source_asset_ids", [])]:
            source = attachment_by_id.get(asset_id)
            if source is None or str(source.get("product_id")) != "QL_PRECIP_6H":
                continue
            source_hours = {int(value) for value in source.get("forecast_hours", [])}
            for hour in sorted(set(significant_hours) & source_hours):
                eligible: list[tuple[int, int, str, dict[str, Any]]] = []
                for candidate in candidates:
                    candidate_id = str(candidate.get("candidate_id", ""))
                    related_assets = {
                        str(value)
                        for value in candidate.get("related_overview_asset_ids", [])
                    }
                    if asset_id not in related_assets:
                        continue
                    window = candidate.get("time_window", {})
                    try:
                        start = int(window.get("forecast_hour_start"))
                        end = int(window.get("forecast_hour_end"))
                    except (TypeError, ValueError):
                        continue
                    if not (start <= hour <= end):
                        continue
                    evidence_group = evidence_by_id.get(candidate_id)
                    if not isinstance(evidence_group, dict):
                        continue
                    evidence_item = next(
                        (
                            item
                            for item in evidence_group.get("evidence_items", [])
                            if isinstance(item, dict)
                            and str(item.get("asset_id")) == asset_id
                        ),
                        None,
                    )
                    if evidence_item is None:
                        continue
                    existing_hours = {
                        int(value) for value in evidence_item.get("forecast_hours", [])
                    }
                    if hour in existing_hours:
                        eligible = []
                        break
                    peak_hours = [
                        int(value) for value in candidate.get("peak_forecast_hours", [])
                    ]
                    peak_distance = (
                        min(abs(hour - value) for value in peak_hours)
                        if peak_hours
                        else 10**9
                    )
                    try:
                        discovery_order = int(candidate.get("discovery_order", 10**9))
                    except (TypeError, ValueError):
                        discovery_order = 10**9
                    eligible.append(
                        (peak_distance, discovery_order, candidate_id, evidence_item)
                    )
                if not eligible:
                    continue
                _, _, candidate_id, evidence_item = min(eligible, key=lambda item: item[:3])
                normalized_hours = sorted(
                    {
                        int(value)
                        for value in evidence_item.get("forecast_hours", [])
                    }
                    | {hour}
                )
                evidence_item["forecast_hours"] = normalized_hours
                logging.info(
                    "step04 candidate discovery precipitation evidence canonicalized "
                    "candidate_id=%s asset_id=%s added_forecast_hour=%s",
                    candidate_id,
                    asset_id,
                    hour,
                )

def _significant_precipitation_coverage_advisories(
    response: dict[str, Any],
    *,
    group_summaries: list[dict[str, Any]],
    attachment_manifest: dict[str, Any],
) -> list[str]:
    """Return non-blocking direct-precipitation coverage review notes."""
    errors: list[str] = []
    attachment_by_id = _attachment_by_id(attachment_manifest)
    candidates = [item for item in response.get("candidates", []) if isinstance(item, dict)]
    evidence_by_id = {
        str(group.get("candidate_id")): group
        for group in response.get("candidate_evidence", [])
        if isinstance(group, dict)
    }
    for summary in group_summaries:
        if not isinstance(summary, dict):
            continue
        audit = summary.get("direct_precipitation_audit")
        if not isinstance(audit, dict) or audit.get("status") != "present":
            continue
        asset_ids = [str(value) for value in audit.get("source_asset_ids", [])]
        significant_hours = sorted({int(value) for value in audit.get("significant_forecast_hours", [])})
        if not asset_ids or not significant_hours:
            errors.append(
                f"group_summary={summary.get('summary_id')} has status=present but lacks precipitation asset/hour audit"
            )
            continue
        for asset_id in asset_ids:
            source = attachment_by_id.get(asset_id)
            if source is None or str(source.get("product_id")) != "QL_PRECIP_6H":
                errors.append(
                    f"group_summary={summary.get('summary_id')} direct precipitation audit references invalid asset_id={asset_id}"
                )
                continue
            source_hours = {int(value) for value in source.get("forecast_hours", [])}
            audited_hours = sorted(set(significant_hours) & source_hours)
            missing_hours: list[int] = []
            for hour in audited_hours:
                covered = False
                for candidate in candidates:
                    candidate_id = str(candidate.get("candidate_id"))
                    related_assets = {str(value) for value in candidate.get("related_overview_asset_ids", [])}
                    if asset_id not in related_assets:
                        continue
                    window = candidate.get("time_window", {})
                    try:
                        if not (int(window.get("forecast_hour_start")) <= hour <= int(window.get("forecast_hour_end"))):
                            continue
                    except (TypeError, ValueError):
                        continue
                    evidence_hours = {
                        int(value)
                        for item in evidence_by_id.get(candidate_id, {}).get("evidence_items", [])
                        if isinstance(item, dict) and str(item.get("asset_id")) == asset_id
                        for value in item.get("forecast_hours", [])
                    }
                    if hour in evidence_hours:
                        covered = True
                        break
                if not covered:
                    missing_hours.append(hour)
            if missing_hours:
                errors.append(
                    "candidate discovery group-summary precipitation observation is not linked to candidate evidence "
                    f"summary_id={summary.get('summary_id')} asset_id={asset_id} forecast_hours={missing_hours}; "
                    "this is observational provenance only and does not require candidate creation. "
                    "Operational candidate coverage is governed by the SYSTEM direct-precipitation coverage audit."
                )
    return errors


def _candidate_discovery_meteorological_qc_notes(
    candidates: list[dict[str, Any]],
    *,
    overview_data: dict[str, Any] | None,
) -> dict[str, list[str]]:
    """Build non-blocking meteorological QC notes for AI discovery judgments.

    Peak selection and candidate merge/split are meteorological judgments owned by
    the AI workflow.  Python still calculates deterministic comparison signals so
    that later candidate planning and Pass2 can revisit ambiguous decisions, but
    those comparisons must not reject an otherwise contract-valid discovery.
    """
    notes: dict[str, list[str]] = defaultdict(list)
    normalized_candidates = [item for item in candidates if isinstance(item, dict)]

    for candidate in normalized_candidates:
        candidate_id = str(
            candidate.get("candidate_id")
            or candidate.get("candidate_name")
            or "<unknown>"
        )
        declared_peaks = sorted(
            {
                int(value)
                for value in candidate.get("peak_forecast_hours", [])
                if isinstance(value, (int, float, str)) and str(value).strip() != ""
            }
        )
        peak_groups = _contiguous_hour_groups(declared_peaks, maximum_gap_hours=12)
        if len(peak_groups) > 1:
            notes[candidate_id].append(
                f"AI 판단 핵심 시각의 분리 구간={peak_groups}"
            )

        basis = str(candidate.get("detection_basis", ""))
        if overview_data is None or basis not in {"direct_precipitation", "mixed"}:
            continue
        bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
        if bbox is None:
            continue
        timeline = _aggregate_precipitation_timeline(overview_data, candidate_bbox=bbox)
        if not timeline:
            notes[candidate_id].append("후보영역 정량 강수 시계열 없음")
            continue
        window = candidate.get("time_window", {})
        try:
            start = int(window.get("forecast_hour_start"))
            end = int(window.get("forecast_hour_end"))
        except (TypeError, ValueError):
            continue
        row_by_hour = {int(row["forecast_hour"]): row for row in timeline}
        local_peak_hours = [
            hour for hour in _local_peak_hours_from_timeline(timeline) if start <= hour <= end
        ]
        if not local_peak_hours:
            notes[candidate_id].append("후보 시간창 내 정량 강수의 국지 최댓값 시각이 확인되지 않음")
            continue
        declared = set(declared_peaks)
        if declared and not declared.issubset(set(local_peak_hours)):
            notes[candidate_id].append(
                f"AI 판단 핵심 시각={declared_peaks}, 정량 강수 국지 최댓값 시각={local_peak_hours}"
            )
        peak_scores = {
            hour: float(row_by_hour[hour].get("score", 0.0)) for hour in local_peak_hours
        }
        if peak_scores:
            best_score = max(peak_scores.values())
            strongest = sorted(
                hour for hour, score in peak_scores.items() if score >= best_score * 0.90
            )
            if declared and not (declared & set(strongest)):
                notes[candidate_id].append(
                    f"정량 강수 최댓값 대표 시각={strongest}"
                )

    for index, first in enumerate(normalized_candidates):
        for second in normalized_candidates[index + 1 :]:
            overlap = _bbox_overlap_fraction_of_smaller(
                first.get("candidate_area"), second.get("candidate_area")
            )
            shared_peaks = sorted(
                {int(value) for value in first.get("peak_forecast_hours", [])}
                & {int(value) for value in second.get("peak_forecast_hours", [])}
            )
            time_overlap = _time_window_overlap_hours(
                first.get("time_window"), second.get("time_window")
            )
            time_gap = _time_window_gap_hours(
                first.get("time_window"), second.get("time_window")
            )
            mechanism_similar, _common_categories = _candidate_mechanism_similarity(first, second)
            peak_gap = _candidate_peak_gap_hours(first, second)
            near_duplicate = overlap >= 0.80 and time_overlap > 0 and bool(shared_peaks)
            adjacent_continuous = (
                overlap >= 0.60
                and peak_gap is not None
                and peak_gap <= 12
                and mechanism_similar
            )
            adjacent_peak_episodes = (
                _candidate_unit_type(first) == "peak_episode"
                and _candidate_unit_type(second) == "peak_episode"
                and overlap >= 0.30
                and time_gap is not None
                and time_gap <= 6
                and peak_gap is not None
                and peak_gap <= 12
            )
            if not near_duplicate and not adjacent_continuous and not adjacent_peak_episodes:
                continue

            def start_hour(candidate: dict[str, Any]) -> int:
                window = candidate.get("time_window", {})
                try:
                    return int(window.get("forecast_hour_start", 10**9))
                except (TypeError, ValueError):
                    return 10**9

            later = max((first, second), key=start_hour)
            later_id = str(later.get("candidate_id") or later.get("candidate_name") or "<unknown>")
            other = first if later is second else second
            other_id = str(other.get("candidate_id") or other.get("candidate_name") or "<unknown>")
            notes[later_id].append(
                f"인접 감시후보와 연속성 검토(중첩={overlap:.2f}, 시간간격={time_gap}시간, 핵심시각 간격={peak_gap}시간)"
            )
    return dict(notes)


def _apply_candidate_discovery_meteorological_qc(
    response: dict[str, Any],
    *,
    overview_data: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Attach deterministic QC as one bounded uncertainty per affected candidate.

    The candidate geometry, time window, peak hours, and merge/split decisions are
    never changed here.  The note is intentionally stored in the existing
    ``uncertainties`` field so it survives Step04 merge and becomes available to
    downstream detailed analysis without introducing a new operational artifact.
    """
    if not isinstance(response, dict):
        return response, {}
    normalized = copy.deepcopy(response)
    candidates = [item for item in normalized.get("candidates", []) if isinstance(item, dict)]
    qc_notes = _candidate_discovery_meteorological_qc_notes(
        candidates,
        overview_data=overview_data,
    )
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or candidate.get("candidate_name") or "<unknown>")
        pieces = qc_notes.get(candidate_id, [])
        if not pieces:
            continue
        suffix = ". 후속 상세분석에서 AI가 핵심 시각과 후보 병합·분리 여부를 재검토합니다."
        body = "정량 점검 참고: " + "; ".join(dict.fromkeys(pieces))
        body_budget = max(0, 160 - len(suffix))
        summary = body[:body_budget].rstrip(" ;,") + suffix
        uncertainties = [
            str(item) for item in candidate.get("uncertainties", []) if str(item).strip()
        ]
        if summary not in uncertainties:
            if len(uncertainties) >= 3:
                # Candidate discovery prompt is limited to two AI uncertainty rows,
                # leaving the third slot for deterministic QC.  Preserve earlier AI
                # text rather than silently replacing it if a provider violates that
                # compactness request.
                logging.warning(
                    "step04 meteorological QC could not attach candidate=%s because uncertainties is full notes=%s",
                    candidate_id,
                    pieces,
                )
                continue
            uncertainties.append(summary)
            candidate["uncertainties"] = uncertainties
        logging.info(
            "step04 meteorological QC advisory candidate=%s notes=%s",
            candidate_id,
            pieces,
        )
    return normalized, qc_notes


def validate_final_response(
    response: dict[str, Any],
    *,
    schema: dict[str, Any],
    package_id: str,
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    feature_catalog: dict[str, Any],
    operational_region_catalog: dict[str, Any],
    require_system_direct_precipitation_anchor: bool = False,
) -> list[str]:
    errors = _validate_jsonschema(response, schema)
    attachment_by_id = _attachment_by_id(attachment_manifest)
    attachment_ids = set(attachment_by_id)
    supported_products = {
        str(item.get("product_id")): {int(value) for value in item.get("allowed_forecast_hours", [])}
        for item in support_catalog.get("spatial_field_products", [])
        if isinstance(item, dict) and item.get("product_id")
    }
    diagnostic_execution_support = support_catalog.get("diagnostic_execution_support", {})
    if not isinstance(diagnostic_execution_support, dict):
        diagnostic_execution_support = {}
    known_features = _feature_ids(feature_catalog)
    known_regions = {
        str(item.get("region_id"))
        for item in operational_region_catalog.get("regions", [])
        if isinstance(item, dict) and item.get("region_id")
    }
    plan_support = support_catalog.get("analysis_plan_support", {})
    supported_analysis_methods = {
        str(item) for item in plan_support.get("supported_analysis_methods", [])
    }
    required_hazard_outputs = {
        str(item) for item in plan_support.get("required_hazard_outputs", [])
    }

    if response.get("package_id") != package_id:
        errors.append("package_id does not match Step04 input package")
    coverage = response.get("analysis_coverage", {})
    if isinstance(coverage, dict):
        if int(coverage.get("overview_attachment_count", -1)) != len(attachment_ids):
            errors.append("analysis_coverage.overview_attachment_count mismatch")
        if coverage.get("coverage_status") != "complete":
            errors.append("analysis_coverage.coverage_status must be complete after all batches passed")

    candidates = response.get("candidates", [])
    candidate_ids = [str(item.get("candidate_id")) for item in candidates if isinstance(item, dict)]
    candidate_id_set = set(candidate_ids)
    if len(candidate_ids) != len(candidate_id_set):
        errors.append("candidate_id values must be unique")
    _log_step04_advisories(
        "final.precursor_evidence",
        _precursor_candidate_evidence_advisories(
            [item for item in candidates if isinstance(item, dict)],
            attachment_manifest=attachment_manifest,
        ),
    )
    if "precursor_candidate_assessment" in response:
        try:
            _validate_precursor_candidate_assessment(
                response.get("precursor_candidate_assessment"),
                candidates=[item for item in candidates if isinstance(item, dict)],
                attachment_manifest=attachment_manifest,
            )
        except ValueError as exc:
            errors.append(str(exc))
        else:
            _log_step04_advisories(
                "final.precursor_assessment",
                _precursor_candidate_assessment_advisories(
                    response.get("precursor_candidate_assessment"),
                    candidates=[item for item in candidates if isinstance(item, dict)],
                    attachment_manifest=attachment_manifest,
                ),
            )
    summary = response.get("run_summary", {})
    if isinstance(summary, dict) and int(summary.get("candidate_count", -1)) != len(candidates):
        errors.append("run_summary.candidate_count mismatch")

    discovery_orders = [
        int(item.get("discovery_order"))
        for item in candidates
        if isinstance(item, dict) and item.get("discovery_order") is not None
    ]
    if len(discovery_orders) != len(set(discovery_orders)):
        errors.append("discovery_order values must be unique")
    if discovery_orders and sorted(discovery_orders) != list(range(1, len(discovery_orders) + 1)):
        errors.append("discovery_order values must be contiguous from 1")

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id"))
        related_assets = [str(value) for value in candidate.get("related_overview_asset_ids", [])]
        unknown = sorted(set(related_assets) - attachment_ids)
        if unknown:
            errors.append(f"{candidate_id} references unknown overview assets: {unknown}")
        related_products = {str(value) for value in candidate.get("related_product_ids", [])}
        unknown_products = sorted(related_products - set(supported_products))
        if unknown_products:
            errors.append(f"{candidate_id} references unsupported product IDs: {unknown_products}")
        actual_products = {
            str(attachment_by_id[asset_id].get("product_id"))
            for asset_id in related_assets
            if asset_id in attachment_by_id
        }
        if not actual_products.issubset(related_products):
            errors.append(f"{candidate_id} related_product_ids do not cover referenced assets")
        area = candidate.get("candidate_area", {})
        if isinstance(area, dict):
            west = float(area.get("west", 0.0))
            east = float(area.get("east", 0.0))
            south = float(area.get("south", 0.0))
            north = float(area.get("north", 0.0))
            if west >= east or south >= north:
                errors.append(f"{candidate_id} candidate_area bbox ordering is invalid")
            if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
                errors.append(f"{candidate_id} candidate_area longitude is invalid")
            if not (-90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
                errors.append(f"{candidate_id} candidate_area latitude is invalid")
        window = candidate.get("time_window", {})
        if isinstance(window, dict):
            start = int(window.get("forecast_hour_start", -1))
            end = int(window.get("forecast_hour_end", -1))
            if start > end:
                errors.append(f"{candidate_id} time_window start exceeds end")
            for hour in candidate.get("peak_forecast_hours", []):
                if int(hour) < start or int(hour) > end:
                    errors.append(f"{candidate_id} peak hour {hour} is outside time_window")
        expected_unit_type = _candidate_unit_type(candidate)
        if candidate.get("candidate_unit_type") != expected_unit_type:
            errors.append(
                f"{candidate_id} candidate_unit_type must be {expected_unit_type!r} "
                f"for detection_basis={candidate.get('detection_basis')!r}"
            )
        _log_step04_advisories(
            "candidate_discovery.signal_classification",
            [f"{candidate_id} {signal_error}" for signal_error in validate_candidate_signal_contract(candidate)],
        )

    plans = response.get("candidate_analysis_plans", [])
    plan_candidate_ids = [str(item.get("candidate_id")) for item in plans if isinstance(item, dict)]
    if Counter(plan_candidate_ids) != Counter(candidate_ids):
        errors.append("candidate_analysis_plans must contain exactly one plan for each candidate")
    plan_ids: list[str] = []
    plan_by_id: dict[str, dict[str, Any]] = {}
    check_index: dict[str, tuple[str, dict[str, Any]]] = {}
    target_index: dict[str, tuple[str, dict[str, Any]]] = {}
    phase_index: dict[str, tuple[str, dict[str, Any]]] = {}
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        candidate_id = str(plan.get("candidate_id"))
        plan_id = str(plan.get("plan_id"))
        plan_ids.append(plan_id)
        if candidate_id not in candidate_id_set:
            errors.append(f"{plan_id} references unknown candidate_id={candidate_id}")
        if plan_id != candidate_id.replace("CAND_", "PLAN_CAND"):
            errors.append(f"{plan_id} must deterministically match candidate_id={candidate_id}")
        if plan_id in plan_by_id:
            errors.append(f"duplicate analysis plan_id: {plan_id}")
        plan_by_id[plan_id] = plan
        candidate = next((item for item in candidates if str(item.get("candidate_id")) == candidate_id), {})
        candidate_bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))

        target_ids: list[str] = []
        target_types: set[str] = set()
        for target in plan.get("target_areas", []):
            if not isinstance(target, dict):
                continue
            target_id = str(target.get("target_id"))
            target_ids.append(target_id)
            target_types.add(str(target.get("target_type")))
            if target_id in target_index:
                errors.append(f"duplicate target_id: {target_id}")
            target_index[target_id] = (plan_id, target)
            region_ids = {str(value) for value in target.get("region_ids", [])}
            unknown_regions = sorted(region_ids - known_regions)
            if unknown_regions:
                errors.append(f"{target_id} references unknown operational regions: {unknown_regions}")
            resolution_method = str(target.get("resolution_method"))
            target_bbox = _normalized_bbox_for_validation(target.get("requested_domain"))
            if resolution_method == "explicit_bbox" and target_bbox is None:
                errors.append(f"{target_id} explicit_bbox requires a valid requested_domain")
            if resolution_method == "explicit_region_ids" and not region_ids:
                errors.append(f"{target_id} explicit_region_ids requires region_ids")
            target_type = str(target.get("target_type"))
            if target_type == "candidate_domain":
                if resolution_method != "explicit_bbox":
                    errors.append(f"{target_id} candidate_domain requires explicit_bbox")
                if candidate_bbox is not None and target_bbox != candidate_bbox:
                    errors.append(f"{target_id} candidate_domain bbox must equal candidate_area")
            if target_type == "context_domain":
                if resolution_method != "explicit_bbox":
                    errors.append(f"{target_id} context_domain requires explicit_bbox")
                if candidate_bbox is not None and target_bbox is not None and not _bbox_contains(target_bbox, candidate_bbox):
                    errors.append(f"{target_id} context_domain must contain candidate_area")
            if target_type == "operational_regions" and resolution_method not in {
                "candidate_area_overlap", "explicit_region_ids"
            }:
                errors.append(f"{target_id} operational_regions has invalid resolution_method")
            if (
                target_type == "operational_regions"
                and resolution_method == "candidate_area_overlap"
                and candidate_bbox is not None
            ):
                try:
                    selected_regions = _selectable_candidate_operational_regions(
                        candidate_bbox=candidate_bbox,
                        operational_region_catalog=operational_region_catalog,
                    )
                except (TypeError, ValueError) as exc:
                    errors.append(f"{target_id} operational region resolution failed: {exc}")
                else:
                    if not selected_regions:
                        errors.append(
                            _operational_region_target_empty_error(
                                candidate_id=candidate_id,
                                candidate_bbox=candidate_bbox,
                            )
                        )
        if len(target_ids) != len(set(target_ids)):
            errors.append(f"{plan_id} target_id values must be unique")
        if "candidate_domain" not in target_types:
            errors.append(f"{plan_id} must include candidate_domain target")

        phase_ids: list[str] = []
        peak_phase_hours: set[int] = set()
        for phase in plan.get("analysis_phases", []):
            if not isinstance(phase, dict):
                continue
            phase_id = str(phase.get("phase_id"))
            phase_ids.append(phase_id)
            if phase_id in phase_index:
                errors.append(f"duplicate phase_id: {phase_id}")
            phase_index[phase_id] = (plan_id, phase)
            if str(phase.get("phase_type")) == "peak":
                peak_phase_hours.update(int(value) for value in phase.get("forecast_hours", []))
        if len(phase_ids) != len(set(phase_ids)):
            errors.append(f"{plan_id} phase_id values must be unique")
        candidate_peak_hours = {int(value) for value in candidate.get("peak_forecast_hours", [])}
        if not candidate_peak_hours.issubset(peak_phase_hours):
            errors.append(f"{plan_id} peak phase does not cover candidate peak_forecast_hours")
        plan_phase_hours = {
            int(value)
            for phase in plan.get("analysis_phases", [])
            if isinstance(phase, dict)
            for value in phase.get("forecast_hours", [])
        }
        errors.extend(
            f"{plan_id} {message}"
            for message in _validate_phase_timeline(
                [item for item in plan.get("analysis_phases", []) if isinstance(item, dict)],
                available_hours=plan_phase_hours,
                candidate_peak_hours=candidate_peak_hours,
            )
        )

        checks = [item for item in plan.get("variable_checks", []) if isinstance(item, dict)]
        check_ids = [str(item.get("check_id")) for item in checks]
        if len(check_ids) != len(set(check_ids)):
            errors.append(f"{plan_id} check_id values must be unique")
        roles = {str(item.get("variable_role")) for item in checks}
        modern_plan = any(
            isinstance(item, dict) and item.get("diagnostic_id")
            for item in response.get("analysis_data_requests", [])
        )
        if "hazard_magnitude" not in roles and not modern_plan:
            errors.append(f"{plan_id} requires a hazard_magnitude check")
        explanatory = roles & {"moisture_supply", "low_level_forcing", "midlevel_forcing", "synoptic_support"}
        if len(explanatory) < 2 and not modern_plan:
            errors.append(f"{plan_id} requires at least two explanatory variable roles")
        for check in checks:
            check_id = str(check.get("check_id"))
            if check_id in check_index:
                errors.append(f"duplicate check_id: {check_id}")
            check_index[check_id] = (plan_id, check)
            product_id = check.get("product_id")
            if product_id is not None and str(product_id) not in supported_products:
                errors.append(f"{check_id} references unsupported product_id={product_id}")
            diagnostic_contract = diagnostic_execution_support.get(
                str(check.get("diagnostic_id", "")), {}
            )
            compatible_products = {
                str(value)
                for value in diagnostic_contract.get("compatible_product_ids", [])
            } if isinstance(diagnostic_contract, dict) else set()
            if product_id is not None and compatible_products and str(product_id) not in compatible_products:
                errors.append(
                    f"{check_id} product_id={product_id} is incompatible with "
                    f"diagnostic_id={check.get('diagnostic_id')!r}"
                )
            check_scope = str(check.get("check_scope") or "")
            required_components = [
                str(value) for value in check.get("required_signal_components", [])
                if str(value)
            ]
            if check_scope not in CHECK_SCOPES:
                errors.append(f"{check_id} has invalid check_scope={check_scope!r}")
            if not required_components or any(value not in SIGNAL_COMPONENTS for value in required_components):
                errors.append(f"{check_id} has invalid required_signal_components={required_components}")
            diagnostic_id = str(check.get("diagnostic_id") or "")
            methods = {str(value) for value in check.get("analysis_methods", [])}
            errors.extend(
                _check_scope_contract_errors(
                    check_key=check_id,
                    diagnostic_id=diagnostic_id,
                    analysis_methods=methods,
                    check_scope=check_scope,
                    required_signal_components=required_components,
                )
            )
            try:
                _validate_same_check_narrative_contract({
                    "check_key": check_id,
                    "check_scope": check_scope,
                    "required_signal_components": required_components,
                    "expected_signal": check.get("expected_signal"),
                    "diagnostic_id": diagnostic_id,
                    "analysis_methods": list(methods),
                })
            except ValueError as exc:
                errors.append(str(exc))
            product_components = (
                _product_signal_components_from_support(str(product_id), support_catalog)
                if product_id is not None else set()
            )
            if product_components and not set(required_components).issubset(product_components):
                errors.append(
                    f"{check_id} required_signal_components={required_components} cannot be provided by product_id={product_id!r}"
                )
            feature_ids = {str(value) for value in check.get("feature_ids", [])}
            unknown_features = sorted(feature_ids - known_features)
            if unknown_features:
                errors.append(f"{check_id} references unsupported feature_ids={unknown_features}")
            unknown_targets = sorted(set(str(value) for value in check.get("target_ids", [])) - set(target_ids))
            if unknown_targets:
                errors.append(f"{check_id} references unknown target_ids={unknown_targets}")
            unknown_phases = sorted(set(str(value) for value in check.get("phase_ids", [])) - set(phase_ids))
            if unknown_phases:
                errors.append(f"{check_id} references unknown phase_ids={unknown_phases}")
            referenced_hours = {
                int(hour)
                for phase_id in check.get("phase_ids", [])
                if str(phase_id) in phase_index
                for hour in phase_index[str(phase_id)][1].get("forecast_hours", [])
            }
            minimum_hours = _minimum_forecast_hours_for_check(diagnostic_id, check_scope)
            if len(referenced_hours) < minimum_hours:
                errors.append(
                    f"{check_id} has insufficient forecast-hour coverage "
                    f"required={minimum_hours} actual={len(referenced_hours)}"
                )
            diagnostic_id = str(check.get("diagnostic_id", ""))
            normalized_methods = _canonical_analysis_methods_for_diagnostic(
                diagnostic_id,
                check.get("analysis_methods", []),
            )
            check["analysis_methods"] = normalized_methods
            methods = set(normalized_methods)
            unsupported_methods = sorted(methods - supported_analysis_methods)
            if unsupported_methods:
                errors.append(f"{check_id} uses unsupported analysis_methods={unsupported_methods}")
            if diagnostic_id not in OPERATIONAL_DIAGNOSTIC_IDS:
                errors.append(f"{check_id} has unsupported diagnostic_id={diagnostic_id!r}")
            canonical_variable_role = _canonical_variable_role_for_diagnostic(
                diagnostic_id, str(check.get("variable_role"))
            )
            check["variable_role"] = canonical_variable_role
            required_roles_for_diagnostic = DIAGNOSTIC_REQUIRED_VARIABLE_ROLES.get(diagnostic_id)
            if (
                required_roles_for_diagnostic is not None
                and canonical_variable_role not in required_roles_for_diagnostic
            ):
                errors.append(
                    f"{check_id} has incompatible diagnostic_id/variable_role "
                    f"diagnostic_id={diagnostic_id!r} variable_role={check.get('variable_role')!r}"
                )
            raw_cross_section_line = check.get("cross_section_line")
            cross_section_line = _normalized_cross_section_line(raw_cross_section_line)
            cross_section_strategy = _normalized_cross_section_strategy(check.get("cross_section_strategy"))
            reference_band_axis = _normalized_reference_band_axis(check.get("reference_band_axis"))
            if diagnostic_id == "vertical_cross_section":
                if cross_section_line is None:
                    errors.append(f"{check_id} vertical_cross_section requires cross_section_line")
                if cross_section_strategy is None:
                    errors.append(f"{check_id} vertical_cross_section requires cross_section_strategy")
                if reference_band_axis is None:
                    errors.append(f"{check_id} vertical_cross_section requires reference_band_axis")
                else:
                    selected_targets = [
                        target_index.get(str(target_id), (None, {}))[1]
                        for target_id in check.get("target_ids", [])
                    ]
                    spatial_targets = [
                        target for target in selected_targets
                        if str(target.get("target_type")) in {"candidate_domain", "context_domain"}
                    ]
                    selected_bbox = (
                        _normalized_bbox_for_validation(spatial_targets[0].get("requested_domain"))
                        if spatial_targets else None
                    )
                    candidate_bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
                    endpoints = (
                        (cross_section_line["start_longitude"], cross_section_line["start_latitude"]),
                        (cross_section_line["end_longitude"], cross_section_line["end_latitude"]),
                    )
                    if selected_bbox is None or any(
                        not _point_in_bbox(lon, lat, selected_bbox) for lon, lat in endpoints
                    ):
                        errors.append(
                            f"{check_id} vertical_cross_section endpoints must be inside selected target"
                        )
                    if candidate_bbox is None or not _segment_intersects_bbox(
                        cross_section_line["start_longitude"],
                        cross_section_line["start_latitude"],
                        cross_section_line["end_longitude"],
                        cross_section_line["end_latitude"],
                        candidate_bbox,
                    ):
                        errors.append(f"{check_id} vertical_cross_section must intersect candidate_area")
            elif raw_cross_section_line is not None:
                errors.append(
                    f"{check_id} cross_section_line is only valid for vertical_cross_section"
                )
            if (
                not modern_plan
                and str(check.get("variable_role")) == "hazard_magnitude"
                and not required_hazard_outputs.issubset(methods)
            ):
                errors.append(f"{check_id} hazard_magnitude methods do not cover required outputs")

        referenced_phase_ids = {
            str(value)
            for check in checks
            for value in check.get("phase_ids", [])
        }
        for phase in plan.get("analysis_phases", []):
            if not isinstance(phase, dict):
                continue
            phase_id = str(phase.get("phase_id"))
            if phase_id not in referenced_phase_ids:
                errors.append(
                    f"{plan_id} contains unreferenced phase_id={phase_id} "
                    f"phase_type={phase.get('phase_type')!r}"
                )

        if any(
            {str(method) for method in check.get("analysis_methods", [])} & REGIONAL_ANALYSIS_METHODS
            for check in checks
        ) and "operational_regions" not in target_types:
            errors.append(f"{plan_id} regional analysis methods require operational_regions target")

        errors.extend(_hypothesis_check_role_errors(plan.get("hypotheses", [])))
        for hypothesis in plan.get("hypotheses", []):
            if not isinstance(hypothesis, dict):
                continue
            refs = {
                str(value)
                for key in ("supporting_check_ids", "opposing_check_ids")
                for value in hypothesis.get(key, [])
            }
            unknown_checks = sorted(refs - set(check_ids))
            if unknown_checks:
                errors.append(f"{plan_id} hypothesis references unknown check_ids={unknown_checks}")
        errors.extend(f"{plan_id} {message}" for message in _unreferenced_variable_check_errors(plan))
    if len(plan_ids) != len(set(plan_ids)):
        errors.append("analysis plan_id values must be unique")

    evidence_groups = response.get("candidate_evidence", [])
    evidence_candidate_ids = [
        str(group.get("candidate_id")) for group in evidence_groups if isinstance(group, dict)
    ]
    if Counter(evidence_candidate_ids) != Counter(candidate_ids):
        errors.append("candidate_evidence must contain exactly one group for each candidate")
    for group in evidence_groups:
        if not isinstance(group, dict):
            continue
        candidate_id = str(group.get("candidate_id"))
        if candidate_id not in candidate_id_set:
            errors.append(f"candidate_evidence references unknown candidate_id: {candidate_id}")
        for item in group.get("evidence_items", []):
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get("asset_id"))
            source = attachment_by_id.get(asset_id)
            if source is None:
                errors.append(f"candidate evidence references unknown asset_id: {asset_id}")
                continue
            if item.get("product_id") != source.get("product_id"):
                errors.append(f"candidate evidence product mismatch asset_id={asset_id}")
            allowed_hours = {int(value) for value in source.get("forecast_hours", [])}
            invalid_hours = sorted({int(value) for value in item.get("forecast_hours", [])} - allowed_hours)
            if invalid_hours:
                errors.append(f"candidate evidence has invalid forecast hours asset_id={asset_id}: {invalid_hours}")

    request_ids: list[str] = []
    for request in response.get("analysis_data_requests", []):
        if not isinstance(request, dict):
            continue
        request_id = str(request.get("request_id"))
        request_ids.append(request_id)
        candidate_id = str(request.get("candidate_id"))
        if candidate_id not in candidate_id_set:
            errors.append(f"{request_id} references unknown candidate_id={candidate_id}")
        plan_id = str(request.get("plan_id", ""))
        check_id = str(request.get("check_id", ""))
        raw_source_check_ids = request.get("source_check_ids")
        source_check_ids = (
            [str(value) for value in raw_source_check_ids]
            if isinstance(raw_source_check_ids, list) and raw_source_check_ids
            else [check_id]
        )
        source_check_ids = list(dict.fromkeys(value for value in source_check_ids if value))
        if check_id not in source_check_ids:
            errors.append(f"{request_id} source_check_ids must include primary check_id={check_id}")
        target_id = str(request.get("target_id", ""))
        phase_ids = {str(value) for value in request.get("phase_ids", [])}
        methods = {str(value) for value in request.get("analysis_methods", [])}
        diagnostic_id = str(request.get("diagnostic_id", ""))
        if diagnostic_id not in OPERATIONAL_DIAGNOSTIC_IDS:
            errors.append(f"{request_id} has unsupported diagnostic_id={diagnostic_id}")
        plan = plan_by_id.get(plan_id)
        if plan is None or str(plan.get("candidate_id")) != candidate_id:
            errors.append(f"{request_id} has invalid plan_id={plan_id}")
        linked_checks: list[dict[str, Any]] = []
        for linked_check_id in source_check_ids:
            check_pair = check_index.get(linked_check_id)
            if check_pair is None or check_pair[0] != plan_id:
                errors.append(f"{request_id} has invalid source_check_id={linked_check_id}")
                continue
            linked_checks.append(check_pair[1])
        target_pair = target_index.get(target_id)
        if target_pair is None or target_pair[0] != plan_id:
            errors.append(f"{request_id} has invalid target_id={target_id}")
        unknown_phase_refs = sorted(
            phase_id for phase_id in phase_ids
            if phase_id not in phase_index or phase_index[phase_id][0] != plan_id
        )
        if unknown_phase_refs:
            errors.append(f"{request_id} has invalid phase_ids={unknown_phase_refs}")
        if linked_checks:
            primary_pair = check_index.get(check_id)
            primary_check = (
                primary_pair[1]
                if primary_pair is not None and primary_pair[0] == plan_id
                else None
            )
            if primary_check is None:
                errors.append(f"{request_id} has invalid primary check_id={check_id}")
            elif diagnostic_id != str(primary_check.get("diagnostic_id", "")):
                errors.append(
                    f"{request_id} diagnostic_id does not match primary check_id={check_id}"
                )
            if any(
                target_id not in {str(value) for value in check.get("target_ids", [])}
                for check in linked_checks
            ):
                errors.append(
                    f"{request_id} target_id is not allowed by all source_check_ids={source_check_ids}"
                )
            allowed_phase_ids = {
                str(value) for check in linked_checks for value in check.get("phase_ids", [])
            }
            if not phase_ids.issubset(allowed_phase_ids):
                errors.append(
                    f"{request_id} phase_ids are not covered by source_check_ids={source_check_ids}"
                )
            allowed_methods = {
                str(value) for check in linked_checks for value in check.get("analysis_methods", [])
            }
            if not methods.issubset(allowed_methods):
                errors.append(
                    f"{request_id} analysis_methods are not covered by source_check_ids={source_check_ids}"
                )
        hours = {int(value) for value in request.get("forecast_hours", [])}
        allowed_phase_hours = {
            int(value)
            for phase_id in phase_ids
            for value in phase_index.get(phase_id, ("", {}))[1].get("forecast_hours", [])
        }
        if not hours.issubset(allowed_phase_hours):
            errors.append(f"{request_id} forecast_hours are not covered by phase_ids")
        if request.get("data_type") == "spatial_field":
            product_id = str(request.get("product_id"))
            request_bbox = _normalized_bbox_for_validation(request.get("requested_domain"))
            target = target_pair[1] if target_pair is not None and target_pair[0] == plan_id else {}
            target_bbox = _normalized_bbox_for_validation(target.get("requested_domain"))
            candidate = next((item for item in candidates if str(item.get("candidate_id")) == candidate_id), {})
            candidate_bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
            if request_bbox is None:
                errors.append(f"{request_id} spatial_field requires a valid requested_domain")
            if str(target.get("target_type")) in {"candidate_domain", "context_domain"}:
                expected_bbox = target_bbox or candidate_bbox
                if expected_bbox is not None and request_bbox != expected_bbox:
                    errors.append(f"{request_id} requested_domain must equal selected spatial target")
            if linked_checks and any(
                check.get("product_id") is not None
                and product_id != str(check.get("product_id"))
                for check in linked_checks
            ):
                errors.append(
                    f"{request_id} product_id does not match source_check_ids={source_check_ids}"
                )
            request_line = _cross_section_line_signature(request.get("cross_section_line"))
            if diagnostic_id == "vertical_cross_section":
                if request_line is None:
                    errors.append(f"{request_id} vertical_cross_section requires cross_section_line")
                if linked_checks and any(
                    request_line != _cross_section_line_signature(check.get("cross_section_line"))
                    for check in linked_checks
                ):
                    errors.append(
                        f"{request_id} cross_section_line does not match source_check_ids={source_check_ids}"
                    )
            elif request.get("cross_section_line") is not None:
                errors.append(
                    f"{request_id} cross_section_line is only valid for vertical_cross_section"
                )
            if product_id not in supported_products:
                errors.append(f"{request_id} uses unsupported product_id={product_id}")
            else:
                invalid = sorted(hours - supported_products[product_id])
                if invalid:
                    errors.append(f"{request_id} uses unsupported forecast_hours={invalid}")
        elif request.get("data_type") == "quantitative_excerpt":
            requested_features = {str(value) for value in request.get("feature_ids", [])}
            allowed_features = {
                str(value) for check in linked_checks for value in check.get("feature_ids", [])
            }
            if linked_checks and not requested_features.issubset(allowed_features):
                errors.append(
                    f"{request_id} feature_ids are not covered by source_check_ids={source_check_ids}"
                )
            invalid_features = sorted(requested_features - known_features)
            if invalid_features:
                errors.append(f"{request_id} uses unsupported feature_ids={invalid_features}")
    physical_request_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for item in response.get("analysis_data_requests", []):
        if isinstance(item, dict):
            physical_request_groups[_analysis_request_merge_key(item)].append(item)
    for duplicate_rows in physical_request_groups.values():
        if len(duplicate_rows) <= 1:
            continue
        exclusive_anchor_rows = []
        for item in duplicate_rows:
            source_ids = item.get("source_check_ids")
            source_ids = [str(value) for value in source_ids] if isinstance(source_ids, list) else []
            if len(source_ids) != 1:
                continue
            linked = check_index.get(source_ids[0])
            if linked is not None and _is_system_direct_precipitation_anchor_check(linked[1]):
                exclusive_anchor_rows.append(item)
        # The SYSTEM presence anchor intentionally duplicates one physical field
        # while retaining an independent request/evidence identity.  No other
        # duplicate physical-output pattern is permitted.
        if len(duplicate_rows) == 2 and len(exclusive_anchor_rows) == 1:
            continue
        errors.append(
            "analysis_data_requests contains duplicate physical outputs; merge forecast hours, "
            "methods, and source_check_ids"
        )
    if require_system_direct_precipitation_anchor:
        errors.extend(system_direct_precipitation_anchor_final_errors(response))
    requests_by_check: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for request in response.get("analysis_data_requests", []):
        if not isinstance(request, dict):
            continue
        raw_source_check_ids = request.get("source_check_ids")
        source_check_ids = (
            [str(value) for value in raw_source_check_ids]
            if isinstance(raw_source_check_ids, list) and raw_source_check_ids
            else [str(request.get("check_id", ""))]
        )
        for linked_check_id in dict.fromkeys(source_check_ids):
            requests_by_check[linked_check_id].append(request)
    for check_id, (_plan_id, check) in check_index.items():
        matching = requests_by_check.get(check_id, [])
        if not matching:
            errors.append(f"{check_id} is not implemented by analysis_data_requests")
        if str(check.get("priority")) == "required" and not any(
            str(item.get("priority")) == "required" for item in matching
        ):
            errors.append(f"{check_id} required check has no required request")
        if str(check.get("variable_role")) == "hazard_magnitude" and not any(
            isinstance(item, dict) and item.get("diagnostic_id") for item in matching
        ):
            types = {str(item.get("data_type")) for item in matching}
            if not {"spatial_field", "quantitative_excerpt"}.issubset(types):
                errors.append(f"{check_id} hazard_magnitude requires spatial and quantitative requests")
            implemented_methods = {
                str(method)
                for item in matching
                for method in item.get("analysis_methods", [])
            }
            if not required_hazard_outputs.issubset(implemented_methods):
                errors.append(f"{check_id} hazard requests do not implement all required outputs")
            quantitative = [item for item in matching if str(item.get("data_type")) == "quantitative_excerpt"]
            if quantitative:
                required_statistics = {"mean", "max", "p95", "max_location"}
                if not any(
                    required_statistics.issubset(
                        {str(value) for value in (item.get("output_spec") or {}).get("statistics", [])}
                    )
                    and bool((item.get("output_spec") or {}).get("include_time_series"))
                    and bool((item.get("output_spec") or {}).get("include_max_location"))
                    for item in quantitative
                ):
                    errors.append(f"{check_id} hazard quantitative request lacks required regional value outputs")

    if len(request_ids) != len(set(request_ids)):
        errors.append("request_id values must be unique")
    if candidates and not response.get("analysis_data_requests"):
        errors.append("analysis_data_requests must not be empty when candidates exist")
    for candidate in candidates:
        if not isinstance(candidate, dict) or str(candidate.get("detection_basis")) not in {"direct_precipitation", "mixed"}:
            continue
        candidate_id = str(candidate.get("candidate_id"))
        direct_requests = [
            item
            for item in response.get("analysis_data_requests", [])
            if isinstance(item, dict)
            and str(item.get("candidate_id")) == candidate_id
            and str(item.get("diagnostic_id")) in {"detailed_spatial_map", "accumulated_precipitation"}
            and "direct_precipitation" in _product_signal_components_from_support(
                str(item.get("product_id", "")), support_catalog
            )
        ]
        if not direct_requests:
            errors.append(f"{candidate_id} requires a direct precipitation confirmation diagnostic")
    if candidates and response.get("pass2_required") is not True:
        errors.append("pass2_required must be true when candidates exist")
    errors.extend(validate_step04_narrative_language(response))
    return errors


def _group_sort_key(tier: str, group_id: str) -> tuple[int, str, str]:
    return ({"temporal_core": 0, "synoptic_context": 1}.get(tier, 99), tier, group_id)


def group_summary_id(tier: str, group_id: str) -> str:
    return f"PASS1_SUMMARY_{tier.upper()}_{group_id.upper()}"


def _valid_time_range(items: list[dict[str, Any]]) -> dict[str, str | None]:
    values = sorted(
        {
            str(value)
            for item in items
            for value in item.get("valid_times_utc", [])
            if value is not None and str(value)
        }
    )
    return {"start_utc": values[0] if values else None, "end_utc": values[-1] if values else None}


def build_group_contexts(
    *,
    batch_observations: list[dict[str, Any]],
    batches: list[dict[str, Any]],
    attachment_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group observations by their validated tier and group fields, never filenames."""
    batches_by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    batch_by_id: dict[str, dict[str, Any]] = {}
    for batch in batches:
        batch_id = str(batch["batch_id"])
        if batch_id in batch_by_id:
            raise ValueError(f"duplicate batch_id in observation plan: {batch_id}")
        batch_by_id[batch_id] = batch
        batches_by_group[(str(batch.get("analysis_tier")), str(batch.get("group_id")))].append(batch)

    observations_by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for observation in batch_observations:
        key = (str(observation.get("analysis_tier")), str(observation.get("group_id")))
        batch_id = str(observation.get("batch_id"))
        batch = batch_by_id.get(batch_id)
        if batch is None:
            raise ValueError(f"observation references batch outside current plan: {batch_id}")
        if key != (str(batch.get("analysis_tier")), str(batch.get("group_id"))):
            raise ValueError(f"observation tier/group does not match current plan: {batch_id}")
        observations_by_group[key].append(observation)

    contexts: list[dict[str, Any]] = []
    for key in sorted(batches_by_group, key=lambda value: _group_sort_key(*value)):
        tier, group_id = key
        planned_batches = sorted(batches_by_group[key], key=lambda item: str(item["batch_id"]))
        observations = sorted(
            observations_by_group.get(key, []), key=lambda item: str(item.get("batch_id"))
        )
        expected_batch_ids = [str(item["batch_id"]) for item in planned_batches]
        actual_batch_ids = [str(item.get("batch_id")) for item in observations]
        if Counter(actual_batch_ids) != Counter(expected_batch_ids):
            raise ValueError(
                f"group observation batch coverage mismatch tier={tier} group={group_id} "
                f"expected={expected_batch_ids} actual={actual_batch_ids}"
            )
        expected_asset_ids = [
            str(asset_id) for batch in planned_batches for asset_id in batch["attachment_ids"]
        ]
        source_items = [attachment_by_id[asset_id] for asset_id in expected_asset_ids]
        source_rows: list[dict[str, Any]] = []
        for observation in observations:
            batch_id = str(observation["batch_id"])
            rows = observation.get("attachment_observations", [])
            if not isinstance(rows, list):
                raise ValueError(f"observation attachment_observations is invalid: {batch_id}")
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError(f"observation row is invalid: {batch_id}")
                source_rows.append({"source_batch_id": batch_id, **row})
        actual_asset_ids = [str(row.get("asset_id")) for row in source_rows]
        if Counter(actual_asset_ids) != Counter(expected_asset_ids):
            raise ValueError(
                f"group observation asset coverage mismatch tier={tier} group={group_id} "
                f"expected={expected_asset_ids} actual={actual_asset_ids}"
            )
        contexts.append(
            {
                "summary_id": group_summary_id(tier, group_id),
                "analysis_tier": tier,
                "group_id": group_id,
                "source_batch_ids": expected_batch_ids,
                "source_asset_ids": expected_asset_ids,
                "source_product_ids": sorted({str(item.get("product_id")) for item in source_items}),
                "forecast_hours": sorted(
                    {int(hour) for item in source_items for hour in item.get("forecast_hours", [])}
                ),
                "valid_time_range": _valid_time_range(source_items),
                "source_observations": observations,
                "source_rows": source_rows,
                "asset_to_batch_id": {str(row["asset_id"]): str(row["source_batch_id"]) for row in source_rows},
                "attachment_by_id": {str(item["asset_id"]): item for item in source_items},
            }
        )
    return contexts


def _unique_nonempty_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _truncate_utf8_text(value: str, *, max_bytes: int) -> str:
    """Trim a narrative string without splitting a UTF-8 code point."""
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    suffix = "..."
    limit = max(0, max_bytes - len(suffix.encode("utf-8")))
    return encoded[:limit].decode("utf-8", errors="ignore").rstrip() + suffix


def _compact_group_summary_narratives(summary: dict[str, Any]) -> dict[str, Any]:
    """Keep mandatory group coverage while bounding verbose VLM narrative fields.

    A single observation batch already includes every asset in a group.  When
    its free-text descriptions exceed the group-summary transport budget, the
    old flow called the provider again for compaction.  That extra provider
    call is non-deterministic and prevents resume from reproducing the same
    candidate-discovery input.  This projection keeps every source asset,
    product, forecast hour, and one concise form of each narrative field.
    """
    if json_utf8_bytes(summary) <= GROUP_SUMMARY_TARGET_BYTES:
        return summary

    compact = json.loads(json.dumps(summary, ensure_ascii=False))
    compacted_field_count = 0
    rows = compact.get("integrated_observations", [])
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            signals = _unique_nonempty_strings(row.get("observed_signals", []))
            limited_signals = [
                _truncate_utf8_text(value, max_bytes=72)
                for value in signals[:2]
            ]
            compacted_field_count += max(0, len(signals) - len(limited_signals))
            row["observed_signals"] = limited_signals
            for field in ("spatial_pattern", "temporal_evolution"):
                original = str(row.get(field, ""))
                shortened = _truncate_utf8_text(original, max_bytes=112)
                if shortened != original:
                    compacted_field_count += 1
                row[field] = shortened
            limitations = _unique_nonempty_strings(row.get("limitations", []))
            limited_limitations = [
                _truncate_utf8_text(value, max_bytes=72)
                for value in limitations[:1]
            ]
            compacted_field_count += max(0, len(limitations) - len(limited_limitations))
            row["limitations"] = limited_limitations
            direct_signal = row.get("direct_precipitation_signal")
            if isinstance(direct_signal, dict):
                original = str(direct_signal.get("rationale", ""))
                shortened = _truncate_utf8_text(original, max_bytes=96)
                if shortened != original:
                    compacted_field_count += 1
                direct_signal["rationale"] = shortened

    if isinstance(rows, list):
        compact["direct_precipitation_audit"] = _direct_precipitation_audit_from_rows(
            [row for row in rows if isinstance(row, dict)]
        )

    for field, max_items, max_bytes in (
        ("cross_product_relationships", 1, 112),
        ("uncertainty", 1, 96),
    ):
        original_values = _unique_nonempty_strings(compact.get(field, []))
        compact_values = [
            _truncate_utf8_text(value, max_bytes=max_bytes)
            for value in original_values[:max_items]
        ]
        compacted_field_count += max(0, len(original_values) - len(compact_values))
        compact[field] = compact_values

    if compacted_field_count:
        compact["omitted_or_unresolved_signals"] = [
            "그룹 요약 용량 제한으로 반복 또는 장문 설명 일부를 축약했습니다."
        ]
    return compact


def build_deterministic_group_summary(
    *,
    package_id: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build a schema-complete summary when one validated batch already covers the group.

    With the normal eight-batch plan, each tier/group has exactly one validated
    multimodal observation containing every source asset in that group. Asking a
    second model call to reproduce the same deterministic identifiers and rows is
    both redundant and fragile. This builder preserves the validated observation
    content and only performs lossless structural projection.
    """

    integrated_observations: list[dict[str, Any]] = []
    for row in context.get("source_rows", []):
        if not isinstance(row, dict):
            continue
        integrated_observations.append(
            {
                "source_batch_id": str(row.get("source_batch_id", "")),
                "asset_id": str(row.get("asset_id", "")),
                "product_id": str(row.get("product_id", "")),
                "forecast_hours": [int(value) for value in row.get("forecast_hours", [])],
                "observed_signals": [str(value) for value in row.get("observed_signals", [])],
                "spatial_pattern": str(row.get("spatial_pattern", "")),
                "temporal_evolution": str(row.get("temporal_evolution", "")),
                "confidence": str(row.get("confidence", "medium")),
                "limitations": [str(value) for value in row.get("limitations", [])],
                "direct_precipitation_signal": _normalize_direct_precipitation_signal(
                    row.get("direct_precipitation_signal")
                ),
            }
        )

    cross_product_relationships = _unique_nonempty_strings(
        value
        for observation in context.get("source_observations", [])
        if isinstance(observation, dict)
        for value in observation.get("cross_product_synthesis", [])
    )
    uncertainty = _unique_nonempty_strings(
        value
        for observation in context.get("source_observations", [])
        if isinstance(observation, dict)
        for value in observation.get("batch_uncertainties", [])
    )

    summary = {
        "schema_version": GROUP_SUMMARY_SCHEMA_VERSION,
        "package_id": package_id,
        "summary_id": context["summary_id"],
        "analysis_tier": context["analysis_tier"],
        "group_id": context["group_id"],
        "source_batch_ids": list(context["source_batch_ids"]),
        "source_asset_ids": list(context["source_asset_ids"]),
        "source_product_ids": list(context["source_product_ids"]),
        "forecast_hours": list(context["forecast_hours"]),
        "valid_time_range": dict(context["valid_time_range"]),
        "direct_precipitation_audit": _direct_precipitation_audit_from_rows(integrated_observations),
        "integrated_observations": integrated_observations,
        "cross_product_relationships": cross_product_relationships,
        "uncertainty": uncertainty,
        "omitted_or_unresolved_signals": [],
    }
    return _compact_group_summary_narratives(summary)


def normalize_group_summary_contract(
    value: dict[str, Any],
    *,
    package_id: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Repair provider output against deterministic group identity and coverage.

    Provider output may flatten a single observation row or omit deterministic
    coverage arrays. Those fields are never model judgments, so they are rebuilt
    from the validated source context. Provider text is retained only for the
    descriptive fields of known assets and group-level synthesis arrays.
    """

    canonical = build_deterministic_group_summary(
        package_id=package_id,
        context=context,
    )
    if not isinstance(value, dict):
        return canonical

    provider_rows: list[dict[str, Any]] = []
    rows = value.get("integrated_observations")
    if isinstance(rows, list):
        provider_rows.extend(item for item in rows if isinstance(item, dict))
    if value.get("asset_id") is not None:
        provider_rows.append(value)
    provider_by_asset = {
        str(item.get("asset_id")): item
        for item in provider_rows
        if item.get("asset_id") is not None
    }

    merged_rows: list[dict[str, Any]] = []
    for source_row in canonical["integrated_observations"]:
        merged = dict(source_row)
        provider_row = provider_by_asset.get(str(source_row["asset_id"]))
        if provider_row is not None:
            if isinstance(provider_row.get("observed_signals"), list):
                merged["observed_signals"] = [
                    str(item) for item in provider_row["observed_signals"]
                ]
            for field in ("spatial_pattern", "temporal_evolution"):
                if isinstance(provider_row.get(field), str):
                    merged[field] = provider_row[field]
            if provider_row.get("confidence") in {"low", "medium", "high"}:
                merged["confidence"] = provider_row["confidence"]
            if isinstance(provider_row.get("limitations"), list):
                merged["limitations"] = [str(item) for item in provider_row["limitations"]]
        merged["direct_precipitation_signal"] = _normalize_direct_precipitation_signal(
            source_row.get("direct_precipitation_signal")
        )
        merged_rows.append(merged)
    canonical["integrated_observations"] = merged_rows
    canonical["direct_precipitation_audit"] = _direct_precipitation_audit_from_rows(merged_rows)

    for field in (
        "cross_product_relationships",
        "uncertainty",
        "omitted_or_unresolved_signals",
    ):
        candidate = value.get(field)
        if isinstance(candidate, list):
            canonical[field] = _unique_nonempty_strings(candidate)
    return canonical


def validate_group_summary(
    summary: dict[str, Any],
    *,
    schema: dict[str, Any],
    package_id: str,
    context: dict[str, Any],
    enforce_byte_budget: bool = True,
) -> list[str]:
    errors = _validate_jsonschema(summary, schema)
    expected_fixed = {
        "schema_version": GROUP_SUMMARY_SCHEMA_VERSION,
        "package_id": package_id,
        "summary_id": context["summary_id"],
        "analysis_tier": context["analysis_tier"],
        "group_id": context["group_id"],
    }
    for field, expected in expected_fixed.items():
        if summary.get(field) != expected:
            errors.append(f"{field} does not match group contract")
    expected_batch_ids = list(context["source_batch_ids"])
    expected_asset_ids = list(context["source_asset_ids"])
    expected_products = list(context["source_product_ids"])
    expected_hours = list(context["forecast_hours"])
    if Counter(str(value) for value in summary.get("source_batch_ids", [])) != Counter(expected_batch_ids):
        errors.append("source_batch_ids coverage mismatch")
    source_asset_ids = [str(value) for value in summary.get("source_asset_ids", [])]
    if Counter(source_asset_ids) != Counter(expected_asset_ids):
        errors.append("source_asset_ids coverage mismatch")
    if len(source_asset_ids) != len(set(source_asset_ids)):
        errors.append("source_asset_ids contains duplicates")
    if sorted(str(value) for value in summary.get("source_product_ids", [])) != expected_products:
        errors.append("source_product_ids mismatch")
    if sorted(int(value) for value in summary.get("forecast_hours", [])) != expected_hours:
        errors.append("forecast_hours mismatch")
    if summary.get("valid_time_range") != context["valid_time_range"]:
        errors.append("valid_time_range mismatch")
    rows = summary.get("integrated_observations", [])
    if isinstance(rows, list):
        integrated_asset_ids = [str(item.get("asset_id")) for item in rows if isinstance(item, dict)]
        if Counter(integrated_asset_ids) != Counter(expected_asset_ids):
            errors.append("integrated_observations asset coverage mismatch")
        for item in rows:
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get("asset_id"))
            source = context["attachment_by_id"].get(asset_id)
            if source is None:
                errors.append(f"integrated_observations has unexpected asset_id: {asset_id}")
                continue
            if item.get("source_batch_id") != context["asset_to_batch_id"].get(asset_id):
                errors.append(f"source_batch_id mismatch for {asset_id}")
            if item.get("product_id") != source.get("product_id"):
                errors.append(f"product_id mismatch for {asset_id}")
            if [int(value) for value in item.get("forecast_hours", [])] != [
                int(value) for value in source.get("forecast_hours", [])
            ]:
                errors.append(f"forecast_hours mismatch for {asset_id}")
            errors.extend(
                _validate_direct_precipitation_signal_for_asset(
                    item.get("direct_precipitation_signal"),
                    product_id=str(source.get("product_id")),
                    allowed_hours=source.get("forecast_hours", []),
                    path=f"integrated_observations[{asset_id}].direct_precipitation_signal",
                )
            )
    expected_audit = _direct_precipitation_audit_from_rows(
        [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []
    )
    if summary.get("direct_precipitation_audit") != expected_audit:
        errors.append("direct_precipitation_audit does not match integrated precipitation observation")
    if enforce_byte_budget and json_utf8_bytes(summary) > GROUP_SUMMARY_MAX_BYTES:
        errors.append(
            f"group summary exceeds {GROUP_SUMMARY_MAX_BYTES} UTF-8 bytes: {json_utf8_bytes(summary)}"
        )
    return errors


def build_group_summary_messages(
    *,
    prompt_template: str,
    package_id: str,
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    group_input = {
        "schema_version": GROUP_SUMMARY_SCHEMA_VERSION,
        "package_id": package_id,
        "summary_id": context["summary_id"],
        "analysis_tier": context["analysis_tier"],
        "group_id": context["group_id"],
        "required_source_batch_ids": context["source_batch_ids"],
        "required_source_asset_ids": context["source_asset_ids"],
        "required_source_product_ids": context["source_product_ids"],
        "required_forecast_hours": context["forecast_hours"],
        "required_valid_time_range": context["valid_time_range"],
        "source_observations": context["source_observations"],
    }
    return [
        {
            "role": "system",
            "content": (
                "엄격한 그룹 요약 JSON 객체 하나만 반환하고 후보 판단은 생성하지 마십시오. "
                "JSON 키와 식별자는 유지하고 사람이 읽는 설명 문장은 모두 한국어로 작성하십시오."
            ),
        },
        {
            "role": "user",
            "content": prompt_template.strip() + "\n\n## group observation input\n" + json.dumps(group_input, ensure_ascii=False),
        },
    ]


def build_group_summary_compaction_messages(
    *,
    summary: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    compact_request = {
        "task": "필수 coverage를 제거하지 말고 이 그룹 요약을 byte 상한 아래로 압축하십시오.",
        "hard_max_utf8_bytes": GROUP_SUMMARY_MAX_BYTES,
        "target_utf8_bytes": GROUP_SUMMARY_TARGET_BYTES,
        "required_contract": {
            "summary_id": context["summary_id"],
            "analysis_tier": context["analysis_tier"],
            "group_id": context["group_id"],
            "source_batch_ids": context["source_batch_ids"],
            "source_asset_ids": context["source_asset_ids"],
            "source_product_ids": context["source_product_ids"],
            "forecast_hours": context["forecast_hours"],
            "valid_time_range": context["valid_time_range"],
        },
        "rules": [
            "Keep every source asset exactly once in integrated_observations.",
            "Keep product, forecast hour, spatial signal, temporal signal, and uncertainty.",
            "Return JSON only. Do not truncate strings or omit required fields.",
            "Keep all human-readable narrative strings in Korean.",
        ],
        "previous_summary": summary,
    }
    return [
        {
            "role": "system",
            "content": "엄격한 JSON 그룹 요약을 압축합니다. 설명 문장은 한국어로 유지하고 JSON만 반환하십시오.",
        },
        {"role": "user", "content": json.dumps(compact_request, ensure_ascii=False)},
    ]


def build_group_checkpoint_contract(
    *,
    package_id: str,
    context: dict[str, Any],
    batches: list[dict[str, Any]],
    prompt_sha256: str,
    schema_sha256: str,
    model: str,
    provider_configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    observation_plan = [
        {
            "batch_id": batch["batch_id"],
            "analysis_tier": batch.get("analysis_tier"),
            "group_id": batch.get("group_id"),
            "attachment_ids": batch["attachment_ids"],
        }
        for batch in batches
    ]
    contract = {
        "package_id": package_id,
        "summary_id": context["summary_id"],
        "analysis_tier": context["analysis_tier"],
        "group_id": context["group_id"],
        "observation_plan_fingerprint": canonical_hash(observation_plan),
        "source_observation_fingerprint": canonical_hash(context["source_observations"]),
        "source_asset_ids": sorted(context["source_asset_ids"]),
        "group_prompt_sha256": prompt_sha256,
        "group_schema_sha256": schema_sha256,
        "model": model,
        "provider_configuration": provider_configuration or {},
        "synthesis_contract_version": GROUP_SUMMARY_CONTRACT_VERSION,
    }
    return {**contract, "fingerprint": canonical_hash(contract)}


def _stage_provider_configuration_equivalent(
    actual_configuration: Any,
    expected_configuration: Any,
    *,
    stage: str,
) -> bool:
    """Allow an older full configuration only when this stage is unchanged."""
    if actual_configuration == expected_configuration:
        return True
    if not isinstance(actual_configuration, dict) or not isinstance(expected_configuration, dict):
        return False
    shared_fields = (
        "provider",
        "api_family",
        "endpoint_id",
        "image_detail",
        "structured_output_mode",
    )
    if any(actual_configuration.get(field) != expected_configuration.get(field) for field in shared_fields):
        return False
    if expected_configuration.get("api_family") != "responses":
        return False
    reasoning_stage = {
        "batch_observation": "observation",
        "group_summary": "group_summary",
        "candidate_discovery": "candidate_discovery",
        "candidate_plan": "candidate_plan",
    }[stage]
    actual_reasoning = actual_configuration.get("reasoning_efforts")
    expected_reasoning = expected_configuration.get("reasoning_efforts")
    actual_hashes = actual_configuration.get("transport_schema_sha256")
    expected_hashes = expected_configuration.get("transport_schema_sha256")
    return (
        isinstance(actual_reasoning, dict)
        and isinstance(expected_reasoning, dict)
        and actual_reasoning.get(reasoning_stage) == expected_reasoning.get(reasoning_stage)
        and isinstance(actual_hashes, dict)
        and isinstance(expected_hashes, dict)
        and actual_hashes.get(stage) == expected_hashes.get(stage)
    )


def _checkpoint_fingerprint_matches(
    checkpoint: dict[str, Any],
    expected_checkpoint: dict[str, Any],
    *,
    provider_stage: str | None = None,
) -> bool:
    if checkpoint.get("fingerprint") == expected_checkpoint.get("fingerprint"):
        return True
    if provider_stage is not None and _stage_provider_configuration_equivalent(
        checkpoint.get("provider_configuration"),
        expected_checkpoint.get("provider_configuration"),
        stage=provider_stage,
    ):
        legacy_expected = {
            key: value for key, value in expected_checkpoint.items() if key != "fingerprint"
        }
        legacy_expected["provider_configuration"] = checkpoint.get("provider_configuration")
        if checkpoint.get("fingerprint") == canonical_hash(legacy_expected):
            logging.info(
                "step04 checkpoint reused compatibility=stage_scoped_configuration stage=%s",
                provider_stage,
            )
            return True
    logging.info("step04 checkpoint invalidated reason=provider_or_fingerprint_mismatch")
    return False


def _load_existing_group_summary(
    *,
    summary_path: Path,
    checkpoint_path: Path,
    expected_checkpoint: dict[str, Any],
    schema: dict[str, Any],
    package_id: str,
    context: dict[str, Any],
) -> dict[str, Any] | None:
    if not summary_path.is_file() or not checkpoint_path.is_file():
        return None
    try:
        checkpoint = load_json(checkpoint_path)
        if not _checkpoint_fingerprint_matches(
            checkpoint,
            expected_checkpoint,
            provider_stage="group_summary",
        ):
            return None
        summary = load_json(summary_path)
        if checkpoint.get("summary_sha256") != sha256sum(summary_path):
            return None
        errors = validate_group_summary(
            summary,
            schema=schema,
            package_id=package_id,
            context=context,
        )
        return summary if not errors else None
    except Exception:  # noqa: BLE001
        return None


def _minimal_asset_index(attachment_manifest: dict[str, Any]) -> dict[str, Any]:
    fields = ("asset_id", "product_id", "analysis_tier", "group_id", "forecast_hours", "valid_times_utc")
    return {
        "asset_count": int(attachment_manifest.get("attachment_count", 0)),
        "assets": [{field: item.get(field) for field in fields} for item in attachment_manifest.get("attachments", [])],
    }


def _minimal_support_catalog(support_catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "supported_request_types": support_catalog.get("supported_request_types", []),
        "spatial_field_products": [
            {
                "product_id": item.get("product_id"),
                "allowed_forecast_hours": item.get("allowed_forecast_hours", []),
                "signal_components": item.get("signal_components", []),
            }
            for item in support_catalog.get("spatial_field_products", [])
            if isinstance(item, dict)
        ],
        "quantitative_excerpt_support": {
            key: value
            for key, value in support_catalog.get("quantitative_excerpt_support", {}).items()
            if key in {
                "data_type", "forecast_hours_required", "requested_domain_required",
                "supported_statistics", "supported_derived_outputs", "ranking_statistics", "maximum_top_n"
            }
        },
        "analysis_plan_support": support_catalog.get("analysis_plan_support", {}),
        "diagnostic_execution_support": support_catalog.get("diagnostic_execution_support", {}),
    }


def _minimal_feature_index(feature_catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "features": [
            {key: item.get(key) for key in ("feature_id", "name_ko", "units")}
            for item in feature_catalog.get("features", [])
            if isinstance(item, dict)
        ]
    }


def _minimal_operational_region_index(operational_region_catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "regions": [
            {
                key: item.get(key)
                for key in (
                    "region_id", "name_ko", "bbox", "region_role",
                    "eligible_for_risk_rank", "allowed_feature_ids"
                )
            }
            for item in operational_region_catalog.get("regions", [])
            if isinstance(item, dict) and item.get("enabled_in_pass1")
        ]
    }


def _candidate_discovery_operational_scope(overview_data: dict[str, Any]) -> dict[str, Any]:
    """Expose the exact Step04 candidate boundary policy to the discovery model.

    Candidate discovery previously received operational precipitation guidance but
    not the catalog geometry used by the semantic validator.  That made the model
    guess whether a remote precipitation area was an operational forecast target
    or only an upstream/source corridor.  Keep the policy deterministic and compact:
    direct/mixed candidates may overlap only Pass1 operational_land or marine_macro
    regions; source_corridor remains contextual/precursor evidence.
    """
    catalog = overview_data.get("operational_region_catalog", {}) if isinstance(overview_data, dict) else {}
    regions = catalog.get("regions", []) if isinstance(catalog, dict) else []

    eligible: list[dict[str, Any]] = []
    context_only: list[dict[str, Any]] = []
    for region in regions:
        if not isinstance(region, dict) or not bool(region.get("enabled_in_pass1")):
            continue
        if str(region.get("stage")) != "pass1":
            continue
        raw_bbox = region.get("bbox")
        bbox = _region_bbox_for_peak_validation(raw_bbox)
        if bbox is None:
            continue
        row = {
            "region_id": str(region.get("region_id") or ""),
            "name_ko": str(region.get("name_ko") or ""),
            "region_role": str(region.get("region_role") or ""),
            "bbox": bbox,
        }
        if row["region_role"] in {"operational_land", "marine_macro"}:
            eligible.append(row)
        elif row["region_role"] == "source_corridor":
            context_only.append(row)

    return {
        "contract_version": CANDIDATE_DISCOVERY_OPERATIONAL_SCOPE_CONTRACT_VERSION,
        "eligible_forecast_regions": sorted(eligible, key=lambda item: item["region_id"]),
        "context_only_source_corridors": sorted(context_only, key=lambda item: item["region_id"]),
        "rules": [
            "direct_precipitation 또는 mixed 후보의 candidate_area는 eligible_forecast_regions 중 하나 이상과 실제로 겹쳐야 합니다.",
            "source_corridor에만 존재하는 강수는 독립 direct/mixed 후보로 만들지 않고 주변 맥락 또는 선행환경 근거로 유지합니다.",
            "운영권역과 겹치게 만들기 위해 실제 강수 핵심영역의 candidate_area를 임의로 확대하거나 이동하지 않습니다.",
        ],
    }


def _candidate_discovery_scope_invalid_direct_ids(
    raw_candidates: list[Any],
    *,
    overview_data: dict[str, Any] | None,
) -> list[str]:
    """Return provider-position candidate IDs that violate the fixed forecast scope."""
    if not isinstance(overview_data, dict):
        return []
    catalog = overview_data.get("operational_region_catalog")
    if not isinstance(catalog, dict):
        return []
    invalid: list[str] = []
    for index, candidate in enumerate(raw_candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("detection_basis") or "") not in {"direct_precipitation", "mixed"}:
            continue
        bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
        if bbox is None:
            continue
        if not _selectable_candidate_operational_regions(
            candidate_bbox=bbox,
            operational_region_catalog=catalog,
        ):
            invalid.append(f"CAND_{index:03d}")
    return invalid


def _canonicalize_unreferenced_nonoperational_direct_candidates(
    value: dict[str, Any],
    *,
    overview_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Drop only safely removable source-corridor-only direct/mixed candidates.

    This is a policy/representation canonicalization, not a meteorological rewrite.
    A direct/mixed candidate outside every eligible forecast region cannot enter
    downstream operational verification.  If no precursor opportunity references
    that positional candidate ID, it can be removed together with its paired evidence
    while retaining and renumbering all other model judgments.  When a precursor
    relation references the invalid candidate, leave it untouched so whole-response
    AI repair can reconsider that meteorological relationship instead of Python
    silently reclassifying it.
    """
    if not isinstance(value, dict):
        return value
    candidates = value.get("candidates")
    evidence = value.get("candidate_evidence")
    if not isinstance(candidates, list) or not isinstance(evidence, list):
        return value
    if len(candidates) != len(evidence):
        return value

    invalid_ids = set(
        _candidate_discovery_scope_invalid_direct_ids(
            candidates, overview_data=overview_data
        )
    )
    if not invalid_ids:
        return value

    opportunities = value.get("precursor_opportunities")
    referenced_direct_ids: set[str] = set()
    if isinstance(opportunities, list):
        for opportunity in opportunities:
            if not isinstance(opportunity, dict):
                continue
            referenced_direct_ids.update(
                str(candidate_id)
                for candidate_id in opportunity.get("related_direct_candidate_ids", [])
                if str(candidate_id)
            )

    safely_removable = invalid_ids - referenced_direct_ids
    if not safely_removable:
        return value

    normalized = copy.deepcopy(value)
    kept_candidates: list[Any] = []
    kept_evidence: list[Any] = []
    id_map: dict[str, str] = {}
    for old_index, (candidate, group) in enumerate(zip(candidates, evidence), start=1):
        old_id = f"CAND_{old_index:03d}"
        if old_id in safely_removable:
            logging.warning(
                "STEP04_CANDIDATE_SCOPE_PRUNE candidate_id=%s reason=source_corridor_only_direct_or_mixed",
                old_id,
            )
            continue
        new_id = f"CAND_{len(kept_candidates) + 1:03d}"
        id_map[old_id] = new_id
        kept_candidates.append(copy.deepcopy(candidate))
        kept_evidence.append(copy.deepcopy(group))

    normalized["candidates"] = kept_candidates
    normalized["candidate_evidence"] = kept_evidence

    if isinstance(normalized.get("precursor_opportunities"), list):
        for opportunity in normalized["precursor_opportunities"]:
            if not isinstance(opportunity, dict):
                continue
            related = opportunity.get("related_direct_candidate_ids")
            if isinstance(related, list):
                opportunity["related_direct_candidate_ids"] = [
                    id_map.get(str(candidate_id), str(candidate_id))
                    for candidate_id in related
                ]
            created_id = opportunity.get("created_candidate_id")
            if isinstance(created_id, str) and created_id in id_map:
                opportunity["created_candidate_id"] = id_map[created_id]

    logging.warning(
        "STEP04_CANDIDATE_SCOPE_CANONICALIZED removed=%s retained=%s",
        sorted(safely_removable),
        len(kept_candidates),
    )
    return normalized


def _selectable_candidate_operational_regions(
    *,
    candidate_bbox: dict[str, Any],
    operational_region_catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    """Resolve Step04 regional targets with the same cheap bbox rule as Step05.

    Step05 remains authoritative for the final grid/mask calculation.  This
    preflight only prevents Step04 from emitting an operational-region target
    that is guaranteed to resolve to an empty set downstream.
    """
    regions = [
        item
        for item in operational_region_catalog.get("regions", [])
        if isinstance(item, dict)
    ]
    return selectable_pass1_regions(
        regions=regions,
        candidate_bbox=candidate_bbox,
    )


def _candidate_operational_region_overlap(
    *,
    candidate: dict[str, Any],
    operational_region_catalog: dict[str, Any],
) -> dict[str, Any]:
    """Expose candidate-specific operational-region execution availability.

    Regional quantitative diagnostics are only executable when the immutable
    candidate bbox overlaps an enabled operational_land/marine_macro region.
    The model previously received the whole catalog and had to infer this
    intersection itself, which is both unnecessary and a source of avoidable
    OPERATIONAL_REGION_TARGET_EMPTY failures.
    """
    bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
    selected: list[dict[str, Any]] = []
    if bbox is not None:
        selected = _selectable_candidate_operational_regions(
            candidate_bbox=bbox,
            operational_region_catalog=operational_region_catalog,
        )
    compact_rows: list[dict[str, Any]] = []
    for region in selected:
        region_bbox = _region_bbox_for_peak_validation(region.get("bbox"))
        compact_rows.append(
            {
                "region_id": str(region.get("region_id") or ""),
                "name_ko": str(region.get("name_ko") or ""),
                "region_role": str(region.get("region_role") or ""),
                "bbox": region_bbox,
            }
        )
    compact_rows.sort(key=lambda item: item["region_id"])
    regional_available = bool(compact_rows)
    allowed_modes = [
        mode
        for mode in VERIFICATION_MODES
        if regional_available or mode not in REGIONAL_VERIFICATION_MODES
    ]
    return {
        "candidate_area": bbox,
        "selectable_regions": compact_rows,
        "regional_verification_available": regional_available,
        "allowed_verification_modes": allowed_modes,
        "forbidden_verification_modes": sorted(
            REGIONAL_VERIFICATION_MODES if not regional_available else set()
        ),
        "rule": (
            "regional_intensity/temporal_change/precipitation_amount may be used only "
            "when regional_verification_available=true. Never expand or move candidate_area "
            "to create an operational-region overlap."
        ),
    }


def _candidate_plan_transport_schema_for_operational_scope(
    schema: dict[str, Any],
    *,
    candidate: dict[str, Any],
    operational_region_catalog: dict[str, Any],
) -> dict[str, Any]:
    """Constrain compact provider output to modes executable for this candidate.

    This is an execution-capability constraint only; it does not choose a
    meteorological conclusion.  For a source-corridor/precursor candidate with
    no operational overlap, the provider can still select spatial/vertical
    verification, but cannot emit a mode that deterministically expands to
    regional statistics and would be rejected downstream.
    """
    scoped = copy.deepcopy(schema)
    overlap = _candidate_operational_region_overlap(
        candidate=candidate,
        operational_region_catalog=operational_region_catalog,
    )
    allowed = list(overlap["allowed_verification_modes"])
    try:
        mode_schema = (
            scoped["properties"]["verification_requests"]["items"]
            ["properties"]["verification_mode"]
        )
    except (KeyError, TypeError):
        return scoped
    if isinstance(mode_schema, dict):
        mode_schema["enum"] = allowed

    # Quantitative claims are executable only against candidate-overlapping
    # operational regions.  Constrain the transport contract so the provider
    # cannot select a valid catalog region that is outside this candidate, and
    # force quantitative_scope=null when there is no regional target at all.
    try:
        quantitative_scope_schema = (
            scoped["properties"]["verification_requests"]["items"]["properties"]
            ["atomic_claims"]["items"]["properties"]["quantitative_scope"]
        )
    except (KeyError, TypeError):
        return scoped
    selectable_region_ids = [
        str(item.get("region_id"))
        for item in overlap.get("selectable_regions", [])
        if isinstance(item, dict) and str(item.get("region_id") or "")
    ]
    if not overlap.get("regional_verification_available"):
        scoped["properties"]["verification_requests"]["items"]["properties"][
            "atomic_claims"
        ]["items"]["properties"]["quantitative_scope"] = {"type": "null"}
    elif isinstance(quantitative_scope_schema, dict):
        for option in quantitative_scope_schema.get("anyOf", []):
            if not isinstance(option, dict) or option.get("type") != "object":
                continue
            region_items = (
                option.get("properties", {}).get("region_ids", {}).get("items")
            )
            if isinstance(region_items, dict):
                region_items["enum"] = selectable_region_ids
    return scoped


def _operational_region_target_empty_error(
    *,
    candidate_id: str,
    candidate_bbox: dict[str, Any],
    check_keys: Iterable[str] = (),
) -> str:
    keys = [str(value) for value in check_keys if str(value)]
    suffix = f" regional_check_keys={keys}" if keys else ""
    return (
        "OPERATIONAL_REGION_TARGET_EMPTY "
        f"candidate_id={candidate_id} candidate_area={candidate_bbox}{suffix}; "
        "candidate_area overlaps no enabled Pass1 operational_land/marine_macro region. "
        "Do not change candidate_area merely to satisfy execution. Use spatial-only "
        "verification for this candidate, or regenerate the regional verification request "
        "only when an actual operational-region overlap exists."
    )


def build_candidate_discovery_schema(final_schema: dict[str, Any]) -> dict[str, Any]:
    """Derive the candidate-only contract from the canonical final Pass1 schema."""
    required = [
        "package_id",
        "analysis_coverage",
        "run_summary",
        "candidates",
        "candidate_evidence",
        "global_uncertainties",
        "precursor_candidate_assessment",
        "pass2_required",
    ]
    optional = ["precursor_opportunities"]
    properties = final_schema.get("properties", {})
    defs = final_schema.get("$defs", {})
    missing_properties = [key for key in required if key not in properties]
    required_defs = ("bbox", "timeWindow", "candidate", "candidateEvidence", "precursorOpportunity")
    missing_defs = [key for key in required_defs if key not in defs]
    if missing_properties or missing_defs:
        raise ValueError(
            "final Pass1 schema does not support split candidate discovery: "
            f"missing_properties={missing_properties} missing_defs={missing_defs}"
        )
    needed_defs = {key: defs[key] for key in required_defs}
    return {
        "$schema": final_schema.get("$schema", "https://json-schema.org/draft/2020-12/schema"),
        "title": "AI Pass 1 Candidate Discovery Response",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": {
            key: final_schema["properties"][key]
            for key in [*required, *optional]
            if key in final_schema.get("properties", {})
        },
        "$defs": needed_defs,
    }


def build_candidate_discovery_provider_schema(
    schema: dict[str, Any],
    *,
    attachment_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Constrain model-selected discovery references to the active manifest.

    Canonical schemas use local ``$defs`` while the OpenAI transport schema is
    fully inlined. Both forms must receive the same runtime enums.
    """
    constrained = copy.deepcopy(schema)
    attachment_by_id = _attachment_by_id(attachment_manifest)
    asset_ids = list(attachment_by_id)
    forecast_hours = sorted(
        {
            int(hour)
            for attachment in attachment_by_id.values()
            for hour in attachment.get("forecast_hours", [])
        }
    )

    defs = constrained.get("$defs")
    if isinstance(defs, dict) and isinstance(defs.get("candidate"), dict):
        candidate_properties = defs["candidate"]["properties"]
        evidence_properties = defs["candidateEvidence"]["properties"]
    else:
        root_properties = constrained.get("properties", {})
        candidate_properties = (
            root_properties.get("candidates", {}).get("items", {}).get("properties", {})
        )
        evidence_properties = (
            root_properties.get("candidate_evidence", {}).get("items", {}).get("properties", {})
        )
    related_asset_items = (
        candidate_properties.get("related_overview_asset_ids", {}).get("items")
    )
    if not isinstance(related_asset_items, dict):
        raise ValueError("candidate discovery schema is missing related_overview_asset_ids.items")
    related_asset_items["enum"] = asset_ids

    evidence_item_properties = (
        evidence_properties.get("evidence_items", {}).get("items", {}).get("properties", {})
    )
    asset_id_schema = evidence_item_properties.get("asset_id")
    if not isinstance(asset_id_schema, dict):
        raise ValueError("candidate discovery schema is missing evidence asset_id")
    asset_id_schema["enum"] = asset_ids
    forecast_hour_items = (
        evidence_item_properties.get("forecast_hours", {}).get("items")
    )
    if forecast_hours and isinstance(forecast_hour_items, dict):
        forecast_hour_items["enum"] = forecast_hours

    assessment_properties = constrained.get("properties", {}).get(
        "precursor_candidate_assessment", {}
    ).get("properties", {})
    source_asset_items = assessment_properties.get("source_asset_ids", {}).get("items")
    if isinstance(source_asset_items, dict):
        source_asset_items["enum"] = asset_ids
    assessment_hour_items = assessment_properties.get("forecast_hours", {}).get("items")
    if forecast_hours and isinstance(assessment_hour_items, dict):
        assessment_hour_items["enum"] = forecast_hours
    opportunity_schema = constrained.get("properties", {}).get("precursor_opportunities", {})
    if isinstance(opportunity_schema, dict):
        opportunity_items = opportunity_schema.get("items", {})
        if isinstance(opportunity_items, dict) and "$ref" in opportunity_items:
            opportunity_items = (constrained.get("$defs", {}) or {}).get("precursorOpportunity", {})
        opportunity_properties = opportunity_items.get("properties", {}) if isinstance(opportunity_items, dict) else {}
        support_items = opportunity_properties.get("support_asset_ids", {}).get("items")
        if isinstance(support_items, dict):
            support_items["enum"] = asset_ids
        representative_items = opportunity_properties.get("representative_forecast_hours", {}).get("items")
        if forecast_hours and isinstance(representative_items, dict):
            representative_items["enum"] = forecast_hours
    return constrained

def build_candidate_plan_schema(final_schema: dict[str, Any]) -> dict[str, Any]:
    """Derive the per-candidate plan contract from the canonical final schema."""
    defs = final_schema.get("$defs", {})
    needed_names = (
        "bbox",
        "crossSectionLine",
        "referenceBandAxis",
        "analysisDataRequest",
        "analysisHypothesis",
        "analysisTargetArea",
        "analysisPhase",
        "variableCheck",
        "candidateAnalysisPlan",
    )
    missing_defs = [key for key in needed_names if key not in defs]
    if missing_defs:
        raise ValueError(
            "final Pass1 schema does not support per-candidate planning: "
            f"missing_defs={missing_defs}. Re-run Step04 with the analysis-plan schema."
        )
    return {
        "$schema": final_schema.get("$schema", "https://json-schema.org/draft/2020-12/schema"),
        "title": "AI Pass 1 Per-Candidate Analysis Plan Response",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "package_id",
            "candidate_id",
            "candidate_analysis_plan",
            "analysis_data_requests",
        ],
        "properties": {
            "package_id": {"type": "string", "minLength": 1},
            "candidate_id": {"type": "string", "pattern": "^CAND_[0-9]{3}$"},
            "candidate_analysis_plan": {"$ref": "#/$defs/candidateAnalysisPlan"},
            "analysis_data_requests": {
                "type": "array",
                "items": {"$ref": "#/$defs/analysisDataRequest"},
                "minItems": 1,
            },
        },
        "$defs": {key: defs[key] for key in needed_names},
    }


def _normalize_candidate_discovery_provider_shape(
    value: dict[str, Any],
    *,
    attachment_manifest: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Wrap a complete single candidate when the provider omitted the stage envelope."""
    candidate_markers = {
        "candidate_name",
        "candidate_area",
        "time_window",
        "related_overview_asset_ids",
    }
    if "candidates" in value or not candidate_markers.issubset(value):
        return value, False
    if attachment_manifest is None:
        raise ValueError(
            "candidate discovery single-candidate response requires attachment manifest"
        )

    candidate = copy.deepcopy(value)
    global_uncertainties = candidate.pop("global_uncertainties", [])
    for key in (
        "package_id",
        "analysis_coverage",
        "run_summary",
        "pass2_required",
        "candidate_evidence",
        "candidate_id",
        "candidate_unit_type",
        "discovery_order",
        "priority_rank",
    ):
        candidate.pop(key, None)

    attachment_by_id = _attachment_by_id(attachment_manifest)
    related_asset_ids = list(
        dict.fromkeys(
            str(asset_id)
            for asset_id in candidate.get("related_overview_asset_ids", [])
        )
    )
    if not related_asset_ids:
        raise ValueError(
            "candidate discovery single-candidate response has no related overview assets"
        )
    unknown_asset_ids = [
        asset_id for asset_id in related_asset_ids if asset_id not in attachment_by_id
    ]
    if unknown_asset_ids:
        raise ValueError(
            "candidate discovery single-candidate response references unknown overview "
            f"assets: {unknown_asset_ids}"
        )
    candidate["related_overview_asset_ids"] = related_asset_ids

    evidence_items = []
    for asset_id in related_asset_ids:
        source = attachment_by_id[asset_id]
        evidence_items.append(
            {
                "asset_id": asset_id,
                "product_id": str(source.get("product_id")),
                "forecast_hours": [
                    int(hour) for hour in source.get("forecast_hours", [])
                ],
                "reason": "후보 응답에서 직접 참조한 개요 자산입니다.",
            }
        )

    return {
        "candidates": [candidate],
        "candidate_evidence": [{"evidence_items": evidence_items}],
        "global_uncertainties": global_uncertainties
        if isinstance(global_uncertainties, list)
        else [],
    }, True


def normalize_candidate_discovery_contract(
    value: dict[str, Any],
    *,
    package_id: str,
    attachment_count: int,
    attachment_manifest: dict[str, Any] | None = None,
    stage_schema: dict[str, Any] | None = None,
    group_summaries: list[dict[str, Any]] | None = None,
    overview_data: dict[str, Any] | None = None,
    support_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assign all discovery system fields after validating model judgments only."""
    if not isinstance(value, dict):
        return value
    value, single_candidate_wrapped = _normalize_candidate_discovery_provider_shape(
        value,
        attachment_manifest=attachment_manifest,
    )
    if single_candidate_wrapped:
        logging.warning(
            "candidate discovery single-candidate root normalized candidates=1"
        )
    raw_candidates_for_compatibility = value.get("candidates")
    if isinstance(raw_candidates_for_compatibility, list):
        value = dict(value)
        value["candidates"] = [
            {
                key: field_value
                for key, field_value in infer_legacy_candidate_signal_contract(item).items()
                if key not in {"candidate_id", "candidate_unit_type", "discovery_order", "priority_rank"}
            }
            if isinstance(item, dict)
            else item
            for item in raw_candidates_for_compatibility
        ]
    raw_evidence_for_compatibility = value.get("candidate_evidence")
    if isinstance(raw_evidence_for_compatibility, list):
        value = dict(value)
        value["candidate_evidence"] = [
            {
                key: field_value
                for key, field_value in item.items()
                if key != "candidate_id"
            }
            if isinstance(item, dict)
            else item
            for item in raw_evidence_for_compatibility
        ]
    if stage_schema is not None:
        errors = _validate_jsonschema(value, stage_schema)
        if errors:
            raise ValueError("candidate discovery stage schema invalid: " + " | ".join(errors[:20]))

    value = _canonicalize_unreferenced_nonoperational_direct_candidates(
        value,
        overview_data=overview_data,
    )

    precursor_candidate_assessment = copy.deepcopy(
        value.get("precursor_candidate_assessment")
    )
    precursor_opportunities = copy.deepcopy(value.get("precursor_opportunities"))
    raw_candidates = value.get("candidates", [])
    raw_evidence = value.get("candidate_evidence", [])
    if not isinstance(raw_candidates, list) or not isinstance(raw_evidence, list):
        raise ValueError("candidate discovery candidates and candidate_evidence must be arrays")
    attachment_by_id = (
        _attachment_by_id(attachment_manifest)
        if attachment_manifest is not None
        else None
    )

    candidates: list[dict[str, Any]] = []
    for index, raw_candidate in enumerate(raw_candidates, start=1):
        if not isinstance(raw_candidate, dict):
            raise ValueError(f"candidate discovery candidate is not an object at index={index}")
        candidate = {
            key: json.loads(json.dumps(item, ensure_ascii=False))
            for key, item in raw_candidate.items()
            if key not in {"candidate_id", "candidate_unit_type", "discovery_order", "priority_rank"}
        }
        candidate["candidate_id"] = f"CAND_{index:03d}"
        candidate["candidate_unit_type"] = _candidate_unit_type(candidate)
        candidate["discovery_order"] = index
        if attachment_by_id is not None:
            related_asset_ids = list(
                dict.fromkeys(
                    str(asset_id)
                    for asset_id in candidate.get("related_overview_asset_ids", [])
                )
            )
            unknown_asset_ids = [
                asset_id for asset_id in related_asset_ids if asset_id not in attachment_by_id
            ]
            if unknown_asset_ids:
                raise ValueError(
                    f"candidate discovery references unknown overview assets: {unknown_asset_ids}"
                )
            candidate["related_overview_asset_ids"] = related_asset_ids
            candidate["related_product_ids"] = list(
                dict.fromkeys(
                    str(attachment_by_id[asset_id].get("product_id"))
                    for asset_id in related_asset_ids
                )
            )
        candidates.append(candidate)

    _normalize_candidate_precursor_metadata(
        candidates,
        attachment_by_id=attachment_by_id,
    )
    if precursor_opportunities is not None:
        _validate_precursor_opportunities(
            precursor_opportunities,
            candidates=candidates,
            attachment_manifest=attachment_manifest,
        )
        precursor_candidate_assessment = _derive_legacy_precursor_assessment(
            precursor_opportunities,
            precursor_candidate_assessment if isinstance(precursor_candidate_assessment, dict) else {},
        )
    if not isinstance(precursor_candidate_assessment, dict):
        raise ValueError("candidate discovery requires precursor_candidate_assessment")
    precursor_candidate_assessment["dismissed_precursor_groups"] = (
        _build_dismissed_precursor_groups(
            candidates,
            attachment_by_id=attachment_by_id,
        )
    )

    _validate_precursor_candidate_assessment(
        precursor_candidate_assessment,
        candidates=candidates,
        attachment_manifest=attachment_manifest,
    )
    _log_step04_advisories(
        "candidate_discovery.precursor_assessment",
        _precursor_candidate_assessment_advisories(
            precursor_candidate_assessment,
            candidates=candidates,
            attachment_manifest=attachment_manifest,
        ),
    )
    logging.info(
        "step04 precursor candidate assessment status=%s qualified_opportunities=%s selected_candidates=%s",
        precursor_candidate_assessment.get("assessment_status")
        if isinstance(precursor_candidate_assessment, dict) else None,
        precursor_candidate_assessment.get("qualified_opportunity_count")
        if isinstance(precursor_candidate_assessment, dict) else None,
        sum(1 for item in candidates if str(item.get("detection_basis")) == "precursor_environment"),
    )

    evidence_groups: list[dict[str, Any]] = []
    for index, raw_group in enumerate(raw_evidence, start=1):
        if not isinstance(raw_group, dict):
            raise ValueError(f"candidate discovery evidence is not an object at index={index}")
        group = {
            key: json.loads(json.dumps(item, ensure_ascii=False))
            for key, item in raw_group.items()
            if key != "candidate_id"
        }
        if attachment_by_id is not None:
            evidence_items = group.get("evidence_items", [])
            if not isinstance(evidence_items, list):
                raise ValueError(
                    f"candidate discovery evidence_items is not an array at index={index}"
                )
            for evidence_index, evidence_item in enumerate(evidence_items, start=1):
                if not isinstance(evidence_item, dict):
                    raise ValueError(
                        "candidate discovery evidence item is not an object "
                        f"at group={index} index={evidence_index}"
                    )
                asset_id = str(evidence_item.get("asset_id", ""))
                source = attachment_by_id.get(asset_id)
                if source is None:
                    raise ValueError(
                        f"candidate discovery evidence references unknown asset_id: {asset_id}"
                    )
                evidence_item["product_id"] = str(source.get("product_id"))
        if index <= len(candidates):
            group["candidate_id"] = candidates[index - 1]["candidate_id"]
        evidence_groups.append(group)

    normalized_response = {
        "candidates": candidates,
        "candidate_evidence": evidence_groups,
    }
    # Group-summary direct_precipitation_audit preserves what was visible in the
    # overview imagery, including remote/source-corridor precipitation.  It is
    # intentionally NOT canonicalized into candidate evidence.  Only the SYSTEM
    # operational-region precipitation audit below may deterministically backfill
    # a precipitation evidence hour for an existing candidate.
    if overview_data is not None and attachment_manifest is not None:
        _canonicalize_system_direct_precipitation_evidence(
            normalized_response,
            overview_data=overview_data,
            attachment_manifest=attachment_manifest,
            support_catalog=support_catalog,
        )

    return {
        "package_id": package_id,
        "analysis_coverage": {
            "coverage_status": "complete",
            "overview_attachment_count": int(attachment_count),
        },
        "run_summary": {
            "candidate_count": len(candidates),
            "summary_text": "검증된 그룹 요약을 기반으로 후보 탐색을 완료했습니다.",
        },
        "candidates": candidates,
        "candidate_evidence": evidence_groups,
        "global_uncertainties": [
            str(item) for item in value.get("global_uncertainties", [])
        ]
        if isinstance(value.get("global_uncertainties"), list)
        else [],
        **({"precursor_opportunities": precursor_opportunities} if precursor_opportunities is not None else {}),
        "precursor_candidate_assessment": precursor_candidate_assessment,
        "pass2_required": bool(candidates),
    }


def filter_request_analysis_methods(
    methods: list[str],
    *,
    allowed_methods: set[str],
) -> tuple[list[str], list[str]]:
    """Keep only methods that the request data type can execute."""
    retained = [method for method in methods if method in allowed_methods]
    removed = [method for method in methods if method not in allowed_methods]
    return retained, removed


def _prune_unreferenced_context_phases(
    plan: dict[str, Any],
    *,
    canonicalization_events: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Remove context-only phases that no diagnostic check actually executes."""
    phases = [
        item for item in plan.get("analysis_phases", []) if isinstance(item, dict)
    ]
    referenced_phase_ids = {
        str(value)
        for check in plan.get("variable_checks", [])
        if isinstance(check, dict)
        for value in check.get("phase_ids", [])
    }
    removed = [
        str(phase.get("phase_id"))
        for phase in phases
        if str(phase.get("phase_type")) == "context"
        and str(phase.get("phase_id")) not in referenced_phase_ids
    ]
    if not removed:
        return []
    removed_set = set(removed)
    plan["analysis_phases"] = [
        phase for phase in phases if str(phase.get("phase_id")) not in removed_set
    ]
    if canonicalization_events is not None:
        canonicalization_events.append(
            {
                "field": "candidate_analysis_plan.analysis_phases",
                "reason": "unreferenced_context_phases_removed",
                "removed_phase_ids": removed,
            }
        )
    return removed


def normalize_candidate_plan_contract(
    value: dict[str, Any],
    *,
    package_id: str,
    candidate_id: str,
    stage_schema: dict[str, Any] | None = None,
    support_catalog: dict[str, Any] | None = None,
    canonicalization_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Project local plan references into deterministic Pass1 identifiers."""
    if not isinstance(value, dict):
        return value
    if (
        "candidate_analysis_plan" not in value
        and isinstance(value.get("candidate_analysis_analysis_plan"), dict)
    ):
        value["candidate_analysis_plan"] = value.pop(
            "candidate_analysis_analysis_plan"
        )
    raw_plan_for_normalization = value.get("candidate_analysis_plan")
    if (
        isinstance(raw_plan_for_normalization, dict)
        and "target_areas" not in raw_plan_for_normalization
        and isinstance(raw_plan_for_normalization.get("target_target_areas"), list)
    ):
        raw_plan_for_normalization["target_areas"] = raw_plan_for_normalization.pop(
            "target_target_areas"
        )
    raw_requests_for_normalization = value.get("analysis_data_requests")
    if isinstance(raw_requests_for_normalization, list):
        for raw_request in raw_requests_for_normalization:
            if not isinstance(raw_request, dict):
                continue
            if not raw_request.get("diagnostic_id"):
                methods = [str(item) for item in raw_request.get("analysis_methods", [])]
                mapping = {
                    "detailed_map": "detailed_spatial_map",
                    "temporal_evolution": "temporal_evolution",
                    "variable_cross_check": "multivariable_composite",
                    "regional_statistics": "temporal_evolution",
                    "regional_ranking": "detailed_spatial_map",
                    "regional_max_location": "detailed_spatial_map",
                }
                raw_request["diagnostic_id"] = next(
                    (mapping[item] for item in methods if item in mapping),
                    "detailed_spatial_map",
                )
            output_spec = raw_request.get("output_spec")
            if isinstance(output_spec, dict):
                raw_request["output_spec"] = {
                    key: item for key, item in output_spec.items() if item is not None
                }
    if stage_schema is not None:
        errors = _validate_jsonschema(value, stage_schema)
        if errors:
            raise ValueError("candidate plan stage schema invalid: " + " | ".join(errors[:20]))

    raw_plan = value.get("candidate_analysis_plan")
    raw_requests = value.get("analysis_data_requests")
    if not isinstance(raw_plan, dict) or not isinstance(raw_requests, list):
        raise ValueError("candidate plan stage response is missing candidate_analysis_plan or requests")
    match = re.fullmatch(r"CAND_(\d{3})", candidate_id)
    if match is None:
        raise ValueError(f"candidate_id is not canonical: {candidate_id}")
    number = match.group(1)
    plan_id = f"PLAN_CAND{number}"

    def canonicalize_rows(
        rows: Any,
        *,
        local_field: str,
        id_prefix: str,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        if not isinstance(rows, list):
            raise ValueError(f"candidate plan {local_field} rows must be an array")
        canonical_rows: list[dict[str, Any]] = []
        mapping: dict[str, str] = {}
        for index, raw_row in enumerate(rows, start=1):
            if not isinstance(raw_row, dict):
                raise ValueError(f"candidate plan {local_field} row is not an object index={index}")
            local_key = str(raw_row.get(local_field, "")).strip()
            if not local_key or local_key in mapping:
                raise ValueError(f"candidate plan {local_field} is missing or duplicated: {local_key!r}")
            canonical_id = f"{id_prefix}{number}_{index:02d}"
            mapping[local_key] = canonical_id
            row = {
                key: json.loads(json.dumps(item, ensure_ascii=False))
                for key, item in raw_row.items()
                if key != local_field
            }
            canonical_rows.append(row)
        return canonical_rows, mapping

    targets, target_ids = canonicalize_rows(
        raw_plan.get("target_areas"), local_field="target_key", id_prefix="AREA_CAND"
    )
    phases, phase_ids = canonicalize_rows(
        raw_plan.get("analysis_phases"), local_field="phase_key", id_prefix="PHASE_CAND"
    )
    checks, check_ids = canonicalize_rows(
        raw_plan.get("variable_checks"), local_field="check_key", id_prefix="CHK_CAND"
    )

    def resolve_local_ids(
        values: Any,
        mapping: dict[str, str],
        *,
        label: str,
    ) -> list[str]:
        if not isinstance(values, list):
            raise ValueError(f"candidate plan {label} must be an array")
        keys = [str(value) for value in values]
        unknown = sorted(set(keys) - set(mapping))
        if unknown:
            raise ValueError(
                f"candidate plan {label} references unknown local keys: {unknown}"
            )
        return [mapping[key] for key in keys]

    def resolve_local_id(
        value: Any,
        mapping: dict[str, str],
        *,
        label: str,
    ) -> str:
        key = str(value)
        if key not in mapping:
            raise ValueError(
                f"candidate plan {label} references unknown local key: {key!r}"
            )
        return mapping[key]

    for row, canonical_id in zip(targets, target_ids.values(), strict=True):
        row["target_id"] = canonical_id
    for row, canonical_id in zip(phases, phase_ids.values(), strict=True):
        row["phase_id"] = canonical_id
    for row, canonical_id in zip(checks, check_ids.values(), strict=True):
        row["check_id"] = canonical_id
        row["target_ids"] = resolve_local_ids(
            row.pop("target_keys", []),
            target_ids,
            label="variable_check.target_keys",
        )
        row["phase_ids"] = resolve_local_ids(
            row.pop("phase_keys", []),
            phase_ids,
            label="variable_check.phase_keys",
        )

    hypotheses: list[dict[str, Any]] = []
    raw_hypotheses = raw_plan.get("hypotheses")
    if not isinstance(raw_hypotheses, list):
        raise ValueError("candidate plan hypotheses must be an array")
    for index, raw_hypothesis in enumerate(raw_hypotheses, start=1):
        if not isinstance(raw_hypothesis, dict):
            raise ValueError(f"candidate plan hypothesis is not an object index={index}")
        hypotheses.append(
            {
                "hypothesis_id": f"H_CAND{number}_{index:02d}",
                "statement": raw_hypothesis.get("statement"),
                "supporting_check_ids": resolve_local_ids(
                    raw_hypothesis.get("supporting_check_keys", []),
                    check_ids,
                    label="hypothesis.supporting_check_keys",
                ),
                "opposing_check_ids": resolve_local_ids(
                    raw_hypothesis.get("opposing_check_keys", []),
                    check_ids,
                    label="hypothesis.opposing_check_keys",
                ),
            }
        )

    requests: list[dict[str, Any]] = []
    for index, raw_request in enumerate(raw_requests, start=1):
        if not isinstance(raw_request, dict):
            raise ValueError(f"candidate plan request is not an object index={index}")
        request = {
            key: json.loads(json.dumps(item, ensure_ascii=False))
            for key, item in raw_request.items()
            if key not in {"check_key", "target_key", "phase_keys"}
        }
        request.update(
            {
                "request_id": f"REQ_CAND{number}_{index:03d}",
                "candidate_id": candidate_id,
                "plan_id": plan_id,
                "check_id": resolve_local_id(
                    raw_request.get("check_key", ""),
                    check_ids,
                    label="analysis_data_request.check_key",
                ),
                "target_id": resolve_local_id(
                    raw_request.get("target_key", ""),
                    target_ids,
                    label="analysis_data_request.target_key",
                ),
                "phase_ids": resolve_local_ids(
                    raw_request.get("phase_keys", []),
                    phase_ids,
                    label="analysis_data_request.phase_keys",
                ),
            }
        )
        requests.append(request)

    plan_support = (support_catalog or {}).get("analysis_plan_support", {})
    method_order = [
        str(method)
        for method in plan_support.get("supported_analysis_methods", [])
    ]
    if not method_order:
        method_order = [
            "detailed_map",
            "regional_statistics",
            "regional_ranking",
            "regional_max_location",
            "temporal_evolution",
            "variable_cross_check",
        ]
    supported_methods = set(method_order)
    methods_by_data_type = {
        "spatial_field": {"detailed_map", "temporal_evolution", "variable_cross_check"},
        "quantitative_excerpt": {
            "regional_statistics",
            "regional_ranking",
            "regional_max_location",
            "temporal_evolution",
        },
    }
    supported_roles = {
        str(role)
        for role in [
            *plan_support.get("required_variable_roles", []),
            *plan_support.get("recommended_explanatory_roles", []),
            "uncertainty",
        ]
    }
    if supported_roles == {"uncertainty"}:
        supported_roles.update(
            {
                "hazard_magnitude",
                "moisture_supply",
                "low_level_forcing",
                "midlevel_forcing",
                "synoptic_support",
            }
        )

    def method_allowed_for_contract(
        *,
        method: str,
        variable_role: str,
        data_type: str,
    ) -> bool:
        """Apply the catalog's current role and data-type method constraints."""
        return (
            variable_role in supported_roles
            and method in methods_by_data_type.get(data_type, set())
        )

    def canonical_methods(values: Any) -> list[str]:
        if not isinstance(values, list):
            raise ValueError("candidate plan analysis_methods must be an array")
        normalized = {str(method) for method in values}
        return [method for method in method_order if method in normalized] + sorted(
            normalized - set(method_order)
        )

    checks_by_id = {str(check["check_id"]): check for check in checks}
    for request in requests:
        request_id = str(request["request_id"])
        check_id = str(request["check_id"])
        check = checks_by_id.get(check_id)
        if check is None:
            raise ValueError(f"{request_id} references unknown check_id={check_id}")
        variable_role = str(check.get("variable_role"))
        data_type = str(request.get("data_type"))
        if variable_role not in supported_roles:
            raise ValueError(
                f"{request_id} references unsupported variable_role={variable_role}"
            )
        request_methods = canonical_methods(request.get("analysis_methods", []))
        unsupported = sorted(set(request_methods) - supported_methods)
        if unsupported:
            raise ValueError(
                f"{request_id} uses unsupported analysis_methods={unsupported}"
            )
        allowed_for_data_type = methods_by_data_type.get(data_type)
        if allowed_for_data_type is None:
            raise ValueError(f"{request_id} has unsupported data_type={request.get('data_type')!r}")
        request_methods, invalid_for_contract = filter_request_analysis_methods(
            request_methods,
            allowed_methods=allowed_for_data_type,
        )
        if invalid_for_contract:
            if not request_methods:
                raise ValueError(
                    f"{request_id} has no analysis_methods allowed for "
                    f"variable_role={variable_role} data_type={data_type}"
                )
            if canonicalization_events is not None:
                canonicalization_events.append(
                    {
                        "field": f"analysis_data_requests[{request_id}].analysis_methods",
                        "reason": "methods_not_allowed_for_data_type_removed",
                        "removed_values": invalid_for_contract,
                        "remaining_values": request_methods,
                    }
                )
        check_methods = canonical_methods(check.get("analysis_methods", []))
        added = [method for method in request_methods if method not in check_methods]
        if added:
            check["analysis_methods"] = canonical_methods([*check_methods, *added])
            if canonicalization_events is not None:
                canonicalization_events.append(
                    {
                        "field": (
                            "candidate_analysis_plan.variable_checks"
                            f"[{check_id}].analysis_methods"
                        ),
                        "reason": "request_method_missing_from_check",
                        "source_request_id": request_id,
                        "added_values": added,
                    }
                )
        else:
            check["analysis_methods"] = check_methods
        request["analysis_methods"] = request_methods

    requests = _merge_analysis_data_requests(requests, candidate_number=number)

    plan = {
        "plan_id": plan_id,
        "candidate_id": candidate_id,
        "analysis_objective": raw_plan.get("analysis_objective"),
        "hypotheses": hypotheses,
        "target_areas": targets,
        "analysis_phases": phases,
        "variable_checks": checks,
        "forecaster_questions": raw_plan.get("forecaster_questions"),
    }
    _link_unreferenced_variable_checks(
        plan,
        canonicalization_events=canonicalization_events,
    )
    _prune_unreferenced_context_phases(
        plan,
        canonicalization_events=canonicalization_events,
    )
    return {
        "package_id": package_id,
        "candidate_id": candidate_id,
        "candidate_analysis_plan": plan,
        "analysis_data_requests": requests,
    }


def validate_candidate_discovery_response(
    response: dict[str, Any],
    *,
    schema: dict[str, Any],
    package_id: str,
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    group_summaries: list[dict[str, Any]] | None = None,
    overview_data: dict[str, Any] | None = None,
    enforce_system_direct_precipitation_coverage: bool = True,
) -> list[str]:
    """Validate candidate discovery without requiring plans or data requests."""
    errors = _validate_jsonschema(response, schema)
    attachment_by_id = _attachment_by_id(attachment_manifest)
    attachment_ids = set(attachment_by_id)
    supported_products = {
        str(item.get("product_id"))
        for item in support_catalog.get("spatial_field_products", [])
        if isinstance(item, dict) and item.get("product_id")
    }
    if response.get("package_id") != package_id:
        errors.append("package_id does not match Step04 input package")
    coverage = response.get("analysis_coverage", {})
    if isinstance(coverage, dict):
        if int(coverage.get("overview_attachment_count", -1)) != len(attachment_ids):
            errors.append("analysis_coverage.overview_attachment_count mismatch")
        if coverage.get("coverage_status") != "complete":
            errors.append("analysis_coverage.coverage_status must be complete")

    candidates = response.get("candidates", [])
    candidate_ids = [str(item.get("candidate_id")) for item in candidates if isinstance(item, dict)]
    candidate_id_set = set(candidate_ids)
    if len(candidate_ids) != len(candidate_id_set):
        errors.append("candidate_id values must be unique")
    summary = response.get("run_summary", {})
    if isinstance(summary, dict) and int(summary.get("candidate_count", -1)) != len(candidates):
        errors.append("run_summary.candidate_count mismatch")
    discovery_orders = [
        int(item.get("discovery_order"))
        for item in candidates
        if isinstance(item, dict) and item.get("discovery_order") is not None
    ]
    if len(discovery_orders) != len(set(discovery_orders)):
        errors.append("discovery_order values must be unique")
    if discovery_orders and sorted(discovery_orders) != list(range(1, len(discovery_orders) + 1)):
        errors.append("discovery_order values must be contiguous from 1")

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id"))
        expected_unit_type = _candidate_unit_type(candidate)
        if candidate.get("candidate_unit_type") != expected_unit_type:
            errors.append(
                f"{candidate_id} candidate_unit_type must be {expected_unit_type!r} "
                f"for detection_basis={candidate.get('detection_basis')!r}"
            )
        related_assets = [str(value) for value in candidate.get("related_overview_asset_ids", [])]
        unknown_assets = sorted(set(related_assets) - attachment_ids)
        if unknown_assets:
            errors.append(f"{candidate_id} references unknown overview assets: {unknown_assets}")
        related_products = {str(value) for value in candidate.get("related_product_ids", [])}
        unknown_products = sorted(related_products - supported_products)
        if unknown_products:
            errors.append(f"{candidate_id} references unsupported product IDs: {unknown_products}")
        actual_products = {
            str(attachment_by_id[asset_id].get("product_id"))
            for asset_id in related_assets
            if asset_id in attachment_by_id
        }
        if not actual_products.issubset(related_products):
            errors.append(f"{candidate_id} related_product_ids do not cover referenced assets")
        bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
        if bbox is None:
            errors.append(f"{candidate_id} candidate_area bbox is invalid")
        elif (
            str(candidate.get("detection_basis") or "") in {"direct_precipitation", "mixed"}
            and isinstance(overview_data, dict)
            and isinstance(overview_data.get("operational_region_catalog"), dict)
        ):
            forecast_regions = _selectable_candidate_operational_regions(
                candidate_bbox=bbox,
                operational_region_catalog=overview_data["operational_region_catalog"],
            )
            if not forecast_regions:
                errors.append(
                    "CANDIDATE_OPERATIONAL_SCOPE_INVALID "
                    f"candidate_id={candidate_id} candidate_area={bbox}; "
                    "direct/mixed candidate_area overlaps no eligible forecast operational_land/marine_macro region; "
                    "source-corridor-only precipitation must remain contextual or precursor evidence"
                )
        window = candidate.get("time_window", {})
        if isinstance(window, dict):
            start = int(window.get("forecast_hour_start", -1))
            end = int(window.get("forecast_hour_end", -1))
            if start > end:
                errors.append(f"{candidate_id} time_window start exceeds end")
            for hour in candidate.get("peak_forecast_hours", []):
                if int(hour) < start or int(hour) > end:
                    errors.append(f"{candidate_id} peak hour {hour} is outside time_window")
        _log_step04_advisories(
            "candidate_discovery.signal_classification",
            [f"{candidate_id} {signal_error}" for signal_error in validate_candidate_signal_contract(candidate)],
        )

    _log_step04_advisories(
        "candidate_discovery.precursor_evidence",
        _precursor_candidate_evidence_advisories(
            [item for item in candidates if isinstance(item, dict)],
            attachment_manifest=attachment_manifest,
        ),
    )
    try:
        _validate_precursor_opportunities(
            response.get("precursor_opportunities"),
            candidates=[item for item in candidates if isinstance(item, dict)],
            attachment_manifest=attachment_manifest,
        )
    except ValueError as exc:
        errors.append(str(exc))
    try:
        _validate_precursor_candidate_assessment(
            response.get("precursor_candidate_assessment"),
            candidates=[item for item in candidates if isinstance(item, dict)],
            attachment_manifest=attachment_manifest,
        )
    except ValueError as exc:
        errors.append(str(exc))
    else:
        _log_step04_advisories(
            "candidate_discovery.precursor_assessment",
            _precursor_candidate_assessment_advisories(
                response.get("precursor_candidate_assessment"),
                candidates=[item for item in candidates if isinstance(item, dict)],
                attachment_manifest=attachment_manifest,
            ),
        )
    if group_summaries is not None:
        _log_step04_advisories(
            "candidate_discovery.precipitation_coverage",
            _significant_precipitation_coverage_advisories(
                response,
                group_summaries=group_summaries,
                attachment_manifest=attachment_manifest,
            ),
        )
    if overview_data is not None:
        system_precipitation_audit = build_system_direct_precipitation_coverage_audit(
            response,
            overview_data=overview_data,
            attachment_manifest=attachment_manifest,
            support_catalog=support_catalog,
        )
        if enforce_system_direct_precipitation_coverage:
            errors.extend(_system_direct_precipitation_coverage_errors(system_precipitation_audit))
        _log_step04_advisories(
            "candidate_discovery.system_direct_precipitation_coverage",
            _system_direct_precipitation_coverage_advisories(system_precipitation_audit),
        )

    evidence_groups = response.get("candidate_evidence", [])
    evidence_candidate_ids = [
        str(item.get("candidate_id")) for item in evidence_groups if isinstance(item, dict)
    ]
    if Counter(evidence_candidate_ids) != Counter(candidate_ids):
        errors.append("candidate_evidence must contain exactly one group for each candidate")
    for group in evidence_groups:
        if not isinstance(group, dict):
            continue
        candidate_id = str(group.get("candidate_id"))
        if candidate_id not in candidate_id_set:
            errors.append(f"candidate_evidence references unknown candidate_id: {candidate_id}")
        for item in group.get("evidence_items", []):
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get("asset_id"))
            source = attachment_by_id.get(asset_id)
            if source is None:
                errors.append(f"candidate evidence references unknown asset_id: {asset_id}")
                continue
            if item.get("product_id") != source.get("product_id"):
                errors.append(f"candidate evidence product mismatch asset_id={asset_id}")
            allowed_hours = {int(value) for value in source.get("forecast_hours", [])}
            invalid_hours = sorted(
                {int(value) for value in item.get("forecast_hours", [])} - allowed_hours
            )
            if invalid_hours:
                errors.append(
                    f"candidate evidence has invalid forecast hours asset_id={asset_id}: {invalid_hours}"
                )

    candidate_by_id = {
        str(item.get("candidate_id")): item
        for item in candidates
        if isinstance(item, dict)
    }
    evidence_by_id = {
        str(item.get("candidate_id")): item
        for item in evidence_groups
        if isinstance(item, dict)
    }
    for candidate_id, candidate in candidate_by_id.items():
        peak_groups = _contiguous_hour_groups(candidate.get("peak_forecast_hours", []))
        if len(peak_groups) <= 1:
            continue
        group = evidence_by_id.get(candidate_id, {})
        evidence_hours = {
            int(hour)
            for item in group.get("evidence_items", [])
            if isinstance(item, dict)
            for hour in item.get("forecast_hours", [])
        }
        related_asset_hours = {
            int(hour)
            for asset_id in candidate.get("related_overview_asset_ids", [])
            for hour in attachment_by_id.get(str(asset_id), {}).get("forecast_hours", [])
        }
        peak_group_notes: list[str] = []
        for peak_group in peak_groups:
            peak_values = set(peak_group)
            if not peak_values & evidence_hours:
                peak_group_notes.append(
                    f"{candidate_id} evidence does not cover separated peak group={peak_group}"
                )
            if not peak_values & related_asset_hours:
                peak_group_notes.append(
                    f"{candidate_id} related overview assets do not cover separated peak group={peak_group}"
                )
        _log_step04_advisories("candidate_discovery.peak_evidence", peak_group_notes)

    pass2_required = response.get("pass2_required")
    if candidates and pass2_required is not True:
        errors.append("pass2_required must be true when candidates exist")
    if not candidates and pass2_required is not False:
        errors.append("pass2_required must be false when no candidates exist")
    errors.extend(validate_candidate_discovery_narrative_language(response))
    return errors


def _candidate_evidence_for(
    discovery_response: dict[str, Any],
    candidate_id: str,
) -> dict[str, Any]:
    matches = [
        item
        for item in discovery_response.get("candidate_evidence", [])
        if isinstance(item, dict) and str(item.get("candidate_id")) == candidate_id
    ]
    if len(matches) != 1:
        raise Pass1Error(
            f"candidate evidence cardinality mismatch candidate_id={candidate_id} count={len(matches)}"
        )
    return matches[0]


def _related_precursor_opportunities(
    discovery_response: dict[str, Any], candidate_id: str
) -> list[dict[str, Any]]:
    """Return immutable Step04 opportunity provenance relevant to one candidate."""
    return [
        item
        for item in discovery_response.get("precursor_opportunities", [])
        if isinstance(item, dict)
        and (
            candidate_id in item.get("related_direct_candidate_ids", [])
            or item.get("created_candidate_id") == candidate_id
        )
    ]



def _expanded_plan_quantitative_contract_errors(plan: dict[str, Any]) -> list[str]:
    """Validate already-expanded plans, including previously cached checkpoints.

    Compact generation preflight alone cannot protect resumed Step04 plans.
    Only the one-claim classes that Step07 already enforces are checked; multi-
    claim image interpretations retain their existing shadow-mode treatment.
    """
    errors: list[str] = []
    for check in plan.get("variable_checks", []):
        if not isinstance(check, dict):
            continue
        contract = check.get("atomic_claim_contract")
        claims = contract.get("canonical_claims") if isinstance(contract, dict) else None
        if not isinstance(claims, list) or len(claims) != 1 or not isinstance(claims[0], dict):
            continue
        claim = claims[0]
        state = str(claim.get("expected_state") or "")
        quantifier = str(claim.get("scope_quantifier") or "")
        scope = claim.get("quantitative_scope")
        check_id = str(check.get("check_id") or "")
        if (
            quantifier == "specific"
            and state in {"greater_than", "less_than", "increase", "decrease", "strengthen", "weaken"}
            and not isinstance(scope, dict)
        ) or (
            quantifier == "majority" and state == "not_strengthen"
            and not isinstance(scope, dict)
        ):
            errors.append(
                "QUANTITATIVE_SCOPE_CONTRACT_INVALID "
                f"check_id={check_id} claim_key={claim.get('claim_key')} "
                "enforced quantitative_scope absent in expanded/checkpointed plan"
            )
            continue
        if quantifier == "majority" and state == "not_strengthen" and any(
            opposite_majority_not_strengthen_wording(text) for text in (
                claim.get("claim_statement"), check.get("expected_signal")
            )
        ):
            errors.append(
                "QUANTITATIVE_MAJORITY_POLARITY_MISMATCH "
                f"check_id={check_id} claim_key={claim.get('claim_key')} "
                "positive majority natural-language proposition contradicts not_strengthen expected_state"
            )
        if quantifier == "specific" and state in {"greater_than", "less_than", "increase", "decrease", "strengthen", "weaken"} and isinstance(scope, dict):
            for proposition in dict.fromkeys((
                str(claim.get("claim_statement") or ""),
                str(check.get("expected_signal") or ""),
            )):
                fixed_cutoff = absolute_threshold_in_hour_comparison(proposition)
                if fixed_cutoff is not None:
                    errors.append(
                        "QUANTITATIVE_ABSOLUTE_THRESHOLD_CONTRACT_MISMATCH "
                        f"check_id={check_id} claim_key={claim.get('claim_key')} "
                        f"fixed_cutoff={fixed_cutoff!r}; "
                        "specific-v2 relative time comparisons cannot verify an absolute numeric cutoff; "
                        "regenerate the check preserving the original threshold proposition"
                    )
                    break
        if quantifier == "specific" and state in {"greater_than", "less_than"} and isinstance(scope, dict):
            for proposition in dict.fromkeys((
                str(claim.get("claim_statement") or ""),
                str(check.get("expected_signal") or ""),
            )):
                expected = inconsistent_pairwise_hour_order(
                    proposition, scope.get("comparison_forecast_hours")
                )
                if expected is not None:
                    errors.append(
                        "QUANTITATIVE_SCOPE_COMPARISON_IDENTITY_MISMATCH "
                        f"check_id={check_id} claim_key={claim.get('claim_key')} "
                        f"expected_comparison_forecast_hours={list(expected)} "
                        f"actual={scope.get('comparison_forecast_hours')}; "
                        "repair=regenerate candidate plan without reversing the original hypothesis"
                    )
                    break
    return errors


def validate_candidate_plan_response(
    response: dict[str, Any],
    *,
    schema: dict[str, Any],
    final_schema: dict[str, Any],
    package_id: str,
    candidate: dict[str, Any],
    candidate_evidence: dict[str, Any],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    feature_catalog: dict[str, Any],
    operational_region_catalog: dict[str, Any],
    require_system_direct_precipitation_anchor: bool = True,
) -> list[str]:
    """Validate one candidate plan by embedding it in the canonical final contract."""
    errors = _validate_jsonschema(response, schema)
    candidate_id = str(candidate.get("candidate_id"))
    if response.get("package_id") != package_id:
        errors.append("package_id does not match Step04 input package")
    if response.get("candidate_id") != candidate_id:
        errors.append(f"candidate_id must remain immutable: {candidate_id}")
    plan = response.get("candidate_analysis_plan")
    requests = response.get("analysis_data_requests", [])
    if not isinstance(plan, dict):
        return errors + ["candidate_analysis_plan must be an object"]
    if not isinstance(requests, list):
        return errors + ["analysis_data_requests must be an array"]
    errors.extend(_expanded_plan_quantitative_contract_errors(plan))
    errors.extend(_expanded_plan_spatial_support_errors(plan, candidate))

    isolated_candidate = json.loads(json.dumps(candidate, ensure_ascii=False))
    isolated_candidate["candidate_unit_type"] = _candidate_unit_type(isolated_candidate)
    isolated_candidate["discovery_order"] = 1
    isolated_candidate.pop("priority_rank", None)
    isolated_response = {
        "package_id": package_id,
        "analysis_coverage": {
            "coverage_status": "complete",
            "overview_attachment_count": int(attachment_manifest.get("attachment_count", 0)),
        },
        "run_summary": {"candidate_count": 1, "summary_text": "isolated candidate plan validation"},
        "candidates": [isolated_candidate],
        "candidate_evidence": [candidate_evidence],
        "candidate_analysis_plans": [plan],
        "analysis_data_requests": requests,
        "global_uncertainties": [],
        "pass2_required": True,
    }
    errors.extend(
        validate_final_response(
            isolated_response,
            schema=final_schema,
            package_id=package_id,
            attachment_manifest=attachment_manifest,
            support_catalog=support_catalog,
            feature_catalog=feature_catalog,
            operational_region_catalog=operational_region_catalog,
            require_system_direct_precipitation_anchor=require_system_direct_precipitation_anchor,
        )
    )
    return errors


def _assert_no_private_paths(value: Any, *, label: str) -> None:
    serialized = json.dumps(value, ensure_ascii=False)
    if "source_panels" in serialized or "absolute_path" in serialized:
        raise ValueError(f"{label} must not contain source panel paths")
    if re.search(r"(?:[A-Za-z]:[\\/]|file://|\\\\\\\\)", serialized, flags=re.IGNORECASE):
        raise ValueError(f"{label} must not contain local absolute paths")


def _candidate_discovery_asset_index(
    attachment_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Return only asset metadata required during candidate discovery."""
    fields = ("asset_id", "product_id", "forecast_hours")
    return {
        "asset_count": int(attachment_manifest.get("attachment_count", 0)),
        "assets": [
            {field: item.get(field) for field in fields}
            for item in attachment_manifest.get("attachments", [])
            if isinstance(item, dict)
        ],
    }


def _candidate_discovery_group_summary_projection(
    group_summaries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build a transport-only projection for candidate discovery.

    Canonical group-summary files and downstream validation continue to use the
    original validated summaries.  This projection only removes repeated
    provenance fields and bounds narrative text before the provider request.
    """
    projected: list[dict[str, Any]] = []
    for summary in group_summaries:
        if not isinstance(summary, dict):
            continue

        rows: list[dict[str, Any]] = []
        for row in summary.get("integrated_observations", []):
            if not isinstance(row, dict):
                continue
            direct_signal = _normalize_direct_precipitation_signal(
                row.get("direct_precipitation_signal")
            )
            direct_signal["rationale"] = _truncate_utf8_text(
                str(direct_signal.get("rationale", "")), max_bytes=120
            )
            rows.append(
                {
                    "asset_id": str(row.get("asset_id", "")),
                    "product_id": str(row.get("product_id", "")),
                    "forecast_hours": [int(value) for value in row.get("forecast_hours", [])],
                    "observed_signals": [
                        _truncate_utf8_text(value, max_bytes=96)
                        for value in _unique_nonempty_strings(row.get("observed_signals", []))[:2]
                    ],
                    "spatial_pattern": _truncate_utf8_text(
                        str(row.get("spatial_pattern", "")), max_bytes=160
                    ),
                    "temporal_evolution": _truncate_utf8_text(
                        str(row.get("temporal_evolution", "")), max_bytes=160
                    ),
                    "confidence": str(row.get("confidence", "medium")),
                    "limitations": [
                        _truncate_utf8_text(value, max_bytes=96)
                        for value in _unique_nonempty_strings(row.get("limitations", []))[:1]
                    ],
                    "direct_precipitation_signal": direct_signal,
                }
            )

        audit = summary.get("direct_precipitation_audit")
        if isinstance(audit, dict):
            audit_projection = {
                "status": audit.get("status"),
                "source_asset_ids": list(audit.get("source_asset_ids", [])),
                "significant_forecast_hours": [
                    int(value) for value in audit.get("significant_forecast_hours", [])
                ],
                "rationale": _truncate_utf8_text(
                    str(audit.get("rationale", "")), max_bytes=160
                ),
            }
        else:
            audit_projection = _direct_precipitation_audit_from_rows(rows)

        item = {
            "summary_id": summary.get("summary_id"),
            "analysis_tier": summary.get("analysis_tier"),
            "group_id": summary.get("group_id"),
            "forecast_hours": [int(value) for value in summary.get("forecast_hours", [])],
            "direct_precipitation_audit": audit_projection,
            "integrated_observations": rows,
            "cross_product_relationships": [
                _truncate_utf8_text(value, max_bytes=160)
                for value in _unique_nonempty_strings(
                    summary.get("cross_product_relationships", [])
                )[:1]
            ],
            "uncertainty": [
                _truncate_utf8_text(value, max_bytes=120)
                for value in _unique_nonempty_strings(summary.get("uncertainty", []))[:1]
            ],
            "omitted_or_unresolved_signals": [
                _truncate_utf8_text(value, max_bytes=120)
                for value in _unique_nonempty_strings(
                    summary.get("omitted_or_unresolved_signals", [])
                )[:1]
            ],
        }

        if json_utf8_bytes(item) > CANDIDATE_DISCOVERY_GROUP_SUMMARY_COMPACTION_TRIGGER_BYTES:
            for row in rows:
                row["observed_signals"] = [
                    _truncate_utf8_text(value, max_bytes=72)
                    for value in row.get("observed_signals", [])[:2]
                ]
                row["spatial_pattern"] = _truncate_utf8_text(
                    str(row.get("spatial_pattern", "")), max_bytes=112
                )
                row["temporal_evolution"] = _truncate_utf8_text(
                    str(row.get("temporal_evolution", "")), max_bytes=112
                )
                row["limitations"] = [
                    _truncate_utf8_text(value, max_bytes=72)
                    for value in row.get("limitations", [])[:1]
                ]
                signal = row.get("direct_precipitation_signal")
                if isinstance(signal, dict):
                    signal["rationale"] = _truncate_utf8_text(
                        str(signal.get("rationale", "")), max_bytes=96
                    )
            item["cross_product_relationships"] = [
                _truncate_utf8_text(value, max_bytes=112)
                for value in item.get("cross_product_relationships", [])[:1]
            ]
            item["uncertainty"] = [
                _truncate_utf8_text(value, max_bytes=96)
                for value in item.get("uncertainty", [])[:1]
            ]
            item["omitted_or_unresolved_signals"] = [
                _truncate_utf8_text(value, max_bytes=96)
                for value in item.get("omitted_or_unresolved_signals", [])[:1]
            ]

        projected.append(item)
    return projected


def _candidate_discovery_payload_component_bytes(
    input_value: dict[str, Any],
) -> dict[str, int]:
    keys = (
        "group_summaries",
        "asset_index",
        "supported_spatial_products",
        "precipitation_peak_guidance",
        "system_direct_precipitation_coverage_guidance",
        "candidate_operational_scope",
        "precursor_candidate_contract",
        "response_compactness",
    )
    return {
        f"{key}_utf8_bytes": json_utf8_bytes(input_value.get(key))
        for key in keys
        if key in input_value
    }


def build_candidate_discovery_messages(
    *,
    prompt_template: str,
    package_id: str,
    group_summaries: list[dict[str, Any]],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    overview_data: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    discovery_group_summaries = _candidate_discovery_group_summary_projection(group_summaries)
    discovery_input = {
        "package_id": package_id,
        "split_synthesis_contract_version": SPLIT_SYNTHESIS_CONTRACT_VERSION,
        "candidate_discovery_meteorological_qc_version": CANDIDATE_DISCOVERY_METEOROLOGICAL_QC_VERSION,
        "candidate_discovery_input_projection_version": CANDIDATE_DISCOVERY_INPUT_PROJECTION_VERSION,
        "group_summaries": discovery_group_summaries,
        "asset_index": _candidate_discovery_asset_index(attachment_manifest),
        "supported_spatial_products": _minimal_support_catalog(support_catalog).get(
            "spatial_field_products", []
        ),
        "precipitation_peak_guidance": build_precipitation_peak_guidance(
            overview_data, minimum_regional_max_mm=_peak_anchor_minimum_mm(support_catalog)
        ),
        "system_direct_precipitation_coverage_guidance": build_system_direct_precipitation_coverage_guidance(
            overview_data, support_catalog=support_catalog
        ),
        "candidate_operational_scope": _candidate_discovery_operational_scope(overview_data),
        "precursor_candidate_contract": {
            "scan_required": True,
            "minimum_output_when_qualified": 1,
            "output_when_no_qualified_signal": 0,
            "independence_rule": (
                "모델 강수 예측 또는 mixed 후보와 같은 시간·영역·메커니즘이면 별도 precursor 후보로 중복 생성하지 않습니다."
            ),
            "qualification_rules": [
                "모델 강수 예측 상태가 weak, absent 또는 uncertain입니다.",
                "서로 다른 선행환경 범주가 둘 이상이며 수분 범주 외에 역학적 강제력 범주가 하나 이상 포함됩니다.",
                "선행환경 신호가 같은 시간과 후보 핵심영역에서 중첩됩니다.",
                "기존 direct_precipitation 또는 mixed 후보로 설명되지 않는 독립적인 감시 필요가 있습니다.",
            ],
        },
        "response_compactness": {
            "rules": [
                "후보 수나 근거 범위를 줄이지 말고 후보별 중복 설명만 제거합니다.",
                "supporting_reasons, opposing_reasons, uncertainties는 각각 최대 두 개의 짧은 문장으로 작성합니다.",
                "diagnostic_plan은 후보 검증에 필요한 서로 다른 diagnostic_id만 최대 네 개 제시합니다.",
                "candidate_evidence는 후보별 핵심 asset만 인용하고 asset마다 reason 한 문장만 작성합니다.",
                "같은 기상학적 설명을 후보, evidence, uncertainty 필드에 반복하지 않습니다.",
                "각 문자열은 간결한 한국어 한두 문장으로 끝내고 Markdown이나 해설을 추가하지 않습니다.",
                "group_summaries.direct_precipitation_audit.status=present는 원본 영상의 직접강수 관찰 보존용입니다. 운영권역 밖 source corridor 신호를 그 이유만으로 Candidate로 만들지 않습니다.",
                "direct_precipitation/mixed 후보의 candidate_area는 candidate_operational_scope.eligible_forecast_regions 중 하나 이상과 실제로 겹쳐야 하며, 맞추기 위해 bbox를 임의로 확대하지 않습니다.",
                "system_direct_precipitation_coverage_guidance의 candidate reference threshold 이상 정량 신호는 SYSTEM 독립 감사 대상이므로 후보 coverage에서 누락하지 않습니다.",
                "1.0 mm 이상이지만 candidate reference threshold 미만인 SYSTEM 미커버 신호는 기록 대상이며 그 값만으로 후보 생성을 강제하지 않습니다.",
                "precipitation_peak_guidance의 정량 peak는 참고 anchor로 사용하되 최종 peak·merge/split은 AI가 판단하고, overview 그룹 시작시각을 자동 peak로 사용하지 않습니다.",
                "유효하고 독립적인 선행환경 신호가 있으면 precursor_environment 후보를 최소 1개 유지하고, 없으면 생성하지 않습니다.",
            ]
        },
    }
    _assert_no_private_paths(discovery_input, label="candidate discovery input")
    return [
        {
            "role": "system",
            "content": (
                "엄격한 후보 탐색 JSON 객체 하나만 반환하십시오. "
                "분석계획이나 자료요청은 생성하지 마십시오. "
                "최상위에는 candidates, candidate_evidence, global_uncertainties, "
                "precursor_candidate_assessment 네 필드를 반드시 포함하고, "
                "단일 후보도 candidates 배열 안에 넣으십시오. "
                "JSON 키·식별자·enum 값은 계약대로 유지하고, 후보명·가설·근거·목적·불확실성 등 "
                "사람이 읽는 모든 설명 문자열은 한국어로 작성하십시오."
            ),
        },
        {
            "role": "user",
            "content": prompt_template.strip()
            + "\n\n## candidate discovery input\n"
            + json.dumps(discovery_input, ensure_ascii=False),
        },
    ], discovery_input


def build_candidate_discovery_length_retry_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Retry candidate discovery once with stricter prose compactness.

    This recovery is intentionally narrow: it reuses the original input and
    response schema, does not reduce candidate/evidence coverage, and is only
    used after the provider reports ``incomplete_reason=max_output_tokens``.
    """
    correction = {
        "task": (
            "직전 candidate discovery 응답이 출력 길이 제한으로 중단되었습니다. "
            "동일한 입력과 동일한 판단 기준으로 처음부터 완전한 JSON 객체 하나를 다시 생성하십시오."
        ),
        "rules": [
            "후보 수, 직접강수 coverage, 정량 peak coverage, precursor 감시 필요성을 줄여 길이를 맞추지 마십시오.",
            "같은 기상학적 설명을 candidates, candidate_evidence, global_uncertainties 사이에 반복하지 마십시오.",
            "supporting_reasons, opposing_reasons, uncertainties는 각 항목을 짧은 한 문장으로 작성하고 꼭 필요한 항목만 유지하십시오.",
            "candidate_evidence.reason과 precursor_candidate_assessment의 설명도 중복 없이 한 문장으로 작성하십시오.",
            "JSON 키·식별자·enum 값과 스키마 계약은 그대로 유지하십시오.",
            "Markdown, 서론, 결론, 부연설명 없이 JSON 객체만 반환하고 마지막 } 직후 종료하십시오.",
        ],
    }
    return [
        *messages,
        {"role": "user", "content": json.dumps(correction, ensure_ascii=False)},
    ]


def add_prompt_only_schema_constraint(
    messages: list[dict[str, Any]],
    response_schema: dict[str, Any],
) -> list[dict[str, Any]]:
    """Give prompt-only fallback calls the same explicit stage contract."""
    return [
        *messages,
        {
            "role": "user",
            "content": (
                "JSON만 반환하십시오. 응답은 단계 스키마를 정확히 만족해야 합니다. "
                "legacy 별칭이나 스키마 밖의 필드를 사용하지 마십시오. "
                "사람이 읽는 모든 설명 문자열은 한국어로 작성하십시오.\n\n"
                "## response schema\n"
                + json.dumps(response_schema, ensure_ascii=False)
            ),
        },
    ]


def _related_group_summaries(
    candidate: dict[str, Any],
    group_summaries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    related_assets = {str(value) for value in candidate.get("related_overview_asset_ids", [])}
    selected = [
        summary
        for summary in group_summaries
        if related_assets & {str(value) for value in summary.get("source_asset_ids", [])}
    ]
    if not selected:
        raise Pass1Error(
            f"candidate has no related group summaries candidate_id={candidate.get('candidate_id')}"
        )
    return selected


def _related_asset_index(
    candidate: dict[str, Any],
    attachment_manifest: dict[str, Any],
) -> dict[str, Any]:
    related = {str(value) for value in candidate.get("related_overview_asset_ids", [])}
    minimal = _minimal_asset_index(attachment_manifest)
    assets = [item for item in minimal["assets"] if str(item.get("asset_id")) in related]
    return {"asset_count": len(assets), "assets": assets}


def _bbox_contains(outer: dict[str, float], inner: dict[str, float]) -> bool:
    return (
        outer["west"] <= inner["west"] <= inner["east"] <= outer["east"]
        and outer["south"] <= inner["south"] <= inner["north"] <= outer["north"]
    )


def _point_in_bbox(longitude: float, latitude: float, bbox: dict[str, float]) -> bool:
    return (
        bbox["west"] <= longitude <= bbox["east"]
        and bbox["south"] <= latitude <= bbox["north"]
    )


def _segment_intersects_bbox(
    start_longitude: float,
    start_latitude: float,
    end_longitude: float,
    end_latitude: float,
    bbox: dict[str, float],
) -> bool:
    """Return whether a lon/lat line segment intersects an axis-aligned bbox."""
    dx = end_longitude - start_longitude
    dy = end_latitude - start_latitude
    p = (-dx, dx, -dy, dy)
    q = (
        start_longitude - bbox["west"],
        bbox["east"] - start_longitude,
        start_latitude - bbox["south"],
        bbox["north"] - start_latitude,
    )
    lower, upper = 0.0, 1.0
    for pi, qi in zip(p, q):
        if abs(pi) < 1.0e-12:
            if qi < 0.0:
                return False
            continue
        ratio = qi / pi
        if pi < 0.0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return False
    return True


def _normalized_cross_section_line(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    try:
        line = {
            "start_latitude": float(value["start_latitude"]),
            "start_longitude": float(value["start_longitude"]),
            "end_latitude": float(value["end_latitude"]),
            "end_longitude": float(value["end_longitude"]),
            "selection_reason": str(value["selection_reason"]).strip(),
        }
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90.0 <= line["start_latitude"] <= 90.0):
        return None
    if not (-90.0 <= line["end_latitude"] <= 90.0):
        return None
    if not (-180.0 <= line["start_longitude"] <= 180.0):
        return None
    if not (-180.0 <= line["end_longitude"] <= 180.0):
        return None
    if not line["selection_reason"] or len(line["selection_reason"]) > 200:
        return None
    if (
        abs(line["start_latitude"] - line["end_latitude"]) < 1.0e-6
        and abs(line["start_longitude"] - line["end_longitude"]) < 1.0e-6
    ):
        return None
    return line


def _cross_section_line_signature(value: Any) -> dict[str, Any] | None:
    line = _normalized_cross_section_line(value)
    if line is None:
        return None
    return {
        "start_latitude": round(float(line["start_latitude"]), 6),
        "start_longitude": round(float(line["start_longitude"]), 6),
        "end_latitude": round(float(line["end_latitude"]), 6),
        "end_longitude": round(float(line["end_longitude"]), 6),
        "selection_reason": re.sub(r"\s+", " ", str(line["selection_reason"])),
    }




def _normalized_reference_band_axis(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    try:
        axis = {
            "start_latitude": float(value["start_latitude"]),
            "start_longitude": float(value["start_longitude"]),
            "end_latitude": float(value["end_latitude"]),
            "end_longitude": float(value["end_longitude"]),
        }
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90.0 <= axis["start_latitude"] <= 90.0 and -90.0 <= axis["end_latitude"] <= 90.0):
        return None
    if not (-180.0 <= axis["start_longitude"] <= 180.0 and -180.0 <= axis["end_longitude"] <= 180.0):
        return None
    if abs(axis["start_latitude"] - axis["end_latitude"]) < 1.0e-6 and abs(axis["start_longitude"] - axis["end_longitude"]) < 1.0e-6:
        return None
    return axis


def _reference_band_axis_signature(value: Any) -> dict[str, Any] | None:
    axis = _normalized_reference_band_axis(value)
    if axis is None:
        return None
    return {k: round(float(v), 6) for k, v in axis.items()}


def _normalized_cross_section_strategy(value: Any) -> str | None:
    if value is None:
        return None
    strategy = str(value).strip()
    allowed = {"across_precipitation_band", "along_precipitation_axis", "along_moisture_inflow"}
    return strategy if strategy in allowed else None


def _line_bearing_degrees(start_lat: float, start_lon: float, end_lat: float, end_lon: float) -> float:
    mean_lat = math.radians((start_lat + end_lat) / 2.0)
    dx = (end_lon - start_lon) * math.cos(mean_lat)
    dy = end_lat - start_lat
    angle = math.degrees(math.atan2(dy, dx))
    return angle % 180.0


def _line_angle_difference_degrees(first: dict[str, Any], second: dict[str, Any]) -> float:
    a = _line_bearing_degrees(first["start_latitude"], first["start_longitude"], first["end_latitude"], first["end_longitude"])
    b = _line_bearing_degrees(second["start_latitude"], second["start_longitude"], second["end_latitude"], second["end_longitude"])
    diff = abs(a - b) % 180.0
    return min(diff, 180.0 - diff)


def _clip_segment_to_bbox(
    start_longitude: float,
    start_latitude: float,
    end_longitude: float,
    end_latitude: float,
    bbox: dict[str, float],
) -> tuple[float, float, float, float] | None:
    """Clip a lon/lat segment to an axis-aligned bbox using Liang-Barsky."""
    dx = end_longitude - start_longitude
    dy = end_latitude - start_latitude
    p = (-dx, dx, -dy, dy)
    q = (
        start_longitude - bbox["west"],
        bbox["east"] - start_longitude,
        start_latitude - bbox["south"],
        bbox["north"] - start_latitude,
    )
    lower, upper = 0.0, 1.0
    for pi, qi in zip(p, q):
        if abs(pi) < 1.0e-12:
            if qi < 0.0:
                return None
            continue
        ratio = qi / pi
        if pi < 0.0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return None
    return (
        start_longitude + lower * dx,
        start_latitude + lower * dy,
        start_longitude + upper * dx,
        start_latitude + upper * dy,
    )


def _line_length_km(line: dict[str, Any]) -> float:
    mean_lat = math.radians(
        (float(line["start_latitude"]) + float(line["end_latitude"])) / 2.0
    )
    dx = (
        (float(line["end_longitude"]) - float(line["start_longitude"]))
        * 111.32
        * math.cos(mean_lat)
    )
    dy = (float(line["end_latitude"]) - float(line["start_latitude"])) * 110.57
    return math.hypot(dx, dy)


def _expanded_candidate_section_bbox(
    candidate_bbox: dict[str, float],
    selected_bbox: dict[str, float],
) -> dict[str, float] | None:
    lon_span = candidate_bbox["east"] - candidate_bbox["west"]
    lat_span = candidate_bbox["north"] - candidate_bbox["south"]
    lon_margin = min(
        max(lon_span * ACROSS_SECTION_MARGIN_FRACTION, ACROSS_SECTION_MARGIN_MIN_DEGREES),
        ACROSS_SECTION_MARGIN_MAX_DEGREES,
    )
    lat_margin = min(
        max(lat_span * ACROSS_SECTION_MARGIN_FRACTION, ACROSS_SECTION_MARGIN_MIN_DEGREES),
        ACROSS_SECTION_MARGIN_MAX_DEGREES,
    )
    bounded = {
        "west": max(selected_bbox["west"], candidate_bbox["west"] - lon_margin),
        "east": min(selected_bbox["east"], candidate_bbox["east"] + lon_margin),
        "south": max(selected_bbox["south"], candidate_bbox["south"] - lat_margin),
        "north": min(selected_bbox["north"], candidate_bbox["north"] + lat_margin),
    }
    if bounded["west"] >= bounded["east"] or bounded["south"] >= bounded["north"]:
        return None
    return bounded


def _canonical_cross_section_line_orientation(
    *,
    line: dict[str, Any],
    strategy: str,
    reference_axis: dict[str, Any],
    selected_bbox: dict[str, float],
    candidate_bbox: dict[str, float],
) -> dict[str, Any] | None:
    """Align and bound an AI-selected vertical section.

    The provider selects the strategy, reference axis, approximate location and
    rationale. Python corrects only contradictory geometry. Across-band cuts are
    additionally limited to the candidate core plus a small margin and to 450 km
    so a broad context domain cannot create a synoptic-scale section.
    """
    if strategy not in {"across_precipitation_band", "along_precipitation_axis"}:
        return line

    original_angle = _line_angle_difference_degrees(line, reference_axis)
    original_length_km = _line_length_km(line)
    angle_is_valid = (
        strategy == "across_precipitation_band"
        and 60.0 <= original_angle <= 120.0
    ) or (strategy == "along_precipitation_axis" and original_angle <= 30.0)
    length_is_valid = not (
        strategy == "across_precipitation_band"
        and original_length_km > ACROSS_SECTION_MAX_LENGTH_KM
    )
    if angle_is_valid and length_is_valid:
        return line

    midpoint_lon = (line["start_longitude"] + line["end_longitude"]) / 2.0
    midpoint_lat = (line["start_latitude"] + line["end_latitude"]) / 2.0
    if _point_in_bbox(midpoint_lon, midpoint_lat, candidate_bbox):
        anchor_lon, anchor_lat = midpoint_lon, midpoint_lat
    else:
        clipped_to_candidate = _clip_segment_to_bbox(
            line["start_longitude"],
            line["start_latitude"],
            line["end_longitude"],
            line["end_latitude"],
            candidate_bbox,
        )
        if clipped_to_candidate is None:
            return None
        c_start_lon, c_start_lat, c_end_lon, c_end_lat = clipped_to_candidate
        anchor_lon = (c_start_lon + c_end_lon) / 2.0
        anchor_lat = (c_start_lat + c_end_lat) / 2.0

    axis_bearing = _line_bearing_degrees(
        reference_axis["start_latitude"],
        reference_axis["start_longitude"],
        reference_axis["end_latitude"],
        reference_axis["end_longitude"],
    )
    target_bearing = (
        (axis_bearing + 90.0) % 180.0
        if strategy == "across_precipitation_band"
        else axis_bearing
    )

    desired_length_km = original_length_km
    effective_bbox = selected_bbox
    if strategy == "across_precipitation_band":
        desired_length_km = min(desired_length_km, ACROSS_SECTION_MAX_LENGTH_KM)
        effective_bbox = _expanded_candidate_section_bbox(candidate_bbox, selected_bbox)
        if effective_bbox is None:
            return None

    target_radians = math.radians(target_bearing)
    unit_x = math.cos(target_radians)
    unit_y = math.sin(target_radians)
    half_length_km = max(desired_length_km / 2.0, 1.0)
    km_per_lon_degree = max(111.32 * abs(math.cos(math.radians(anchor_lat))), 1.0e-6)
    km_per_lat_degree = 110.57
    start_lon = anchor_lon - (half_length_km * unit_x) / km_per_lon_degree
    start_lat = anchor_lat - (half_length_km * unit_y) / km_per_lat_degree
    end_lon = anchor_lon + (half_length_km * unit_x) / km_per_lon_degree
    end_lat = anchor_lat + (half_length_km * unit_y) / km_per_lat_degree
    clipped = _clip_segment_to_bbox(
        start_lon,
        start_lat,
        end_lon,
        end_lat,
        effective_bbox,
    )
    if clipped is None:
        return None
    start_lon, start_lat, end_lon, end_lat = clipped
    canonical = _normalized_cross_section_line(
        {
            "start_latitude": start_lat,
            "start_longitude": start_lon,
            "end_latitude": end_lat,
            "end_longitude": end_lon,
            "selection_reason": line["selection_reason"],
        }
    )
    if canonical is None:
        return None
    if not _segment_intersects_bbox(
        canonical["start_longitude"],
        canonical["start_latitude"],
        canonical["end_longitude"],
        canonical["end_latitude"],
        candidate_bbox,
    ):
        return None
    if (
        strategy == "across_precipitation_band"
        and _line_length_km(canonical) > ACROSS_SECTION_MAX_LENGTH_KM + 1.0
    ):
        return None
    return canonical

def _text_mentions_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in keywords)


_SIGNAL_TEXT_COMPONENT_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "direct_precipitation": (
        re.compile(r"강수대|강수\s*(?:분포|중심|영역|량|강도|발생|지속|강화|약화|와|과)|강수"),
    ),
    "moisture_supply": (
        re.compile(r"수분\s*(?:공급|수송|플럭스)?|수증기"),
    ),
    "low_level_forcing": (
        re.compile(r"925\s*hpa\s*수렴|하층\s*수렴", re.IGNORECASE),
    ),
    "mid_upper_forcing": (
        re.compile(r"700\s*hpa\s*(?:상승|오메가|omega)|중층\s*상승|상승운동|오메가|omega", re.IGNORECASE),
    ),
    "organization_persistence": (
        re.compile(r"연직\s*(?:바람)?시어|바람\s*시어|850\s*(?:-|~|–|—)?\s*500\s*hpa\s*(?:바람|풍속|풍향)", re.IGNORECASE),
        re.compile(r"단면\s*평행\s*바람|단면평행\s*바람|풍속의?\s*연직\s*(?:분포|구조)|바람의?\s*연직\s*구조", re.IGNORECASE),
    ),
}
_EXPLICIT_MULTIVARIABLE_RELATION_WORDS = re.compile(
    r"중첩|겹(?:치|쳐|침|친|쳤)|일치|정합|대응|동반|공존|결합|연계|연동"
    r"|같은\s*(?:위치|영역)|함께\s*(?:나타|이동|강화|약화|분포)"
    r"|공간적(?:으로)?\s*(?:관계|대응|일치|중첩|결합)"
)
_VERTICAL_SHEAR_INTENT_WORDS = re.compile(
    r"연직\s*(?:바람)?시어|바람\s*시어|시어\s*(?:크기|방향|벡터|분포|구조)"
    r"|850\s*(?:-|~|–|—)?\s*500\s*hpa\s*(?:연직\s*)?(?:바람)?시어",
    re.IGNORECASE,
)
_LOW_LEVEL_INFLOW_INTENT_WORDS = re.compile(
    r"유입축|수분\s*유입|수증기\s*유입|하층\s*(?:바람|풍향|풍속|흐름)"
    r"|850\s*hpa\s*(?:바람|풍향|풍속|흐름)|남서풍\s*유입|남풍\s*유입|서풍\s*유입",
    re.IGNORECASE,
)


_EXPECTED_SIGNAL_PROCEDURAL_ENDING = re.compile(
    r"(?:확인|비교|살펴|판단|검토|분석|평가)(?:해\s*)?(?:본다|한다|합니다|하십시오|해본다)\s*[.!?]?$"
)

# ``expected_signal`` is the positive meteorological proposition that Step07
# tests.  Statements that merely say two times/areas are "different" are
# almost tautological and do not define which state would support the candidate.
# Keep this validator intentionally narrow: it rejects only generic comparative
# wording and leaves concrete magnitude/location/alignment statements to the AI.
_EXPECTED_SIGNAL_VAGUE_COMPARISON = re.compile(
    r"정량적으로\s*(?:구분|차별)(?:된다|됨)"
    r"|서로\s*(?:다르게|상이하게)\s*(?:나타난다|보인다|분포한다|확인된다)"
    r"|(?:차이|차이가)\s*(?:나타난다|존재한다|있다)"
    r"|서로\s*(?:다르다|상이하다)",
    re.IGNORECASE,
)
_EXPECTED_SIGNAL_VAGUE_CHANGE_ENDING = re.compile(
    r"(?:변화한다|달라진다|달라짐)\s*[.!?]?$"
)
_EXPECTED_SIGNAL_CONCRETE_CHANGE = re.compile(
    r"강화|약화|재강화|증가|감소|유지|지속|확대|축소"
    r"|이동|동진|서진|북상|남하|재배치|재조직|병합|분리"
    r"|크다|작다|높다|낮다|많다|적다|앞선다|늦다"
    r"|겹친다|중첩(?:된다|되며|한다)|일치|정합|어긋|불일치",
    re.IGNORECASE,
)
_EXPECTED_SIGNAL_OPPOSING_ALTERNATIVE = re.compile(
    r"(?:강화|증가|재강화)\s*(?:또는|혹은|/)\s*(?:약화|감소)"
    r"|(?:약화|감소)\s*(?:또는|혹은|/)\s*(?:강화|증가|재강화)"
    r"|(?:이동|확대|축소)\s*(?:또는|혹은|/)\s*(?:유지|정체)"
    r"|(?:유지|정체)\s*(?:또는|혹은|/)\s*(?:이동|확대|축소)",
    re.IGNORECASE,
)
_EXPLICIT_SPATIAL_MOTION = re.compile(
    r"이동|동진|서진|북상|남하|재배치|재조직|병합|분리|확대|축소",
    re.IGNORECASE,
)
_SPATIAL_MOTION_CONTEXT_SUFFIX = re.compile(
    r"^(?:한\s*)?(?:이후|이전|뒤|후|전)"
)
_SYNOPTIC_MOTION_CONTEXT = re.compile(
    r"전선|기압골|저기압|고기압|강풍대|제트|기압계"
)

# Horizontal Step05 quantitative excerpts are calculated for the operational
# regions overlapping the candidate bbox. They do not provide one aggregate
# mean over the whole candidate bbox.
_CANDIDATE_AGGREGATE_MEAN = re.compile(
    r"후보\s*(?:영역|지역)(?:\s*전체)?(?:\s*(?:의|내|에서))?\s*(?:평균값?|영역평균|지역평균)",
    re.IGNORECASE,
)

# Regional/scalar quantitative modes measure intensity statistics, not areal
# coverage or width. Spatial extent belongs to map-based modes.
_SPATIAL_EXTENT_WORDS = re.compile(
    # Scalar regional statistics can legitimately support statements such as
    # "여러 운영권역에서 평균값의 뚜렷한 강화가 없다".  Do not treat a
    # generic adjective such as "넓은 강화" as an areal-extent claim by
    # itself.  Reject only explicit area/width/coverage language or an
    # expansion/contraction claim about a spatial field.
    r"면적|공간\s*범위|분포\s*범위|폭"
    r"|(?:넓(?:은|어진|어지는|게)|좁(?:은|아진|아지는|게))\s*(?:영역|범위|분포|강수대|수렴대|상승\s*영역|상승운동\s*영역)"
    r"|(?:영역|범위|분포|강수대|수렴대|상승\s*영역|상승운동\s*영역)\s*(?:이|가)?\s*(?:넓(?:다|어지|어진)|좁(?:다|아지|아진))"
    r"|확대|축소|확장|수축",
    re.IGNORECASE,
)

# QL_MOISTURE_FLUX850_WIND850 uses moisture-transport magnitude shading and
# real 850-hPa wind arrows. There is no moisture-transport vector arrow.
_MOISTURE_VECTOR_MISLABEL = re.compile(
    r"수분\s*(?:수송|플럭스)\s*(?:벡터|화살표|흐름|방향)",
    re.IGNORECASE,
)
_MOISTURE_DIRECTION_WITHOUT_WIND = re.compile(
    r"(?:향하는|향한다|유입되는|유입된다)\s*(?:높은\s*)?수분\s*(?:수송|플럭스)"
    r"|수분\s*(?:수송|플럭스)[^.!?]{0,16}(?:북향|북동향|동향|남동향|남향|남서향|서향|북서향|향한다|유입된다)",
    re.IGNORECASE,
)
_850_WIND_EXPLICIT = re.compile(r"850\s*hpa\s*(?:바람|풍향|풍속|흐름)", re.IGNORECASE)

# Standalone horizontal moisture_supply checks are executed with
# QL_MOISTURE_FLUX850_WIND850. They do not provide total-column-water
# shading. Keep TCW wording reserved for the multivariable composite.
_TOTAL_COLUMN_WATER_WORDS = re.compile(
    r"전층\s*(?:총)?수분량|총\s*수분량|\bTCW\b|total[-\s]*column\s*water",
    re.IGNORECASE,
)

# Vertical cross-section/time-height products provide specific humidity,
# pressure vertical velocity, wind speed and along-section wind. They do not
# provide the dedicated 850-500 hPa shear diagnostic or horizontal moisture
# transport magnitude.
_VERTICAL_SECTION_HORIZONTAL_MOISTURE_WORDS = re.compile(
    r"수분\s*(?:수송|플럭스)|수증기\s*(?:수송|플럭스)|수분\s*유입축|수증기\s*유입축",
    re.IGNORECASE,
)

# The multivariable composite does not use the standalone moisture-flux panel.
# Its moisture field is QL_TCW_WIND850: total-column water shading with real
# 850-hPa wind arrows.  Prevent Step04 from asking Step07 to verify a field
# that the composite does not actually provide.
_MULTIVARIABLE_MOISTURE_FLUX_WORDS = re.compile(
    r"수분\s*(?:수송|플럭스)|수증기\s*(?:수송|플럭스)|수분\s*유입축|수증기\s*유입축",
    re.IGNORECASE,
)


# A precipitation reference may be used only as geometry/time context for a
# vertical Check. Strip only a bounded leading context phrase before deciding
# which components actually participate in an overlap/alignment proposition.
# A selector that itself asserts a cross-variable relation is intentionally not
# stripped (for example, "강수대와 고수분층이 겹치는 단면에서").
_VERTICAL_PRECIP_CONTEXT_PREFIX = re.compile(
    r"^\s*(?:F\s*\d{1,3}\s*)?(?:"
    r"(?:강수대(?:\s*중심(?:축|부)?)?|강수\s*(?:영역|중심))\s*(?:을|를)?\s*"
    r"(?:가로지르는|통과하는|관통하는|기준으로\s*(?:한|설정한)|따라\s*설정한)\s*"
    r"(?:연직\s*)?단면(?:에서|상에서|상|의)?"
    r"|(?:강수대|강수\s*(?:영역|중심))(?:\s*(?:내|부근|중심부))?\s*(?:에서|내에서|부근에서)"
    r"|(?:강수(?:량)?\s*(?:최대|피크)(?:\s*시각)?|강수\s*발생(?:\s*시각)?)\s*(?:이전|이후|전후|전|후)"
    r")\s*[,，]?\s*",
    re.IGNORECASE,
)


def _vertical_relation_semantic_text(text: str) -> str:
    """Remove only a leading precipitation geometry/time anchor for relation checks."""
    cleaned = str(text or "").strip()
    match = _VERTICAL_PRECIP_CONTEXT_PREFIX.match(cleaned)
    if match:
        cleaned = cleaned[match.end():].lstrip()
    return cleaned


def _declared_component_has_spatial_motion_claim(text: str, declared: set[str]) -> bool:
    """Return whether a non-spatial mode asks the declared component itself to move.

    A different meteorological element may still be mentioned as a temporal/context
    anchor (for example, "강수대가 이동한 이후 수렴이 약화한다").  Do not
    mistake that context for a movement claim about the declared component.
    """
    for component in declared:
        for component_pattern in _SIGNAL_TEXT_COMPONENT_PATTERNS.get(component, ()):
            for component_match in component_pattern.finditer(text):
                window_end = min(len(text), component_match.end() + 100)
                window = text[component_match.end():window_end]
                for motion_match in _EXPLICIT_SPATIAL_MOTION.finditer(window):
                    absolute_motion_start = component_match.end() + motion_match.start()
                    absolute_motion_end = component_match.end() + motion_match.end()
                    between = text[component_match.end():absolute_motion_start]
                    # If another supported signal component is named closer to the
                    # motion word, treat that element as the likely context subject.
                    if _mentioned_signal_components(between) - {component}:
                        continue
                    # Synoptic systems may also be temporal anchors rather than the
                    # actual object of this Check.
                    if _SYNOPTIC_MOTION_CONTEXT.search(between):
                        continue
                    after_motion = text[absolute_motion_end:absolute_motion_end + 12]
                    if _SPATIAL_MOTION_CONTEXT_SUFFIX.search(after_motion):
                        continue
                    return True
    return False



def _validate_ai_verification_request_intent(row: dict[str, Any]) -> None:
    """Validate only meteorological intent fields authored by the Step04 AI.

    Other variables may appear in expected_signal as temporal/context anchors
    (for example, '강수 최대 시각 이전의 하층 수렴 강화'). Such references
    are not treated as additional evidence requirements. A hard failure is
    raised only when the narrative explicitly asks for a cross-variable
    relationship involving a component that signal_components did not declare.
    """
    check_key = str(row.get("check_key") or "")
    declared = {
        str(value) for value in row.get("signal_components", []) if str(value)
    }
    text = str(row.get("expected_signal") or "").strip()
    if _EXPECTED_SIGNAL_PROCEDURAL_ENDING.search(text):
        raise ValueError(
            "EXPECTED_SIGNAL_PROCEDURAL_TEXT_INVALID "
            f"check_key={check_key} expected_signal={text!r}; "
            "repair=expected_signal must be a testable meteorological state or change, not an instruction such as 확인한다/비교한다/판단한다"
        )
    if _EXPECTED_SIGNAL_VAGUE_COMPARISON.search(text) or (
        _EXPECTED_SIGNAL_VAGUE_CHANGE_ENDING.search(text)
        and not _EXPECTED_SIGNAL_CONCRETE_CHANGE.search(text)
    ):
        raise ValueError(
            "EXPECTED_SIGNAL_VAGUE_COMPARISON_INVALID "
            f"check_key={check_key} expected_signal={text!r}; "
            "repair=state a directional or concrete meteorological proposition, such as which time/area is stronger or weaker, where the center is located, or whether the fields overlap; do not use generic claims such as 서로 다르다/정량적으로 구분된다/변화한다"
        )
    if _EXPECTED_SIGNAL_OPPOSING_ALTERNATIVE.search(text):
        raise ValueError(
            "EXPECTED_SIGNAL_AMBIGUOUS_ALTERNATIVE_INVALID "
            f"check_key={check_key} expected_signal={text!r}; "
            "repair=expected_signal must state one falsifiable expected state; do not join mutually opposing outcomes with 또는/혹은"
        )

    mode = str(row.get("verification_mode") or "")
    spatial_relation_scope = str(row.get("spatial_relation_scope") or "not_applicable")
    domain_scope = str(row.get("domain_scope") or "candidate")
    if spatial_relation_scope not in SPATIAL_RELATION_SCOPES:
        raise ValueError(
            "SPATIAL_RELATION_SCOPE_INVALID "
            f"check_key={check_key} spatial_relation_scope={spatial_relation_scope!r}"
        )
    # `spatial_relation_scope` describes the horizontal relation that the Check must
    # prove, not how wide the rendering domain is.  A vertical section may use a
    # context domain so that its endpoints extend beyond the candidate bbox, but
    # the Check is still a vertical-structure test and therefore has no horizontal
    # spatial-relation claim.  Requiring `not_applicable` here prevents the model
    # from conflating "section uses outside context" with "outside placement or
    # boundary crossing is itself the conclusion".
    if mode in {"vertical_structure", "vertical_time_evolution"} and spatial_relation_scope != "not_applicable":
        raise ValueError(
            "SPATIAL_RELATION_VERTICAL_MODE_SCOPE_MISMATCH "
            f"check_key={check_key} signal_components={sorted(declared)} "
            f"spatial_relation_scope={spatial_relation_scope} domain_scope={domain_scope} "
            f"verification_mode={mode} expected_signal={text[:500]!r}; "
            "repair=vertical_structure/vertical_time_evolution must use spatial_relation_scope=not_applicable. "
            "A vertical_structure Check may still use domain_scope=context when the section line needs a wider domain. "
            "If the intended conclusion is an outside/cross-boundary horizontal relationship, create a separate "
            "spatial_distribution/spatial_evolution/multivariable_alignment request for that relationship instead of "
            "encoding it in the vertical Check"
        )

    relation_text = (
        _vertical_relation_semantic_text(text)
        if mode in {"vertical_structure", "vertical_time_evolution"}
        else text
    )
    mentioned = _mentioned_signal_components(relation_text)
    if (
        mode in {"vertical_structure", "vertical_time_evolution"}
        and "direct_precipitation" in mentioned
        and _EXPLICIT_MULTIVARIABLE_RELATION_WORDS.search(relation_text)
    ):
        raise ValueError(
            "VERIFICATION_VERTICAL_SECTION_PRECIPITATION_CONTEXT_MISMATCH "
            f"check_key={check_key} verification_mode={mode} "
            f"declared_signal_components={sorted(declared)} "
            f"narrative_relation_components={sorted(mentioned)} "
            f"expected_signal={text[:500]!r}; "
            "repair=vertical section/time-height evidence does not directly contain precipitation. "
            "A precipitation band/center/peak may be used only as a clearly subordinate geometry or time anchor. "
            "Do not make precipitation itself a participant in an overlap/alignment conclusion. Rewrite the proposition "
            "around the declared vertical moisture_supply, mid_upper_forcing, and/or organization_persistence components. "
            "If precipitation overlap itself is decision-relevant, create a separate horizontal multivariable_alignment request"
        )

    if spatial_relation_scope in {"candidate_boundary_crossing", "candidate_external_context"}:
        if domain_scope != "context":
            raise ValueError(
                "SPATIAL_RELATION_DOMAIN_SCOPE_MISMATCH "
                f"check_key={check_key} spatial_relation_scope={spatial_relation_scope} "
                f"domain_scope={domain_scope}; repair=use domain_scope=context because this "
                "verification requires evidence outside the candidate bbox or across its boundary"
            )
        if mode not in {"spatial_distribution", "spatial_evolution", "multivariable_alignment"}:
            raise ValueError(
                "SPATIAL_RELATION_VERIFICATION_MODE_MISMATCH "
                f"check_key={check_key} signal_components={sorted(declared)} "
                f"spatial_relation_scope={spatial_relation_scope} domain_scope={domain_scope} "
                f"verification_mode={mode} expected_signal={text[:500]!r}; "
                "repair=outside/cross-boundary horizontal placement must be verified with a spatial map mode. "
                "If the original purpose is vertical structure rather than horizontal placement, keep the vertical mode "
                "but use spatial_relation_scope=not_applicable; split horizontal and vertical purposes into separate "
                "verification requests when both are needed"
            )

    if mode != "vertical_time_evolution" and _CANDIDATE_AGGREGATE_MEAN.search(text):
        raise ValueError(
            "EXPECTED_SIGNAL_CANDIDATE_AGGREGATE_UNAVAILABLE "
            f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
            "repair=horizontal Step05 quantitative evidence is provided per overlapping operational region, not as one aggregate mean over the full candidate bbox; use operational-region mean/max/ranking wording or request a spatial map instead"
        )

    if mode in {"regional_intensity", "temporal_change", "precipitation_amount"} and _SPATIAL_EXTENT_WORDS.search(text):
        raise ValueError(
            "EXPECTED_SIGNAL_SPATIAL_EXTENT_MODE_MISMATCH "
            f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
            "repair=regional/scalar quantitative modes may test mean/max/intensity but not area/width/expansion/contraction; move spatial extent to spatial_distribution or spatial_evolution and keep scalar intensity separate"
        )

    if mode == "multivariable_alignment" and "moisture_supply" in declared:
        if _MULTIVARIABLE_MOISTURE_FLUX_WORDS.search(text):
            raise ValueError(
                "VERIFICATION_MULTIVARIABLE_MOISTURE_SEMANTICS_MISMATCH "
                f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
                "repair=multivariable_composite represents moisture with total-column water (TCW) shading and real 850-hPa wind arrows, not the standalone 850-hPa moisture-transport magnitude field. "
                "Describe the moisture overlap as a high total-column-water/high-moisture area (for example, 대기 전층 수분량(TCW)이 상대적으로 큰 영역) and treat 850-hPa wind only as supplementary flow context; do not ask this check to verify moisture-transport magnitude/vector/inflow-axis alignment"
            )

    if mode in {"spatial_distribution", "regional_intensity", "temporal_change", "spatial_evolution"} and declared == {"moisture_supply"}:
        if _TOTAL_COLUMN_WATER_WORDS.search(text):
            raise ValueError(
                "VERIFICATION_STANDALONE_MOISTURE_SEMANTICS_MISMATCH "
                f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
                "repair=standalone horizontal moisture_supply is executed with QL_MOISTURE_FLUX850_WIND850, whose shading is 850-hPa moisture-transport magnitude and whose arrows are real 850-hPa wind. "
                "Do not ask this check to verify total-column water/TCW. For spatial modes describe moisture-transport magnitude/axis and, if needed, 850-hPa wind direction separately; for regional_intensity/temporal_change use moisture-transport magnitude statistics only"
            )

    if mode in {"vertical_structure", "vertical_time_evolution"} and "moisture_supply" in declared:
        if _TOTAL_COLUMN_WATER_WORDS.search(text) or _VERTICAL_SECTION_HORIZONTAL_MOISTURE_WORDS.search(text):
            raise ValueError(
                "VERIFICATION_VERTICAL_SECTION_MOISTURE_SEMANTICS_MISMATCH "
                f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
                "repair=vertical section/time-height moisture evidence is specific humidity/high-moisture-layer structure, not TCW and not horizontal 850-hPa moisture-transport magnitude. Rewrite the moisture part as specific-humidity/high-moisture-layer vertical structure"
            )

    if mode in {"vertical_structure", "vertical_time_evolution"} and "organization_persistence" in declared:
        if _VERTICAL_SHEAR_INTENT_WORDS.search(text):
            raise ValueError(
                "VERIFICATION_VERTICAL_SECTION_SHEAR_SEMANTICS_MISMATCH "
                f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
                "repair=vertical section/time-height products provide wind speed and along-section wind structure, not the dedicated 850-500 hPa vertical-wind-shear diagnostic. "
                "Remove the shear claim from this section request and create a separate signal_components=[organization_persistence] spatial_distribution/spatial_evolution request when 850-500 hPa shear is decision-relevant"
            )

    if declared == {"moisture_supply"}:
        if _MOISTURE_VECTOR_MISLABEL.search(text) or (
            _MOISTURE_DIRECTION_WITHOUT_WIND.search(text) and not _850_WIND_EXPLICIT.search(text)
        ):
            raise ValueError(
                "VERIFICATION_MOISTURE_VECTOR_SEMANTICS_MISMATCH "
                f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
                "repair=the horizontal moisture-supply panel shades 850-hPa moisture-transport magnitude while arrows are real 850-hPa wind; do not call the arrows moisture-transport vectors/flow/direction. State moisture-transport magnitude/axis and 850-hPa wind direction separately"
            )
        if mode == "temporal_change" and _850_WIND_EXPLICIT.search(text):
            raise ValueError(
                "VERIFICATION_MOISTURE_VECTOR_SEMANTICS_MISMATCH "
                f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
                "repair=temporal_change for moisture_supply is backed by operational-region moisture-transport magnitude time series and cannot establish 850-hPa wind direction; use spatial_distribution/spatial_evolution for wind-direction context"
            )

    if mode in {
        "spatial_distribution",
        "regional_intensity",
        "temporal_change",
        "precipitation_amount",
    } and _declared_component_has_spatial_motion_claim(text, declared):
        raise ValueError(
            "EXPECTED_SIGNAL_SPATIAL_EVOLUTION_MODE_MISMATCH "
            f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
            "repair=movement/reorganization/expansion/contraction of the declared meteorological component requires a separate verification_request with verification_mode=spatial_evolution; keep scalar mean/intensity/amount checks separate"
        )

    undeclared = sorted(mentioned - declared)
    if undeclared and _EXPLICIT_MULTIVARIABLE_RELATION_WORDS.search(relation_text):
        raise ValueError(
            "VERIFICATION_MULTIVARIABLE_INTENT_MISMATCH "
            f"check_key={check_key} verification_mode={str(row.get('verification_mode') or '<missing>')} "
            f"declared_signal_components={sorted(declared)} "
            f"narrative_relation_components={sorted(mentioned)} "
            f"undeclared_relation_components={undeclared} "
            f"expected_signal={text[:500]!r}; "
            "repair=if another variable is only a time/context anchor, rewrite expected_signal without an overlap/alignment claim; "
            "if the cross-variable spatial relationship is the actual target, declare all compared signal_components and use verification_mode=multivariable_alignment"
        )

    # For horizontal organization_persistence checks, Python deterministically
    # maps the request to 850-500 hPa vertical wind shear.  A shear vector is
    # V500-V850, not the actual 850-hPa inflow wind.  Prevent the AI from using
    # this component to ask about a low-level inflow axis or flow direction,
    # which would otherwise invite Step07 to over-interpret shear arrows as
    # real wind vectors.  Low-level moisture inflow belongs to moisture_supply.
    if declared == {"organization_persistence"} and mode in {"spatial_distribution", "spatial_evolution"}:
        if _LOW_LEVEL_INFLOW_INTENT_WORDS.search(text) or not _VERTICAL_SHEAR_INTENT_WORDS.search(text):
            raise ValueError(
                "VERIFICATION_WIND_STRUCTURE_SEMANTICS_MISMATCH "
                f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
                "repair=horizontal organization_persistence is executed as 850-500 hPa vertical wind shear, so expected_signal must ask only about shear magnitude/direction/spatial structure; "
                "if the actual target is the low-level inflow axis, 850-hPa flow direction, or moisture transport, use signal_components=[moisture_supply] instead"
            )

_TEMPORAL_SIGNAL_WORDS = re.compile(r"강화|약화|증가|감소|변화|이동|유지|지속|재강화|최대\s*시각")
# Strong spatial-transition wording cannot be established from an area-mean
# time series or regional maximum-location sequence alone.  A temporal check
# using these terms must also request multi-time spatial maps.
_SPATIAL_EVOLUTION_SIGNAL_WORDS = re.compile(
    r"(?:이동|동진|서진|북상|남하|재조직|병합|분리)"
    r"|(?:강수대|강수\s*(?:영역|중심|분포)|수렴\s*(?:영역|중심)|상승\s*(?:영역|중심))[^.!?]{0,24}(?:연속|이어)"
)
_TEMPORAL_CONTEXT_WORDS = re.compile(r"이후|이전|부터|까지|동안|사이|대비|전후")
_FORECAST_HOUR_TOKEN = re.compile(r"F(\d{3})", re.IGNORECASE)


def _mentioned_signal_components(text: str) -> set[str]:
    return {
        component
        for component, patterns in _SIGNAL_TEXT_COMPONENT_PATTERNS.items()
        if any(pattern.search(text) for pattern in patterns)
    }


def _validate_same_check_narrative_contract(check: dict[str, Any]) -> None:
    """Do not derive execution scope from free-form narrative text.

    Step04 now gives the AI explicit structured intent fields such as
    ``signal_components`` and ``verification_mode``. Python deterministically
    translates those fields into ``diagnostic_id``, ``check_scope`` and
    ``analysis_methods``. The derived execution contract therefore remains the
    authoritative source for evidence shape.

    ``expected_signal`` may legitimately contain temporal/context anchors such
    as "강수 최대 시각 이전" or descriptive words such as "강화" even when the
    requested evidence itself is a single-time spatial field. Re-inferring scope
    from those words creates false-positive validation failures and reverses the
    intended AI/Python responsibility boundary.

    Cross-variable intent is validated earlier on the AI-authored
    verification_request by ``_validate_ai_verification_request_intent``.
    Internal execution invariants are validated separately by the deterministic
    scope/execution-contract validators.
    """
    return None


def _enforce_component_coverage_for_signal_text(checks: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    normalized_checks = [item for item in checks if isinstance(item, dict)]
    for check in normalized_checks:
        phases = {str(value) for value in check.get("phase_keys", [])}
        if not phases:
            continue
        scope = str(check.get("domain_scope", ""))
        text = " ".join(str(check.get(field, "")) for field in ("purpose", "expected_signal", "decision_relevance"))
        needs_convergence = _text_mentions_any(text, ("925", "수렴", "convergence"))
        needs_midlevel = _text_mentions_any(text, ("700", "오메가", "omega", "상승", "ascent"))
        def same_phase_scope(other: dict[str, Any]) -> bool:
            return (
                str(other.get("domain_scope", "")) == scope
                and bool(phases & {str(v) for v in other.get("phase_keys", [])})
            )

        def covers_convergence(other: dict[str, Any]) -> bool:
            diagnostic_id = str(other.get("diagnostic_id"))
            return same_phase_scope(other) and diagnostic_id in {
                "horizontal_convergence",
                "multivariable_composite",
            }

        def covers_midlevel_ascent(other: dict[str, Any]) -> bool:
            diagnostic_id = str(other.get("diagnostic_id"))
            if not same_phase_scope(other):
                return False
            if diagnostic_id == "multivariable_composite":
                return True
            if (
                diagnostic_id == "detailed_spatial_map"
                and str(other.get("variable_role")) == "midlevel_forcing"
            ):
                return True
            # The dedicated section diagnostics read AIFS pressure-level w and
            # produce omega fields through 700 hPa.  Therefore a section check
            # that mentions 700-hPa ascent is itself direct component evidence
            # on the same phase/scope; requiring an additional 2-D omega map
            # would duplicate the requested evidence.  Horizontal placement can
            # still be requested separately when it is decision-relevant.
            return diagnostic_id in {
                "vertical_cross_section",
                "time_height_section",
            }

        if needs_convergence and not any(covers_convergence(other) for other in normalized_checks):
            errors.append(
                f"check_key={check.get('check_key')} mentions 925 hPa convergence but lacks "
                "horizontal_convergence or multivariable_composite coverage on the same event phase"
            )
        if needs_midlevel and not any(covers_midlevel_ascent(other) for other in normalized_checks):
            errors.append(
                f"check_key={check.get('check_key')} mentions 700 hPa ascent/omega but lacks "
                "direct midlevel-ascent coverage on the same event phase "
                "(detailed_spatial_map midlevel_forcing, multivariable_composite, "
                "vertical_cross_section, or time_height_section)"
            )
    return errors


def _catalog_bbox(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    aliases = {
        "west": ("west", "lon_min"),
        "east": ("east", "lon_max"),
        "south": ("south", "lat_min"),
        "north": ("north", "lat_max"),
    }
    normalized: dict[str, float] = {}
    try:
        for target, names in aliases.items():
            source = next(name for name in names if name in value)
            normalized[target] = float(value[source])
    except (StopIteration, TypeError, ValueError):
        return None
    return _normalized_bbox_for_validation(normalized)


def _available_context_domain(
    support_catalog: dict[str, Any],
    feature_catalog: dict[str, Any] | None = None,
) -> dict[str, float]:
    bboxes = [
        bbox
        for item in support_catalog.get("spatial_field_products", [])
        if isinstance(item, dict)
        for bbox in [_catalog_bbox(item.get("default_render_domain_bbox"))]
        if bbox is not None
    ]
    if feature_catalog is not None:
        for field in ("map_domain_bbox", "spatial_grid_bbox"):
            bbox = _catalog_bbox(feature_catalog.get(field))
            if bbox is not None:
                bboxes.append(bbox)
    if not bboxes:
        return {"west": -180.0, "east": 180.0, "south": -90.0, "north": 90.0}
    return {
        "west": min(item["west"] for item in bboxes),
        "east": max(item["east"] for item in bboxes),
        "south": min(item["south"] for item in bboxes),
        "north": max(item["north"] for item in bboxes),
    }


def build_candidate_plan_messages(
    *,
    prompt_template: str,
    package_id: str,
    candidate: dict[str, Any],
    candidate_evidence: dict[str, Any],
    group_summaries: list[dict[str, Any]],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    feature_catalog: dict[str, Any],
    operational_region_catalog: dict[str, Any],
    related_precursor_opportunities: list[dict[str, Any]] | None = None,
    compact_transport: bool = False,
    plan_chunk_index: int = 1,
    continuation_context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidate_id = str(candidate.get("candidate_id"))
    candidate_operational_region_overlap = _candidate_operational_region_overlap(
        candidate=candidate,
        operational_region_catalog=operational_region_catalog,
    )
    candidate_available_verification_modes = list(
        candidate_operational_region_overlap["allowed_verification_modes"]
    )
    if compact_transport:
        phase_hours = _compact_candidate_phase_hours(candidate, attachment_manifest)
        compact_input = {
            "package_id": package_id,
            "candidate_id": candidate_id,
            "immutable_candidate": candidate,
            "candidate_evidence": candidate_evidence,
            "related_precursor_opportunities": related_precursor_opportunities or [],
            "related_group_summaries": _related_group_summaries(candidate, group_summaries),
            "related_asset_index": _related_asset_index(candidate, attachment_manifest),
            "available_verification_signals": list(VERIFICATION_SIGNAL_COMPONENTS),
            "available_verification_modes": candidate_available_verification_modes,
            "candidate_operational_region_overlap": candidate_operational_region_overlap,
            "verification_mode_guidance": {
                "spatial_distribution": "한 기상요소의 한 시각 또는 선택 시각 공간분포·위치. 시간에 따른 이동은 spatial_evolution으로 분리",
                "regional_intensity": "한 기상요소의 후보지역 강도와 최대위치 정량 비교. 시간에 따른 중심 이동은 다루지 않음",
                "temporal_change": "한 기상요소의 후보지역 평균 강화·약화·최대시각. 전체 정량 시계열과 AI 선택 대표 공간장을 함께 제공하되 공간 중심 이동은 다루지 않음",
                "spatial_evolution": "한 기상요소의 여러 시각 공간구조·중심 이동·분리·재조직 변화",
                "multivariable_alignment": "강수·수분·하층수렴·중층상승 중 둘 이상 공간 중첩",
                "vertical_structure": "수분·중층상승·바람구조의 지정 단면 연직구조",
                "vertical_time_evolution": "후보영역 평균 연직구조의 시간변화",
                "precipitation_amount": "선택 시각의 6시간 강수량과 후보지역 최대위치. tp_accum_mm는 초기시각부터 해당 예측시각까지의 누적강수량이며, 6시간 누적 강수량이라는 표현은 사용하지 않음. 시간에 따른 중심 이동은 spatial_evolution으로 분리",
            },
            "available_context_domain": _available_context_domain(
                support_catalog, feature_catalog
            ),
            "operational_region_index": _minimal_operational_region_index(
                operational_region_catalog
            ),
            "feature_index": _minimal_feature_index(feature_catalog),
            "coverage_roles": list(COMPACT_COVERAGE_ROLES),
            "available_analysis_phase_types": list(phase_hours),
            "available_forecast_hours": _available_candidate_forecast_hours(
                candidate, attachment_manifest
            ),
            "candidate_peak_forecast_hours": [
                int(value) for value in candidate.get("peak_forecast_hours", [])
            ],
            "candidate_anchor_interpretation": (
                "precursor_overlap"
                if str(candidate.get("detection_basis")) == "precursor_environment"
                else "precipitation_peak"
            ),
            "phase_type_constraint": (
                "Use only listed phase types and explicit available_forecast_hours. "
                "Non-context phases must form chronological, non-overlapping, consecutive-cadence "
                "blocks. Map one contiguous candidate peak group to one peak phase and merge "
                "adjacent phases of the same type."
            ),
            "plan_chunk_index": int(plan_chunk_index),
        }
        # Keep the original model input and checkpoint semantics unchanged for
        # already feasible multi-hour candidates.
        temporal_contract = _candidate_temporal_feasibility_contract(
            candidate, attachment_manifest, support_catalog=support_catalog
        )
        if temporal_contract["added_hours"]:
            compact_input["time_feasibility_contract"] = temporal_contract
        compact_input["verification_mode_guidance"] = {
            key: value
            for key, value in compact_input["verification_mode_guidance"].items()
            if key in candidate_available_verification_modes
        }
        if continuation_context is not None:
            compact_input["continuation_context"] = continuation_context
        _assert_no_private_paths(compact_input, label=f"compact candidate plan input {candidate_id}")
        return [
            {
                "role": "system",
                "content": (
                    "AIFS 후보별 기상학적 검증계획 JSON만 반환하십시오. "
                    "AI의 역할은 언제, 어디에서, 어떤 기상요소를, 왜 추가 확인할지 결정하는 것입니다. "
                    "후보 candidate_area는 변경하지 말고, 필요한 경우 context_domain bbox를 직접 지정하십시오. "
                    "각 analysis_phase에는 실제 forecast_hours를 지정하고 verification_requests에는 "
                    "signal_components, verification_mode, domain_scope, spatial_relation_scope, phase_keys, representative_spatial_hours와 확인 이유를 작성하십시오. "
                    "diagnostic_id, variable_role, check_scope, required_signal_components, analysis_methods, "
                    "product_id, request_id, output_spec은 Python이 결정하므로 출력하지 마십시오. "
                    "단, atomic_claims[].quantitative_scope의 feature_id와 region_ids는 입력의 feature_index와 operational_region_index에서 "
                    "실제 식별자를 선택해 작성하며 새 식별자를 만들지 마십시오. "
                    "candidate_operational_region_overlap.regional_verification_available=false이면 available_verification_modes에 없는 "
                    "regional_intensity, temporal_change, precipitation_amount를 사용하거나 candidate_area를 운영권역 쪽으로 확대하지 마십시오. "
                    "precursor_environment 후보의 핵심 시각은 선행환경 중첩 대표시각으로 해석하십시오. "
                    "모든 후보의 순수 모델 강수 예측 존재 여부는 후보 계획 수용 후 SYSTEM이 별도 필수 Check로 추가하므로, "
                    "존재 여부만을 위한 verification_request를 중복 생성하지 마십시오. 모델 강수 예측 추가 검증은 이동, 연속성, "
                    "조직화, 강도 변화 등 존재 여부와 다른 특성만 다루십시오. 설명 문자열은 모두 간결한 한국어로 작성하십시오."
                ),
            },
            {
                "role": "user",
                "content": prompt_template.strip()
                + "\n\n## compact candidate planning input\n"
                + json.dumps(compact_input, ensure_ascii=False),
            },
        ], compact_input
    plan_input = {
        "package_id": package_id,
        "candidate_id": candidate_id,
        "immutable_candidate": candidate,
        "candidate_evidence": candidate_evidence,
        "related_precursor_opportunities": related_precursor_opportunities or [],
        "related_group_summaries": _related_group_summaries(candidate, group_summaries),
        "related_asset_index": _related_asset_index(candidate, attachment_manifest),
        "supported_analysis_data_requests": _minimal_support_catalog(support_catalog),
        "feature_index": _minimal_feature_index(feature_catalog),
        "operational_region_index": _minimal_operational_region_index(
            operational_region_catalog
        ),
        "candidate_operational_region_overlap": candidate_operational_region_overlap,
        "available_verification_modes": candidate_available_verification_modes,
    }
    _assert_no_private_paths(plan_input, label=f"candidate plan input {candidate_id}")
    return [
        {
            "role": "system",
            "content": (
                "후보별 엄격한 분석계획 JSON 객체 하나만 반환하십시오. "
                "최상위에는 candidate_analysis_plan과 analysis_data_requests만 포함하십시오. "
                "중첩된 hypothesis나 plan 객체 하나를 최상위 응답으로 반환하지 마십시오. "
                "모든 자료요청에는 현행 필드인 data_type, diagnostic_id, product_id, feature_ids, "
                "forecast_hours, requested_domain, cross_section_line, check_key, target_key, phase_keys, "
                "analysis_methods, purpose, priority만 사용하십시오. "
                "request_id, request_type, feature_id, derived_outputs 같은 legacy 필드는 사용하지 마십시오. "
                "explicit_bbox는 필드명이 아니라 resolution_method의 enum 값입니다. "
                "priority에는 required 또는 supplementary만 사용하십시오. "
                "immutable candidate 필드는 변경하지 마십시오. "
                "공간 중첩·공간 구조 변화 검증에 사용할 지도 범위가 AIFS 원본 0.25도 격자 중심 좌표에서 어느 한 축에 격자가 2개 미만이라면, 후보 candidate_area는 고정하고 주변 격자를 포함하는 context_domain을 설정해 해당 verification_request의 domain_scope=context로 지정하십시오. 강수 존재 확인용 작은 핵심영역은 유지하십시오. "
                "JSON 키·식별자·enum 값은 계약대로 유지하고, 분석목적·가설·근거·질문·요청 목적 등 "
                "사람이 읽는 모든 설명 문자열은 한국어로 작성하십시오."
            ),
        },
        {
            "role": "user",
            "content": prompt_template.strip()
            + "\n\n## per-candidate planning input\n"
            + json.dumps(plan_input, ensure_ascii=False),
        },
    ], plan_input


def _compact_plan_rows(value: dict[str, Any], name: str) -> list[dict[str, Any]]:
    rows = value.get(name)
    if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
        raise ValueError(f"compact candidate plan {name} must be an object array")
    return rows


def _compact_local_keys(rows: list[dict[str, Any]], field: str, *, label: str) -> set[str]:
    keys = [str(item.get(field, "")).strip() for item in rows]
    if any(not key for key in keys) or len(keys) != len(set(keys)):
        raise ValueError(f"compact candidate plan {label} has missing or duplicate {field}")
    return set(keys)


def _compact_check_combinations(rows: list[dict[str, Any]]) -> set[tuple[str, str, str, str]]:
    """Return execution-meaningful diagnostic combinations.

    A compact check is unique by diagnostic, variable role, selected domain scope,
    and analysis phase.  Actual BBOX coordinates are candidate-plan fields rather
    than per-check values.
    """
    combinations: set[tuple[str, str, str, str]] = set()
    for row in rows:
        diagnostic_id = str(row.get("diagnostic_id", ""))
        variable_role = str(row.get("variable_role", ""))
        domain_scope = str(row.get("domain_scope", ""))
        phases = row.get("phase_keys", [])
        if not isinstance(phases, list):
            raise ValueError("compact diagnostic check phase_keys must be an array")
        for phase_key in phases:
            combinations.add(
                (diagnostic_id, variable_role, domain_scope, str(phase_key))
            )
    return combinations


def _compact_phase_hours(row: dict[str, Any]) -> tuple[int, ...]:
    raw = row.get("forecast_hours", [])
    if not isinstance(raw, list):
        raise ValueError("compact analysis phase forecast_hours must be an array")
    try:
        hours = tuple(sorted({int(value) for value in raw}))
    except (TypeError, ValueError) as exc:
        raise ValueError("compact analysis phase forecast_hours must contain integers") from exc
    if not hours:
        raise ValueError("compact analysis phase requires forecast_hours")
    return hours


def _compact_phase_signature(row: dict[str, Any]) -> tuple[str, tuple[int, ...]]:
    return str(row.get("phase_type", "")).strip(), _compact_phase_hours(row)


def _contiguous_hour_groups(hours: Iterable[int], *, maximum_gap_hours: int = 12) -> list[list[int]]:
    values = sorted({int(value) for value in hours})
    if not values:
        return []
    groups: list[list[int]] = [[values[0]]]
    for value in values[1:]:
        if value - groups[-1][-1] > maximum_gap_hours:
            groups.append([value])
        else:
            groups[-1].append(value)
    return groups


def _forecast_cadence_hours(available_hours: Iterable[int]) -> int:
    values = sorted({int(value) for value in available_hours})
    differences = [right - left for left, right in zip(values, values[1:]) if right > left]
    return min(differences) if differences else 6


def _canonical_phase_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove exact phase aliases while preserving the first provider order."""
    retained: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[int, ...]]] = set()
    for row in rows:
        signature = _compact_phase_signature(row)
        if signature in seen:
            continue
        seen.add(signature)
        retained.append(row)
    return retained


def _phase_timeline_advisories(
    rows: Iterable[dict[str, Any]],
    *,
    available_hours: Iterable[int],
) -> list[str]:
    """Return non-blocking notes about AI phase segmentation choices."""
    notes: list[str] = []
    cadence = _forecast_cadence_hours(available_hours)
    phases = _canonical_phase_rows(list(rows))
    event_phases: list[tuple[int, int, str, tuple[int, ...], str]] = []
    for row in phases:
        phase_type = str(row.get("phase_type", "")).strip()
        phase_key = str(row.get("phase_key") or row.get("phase_id") or "<unknown>")
        hours = _compact_phase_hours(row)
        if phase_type == "context" or not hours:
            continue
        groups = _contiguous_hour_groups(hours, maximum_gap_hours=cadence)
        if len(groups) > 1:
            notes.append(
                "AI analysis phase contains separated forecast-hour groups "
                f"phase={phase_key} phase_type={phase_type!r} groups={groups}"
            )
        event_phases.append((min(hours), max(hours), phase_type, hours, phase_key))

    event_phases.sort(key=lambda item: (item[0], item[1], item[4]))
    for previous, current in zip(event_phases, event_phases[1:]):
        prev_start, prev_end, _prev_type, _prev_hours, prev_key = previous
        curr_start, curr_end, _curr_type, _curr_hours, curr_key = current
        if curr_start <= prev_end:
            notes.append(
                "AI non-context analysis phases overlap/interleave "
                f"phases={[prev_key, curr_key]} intervals={[(prev_start, prev_end), (curr_start, curr_end)]}"
            )
    return list(dict.fromkeys(notes))


def _validate_phase_timeline(
    rows: Iterable[dict[str, Any]],
    *,
    available_hours: Iterable[int],
    candidate_peak_hours: Iterable[int] = (),
) -> list[str]:
    """Validate only executable phase-timeline contract.

    Phase segmentation, overlap, and whether separated peaks share one phase are
    AI planning judgments. Python only hard-fails when an AI-declared candidate
    peak is omitted from every peak phase.
    """
    errors: list[str] = []
    phases = _canonical_phase_rows(list(rows))
    peak_rows: list[tuple[set[int], str]] = []
    for row in phases:
        phase_type = str(row.get("phase_type", "")).strip()
        phase_key = str(row.get("phase_key") or row.get("phase_id") or "<unknown>")
        hours = _compact_phase_hours(row)
        if phase_type == "peak":
            peak_rows.append((set(hours), phase_key))

    candidate_peak_set = {int(value) for value in candidate_peak_hours}
    covered_peak_hours = {hour for hours, _key in peak_rows for hour in hours}
    missing_peak_hours = sorted(candidate_peak_set - covered_peak_hours)
    if missing_peak_hours:
        errors.append(
            "peak phases must cover every AI-declared candidate peak hour "
            f"missing={missing_peak_hours}"
        )
    return errors

def _mentioned_forecast_hours(text: str, available_hours: Iterable[int]) -> set[int]:
    """Extract explicit F-hour mentions, expanding ranges on the available schedule."""
    available = sorted({int(value) for value in available_hours})
    mentioned: set[int] = set()
    pattern = re.compile(r"F\s*(\d{1,3})(?:\s*[-~–—]\s*F?\s*(\d{1,3}))?", re.IGNORECASE)
    for match in pattern.finditer(str(text)):
        start = int(match.group(1))
        end_text = match.group(2)
        if end_text is None:
            mentioned.add(start)
            continue
        end = int(end_text)
        lower, upper = sorted((start, end))
        covered = [hour for hour in available if lower <= hour <= upper]
        mentioned.update(covered or {start, end})
    return mentioned


def _unique_existing_phase_key_for_hour(
    hour: int,
    *,
    all_phase_rows: Iterable[dict[str, Any]],
) -> str | None:
    """Return the unique existing phase that owns ``hour``.

    Non-context event phases take precedence over context phases. Exact phase
    restatements across continuation chunks are treated as one semantic phase.
    Ambiguous or uncovered hours return ``None``; this helper never invents a
    new phase or forecast hour.
    """
    event_keys: list[str] = []
    context_keys: list[str] = []
    seen_signatures: set[tuple[str, tuple[int, ...]]] = set()
    for row in all_phase_rows:
        phase_key = str(row.get("phase_key", "")).strip()
        if not phase_key or int(hour) not in set(_compact_phase_hours(row)):
            continue
        signature = _compact_phase_signature(row)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        if str(row.get("phase_type", "")).strip() == "context":
            context_keys.append(phase_key)
        else:
            event_keys.append(phase_key)
    candidates = event_keys or context_keys
    return candidates[0] if len(candidates) == 1 else None


def _canonicalize_check_phase_keys_for_explicit_hours(
    check: dict[str, Any],
    *,
    all_phase_rows: Iterable[dict[str, Any]],
    available_hours: Iterable[int],
    candidate_id: str,
) -> list[int]:
    """Attach existing phases needed by explicit F-hour mentions in check text.

    The provider can legitimately describe a comparison such as ``F006 대비
    F012 이후 약화`` while referencing only the post-peak phase.  The text then
    requests evidence at F006, but the executable check would omit it.  When the
    mentioned hour already belongs to a uniquely resolved analysis phase, add
    that phase key deterministically instead of rejecting the complete plan.

    This function never invents a phase or forecast hour.  Hours not represented
    by any analysis phase are returned so the caller can keep the semantic error.
    Non-context event phases take precedence over an overlapping context phase.
    """
    mentioned_hours: set[int] = set()
    for field in ("purpose", "expected_signal", "decision_relevance"):
        mentioned_hours.update(
            _mentioned_forecast_hours(str(check.get(field, "")), available_hours)
        )
    if not mentioned_hours:
        return []

    phase_rows = list(all_phase_rows)
    phase_hours_by_key = {
        str(row.get("phase_key", "")).strip(): set(_compact_phase_hours(row))
        for row in phase_rows
        if str(row.get("phase_key", "")).strip()
    }
    original_keys = [
        str(item).strip()
        for item in check.get("phase_keys", [])
        if str(item).strip()
    ]
    referenced_hours = {
        hour
        for phase_key in original_keys
        for hour in phase_hours_by_key.get(phase_key, set())
    }
    missing_hours = sorted(mentioned_hours - referenced_hours)
    if not missing_hours:
        return []

    additions: list[str] = []
    unresolved: list[int] = []
    for hour in missing_hours:
        phase_key = _unique_existing_phase_key_for_hour(
            hour,
            all_phase_rows=phase_rows,
        )
        if phase_key is None:
            unresolved.append(hour)
            continue
        if phase_key not in original_keys and phase_key not in additions:
            additions.append(phase_key)

    if additions:
        check["phase_keys"] = original_keys + additions
        logging.warning(
            "step04 candidate plan phase references canonicalized from explicit forecast-hour text "
            "candidate_id=%s check_key=%s added_phase_keys=%s mentioned_hours=%s",
            candidate_id,
            check.get("check_key"),
            additions,
            sorted(mentioned_hours),
        )
    return unresolved


def _canonicalize_quantitative_scope_phase_keys(
    check: dict[str, Any],
    *,
    all_phase_rows: Iterable[dict[str, Any]],
    candidate_id: str,
) -> list[int]:
    """Align quantitative forecast hours to already-declared phase references.

    The model owns the meteorological choice of forecast hours and phase
    definitions. Python may only connect an already-declared, uniquely resolved
    phase when a quantitative scope explicitly requires one of its hours. Both
    the verification request and the atomic claim receive the same added phase
    reference. Missing or ambiguous hours are returned so the validator can
    escalate to whole candidate-plan chunk regeneration.
    """
    claims = check.get("atomic_claims")
    if not isinstance(claims, list) or not claims:
        return []

    phase_rows = list(all_phase_rows)
    phase_hours_by_key = {
        str(row.get("phase_key", "")).strip(): set(_compact_phase_hours(row))
        for row in phase_rows
        if str(row.get("phase_key", "")).strip()
    }
    check_keys = [
        str(item).strip()
        for item in check.get("phase_keys", [])
        if str(item).strip()
    ]
    unresolved: set[int] = set()
    advisory_rows: list[str] = []

    for claim in claims:
        if not isinstance(claim, dict):
            continue
        scope = claim.get("quantitative_scope")
        if not isinstance(scope, dict):
            continue
        raw_hours = scope.get("forecast_hours")
        if (
            not isinstance(raw_hours, list)
            or any(isinstance(hour, bool) or not isinstance(hour, int) or hour < 0 for hour in raw_hours)
        ):
            continue
        quantitative_hours = set(int(hour) for hour in raw_hours)
        if not quantitative_hours:
            continue

        claim_keys = [
            str(item).strip()
            for item in claim.get("phase_keys", [])
            if str(item).strip()
        ]
        check_hours = {
            hour
            for phase_key in check_keys
            for hour in phase_hours_by_key.get(phase_key, set())
        }
        claim_hours = {
            hour
            for phase_key in claim_keys
            for hour in phase_hours_by_key.get(phase_key, set())
        }
        missing_check_hours = quantitative_hours - check_hours
        missing_claim_hours = quantitative_hours - claim_hours
        additions_check: list[str] = []
        additions_claim: list[str] = []

        for hour in sorted(missing_check_hours | missing_claim_hours):
            phase_key = _unique_existing_phase_key_for_hour(
                hour,
                all_phase_rows=phase_rows,
            )
            if phase_key is None:
                unresolved.add(hour)
                continue
            if hour in missing_check_hours and phase_key not in check_keys and phase_key not in additions_check:
                additions_check.append(phase_key)
            if hour in missing_claim_hours and phase_key not in claim_keys and phase_key not in additions_claim:
                additions_claim.append(phase_key)

        if additions_check:
            check_keys.extend(additions_check)
            check["phase_keys"] = list(dict.fromkeys(check_keys))
        if additions_claim:
            claim_keys.extend(additions_claim)
            claim["phase_keys"] = list(dict.fromkeys(claim_keys))
        if additions_check or additions_claim:
            advisory_rows.append(
                f"check_key={check.get('check_key')!r} claim_key={claim.get('claim_key')!r} "
                f"quantitative_hours={sorted(quantitative_hours)} "
                f"added_check_phase_keys={additions_check} added_claim_phase_keys={additions_claim}"
            )

    if advisory_rows:
        _log_step04_advisories(
            "candidate_plan.quantitative_phase_alignment",
            advisory_rows,
        )
        logging.warning(
            "step04 candidate plan quantitative phase references canonicalized "
            "candidate_id=%s check_key=%s",
            candidate_id,
            check.get("check_key"),
        )
    return sorted(unresolved)


def _compact_phase_key_aliases(chunks: list[dict[str, Any]]) -> dict[str, str]:
    """Map only exact phase restatements to the first canonical phase key.

    The same phase type may legitimately occur more than once when forecast-hour
    windows are temporally distinct.  Only an identical ``phase_type`` and
    ``forecast_hours`` combination is treated as a continuation alias.
    """
    canonical_by_signature: dict[tuple[str, tuple[int, ...]], str] = {}
    phase_signature_by_key: dict[str, tuple[str, tuple[int, ...]]] = {}
    aliases: dict[str, str] = {}
    for chunk in chunks:
        for row in _compact_plan_rows(chunk, "analysis_phases"):
            phase_key = str(row.get("phase_key", "")).strip()
            if not phase_key:
                continue
            signature = _compact_phase_signature(row)
            previous_signature = phase_signature_by_key.setdefault(phase_key, signature)
            if previous_signature != signature:
                raise ValueError(
                    "compact candidate plan reuses phase_key with a different phase definition "
                    f"phase_key={phase_key!r} previous={previous_signature!r} current={signature!r}"
                )
            canonical_key = canonical_by_signature.setdefault(signature, phase_key)
            aliases[phase_key] = canonical_key
    return aliases

def _compact_check_combinations_with_aliases(
    rows: list[dict[str, Any]],
    phase_aliases: dict[str, str],
) -> set[tuple[str, str, str, str]]:
    """Return combinations after continuation phase aliases are canonicalized."""
    combinations: set[tuple[str, str, str, str]] = set()
    for diagnostic_id, variable_role, domain_scope, phase_key in _compact_check_combinations(rows):
        combinations.add(
            (
                diagnostic_id,
                variable_role,
                domain_scope,
                phase_aliases.get(phase_key, phase_key),
            )
        )
    return combinations


def _canonicalize_redundant_temporal_evolution_checks(
    checks: list[dict[str, Any]],
    hypotheses: list[dict[str, Any]],
    *,
    phase_hours_by_key: dict[str, set[int]],
    candidate_id: str,
) -> None:
    """Resolve harmless duplicate time-series intent without failing Step04.

    A dedicated ``diagnostic_id=temporal_evolution`` check and a component
    diagnostic carrying ``analysis_methods=[..., temporal_evolution]`` can
    describe the same executable time series. Keep exactly one time-series
    producer while preserving all non-temporal purposes and hypothesis links.
    """

    def referenced_hours(check: dict[str, Any]) -> set[int]:
        return {
            hour
            for phase_key in check.get("phase_keys", [])
            for hour in phase_hours_by_key.get(str(phase_key), set())
        }

    aliases: dict[str, str] = {}
    remove_keys: set[str] = set()
    dedicated_checks = [
        check
        for check in checks
        if str(check.get("diagnostic_id")) == "temporal_evolution"
    ]
    for dedicated in dedicated_checks:
        dedicated_key = str(dedicated.get("check_key", ""))
        dedicated_hours = referenced_hours(dedicated)
        redundant_others: list[dict[str, Any]] = []
        for other in checks:
            if other is dedicated or str(other.get("diagnostic_id")) == "temporal_evolution":
                continue
            if str(other.get("variable_role")) != str(dedicated.get("variable_role")):
                continue
            if str(other.get("evidence_role") or "context") != str(dedicated.get("evidence_role") or "context"):
                continue
            if not _compact_checks_have_compatible_atomic_claims(
                dedicated,
                other,
                phase_hours_by_key=phase_hours_by_key,
            ):
                continue
            if str(other.get("domain_scope")) != str(dedicated.get("domain_scope")):
                continue
            methods = [str(item) for item in other.get("analysis_methods", [])]
            if "temporal_evolution" not in methods:
                continue
            other_hours = referenced_hours(other)
            if dedicated_hours and dedicated_hours.issubset(other_hours):
                redundant_others.append(other)

        if not redundant_others:
            continue

        time_series_only = next(
            (
                other
                for other in redundant_others
                if set(str(item) for item in other.get("analysis_methods", []))
                == {"temporal_evolution"}
            ),
            None,
        )
        if time_series_only is not None:
            replacement_key = str(time_series_only.get("check_key", ""))
            aliases[dedicated_key] = replacement_key
            remove_keys.add(dedicated_key)
            logging.info(
                "step04 redundant temporal diagnostic canonicalized "
                "candidate_id=%s removed_check_key=%s retained_check_key=%s mode=retain_component_time_series",
                candidate_id,
                dedicated_key,
                replacement_key,
            )
            continue

        for other in redundant_others:
            original_methods = list(
                dict.fromkeys(str(item) for item in other.get("analysis_methods", []))
            )
            canonical_methods = [
                item for item in original_methods if item != "temporal_evolution"
            ]
            if canonical_methods:
                other["analysis_methods"] = canonical_methods
                logging.info(
                    "step04 redundant temporal method canonicalized "
                    "candidate_id=%s dedicated_check_key=%s component_check_key=%s "
                    "original=%s canonical=%s",
                    candidate_id,
                    dedicated_key,
                    other.get("check_key"),
                    original_methods,
                    canonical_methods,
                )

    if not remove_keys:
        return

    checks[:] = [
        check for check in checks if str(check.get("check_key", "")) not in remove_keys
    ]
    for hypothesis in hypotheses:
        for field in ("supporting_check_keys", "opposing_check_keys"):
            hypothesis[field] = list(
                dict.fromkeys(
                    aliases.get(str(key), str(key))
                    for key in hypothesis.get(field, [])
                )
            )


def _compact_check_fingerprint(
    row: dict[str, Any],
    phase_aliases: dict[str, str],
) -> str:
    """Return a stable semantic fingerprint for one compact diagnostic check.

    Local identifiers are excluded. Continuation phase aliases are canonicalized so
    an exact restatement can be safely reused instead of causing a provider failure.
    """
    normalized = {
        "diagnostic_id": str(row.get("diagnostic_id", "")),
        "variable_role": str(row.get("variable_role", "")),
        "domain_scope": str(row.get("domain_scope", "")),
        "phase_keys": sorted(
            {phase_aliases.get(str(key), str(key)) for key in row.get("phase_keys", [])}
        ),
        "representative_spatial_hours": sorted({
            int(value)
            for value in row.get("representative_spatial_hours", [])
            if isinstance(value, int) and not isinstance(value, bool)
        }),
        "analysis_methods": sorted({str(item) for item in row.get("analysis_methods", [])}),
        "expected_signal": re.sub(r"\s+", " ", str(row.get("expected_signal", "")).strip()),
        "decision_relevance": re.sub(r"\s+", " ", str(row.get("decision_relevance", "")).strip()),
        "priority": str(row.get("priority", "")),
        "evidence_role": str(row.get("evidence_role", "")),
        "coverage_roles": sorted(_effective_compact_check_coverage_roles(row)),
        "purpose": re.sub(r"\s+", " ", str(row.get("purpose", "")).strip()),
        "cross_section_line": _cross_section_line_signature(row.get("cross_section_line")),
        "cross_section_strategy": _normalized_cross_section_strategy(row.get("cross_section_strategy")),
        "reference_band_axis": _reference_band_axis_signature(row.get("reference_band_axis")),
    }
    return canonical_hash(normalized)


def _canonicalize_compact_continuation_domain_fields(
    chunk: dict[str, Any],
    previous_chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Use the first compact chunk as the canonical context-domain contract.

    Continuation calls often omit ``context_domain_reason`` or paraphrase it even
    though the executable bbox is unchanged.  The reason is descriptive metadata,
    not a new planning decision.  Preserve the first chunk's bbox/reason
    deterministically so a harmless continuation wording difference does not fail
    the complete candidate plan.  A genuinely different non-null bbox is left
    untouched and is still rejected by semantic validation.
    """
    if not previous_chunks:
        return chunk

    value = copy.deepcopy(chunk)
    first = previous_chunks[0]
    canonical_context = _normalized_bbox_for_validation(first.get("context_domain"))
    canonical_reason_raw = first.get("context_domain_reason")
    canonical_reason = (
        str(canonical_reason_raw).strip() if canonical_reason_raw is not None else None
    )
    current_context = _normalized_bbox_for_validation(value.get("context_domain"))

    if canonical_context is None:
        # The first chunk established that no context domain is used.  Ignore only
        # stray reason text on a null continuation; a newly introduced bbox remains
        # visible so the existing invariant check rejects it.
        if current_context is None:
            value["context_domain"] = None
            value["context_domain_reason"] = None
        return value

    if current_context is None:
        # Missing/null continuation metadata means "reuse the established domain".
        value["context_domain"] = copy.deepcopy(canonical_context)
    elif current_context != canonical_context:
        # Do not silently rewrite a real spatial change.
        return value

    value["context_domain_reason"] = canonical_reason
    return value


def _namespace_compact_chunk_local_keys(
    chunk: dict[str, Any],
    *,
    chunk_index: int,
) -> dict[str, Any]:
    """Give one compact transport chunk a deterministic internal key namespace.

    ``phase_key``, ``check_key``, and ``hypothesis_key`` are provider-authored
    transport keys, not canonical plan identifiers.  A continuation is therefore
    allowed to restart its local numbering (P1/C1/H1).  Namespace declarations
    before cross-chunk validation and remap references to declarations in the same
    chunk.  References to an already namespaced key from a previous chunk are left
    unchanged, including when the provider restates that key in a declaration.

    Duplicate declarations *within* one chunk intentionally remain duplicates
    after mapping so the chunk-level validator can reject the ambiguous response.
    """
    if chunk_index < 1:
        raise ValueError("compact candidate plan chunk index must be positive")
    value = copy.deepcopy(chunk)
    prefix = f"chunk{chunk_index:02d}:"

    def declaration_map(rows: list[dict[str, Any]], field: str) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for row in rows:
            local_key = str(row.get(field, "")).strip()
            if not local_key:
                continue
            mapping[local_key] = (
                local_key
                if re.fullmatch(r"chunk\d+:.+", local_key)
                else f"{prefix}{local_key}"
            )
            row[field] = mapping[local_key]
        return mapping

    phases = _compact_plan_rows(value, "analysis_phases")
    checks = _compact_plan_rows(value, "diagnostic_checks")
    hypotheses = _compact_plan_rows(value, "hypotheses")
    phase_keys = declaration_map(phases, "phase_key")
    check_keys = declaration_map(checks, "check_key")
    declaration_map(hypotheses, "hypothesis_key")

    for check in checks:
        check["phase_keys"] = [
            phase_keys.get(str(key).strip(), str(key).strip())
            for key in check.get("phase_keys", [])
        ]
        for claim in check.get("atomic_claims", []):
            if not isinstance(claim, dict):
                continue
            claim["phase_keys"] = [
                phase_keys.get(str(key).strip(), str(key).strip())
                for key in claim.get("phase_keys", [])
            ]
    for hypothesis in hypotheses:
        for field in ("supporting_check_keys", "opposing_check_keys"):
            hypothesis[field] = [
                check_keys.get(str(key).strip(), str(key).strip())
                for key in hypothesis.get(field, [])
            ]
    return value


def _deduplicate_compact_chunk_phase_aliases(value: dict[str, Any]) -> dict[str, Any]:
    """Collapse exact phase restatements inside one provider chunk.

    Identical ``phase_type + forecast_hours`` rows are representational duplicates,
    not meteorological disagreements. The first row is retained and diagnostic
    ``phase_keys`` references are redirected deterministically. Reusing one key for
    different phase definitions is still left for contract validation to reject.
    """
    result = copy.deepcopy(value)
    phases = _compact_plan_rows(result, "analysis_phases")
    checks = _compact_plan_rows(result, "diagnostic_checks")
    canonical_by_signature: dict[tuple[str, tuple[int, ...]], str] = {}
    phase_signature_by_key: dict[str, tuple[str, tuple[int, ...]]] = {}
    aliases: dict[str, str] = {}
    retained: list[dict[str, Any]] = []
    for phase in phases:
        phase_key = str(phase.get("phase_key", "")).strip()
        signature = _compact_phase_signature(phase)
        previous_signature = phase_signature_by_key.setdefault(phase_key, signature)
        if previous_signature != signature:
            retained.append(phase)
            continue
        canonical_key = canonical_by_signature.setdefault(signature, phase_key)
        aliases[phase_key] = canonical_key
        if canonical_key == phase_key:
            retained.append(phase)
        else:
            logging.info(
                "step04 recoverable duplicate phase collapsed phase_key=%s canonical_phase_key=%s signature=%s",
                phase_key,
                canonical_key,
                signature,
            )
    result["analysis_phases"] = retained
    if aliases:
        for check in checks:
            check["phase_keys"] = list(
                dict.fromkeys(
                    aliases.get(str(key).strip(), str(key).strip())
                    for key in check.get("phase_keys", [])
                )
            )
            for claim in check.get("atomic_claims", []):
                if not isinstance(claim, dict):
                    continue
                claim["phase_keys"] = list(
                    dict.fromkeys(
                        aliases.get(str(key).strip(), str(key).strip())
                        for key in claim.get("phase_keys", [])
                    )
                )
    return result


def _normalize_compact_chunk_for_validation(
    chunk: dict[str, Any],
    previous_chunks: list[dict[str, Any]],
    *,
    chunk_index: int,
) -> dict[str, Any]:
    """Normalize recoverable provider representation issues before validation."""
    value = _canonicalize_compact_continuation_domain_fields(chunk, previous_chunks)
    value = _namespace_compact_chunk_local_keys(value, chunk_index=chunk_index)
    return _deduplicate_compact_chunk_phase_aliases(value)


def _normalize_compact_continuation_chunk(
    chunk: dict[str, Any],
    previous_chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Remove exact continuation restatements and remap their local check keys.

    Structured-output continuation may restate phases or diagnostic checks even when
    the prompt asks it not to. Exact restatements are harmless: the prior check is
    reused and hypotheses are redirected to its canonical key. Checks with different
    analytical intent remain distinct.
    """
    if not previous_chunks:
        return chunk
    value = _canonicalize_compact_continuation_domain_fields(chunk, previous_chunks)
    phase_aliases = _compact_phase_key_aliases([*previous_chunks, value])
    used_check_keys = {
        str(row.get("check_key", ""))
        for previous in previous_chunks
        for row in _compact_plan_rows(previous, "diagnostic_checks")
    }
    fingerprint_to_key: dict[str, str] = {}
    for previous in previous_chunks:
        for row in _compact_plan_rows(previous, "diagnostic_checks"):
            fingerprint_to_key.setdefault(
                _compact_check_fingerprint(row, phase_aliases),
                str(row.get("check_key", "")),
            )

    aliases: dict[str, str] = {}
    retained: list[dict[str, Any]] = []
    for index, raw_row in enumerate(_compact_plan_rows(value, "diagnostic_checks"), start=1):
        row = copy.deepcopy(raw_row)
        old_key = str(row.get("check_key", "")).strip()
        row["phase_keys"] = list(
            dict.fromkeys(phase_aliases.get(str(key), str(key)) for key in row.get("phase_keys", []))
        )
        for claim in row.get("atomic_claims", []):
            if not isinstance(claim, dict):
                continue
            claim["phase_keys"] = list(
                dict.fromkeys(
                    phase_aliases.get(str(key), str(key)) for key in claim.get("phase_keys", [])
                )
            )
        fingerprint = _compact_check_fingerprint(row, phase_aliases)
        canonical_key = fingerprint_to_key.get(fingerprint)
        if canonical_key:
            aliases[old_key] = canonical_key
            continue
        new_key = old_key
        if not new_key or new_key in used_check_keys:
            base = f"CHK_CONT_{int(value.get('plan_chunk_index', 0)):02d}_{index:02d}"
            new_key = base
            suffix = 1
            while new_key in used_check_keys:
                suffix += 1
                new_key = f"{base}_{suffix}"
        aliases[old_key] = new_key
        row["check_key"] = new_key
        retained.append(row)
        used_check_keys.add(new_key)
        fingerprint_to_key[fingerprint] = new_key
    value["diagnostic_checks"] = retained

    for hypothesis in _compact_plan_rows(value, "hypotheses"):
        for field in ("supporting_check_keys", "opposing_check_keys"):
            hypothesis[field] = list(
                dict.fromkeys(aliases.get(str(key), str(key)) for key in hypothesis.get(field, []))
            )
    return value


_COMPACT_EVIDENCE_ROLE_RANK = {
    "context": 0,
    "opposing": 1,
    "supporting": 2,
}


def _compact_check_forecast_hours(
    row: dict[str, Any],
    phase_hours_by_key: dict[str, set[int]],
) -> set[int]:
    return {
        int(hour)
        for phase_key in row.get("phase_keys", [])
        for hour in phase_hours_by_key.get(str(phase_key), set())
    }


def _compact_atomic_claim_semantic_signature(
    row: dict[str, Any],
    *,
    phase_hours_by_key: dict[str, set[int]],
) -> tuple[Any, ...]:
    """Return a conservative semantic signature for atomic claims.

    Semantic duplicate consolidation is allowed only when the structured claim
    meaning is equivalent.  Narrative similarity and one overlapping forecast
    hour are not sufficient because they can collapse a supporting pre-peak
    tendency and an opposing post-peak tendency into one contradictory Check.
    """
    claims = row.get("atomic_claims")
    if not isinstance(claims, list):
        return ()
    signatures: list[tuple[Any, ...]] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        phase_hours = tuple(sorted({
            int(hour)
            for phase_key in claim.get("phase_keys", [])
            for hour in phase_hours_by_key.get(str(phase_key), set())
        }))
        scope = claim.get("quantitative_scope")
        if isinstance(scope, dict):
            quantitative_signature: tuple[Any, ...] | None = (
                str(scope.get("feature_id") or ""),
                str(scope.get("region_scope") or ""),
                tuple(str(value) for value in scope.get("region_ids", []) if str(value)),
                tuple(int(value) for value in scope.get("forecast_hours", []) if isinstance(value, int) and not isinstance(value, bool)),
                tuple(int(value) for value in scope.get("comparison_forecast_hours", []) if isinstance(value, int) and not isinstance(value, bool))
                if isinstance(scope.get("comparison_forecast_hours"), list)
                else None,
                str(scope.get("statistic") or ""),
            )
        else:
            quantitative_signature = None
        signatures.append((
            tuple(sorted(str(value) for value in claim.get("claim_components", []) if str(value))),
            str(claim.get("expected_state") or ""),
            str(claim.get("scope_quantifier") or ""),
            phase_hours,
            quantitative_signature,
        ))
    return tuple(sorted(signatures, key=repr))



_POSITIVE_ALIGNMENT_ENVIRONMENT_COMPONENTS = frozenset({
    "moisture_supply",
    "low_level_forcing",
    "mid_upper_forcing",
})


def _positive_alignment_scope_identity(claim: dict[str, Any]) -> tuple[str, str]:
    """Return a scope identity that preserves genuinely distinct subareas.

    Generic candidate-wide wording is normalized so the same core overlap cannot
    escape duplicate detection merely by switching ``all``/``specific``/
    ``at_least_one``.  A concrete ``specific`` subarea remains distinct, allowing
    separate east/west (or otherwise named) alignment checks when they are truly
    different verification targets.
    """
    quantifier = str(claim.get("scope_quantifier") or "")
    description = " ".join(str(claim.get("scope_description") or "").split())
    compact = description.replace(" ", "")
    generic_candidate_scopes = {
        "후보영역",
        "후보영역전체",
        "후보영역내",
        "후보영역내결합영역",
    }
    if quantifier in {"all", "at_least_one"} or compact in generic_candidate_scopes:
        return ("candidate_overlap", "")
    return (quantifier or "unspecified", description)


def _positive_alignment_claim_identity(
    check: dict[str, Any],
    claim: dict[str, Any],
    *,
    phase_hours_by_key: dict[str, set[int]],
) -> tuple[Any, ...]:
    """Return the semantic identity of one positive overlap claim.

    Quantifier differences alone do not make two generic candidate-overlap claims
    independent.  Concrete ``specific`` subareas, however, remain distinct.
    """
    components = tuple(sorted(
        str(value) for value in claim.get("claim_components", []) if str(value)
    ))
    phase_hours = tuple(sorted({
        int(hour)
        for phase_key in claim.get("phase_keys", [])
        for hour in phase_hours_by_key.get(str(phase_key), set())
    }))
    return (
        components,
        phase_hours,
        str(check.get("domain_scope") or "candidate"),
        str(check.get("spatial_relation_scope") or "not_applicable"),
        _positive_alignment_scope_identity(claim),
    )


_COMPLEMENTARY_QUANTITATIVE_STATES = {
    "increase": "decrease",
    "decrease": "increase",
    "greater_than": "less_than",
    "less_than": "greater_than",
    "strengthen": "weaken",
    "weaken": "strengthen",
}


def _validate_complementary_quantitative_checks(
    checks: list[dict[str, Any]],
) -> list[str]:
    """Reject duplicate quantitative Checks that ask the exact opposite question.

    Step07 already supports ``opposite_observed``.  Asking both "increase" and
    "decrease" for the same feature, region, statistic, and time pair therefore
    duplicates one physical fact and can double-count it downstream.
    """
    errors: list[str] = []
    seen: dict[tuple[Any, ...], tuple[str, str]] = {}
    for check in checks:
        if not isinstance(check, dict):
            continue
        check_key = str(check.get("check_key") or "")
        claims = check.get("atomic_claims")
        if not isinstance(claims, list):
            continue
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            state = str(claim.get("expected_state") or "")
            opposite = _COMPLEMENTARY_QUANTITATIVE_STATES.get(state)
            scope = claim.get("quantitative_scope")
            if opposite is None or not isinstance(scope, dict):
                continue
            region_ids = tuple(sorted(str(value) for value in scope.get("region_ids", []) if str(value)))
            forecast_hours = tuple(
                int(value) for value in scope.get("forecast_hours", [])
                if isinstance(value, int) and not isinstance(value, bool)
            )
            comparison_hours = tuple(
                int(value) for value in (scope.get("comparison_forecast_hours") or [])
                if isinstance(value, int) and not isinstance(value, bool)
            )
            components = tuple(sorted(
                str(value) for value in claim.get("claim_components", []) if str(value)
            ))
            identity = (
                str(scope.get("feature_id") or ""),
                str(scope.get("region_scope") or ""),
                region_ids,
                forecast_hours,
                comparison_hours,
                str(scope.get("statistic") or ""),
                components,
            )
            previous = seen.get(identity)
            if previous is not None and previous[1] == opposite:
                errors.append(
                    "QUANTITATIVE_COMPLEMENTARY_DUPLICATE_CHECK "
                    f"check_key={check_key} duplicate_of={previous[0]} "
                    f"expected_state={state} duplicate_expected_state={previous[1]}; "
                    "repair=keep only one directional quantitative proposition for the same feature/region/time/statistic; "
                    "Step07 opposite_observed handles the opposite outcome"
                )
            else:
                seen[identity] = (check_key, state)
    return list(dict.fromkeys(errors))


def _validate_positive_alignment_contracts(
    checks: list[dict[str, Any]],
    *,
    phase_hours_by_key: dict[str, set[int]],
) -> list[str]:
    """Validate Step04 positive precipitation/environment alignment intents.

    A Check that is intended to establish positive precipitation-environment
    alignment must stay *pure*: it contains only positive ``overlap`` claims.
    Follow-up weakening/loss/persistence limitations belong in separate Checks.
    This preserves the existing Step07 Check-level localization contract instead
    of requiring claim-level localization downstream.
    """
    errors: list[str] = []
    seen_positive_claims: dict[tuple[Any, ...], tuple[str, str]] = {}

    for check in checks:
        if not isinstance(check, dict):
            continue
        check_key = str(check.get("check_key") or "")
        verification_mode = str(
            check.get("verification_mode")
            or _verification_mode_from_diagnostic_check(check)
        )
        if verification_mode != "multivariable_alignment":
            continue
        if str(check.get("evidence_role") or "") != "supporting":
            continue

        raw_claims = check.get("atomic_claims")
        claims = [claim for claim in raw_claims if isinstance(claim, dict)] if isinstance(raw_claims, list) else []
        positive_claims: list[dict[str, Any]] = []
        for claim in claims:
            components = {
                str(value) for value in claim.get("claim_components", []) if str(value)
            }
            if (
                str(claim.get("expected_state") or "") == "overlap"
                and "direct_precipitation" in components
                and bool(components & _POSITIVE_ALIGNMENT_ENVIRONMENT_COMPONENTS)
            ):
                positive_claims.append(claim)

        if not positive_claims:
            continue

        non_overlap_states = sorted({
            str(claim.get("expected_state") or "")
            for claim in claims
            if str(claim.get("expected_state") or "") != "overlap"
        })
        limiting_states = sorted({
            state for state in non_overlap_states
            if state in NON_POSITIVE_PRECURSOR_EXPECTED_STATES
        })
        if limiting_states:
            errors.append(
                "POSITIVE_ALIGNMENT_MIXED_POLARITY "
                f"check_key={check_key} limiting_states={limiting_states}; "
                "repair=keep the core positive precipitation-environment overlap in one pure "
                "multivariable_alignment verification_request and move weakening/decrease/"
                "not_overlap/split/reorganization persistence limits to separate verification_requests"
            )
        elif non_overlap_states:
            errors.append(
                "POSITIVE_ALIGNMENT_NON_OVERLAP_CLAIM "
                f"check_key={check_key} other_states={non_overlap_states}; "
                "repair=a positive precipitation-environment alignment Check must contain only "
                "overlap atomic_claims; move other decision axes to separate verification_requests"
            )

        for claim in positive_claims:
            quantifier = str(claim.get("scope_quantifier") or "")
            if quantifier == "all":
                errors.append(
                    "POSITIVE_ALIGNMENT_SCOPE_TOO_BROAD "
                    f"check_key={check_key} claim_key={claim.get('claim_key')} scope_quantifier=all; "
                    "repair=use at_least_one for an operationally meaningful overlap somewhere "
                    "inside the candidate, or specific when a concrete subarea is explicitly the target. "
                    "Do not require positive alignment over the entire candidate bbox"
                )

            identity = _positive_alignment_claim_identity(
                check, claim, phase_hours_by_key=phase_hours_by_key
            )
            previous = seen_positive_claims.get(identity)
            current_claim_key = str(claim.get("claim_key") or "")
            if previous is not None and previous != (check_key, current_claim_key):
                errors.append(
                    "POSITIVE_ALIGNMENT_DUPLICATE_ATOMIC_CLAIM "
                    f"check_key={check_key} duplicate_of={previous[0]} "
                    f"claim_key={current_claim_key} duplicate_claim_key={previous[1]}; "
                    "repair=retain exactly one pure positive multivariable_alignment Check/claim for the "
                    "same component/time/domain overlap. Put later weakening or persistence questions "
                    "in separate non-duplicate verification_requests"
                )
            else:
                seen_positive_claims[identity] = (check_key, current_claim_key)

    return list(dict.fromkeys(errors))


def _compact_checks_have_compatible_atomic_claims(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    phase_hours_by_key: dict[str, set[int]],
) -> bool:
    left_signature = _compact_atomic_claim_semantic_signature(
        left, phase_hours_by_key=phase_hours_by_key
    )
    right_signature = _compact_atomic_claim_semantic_signature(
        right, phase_hours_by_key=phase_hours_by_key
    )
    if bool(left_signature) != bool(right_signature):
        return False
    return left_signature == right_signature


def _compact_check_signal_families(row: dict[str, Any]) -> set[str]:
    """Return broad physical signal families stated by a compact check.

    The check's diagnostic and variable role are the primary contract. Narrative
    terms are used only for the narrow temporal-evolution role compatibility case,
    where ``hazard_magnitude`` and ``uncertainty`` may describe the same precipitation
    time series with different wording.
    """
    text = " ".join(
        str(row.get(field, "")).casefold()
        for field in ("expected_signal", "decision_relevance", "purpose")
    )
    families: set[str] = set()
    patterns: dict[str, tuple[str, ...]] = {
        "precipitation": ("강수", "호우", "강수량", "누적", "피크", "peak"),
        "moisture": ("수분", "수증기", "플럭스", "습윤", "850 hpa"),
        "convergence": ("수렴", "925 hpa", "하층", "저층"),
        "ascent": ("상승", "오메가", "omega", "700 hpa", "중층"),
        "synoptic": ("저기압", "기압골", "전선", "제트", "종관", "상층"),
        "evolution": ("이동", "변화", "약화", "강화", "재강화", "지속", "확장", "후퇴"),
    }
    for family, terms in patterns.items():
        if any(term in text for term in terms):
            families.add(family)
    return families


def _compact_cross_section_contract_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _cross_section_line_signature(row.get("cross_section_line")),
        _normalized_cross_section_strategy(row.get("cross_section_strategy")),
        _reference_band_axis_signature(row.get("reference_band_axis")),
    )


def _compact_checks_are_semantic_duplicates(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    phase_hours_by_key: dict[str, set[int]],
) -> bool:
    """Return True only when two checks represent one executable diagnosis.

    Continuation output commonly restates a prior check with broader phases or
    methods and slightly different Korean wording. The executable identity is the
    diagnostic, selected domain, physical role, and overlapping event phase. A
    distinct cross-section geometry remains a distinct check.
    """
    diagnostic_id = str(left.get("diagnostic_id", ""))
    if diagnostic_id != str(right.get("diagnostic_id", "")):
        return False
    if str(left.get("verification_mode", "")) != str(right.get("verification_mode", "")):
        return False
    if str(left.get("check_scope", "")) != str(right.get("check_scope", "")):
        return False
    if {str(value) for value in left.get("required_signal_components", [])} != {
        str(value) for value in right.get("required_signal_components", [])
    }:
        return False
    if str(left.get("domain_scope", "")) != str(right.get("domain_scope", "")):
        return False
    if str(left.get("spatial_relation_scope", "not_applicable")) != str(
        right.get("spatial_relation_scope", "not_applicable")
    ):
        return False
    if str(left.get("evidence_role") or "context") != str(right.get("evidence_role") or "context"):
        return False
    if not _compact_checks_have_compatible_atomic_claims(
        left, right, phase_hours_by_key=phase_hours_by_key
    ):
        return False
    if diagnostic_id == "vertical_cross_section":
        if (
            _compact_cross_section_contract_signature(left)
            != _compact_cross_section_contract_signature(right)
        ):
            return False

    left_hours = _compact_check_forecast_hours(left, phase_hours_by_key)
    right_hours = _compact_check_forecast_hours(right, phase_hours_by_key)
    if not left_hours or not right_hours or not (left_hours & right_hours):
        return False

    left_role = str(left.get("variable_role", ""))
    right_role = str(right.get("variable_role", ""))
    if left_role == right_role:
        return True

    # A precipitation temporal series is frequently labelled once as hazard
    # magnitude and once as uncertainty. It is still the same physical output.
    if diagnostic_id == "temporal_evolution" and {
        left_role,
        right_role,
    } <= {"hazard_magnitude", "uncertainty"}:
        left_families = _compact_check_signal_families(left)
        right_families = _compact_check_signal_families(right)
        return "precipitation" in left_families and "precipitation" in right_families
    return False


def _compact_narrative_specificity(value: Any) -> tuple[int, int, int, int]:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    forecast_hour_count = len(set(re.findall(r"(?i)(?:F|\+)\s*\d{1,3}", text)))
    pressure_level_count = len(set(re.findall(r"\b\d{3,4}\s*hPa\b", text, flags=re.I)))
    token_count = len(re.findall(r"[0-9a-z가-힣_]+", text.casefold()))
    return (forecast_hour_count, pressure_level_count, token_count, len(text))


def _more_specific_compact_narrative(first: Any, second: Any) -> str:
    left = str(first or "").strip()
    right = str(second or "").strip()
    if not left:
        return right
    if not right:
        return left
    if _compact_narrative_specificity(right) > _compact_narrative_specificity(left):
        return right
    return left


def _source_check_intent_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    """Capture one pre-merge Step04 verification request for lossless provenance."""
    return {
        "check_key": str(row.get("check_key") or ""),
        "signal_components": list(dict.fromkeys(
            str(value)
            for value in row.get("required_signal_components", [])
            if str(value)
        )),
        "verification_mode": str(
            row.get("verification_mode")
            or _verification_mode_from_diagnostic_check(row)
        ),
        "check_scope": str(row.get("check_scope") or ""),
        "domain_scope": str(row.get("domain_scope") or "candidate"),
        "spatial_relation_scope": str(row.get("spatial_relation_scope") or "not_applicable"),
        "phase_keys": [str(value) for value in row.get("phase_keys", []) if str(value)],
        "representative_spatial_hours": [
            int(value)
            for value in row.get("representative_spatial_hours", [])
            if isinstance(value, int) and not isinstance(value, bool)
        ],
        "direct_precipitation_anchor": _is_system_direct_precipitation_anchor_row(row),
        "expected_signal": row.get("expected_signal"),
        "decision_relevance": row.get("decision_relevance"),
        "purpose": row.get("purpose"),
        "priority": row.get("priority"),
        "evidence_role": row.get("evidence_role"),
        "coverage_roles": _effective_compact_check_coverage_roles(row),
        "cross_section_line": _cross_section_line_signature(row.get("cross_section_line")),
        "cross_section_strategy": _normalized_cross_section_strategy(row.get("cross_section_strategy")),
        "reference_band_axis": _reference_band_axis_signature(row.get("reference_band_axis")),
    }


def _source_atomic_claim_set(row: dict[str, Any]) -> dict[str, Any]:
    """Preserve the model-declared atomic claims separately from operational intent lineage."""
    claims = row.get("atomic_claims")
    if not isinstance(claims, list):
        claims = []
    return {
        "source_check_key": str(row.get("check_key") or ""),
        "atomic_claims": copy.deepcopy([item for item in claims if isinstance(item, dict)]),
    }


def _normalize_atomic_claims_for_shadow(row: dict[str, Any]) -> list[dict[str, Any]]:
    claims = row.get("atomic_claims")
    if not isinstance(claims, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in claims:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "claim_key": str(item.get("claim_key") or "").strip(),
            "claim_statement": str(item.get("claim_statement") or "").strip(),
            "claim_components": list(dict.fromkeys(
                str(value) for value in item.get("claim_components", []) if str(value)
            )),
            "phase_keys": list(dict.fromkeys(
                str(value) for value in item.get("phase_keys", []) if str(value)
            )),
            "expected_state": str(item.get("expected_state") or "").strip(),
            "scope_quantifier": str(item.get("scope_quantifier") or "").strip(),
            "scope_description": str(item.get("scope_description") or "").strip(),
            "quantitative_scope": copy.deepcopy(item.get("quantitative_scope"))
            if isinstance(item.get("quantitative_scope"), dict) else None,
        })
    return normalized


def _build_atomic_claim_contract(
    row: dict[str, Any],
    *,
    check_id: str,
) -> dict[str, Any]:
    source_sets = row.get("_source_atomic_claim_sets")
    if not isinstance(source_sets, list) or not source_sets:
        source_sets = [_source_atomic_claim_set(row)]
    return {
        "contract_version": ATOMIC_CLAIM_CONTRACT_VERSION,
        "mode": ATOMIC_CLAIM_VALIDATION_MODE,
        "quantitative_enforcement": "specific-v2+convergence-majority-v1",
        "check_id": check_id,
        "source_check_key": str(row.get("check_key") or ""),
        "inherited_evidence_role": str(row.get("evidence_role") or "context"),
        "verification_mode": str(
            row.get("verification_mode") or _verification_mode_from_diagnostic_check(row)
        ),
        "canonical_claims": _normalize_atomic_claims_for_shadow(row),
        "source_claim_sets": copy.deepcopy([
            item for item in source_sets if isinstance(item, dict)
        ]),
    }


def _shadow_validate_atomic_claim_contract(
    *,
    check_id: str,
    row: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Audit claim atomicity and the supported quantitative contract shapes.

    General claim semantics remain shadow-only.  Step07 deterministically
    enforces only the explicitly supported quantitative shapes declared here.
    """
    warnings: list[str] = []
    claims = contract.get("canonical_claims", [])
    request_components = {
        str(value) for value in row.get("required_signal_components", []) if str(value)
    }
    request_phases = {str(value) for value in row.get("phase_keys", []) if str(value)}
    mode = str(contract.get("verification_mode") or "")
    if not claims:
        warnings.append("ATOMIC_CLAIMS_MISSING")
    seen_keys: set[str] = set()
    for claim in claims:
        key = str(claim.get("claim_key") or "")
        statement = str(claim.get("claim_statement") or "")
        components = {str(value) for value in claim.get("claim_components", []) if str(value)}
        phases = {str(value) for value in claim.get("phase_keys", []) if str(value)}
        state = str(claim.get("expected_state") or "")
        quantifier = str(claim.get("scope_quantifier") or "")
        quantitative_scope = claim.get("quantitative_scope")
        if not key or not statement or not components or not phases or not state or not quantifier:
            warnings.append("ATOMIC_CLAIM_FIELDS_INCOMPLETE")
            continue

        specific_states = {"greater_than", "less_than", "increase", "decrease", "strengthen", "weaken", "peak_at"}
        if quantifier == "specific" and state in specific_states:
            if not isinstance(quantitative_scope, dict):
                warnings.append("ATOMIC_QUANTITATIVE_SCOPE_MISSING")
            else:
                feature_id = quantitative_scope.get("feature_id")
                region_scope = quantitative_scope.get("region_scope")
                region_ids = quantitative_scope.get("region_ids")
                forecast_hours = quantitative_scope.get("forecast_hours")
                comparison_hours = quantitative_scope.get("comparison_forecast_hours")
                statistic = quantitative_scope.get("statistic")
                invalid = (
                    not isinstance(feature_id, str) or not feature_id
                    or region_scope != "explicit_regions"
                    or not isinstance(region_ids, list) or len(region_ids) != 1
                    or not isinstance(region_ids[0], str) or not region_ids[0]
                    or not isinstance(forecast_hours, list) or len(forecast_hours) < 2
                    or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in forecast_hours)
                    or forecast_hours != sorted(set(forecast_hours))
                    or statistic not in {"min", "max", "mean", "p95"}
                )
                if state in {"greater_than", "less_than"}:
                    invalid = invalid or (
                        not isinstance(comparison_hours, list) or len(comparison_hours) != 2
                        or any(isinstance(value, bool) or not isinstance(value, int) for value in comparison_hours)
                        or len(set(comparison_hours)) != 2
                        or any(value not in forecast_hours for value in comparison_hours)
                    )
                elif comparison_hours is not None:
                    invalid = True
                if invalid:
                    warnings.append("ATOMIC_QUANTITATIVE_SCOPE_INVALID")

        if quantifier == "majority" and state == "not_strengthen":
            if not isinstance(quantitative_scope, dict):
                warnings.append("ATOMIC_QUANTITATIVE_SCOPE_MISSING")
            else:
                feature_id = quantitative_scope.get("feature_id")
                region_scope = quantitative_scope.get("region_scope")
                region_ids = quantitative_scope.get("region_ids")
                forecast_hours = quantitative_scope.get("forecast_hours")
                comparison_hours = quantitative_scope.get("comparison_forecast_hours")
                statistic = quantitative_scope.get("statistic")
                if (
                    feature_id != "convergence925_1e5_s"
                    or region_scope != "candidate_overlap"
                    or region_ids != []
                    or not isinstance(forecast_hours, list) or len(forecast_hours) != 2
                    or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in forecast_hours)
                    or forecast_hours != sorted(set(forecast_hours))
                    or comparison_hours is not None
                    or statistic != "mean"
                ):
                    warnings.append("ATOMIC_QUANTITATIVE_SCOPE_INVALID")

        if key in seen_keys:
            warnings.append("ATOMIC_CLAIM_KEY_DUPLICATE")
        seen_keys.add(key)
        if not components.issubset(request_components):
            warnings.append("ATOMIC_CLAIM_COMPONENT_OUT_OF_REQUEST")
        if not phases.issubset(request_phases):
            warnings.append("ATOMIC_CLAIM_PHASE_OUT_OF_REQUEST")
        if re.search(r"(?:또는|혹은|거나)", statement):
            warnings.append("ATOMIC_CLAIM_LOGICAL_ALTERNATIVE")
        if state in {"overlap", "not_overlap"}:
            if mode != "multivariable_alignment" or len(components) < 2:
                warnings.append("ATOMIC_CLAIM_ALIGNMENT_MODE_MISMATCH")
        if state in {"move", "split", "merge", "expand", "contract", "reorganize"}:
            if mode != "spatial_evolution":
                warnings.append("ATOMIC_CLAIM_SPATIAL_EVOLUTION_MODE_MISMATCH")
    warning_codes = list(dict.fromkeys(warnings))
    result = {
        "mode": ATOMIC_CLAIM_VALIDATION_MODE,
        "status": "pass" if not warning_codes else "warning",
        "warning_codes": warning_codes,
    }
    if warning_codes:
        logging.warning(
            "STEP04_ATOMIC_CLAIM_WARNING check_id=%s codes=%s",
            check_id,
            ",".join(warning_codes),
        )
    return result



def _merge_compact_semantic_check(
    canonical: dict[str, Any],
    duplicate: dict[str, Any],
) -> None:
    source_intents = canonical.get("_source_intents")
    if not isinstance(source_intents, list):
        source_intents = [_source_check_intent_snapshot(canonical)]
        canonical["_source_intents"] = source_intents
    source_intents.append(_source_check_intent_snapshot(duplicate))
    source_claim_sets = canonical.get("_source_atomic_claim_sets")
    if not isinstance(source_claim_sets, list):
        source_claim_sets = [_source_atomic_claim_set(canonical)]
        canonical["_source_atomic_claim_sets"] = source_claim_sets
    source_claim_sets.append(_source_atomic_claim_set(duplicate))
    canonical["phase_keys"] = list(
        dict.fromkeys(
            [str(value) for value in canonical.get("phase_keys", [])]
            + [str(value) for value in duplicate.get("phase_keys", [])]
        )
    )
    canonical["analysis_methods"] = list(
        dict.fromkeys(
            [str(value) for value in canonical.get("analysis_methods", [])]
            + [str(value) for value in duplicate.get("analysis_methods", [])]
        )
    )
    canonical["representative_spatial_hours"] = sorted(set(
        [
            int(value)
            for value in canonical.get("representative_spatial_hours", [])
            if isinstance(value, int) and not isinstance(value, bool)
        ]
        + [
            int(value)
            for value in duplicate.get("representative_spatial_hours", [])
            if isinstance(value, int) and not isinstance(value, bool)
        ]
    ))
    canonical["coverage_roles"] = list(
        dict.fromkeys(
            [str(value) for value in canonical.get("coverage_roles", [])]
            + [str(value) for value in duplicate.get("coverage_roles", [])]
        )
    )
    if str(duplicate.get("priority")) == "required":
        canonical["priority"] = "required"
    canonical_role = str(canonical.get("evidence_role", "context"))
    duplicate_role = str(duplicate.get("evidence_role", "context"))
    if _COMPACT_EVIDENCE_ROLE_RANK.get(
        duplicate_role, -1
    ) > _COMPACT_EVIDENCE_ROLE_RANK.get(canonical_role, -1):
        canonical["evidence_role"] = duplicate_role

    if (
        str(canonical.get("diagnostic_id")) == "temporal_evolution"
        and str(canonical.get("variable_role")) == "uncertainty"
        and str(duplicate.get("variable_role")) == "hazard_magnitude"
    ):
        canonical["variable_role"] = "hazard_magnitude"

    for field in ("expected_signal", "decision_relevance", "purpose"):
        canonical[field] = _more_specific_compact_narrative(
            canonical.get(field), duplicate.get(field)
        )


def _validate_compact_merged_check_coherence(
    row: dict[str, Any],
    *,
    phase_hours_by_key: dict[str, set[int]],
) -> None:
    """Fail closed when a merged Check contains contradictory source intent."""
    source_intents = row.get("_source_intents")
    if isinstance(source_intents, list) and source_intents:
        roles = {
            str(item.get("evidence_role") or "context")
            for item in source_intents
            if isinstance(item, dict)
        }
        canonical_role = str(row.get("evidence_role") or "context")
        if len(roles) > 1 or (roles and canonical_role not in roles):
            raise ValueError(
                "COMPACT_CHECK_MERGE_CONTRACT_CONFLICT "
                f"check_key={row.get('check_key')} source_evidence_roles={sorted(roles)} "
                f"canonical_evidence_role={canonical_role!r}; "
                "repair=split supporting/opposing/context propositions into separate verification_requests"
            )

    source_claim_sets = row.get("_source_atomic_claim_sets")
    if isinstance(source_claim_sets, list) and source_claim_sets:
        signatures: list[tuple[Any, ...]] = []
        for item in source_claim_sets:
            if not isinstance(item, dict):
                continue
            signatures.append(
                _compact_atomic_claim_semantic_signature(
                    {"atomic_claims": item.get("atomic_claims")},
                    phase_hours_by_key=phase_hours_by_key,
                )
            )
        if signatures and any(signature != signatures[0] for signature in signatures[1:]):
            raise ValueError(
                "COMPACT_CHECK_MERGE_CONTRACT_CONFLICT "
                f"check_key={row.get('check_key')} source_atomic_claims_are_not_equivalent; "
                "repair=keep distinct expected_state/time-window/quantitative-scope claims as separate verification_requests"
            )
        canonical_signature = _compact_atomic_claim_semantic_signature(
            row, phase_hours_by_key=phase_hours_by_key
        )
        if signatures and canonical_signature != signatures[0]:
            raise ValueError(
                "COMPACT_CHECK_MERGE_CONTRACT_CONFLICT "
                f"check_key={row.get('check_key')} canonical_atomic_claim_differs_from_sources; "
                "repair=regenerate the complete verification_request rather than merging incompatible fields"
            )


def _compact_hypothesis_reference_sets(
    row: dict[str, Any],
) -> tuple[set[str], set[str]]:
    supporting = {
        str(value)
        for value in row.get("supporting_check_keys", [])
        if str(value)
    }
    opposing = {
        str(value)
        for value in row.get("opposing_check_keys", [])
        if str(value)
    }
    return supporting, opposing


def _compact_hypotheses_are_semantic_duplicates(
    left: dict[str, Any],
    right: dict[str, Any],
) -> bool:
    left_supporting, left_opposing = _compact_hypothesis_reference_sets(left)
    right_supporting, right_opposing = _compact_hypothesis_reference_sets(right)
    if not left_supporting or not right_supporting:
        return False
    if (left_supporting & right_opposing) or (
        right_supporting & left_opposing
    ):
        return False
    supporting_union = left_supporting | right_supporting
    all_left = left_supporting | left_opposing
    all_right = right_supporting | right_opposing
    all_union = all_left | all_right
    if not supporting_union or not all_union:
        return False
    supporting_jaccard = len(left_supporting & right_supporting) / len(supporting_union)
    all_jaccard = len(all_left & all_right) / len(all_union)
    left_statement = re.sub(r"\s+", "", str(left.get("statement", "")).casefold())
    right_statement = re.sub(r"\s+", "", str(right.get("statement", "")).casefold())
    statement_similarity = SequenceMatcher(
        None, left_statement, right_statement, autojunk=False
    ).ratio()
    return (
        supporting_jaccard >= 0.67
        and all_jaccard >= 0.60
        and statement_similarity >= 0.38
    )


def _merge_semantic_duplicate_compact_hypotheses(
    rows: list[dict[str, Any]],
    *,
    candidate_id: str,
) -> list[dict[str, Any]]:
    retained: list[dict[str, Any]] = []
    for raw in rows:
        row = copy.deepcopy(raw)
        duplicate_of = next(
            (
                existing
                for existing in retained
                if _compact_hypotheses_are_semantic_duplicates(existing, row)
            ),
            None,
        )
        if duplicate_of is None:
            retained.append(row)
            continue
        duplicate_of["supporting_check_keys"] = list(
            dict.fromkeys(
                [str(value) for value in duplicate_of.get("supporting_check_keys", [])]
                + [str(value) for value in row.get("supporting_check_keys", [])]
            )
        )
        duplicate_of["opposing_check_keys"] = list(
            dict.fromkeys(
                [str(value) for value in duplicate_of.get("opposing_check_keys", [])]
                + [str(value) for value in row.get("opposing_check_keys", [])]
            )
        )
        overlap = set(duplicate_of["supporting_check_keys"]) & set(duplicate_of["opposing_check_keys"])
        if overlap:
            duplicate_of["opposing_check_keys"] = [
                value for value in duplicate_of["opposing_check_keys"] if value not in overlap
            ]
        duplicate_of["statement"] = _more_specific_compact_narrative(
            duplicate_of.get("statement"), row.get("statement")
        )
        logging.info(
            "step04 continuation hypothesis semantic duplicate merged "
            "candidate_id=%s retained_hypothesis_key=%s removed_hypothesis_key=%s",
            candidate_id,
            duplicate_of.get("hypothesis_key"),
            row.get("hypothesis_key"),
        )
    return retained


def _canonicalize_semantic_duplicate_compact_plan(
    check_rows: list[dict[str, Any]],
    hypothesis_rows: list[dict[str, Any]],
    phase_rows: list[dict[str, Any]],
    *,
    candidate_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge continuation checks that request the same physical diagnosis.

    The first check key is canonical. Phase and method coverage are unioned, the
    more specific narratives are retained, and all hypothesis references are
    redirected before duplicate hypotheses are consolidated.
    """
    phase_hours_by_key = {
        str(row.get("phase_key")): set(_compact_phase_hours(row))
        for row in phase_rows
        if str(row.get("phase_key", "")).strip()
    }
    retained: list[dict[str, Any]] = []
    aliases: dict[str, str] = {}
    for raw in check_rows:
        row = copy.deepcopy(raw)
        local_key = str(row.get("check_key", "")).strip()
        duplicate_of = next(
            (
                existing
                for existing in retained
                if _compact_checks_are_semantic_duplicates(
                    existing,
                    row,
                    phase_hours_by_key=phase_hours_by_key,
                )
            ),
            None,
        )
        if duplicate_of is None:
            retained.append(row)
            aliases[local_key] = local_key
            continue
        canonical_key = str(duplicate_of.get("check_key", "")).strip()
        aliases[local_key] = canonical_key
        _merge_compact_semantic_check(duplicate_of, row)
        _validate_compact_merged_check_coherence(
            duplicate_of,
            phase_hours_by_key=phase_hours_by_key,
        )
        logging.info(
            "step04 continuation diagnostic semantic duplicate merged "
            "candidate_id=%s retained_check_key=%s removed_check_key=%s diagnostic_id=%s",
            candidate_id,
            canonical_key,
            local_key,
            row.get("diagnostic_id"),
        )

    remapped_hypotheses: list[dict[str, Any]] = []
    for raw in hypothesis_rows:
        hypothesis = copy.deepcopy(raw)
        for field in ("supporting_check_keys", "opposing_check_keys"):
            hypothesis[field] = list(
                dict.fromkeys(
                    aliases.get(str(value), str(value))
                    for value in hypothesis.get(field, [])
                )
            )
        remapped_hypotheses.append(hypothesis)
    _canonicalize_redundant_temporal_evolution_checks(
        retained,
        remapped_hypotheses,
        phase_hours_by_key=phase_hours_by_key,
        candidate_id=candidate_id,
    )
    _canonicalize_disjoint_hypothesis_check_references(
        remapped_hypotheses,
        candidate_id=candidate_id,
    )
    remapped_hypotheses = _drop_hypotheses_without_supporting_checks(
        remapped_hypotheses,
        candidate_id=candidate_id,
        field_suffix="keys",
        require_one=True,
    )
    remapped_hypotheses = _merge_semantic_duplicate_compact_hypotheses(
        remapped_hypotheses,
        candidate_id=candidate_id,
    )
    return retained, remapped_hypotheses

def _canonicalize_disjoint_hypothesis_check_references(
    hypotheses: list[dict[str, Any]],
    *,
    candidate_id: str,
) -> None:
    """Reject a Check that is assigned both supporting and opposing polarity."""
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            continue
        supporting = list(
            dict.fromkeys(
                str(value)
                for value in hypothesis.get(
                    "supporting_check_keys",
                    hypothesis.get("supporting_check_ids", []),
                )
            )
        )
        opposing = list(
            dict.fromkeys(
                str(value)
                for value in hypothesis.get(
                    "opposing_check_keys",
                    hypothesis.get("opposing_check_ids", []),
                )
            )
        )
        overlap = set(supporting) & set(opposing)
        if not overlap:
            continue
        raise ValueError(
            "HYPOTHESIS_CHECK_ROLE_CONFLICT "
            f"candidate_id={candidate_id} "
            f"hypothesis={hypothesis.get('hypothesis_key', hypothesis.get('hypothesis_id'))} "
            f"check_ids={sorted(overlap)}; "
            "repair=one Check cannot be both supporting and opposing for the same hypothesis; "
            "split the physical propositions into separate Checks or keep only the intended evidence role"
        )


def _drop_hypotheses_without_supporting_checks(
    hypotheses: list[dict[str, Any]],
    *,
    candidate_id: str,
    field_suffix: str,
    require_one: bool,
) -> list[dict[str, Any]]:
    """Drop provider hypotheses that have no positive evaluation path.

    The canonical contract requires at least one supporting check for every
    hypothesis.  A continuation model can still emit an empty supporting
    array because the OpenAI transport schema intentionally avoids some array
    cardinality keywords.  Promoting an opposing check to supporting would
    invent semantics, so unsupported hypotheses are removed instead.
    """
    supporting_field = f"supporting_check_{field_suffix}"
    retained: list[dict[str, Any]] = []
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            continue
        supporting = [
            str(value).strip()
            for value in hypothesis.get(supporting_field, [])
            if str(value).strip()
        ]
        hypothesis[supporting_field] = list(dict.fromkeys(supporting))
        if hypothesis[supporting_field]:
            retained.append(hypothesis)
            continue
        logging.warning(
            "step04 candidate plan unsupported hypothesis removed "
            "candidate_id=%s hypothesis=%s field=%s",
            candidate_id,
            hypothesis.get("hypothesis_key", hypothesis.get("hypothesis_id")),
            supporting_field,
        )
    if require_one and not retained:
        raise ValueError(
            "candidate plan requires at least one hypothesis with a supporting check"
        )
    return retained


def _hypothesis_check_role_errors(
    hypotheses: Iterable[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            continue
        supporting = {str(value) for value in hypothesis.get("supporting_check_ids", [])}
        opposing = {str(value) for value in hypothesis.get("opposing_check_ids", [])}
        overlap = sorted(supporting & opposing)
        if overlap:
            errors.append(
                f"{hypothesis.get('hypothesis_id')} uses the same check as "
                f"supporting and opposing: {overlap}"
            )
    return errors


_VARIABLE_ROLE_HYPOTHESIS_TERMS: dict[str, tuple[str, ...]] = {
    "hazard_magnitude": ("강수", "호우", "강도", "최대", "피크", "peak", "누적"),
    "moisture_supply": ("수분", "수증기", "유입", "플럭스", "850", "습윤"),
    "low_level_forcing": ("수렴", "925", "하층", "저층", "경계층"),
    "midlevel_forcing": ("상승", "오메가", "omega", "700", "중층", "연직", "단면"),
    "synoptic_support": ("저기압", "기압골", "전선", "제트", "종관", "상층", "기압계"),
    "uncertainty": ("중첩", "일치", "불확실", "신뢰", "복합", "정합", "검증"),
}


def _narrative_tokens(*values: Any) -> set[str]:
    """Return stable Korean/English tokens for local semantic matching."""
    text = " ".join(str(value) for value in values if value is not None).casefold()
    return {
        token
        for token in re.findall(r"[0-9a-z가-힣_]+", text)
        if len(token) >= 2
    }


def _hypothesis_check_link_score(
    hypothesis: dict[str, Any],
    check: dict[str, Any],
    *,
    checks_by_id: dict[str, dict[str, Any]],
) -> tuple[int, int, int]:
    """Rank the most defensible hypothesis for an otherwise unlinked check.

    The score uses only information already present in the validated plan:
    narrative overlap, variable-role terms, and event-phase overlap with checks
    already referenced by the hypothesis.  No new meteorological assertion is
    invented.
    """
    statement = str(hypothesis.get("statement", ""))
    hypothesis_tokens = _narrative_tokens(statement)
    check_tokens = _narrative_tokens(
        check.get("expected_signal"),
        check.get("decision_relevance"),
        check.get("diagnostic_id"),
        check.get("variable_role"),
    )
    lexical_overlap = len(hypothesis_tokens & check_tokens)

    role = str(check.get("variable_role", ""))
    role_terms = _VARIABLE_ROLE_HYPOTHESIS_TERMS.get(role, ())
    role_hits = sum(1 for term in role_terms if term.casefold() in statement.casefold())

    referenced_ids = {
        str(value)
        for field in ("supporting_check_ids", "opposing_check_ids")
        for value in hypothesis.get(field, [])
    }
    referenced_phases = {
        str(phase_id)
        for check_id in referenced_ids
        for phase_id in checks_by_id.get(check_id, {}).get("phase_ids", [])
    }
    orphan_phases = {str(value) for value in check.get("phase_ids", [])}
    phase_overlap = len(referenced_phases & orphan_phases)
    return (role_hits * 8 + lexical_overlap * 2 + phase_overlap * 5, phase_overlap, lexical_overlap)


def _link_unreferenced_variable_checks(
    plan: dict[str, Any],
    *,
    canonicalization_events: list[dict[str, Any]] | None = None,
) -> None:
    """Ensure every generated diagnostic check has an explicit hypothesis use.

    Provider output occasionally creates a valid and useful diagnostic but omits
    its hypothesis reference.  Dropping such a check loses requested evidence;
    leaving it orphaned makes Pass2 intent ambiguous.  Attach each orphan to the
    best matching hypothesis using deterministic local evidence already present
    in the plan.  The check is added as supporting evidence because its
    ``expected_signal`` states the positive condition it is designed to test.
    """
    hypotheses = [item for item in plan.get("hypotheses", []) if isinstance(item, dict)]
    checks = [item for item in plan.get("variable_checks", []) if isinstance(item, dict)]
    if not hypotheses or not checks:
        return

    checks_by_id = {
        str(check.get("check_id")): check
        for check in checks
        if str(check.get("check_id", "")).strip()
    }
    referenced = {
        str(value)
        for hypothesis in hypotheses
        for field in ("supporting_check_ids", "opposing_check_ids")
        for value in hypothesis.get(field, [])
    }
    for check_index, check in enumerate(checks):
        check_id = str(check.get("check_id", "")).strip()
        if not check_id or check_id in referenced:
            continue
        if _is_system_direct_precipitation_anchor_check(check):
            # System validation contracts are deliberately independent of
            # model-authored hypotheses.
            continue
        ranked = sorted(
            enumerate(hypotheses),
            key=lambda item: (
                _hypothesis_check_link_score(
                    item[1],
                    check,
                    checks_by_id=checks_by_id,
                ),
                -item[0],
            ),
            reverse=True,
        )
        target_index, target = ranked[0]
        supporting = [str(value) for value in target.get("supporting_check_ids", [])]
        target["supporting_check_ids"] = list(dict.fromkeys([*supporting, check_id]))
        referenced.add(check_id)
        event = {
            "field": (
                "candidate_analysis_plan.variable_checks"
                f"[{check_index}].hypothesis_reference"
            ),
            "reason": "unreferenced_variable_check_linked_to_best_matching_hypothesis",
            "check_id": check_id,
            "hypothesis_id": target.get("hypothesis_id"),
            "score": list(
                _hypothesis_check_link_score(
                    target,
                    check,
                    checks_by_id=checks_by_id,
                )
            ),
        }
        if canonicalization_events is not None:
            canonicalization_events.append(event)
        logging.warning(
            "step04 candidate plan unreferenced check linked "
            "candidate_id=%s check_id=%s hypothesis_id=%s score=%s",
            plan.get("candidate_id"),
            check_id,
            target.get("hypothesis_id"),
            event["score"],
        )


def _unreferenced_variable_check_errors(plan: dict[str, Any]) -> list[str]:
    hypotheses = [item for item in plan.get("hypotheses", []) if isinstance(item, dict)]
    checks = [item for item in plan.get("variable_checks", []) if isinstance(item, dict)]
    referenced = {
        str(value)
        for hypothesis in hypotheses
        for field in ("supporting_check_ids", "opposing_check_ids")
        for value in hypothesis.get(field, [])
    }
    orphan_ids = sorted(
        str(check.get("check_id"))
        for check in checks
        if str(check.get("check_id", "")).strip()
        and str(check.get("check_id")) not in referenced
        and not _is_system_direct_precipitation_anchor_check(check)
    )
    if not orphan_ids:
        return []
    return [
        "candidate analysis plan contains variable_checks not linked to any hypothesis: "
        f"{orphan_ids}"
    ]


def _limit_canonical_hypotheses(
    hypotheses: list[dict[str, Any]],
    *,
    candidate_number: str,
) -> list[dict[str, Any]]:
    """Select the bounded canonical hypotheses with the broadest check coverage."""
    if len(hypotheses) <= CANONICAL_PLAN_MAX_HYPOTHESES:
        return hypotheses

    remaining = list(enumerate(hypotheses))
    selected: list[tuple[int, dict[str, Any]]] = []
    covered_check_ids: set[str] = set()
    while remaining and len(selected) < CANONICAL_PLAN_MAX_HYPOTHESES:
        def score(item: tuple[int, dict[str, Any]]) -> tuple[int, int]:
            index, hypothesis = item
            referenced = {
                str(value)
                for field in ("supporting_check_ids", "opposing_check_ids")
                for value in hypothesis.get(field, [])
            }
            return len(referenced - covered_check_ids), -index

        chosen = max(remaining, key=score)
        remaining.remove(chosen)
        selected.append(chosen)
        covered_check_ids.update(
            str(value)
            for field in ("supporting_check_ids", "opposing_check_ids")
            for value in chosen[1].get(field, [])
        )

    limited: list[dict[str, Any]] = []
    for output_index, (_source_index, hypothesis) in enumerate(sorted(selected), start=1):
        value = copy.deepcopy(hypothesis)
        value["hypothesis_id"] = f"H_CAND{candidate_number}_{output_index:02d}"
        limited.append(value)
    return limited


def _limit_canonical_questions(questions: list[str]) -> list[str]:
    """Keep the provider's relevance order while removing exact question duplicates."""
    seen: set[str] = set()
    unique: list[str] = []
    for question in questions:
        value = str(question).strip()
        key = re.sub(r"\s+", " ", value).casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique[:CANONICAL_PLAN_MAX_FORECASTER_QUESTIONS]


def _compact_plan_context(
    chunks: list[dict[str, Any]],
    *,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    phases: list[str] = []
    phase_types: list[str] = []
    existing_phases: list[dict[str, Any]] = []
    checks: list[str] = []
    combinations: list[dict[str, str]] = []
    coverage: dict[str, str] = {}
    context_domain: dict[str, float] | None = None
    context_domain_reason: str | None = None
    phase_aliases = _compact_phase_key_aliases(chunks)
    for chunk in chunks:
        phase_rows = _compact_plan_rows(chunk, "analysis_phases")
        check_rows = _compact_plan_rows(chunk, "diagnostic_checks")
        phases.extend(str(item["phase_key"]) for item in phase_rows)
        phase_types.extend(str(item.get("phase_type")) for item in phase_rows)
        existing_phases.extend(
            {
                "phase_key": str(item["phase_key"]),
                "phase_type": str(item.get("phase_type")),
                "forecast_hours": list(_compact_phase_hours(item)),
            }
            for item in phase_rows
        )
        checks.extend(str(item["check_key"]) for item in check_rows)
        for check in check_rows:
            for phase_key in check.get("phase_keys", []):
                combinations.append(
                    {
                        "signal_components": list(check.get("required_signal_components", [])),
                        "verification_mode": _verification_mode_from_diagnostic_check(check),
                        "domain_scope": str(check.get("domain_scope", "candidate")),
                        "phase_key": phase_aliases.get(str(phase_key), str(phase_key)),
                    }
                )
        raw_context = _normalized_bbox_for_validation(chunk.get("context_domain"))
        if raw_context is not None and context_domain is None:
            context_domain = raw_context
            reason = chunk.get("context_domain_reason")
            context_domain_reason = str(reason).strip() if reason is not None else None
        coverage = evaluate_compact_candidate_plan_coverage(chunks, candidate=candidate)
    remaining_roles = sorted(
        role for role, status in coverage.items() if status == "missing_but_relevant"
    )
    role_guidance = {
        "temporal_evolution": (
            "둘 이상의 예측시각을 실제로 비교하는 새 verification_request를 만드십시오. "
            "강도·평균값 변화는 verification_mode=temporal_change, 공간 중심의 이동·분리·재조직은 "
            "verification_mode=spatial_evolution, 연직 시간변화는 vertical_time_evolution을 사용하십시오. "
            "spatial_evolution과 temporal_evolution이 함께 남아 있으면 둘 이상의 실제 시각을 사용하는 "
            "단일 spatial_evolution 요청으로 두 역할을 함께 충족할 수 있습니다. "
            "단일시각 요청에 coverage_roles 태그만 추가해서는 충족되지 않습니다."
        ),
        "spatial_evolution": (
            "둘 이상의 예측시각 공간장을 비교하는 verification_mode=spatial_evolution 요청을 추가하십시오. "
            "이동·확대·축소·분리·재조직처럼 방향성이 있는 검증 명제를 작성하십시오. "
            "이 요청은 둘 이상의 실제 예측시각을 사용하면 temporal_evolution 역할도 함께 충족합니다."
        ),
        "moisture_supply": "signal_components에 moisture_supply를 포함한 실제 수분공급 검증 요청을 추가하십시오.",
        "low_level_forcing": "signal_components에 low_level_forcing을 포함한 실제 하층수렴 검증 요청을 추가하십시오.",
        "mid_level_forcing": "signal_components에 mid_upper_forcing을 포함한 실제 중층상승 검증 요청을 추가하십시오.",
        "supporting_evidence": "evidence_role=supporting인 실제 지지 검증 요청을 추가하십시오.",
        "opposing_evidence": "evidence_role=opposing인 실제 제한·반대 검증 요청을 추가하십시오.",
    }
    return {
        "existing_phase_keys": phases,
        "existing_phase_types": list(dict.fromkeys(phase_types)),
        "existing_phases": existing_phases,
        "existing_check_keys": checks,
        "existing_verification_domain_phase_combinations": combinations,
        "context_domain": context_domain,
        "context_domain_reason": context_domain_reason,
        "coverage_status": coverage,
        "remaining_roles": remaining_roles,
        "remaining_role_guidance": {
            role: role_guidance.get(
                role,
                "해당 역할을 실제로 검증하는 새로운 verification_request를 추가하십시오."
            )
            for role in remaining_roles
        },
        "instruction": (
            "후속 chunk에서는 remaining_roles를 최우선으로 채우십시오. "
            "coverage_roles 문자열만 추가하지 말고 remaining_role_guidance에 맞는 실제 verification 구조를 만드십시오. "
            "existing_phases의 forecast_hours를 사용해 기존 phase를 재사용할 수 있습니다. "
            "남은 필수 역할이 하나 이상이면 최소 하나의 remaining_role을 실제로 감소시키는 verification_request를 추가해야 하며, "
            "이미 충족된 역할의 요청만 추가하거나 빈 continuation을 반환해서는 안 됩니다."
        ),
    }


def validate_compact_candidate_plan_response(
    value: dict[str, Any],
    *,
    schema: dict[str, Any] | None,
    candidate: dict[str, Any],
    attachment_manifest: dict[str, Any] | None = None,
    support_catalog: dict[str, Any] | None = None,
    operational_region_catalog: dict[str, Any] | None = None,
    expected_chunk_index: int,
    previous_chunks: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    if schema is not None:
        errors.extend(_validate_jsonschema(value, schema))
        if errors:
            return errors
    errors.extend(validate_step04_compact_plan_narrative(value))
    if errors:
        return errors
    if int(value.get("plan_chunk_index", -1)) != expected_chunk_index:
        return [f"compact candidate plan chunk index must be {expected_chunk_index}"]
    try:
        phases = _compact_plan_rows(value, "analysis_phases")
        checks = _compact_plan_rows(value, "diagnostic_checks")
        hypotheses = _compact_plan_rows(value, "hypotheses")
        phase_keys = _compact_local_keys(phases, "phase_key", label="analysis_phases")
        check_keys = _compact_local_keys(checks, "check_key", label="diagnostic_checks")
        _compact_local_keys(hypotheses, "hypothesis_key", label="hypotheses")

        signatures = [_compact_phase_signature(item) for item in phases]
        if len(signatures) != len(set(signatures)):
            raise ValueError("compact candidate plan repeats an identical phase_type/forecast_hours phase")
        phase_segmentation_notes: list[str] = []
        for index, (phase_type, hours) in enumerate(signatures):
            if not phase_type:
                raise ValueError("compact analysis phase requires phase_type")
            if len(_contiguous_hour_groups(hours)) > 1:
                phase_segmentation_notes.append(
                    "AI analysis phase contains temporally separated forecast hours "
                    f"phase_type={phase_type!r} hours={list(hours)}"
                )
            for other_type, other_hours in signatures[index + 1 :]:
                if phase_type == other_type and set(hours) & set(other_hours):
                    phase_segmentation_notes.append(
                        "AI phases with the same phase_type overlap forecast_hours "
                        f"phase_type={phase_type!r}"
                    )
        _log_step04_advisories("candidate_plan.phase_segmentation", phase_segmentation_notes)

        if len(str(value.get("analysis_objective", ""))) > 240:
            raise ValueError("analysis_objective must be concise (<=240 characters)")
        for hypothesis in hypotheses:
            if len(str(hypothesis.get("statement", ""))) > 250:
                raise ValueError("hypothesis statement must be concise (<=250 characters)")
        for check in checks:
            for field in ("purpose", "expected_signal", "decision_relevance"):
                if len(str(check.get(field, ""))) > 180:
                    raise ValueError(f"diagnostic check {field} must be concise (<=180 characters)")
        for question in value.get("forecaster_questions", []):
            if len(str(question)) > 180:
                raise ValueError("forecaster question must be concise (<=180 characters)")

        candidate_bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
        if candidate_bbox is None:
            raise ValueError("candidate_area bbox is invalid")
        selectable_operational_regions = None
        if operational_region_catalog is not None:
            selectable_operational_regions = _selectable_candidate_operational_regions(
                candidate_bbox=candidate_bbox,
                operational_region_catalog=operational_region_catalog,
            )
        raw_context = value.get("context_domain")
        context_bbox = _normalized_bbox_for_validation(raw_context)
        reason = value.get("context_domain_reason")
        reason_text = str(reason).strip() if reason is not None else ""
        if raw_context is None:
            if reason is not None:
                raise ValueError("context_domain_reason must be null when context_domain is null")
        else:
            if context_bbox is None:
                raise ValueError("context_domain bbox is invalid")
            if not _bbox_contains(context_bbox, candidate_bbox):
                raise ValueError("context_domain must contain the complete candidate_area")
            if not reason_text:
                raise ValueError("context_domain requires a concise context_domain_reason")
            if len(reason_text) > 200:
                raise ValueError("context_domain_reason must be concise (<=200 characters)")

        previous_phase_keys: set[str] = set()
        previous_phase_signature_by_key: dict[str, tuple[str, tuple[int, ...]]] = {}
        previous_check_keys: set[str] = set()
        previous_context: dict[str, float] | None = None
        previous_reason: str | None = None
        all_phase_rows: list[dict[str, Any]] = []
        for previous in previous_chunks:
            previous_phase_rows = _compact_plan_rows(previous, "analysis_phases")
            all_phase_rows.extend(previous_phase_rows)
            previous_phase_keys.update(
                _compact_local_keys(previous_phase_rows, "phase_key", label="previous analysis_phases")
            )
            for previous_phase in previous_phase_rows:
                previous_key = str(previous_phase.get("phase_key", "")).strip()
                previous_signature = _compact_phase_signature(previous_phase)
                known_signature = previous_phase_signature_by_key.setdefault(
                    previous_key, previous_signature
                )
                if known_signature != previous_signature:
                    raise ValueError(
                        "previous compact candidate plan reuses phase_key with a different phase definition "
                        f"phase_key={previous_key!r}"
                    )
            previous_rows = _compact_plan_rows(previous, "diagnostic_checks")
            previous_check_keys.update(
                _compact_local_keys(previous_rows, "check_key", label="previous diagnostic_checks")
            )
            previous_bbox = _normalized_bbox_for_validation(previous.get("context_domain"))
            if previous_bbox is not None and previous_context is None:
                previous_context = previous_bbox
                previous_value = previous.get("context_domain_reason")
                previous_reason = str(previous_value).strip() if previous_value is not None else None
        all_phase_rows.extend(phases)

        for phase in phases:
            current_key = str(phase.get("phase_key", "")).strip()
            current_signature = _compact_phase_signature(phase)
            previous_signature = previous_phase_signature_by_key.get(current_key)
            if previous_signature is not None and previous_signature != current_signature:
                raise ValueError(
                    "candidate_id="
                    f"{candidate.get('candidate_id')} chunk={expected_chunk_index} object=analysis_phase "
                    "reason=conflicting phase definition for an existing continuation key "
                    f"phase_key={current_key!r} previous={previous_signature!r} current={current_signature!r}"
                )
        if previous_chunks:
            if previous_context != context_bbox:
                raise ValueError("continuation chunk must preserve the original context_domain")
            if (previous_reason or None) != (reason_text or None):
                raise ValueError("continuation chunk must preserve context_domain_reason")

        available_hours = (
            _available_candidate_forecast_hours(candidate, attachment_manifest)
            if attachment_manifest is not None
            else sorted({hour for row in all_phase_rows for hour in _compact_phase_hours(row)})
        )
        available_hour_set = set(available_hours)
        for phase in phases:
            invalid_hours = sorted(set(_compact_phase_hours(phase)) - available_hour_set)
            if invalid_hours:
                raise ValueError(
                    "compact analysis phase uses unavailable forecast_hours="
                    f"{invalid_hours}; available={available_hours}"
                )

        _log_step04_advisories(
            "candidate_plan.phase_timeline",
            _phase_timeline_advisories(
                all_phase_rows,
                available_hours=available_hours,
            ),
        )
        timeline_errors = _validate_phase_timeline(
            all_phase_rows,
            available_hours=available_hours,
            candidate_peak_hours=candidate.get("peak_forecast_hours", []),
        )
        if timeline_errors:
            raise ValueError(" | ".join(timeline_errors))

        # Across continuation chunks, exact restatements are aliases. Distinct
        # same-type phases must remain temporally disjoint.
        signature_to_key: dict[tuple[str, tuple[int, ...]], str] = {}
        type_hours: dict[str, list[tuple[str, set[int]]]] = defaultdict(list)
        for phase in all_phase_rows:
            key = str(phase.get("phase_key", "")).strip()
            signature = _compact_phase_signature(phase)
            canonical_key = signature_to_key.setdefault(signature, key)
            if canonical_key != key and key in previous_phase_signature_by_key:
                continue
            phase_type, hours = signature
            current_hours = set(hours)
            for other_key, other_hours in type_hours[phase_type]:
                if current_hours & other_hours and signature_to_key.get(signature) != other_key:
                    _log_step04_advisories(
                        "candidate_plan.phase_segmentation",
                        [
                            "AI phases with the same phase_type overlap across chunks "
                            f"phase_type={phase_type!r} keys={[other_key, key]}"
                        ],
                    )
            type_hours[phase_type].append((canonical_key, current_hours))

        available_phase_keys = previous_phase_keys | phase_keys
        phase_hours_by_key = {
            str(row.get("phase_key", "")): set(_compact_phase_hours(row))
            for row in all_phase_rows
        }

        # One deterministic normalization layer owns all representation-level
        # candidate-plan cleanup before strict semantic validation.  This path is
        # also executed after full-Check repair merges because every semantic
        # validation pass re-enters this function.
        _normalize_compact_candidate_plan_contracts(
            value,
            all_phase_rows=all_phase_rows,
            available_hours=available_hours,
            candidate_id=str(candidate.get("candidate_id", "")),
        )
        checks = _compact_plan_rows(value, "diagnostic_checks")
        hypotheses = _compact_plan_rows(value, "hypotheses")

        for check in checks:
            references = {str(item) for item in check.get("phase_keys", [])}
            unknown = sorted(references - available_phase_keys)
            if unknown:
                raise ValueError(
                    "candidate_id="
                    f"{candidate.get('candidate_id')} chunk={expected_chunk_index} "
                    "object=diagnostic_check reason=unresolved phase reference after normalization "
                    f"phase_keys={unknown}"
                )
            diagnostic_id = str(check.get("diagnostic_id"))
            if diagnostic_id not in OPERATIONAL_DIAGNOSTIC_IDS:
                raise ValueError(
                    f"compact diagnostic check has unsupported diagnostic_id={diagnostic_id!r}"
                )
            if support_catalog is not None:
                supported_ids = {
                    str(item)
                    for item in support_catalog.get("analysis_plan_support", {}).get(
                        "supported_diagnostic_ids", []
                    )
                }
                if supported_ids and diagnostic_id not in supported_ids:
                    raise ValueError(
                        f"compact diagnostic check is not executable by the active catalog "
                        f"diagnostic_id={diagnostic_id!r}"
                    )
            regional_methods = {str(item) for item in check.get("analysis_methods", [])} & REGIONAL_ANALYSIS_METHODS
            if regional_methods and selectable_operational_regions == []:
                raise ValueError(
                    _operational_region_target_empty_error(
                        candidate_id=str(candidate.get("candidate_id") or ""),
                        candidate_bbox=candidate_bbox,
                        check_keys=[str(check.get("check_key") or "")],
                    )
                )
            variable_role = _canonical_variable_role_for_diagnostic(
                diagnostic_id, str(check.get("variable_role"))
            )
            check["variable_role"] = variable_role
            required_roles = DIAGNOSTIC_REQUIRED_VARIABLE_ROLES.get(diagnostic_id)
            if required_roles is not None and variable_role not in required_roles:
                raise ValueError(
                    "compact diagnostic check has incompatible diagnostic_id/variable_role "
                    f"diagnostic_id={diagnostic_id!r} variable_role={variable_role!r} "
                    f"allowed={sorted(required_roles)}"
                )
            scope = str(check.get("domain_scope"))
            if scope == "context" and context_bbox is None:
                raise ValueError("domain_scope=context requires context_domain")
            if diagnostic_id in CANDIDATE_SCOPE_ONLY_DIAGNOSTIC_IDS and scope != "candidate":
                raise ValueError(
                    f"{diagnostic_id} must use domain_scope=candidate because the standard "
                    "Step05 contract aggregates the candidate core area"
                )
            raw_cross_section_line = check.get("cross_section_line")
            cross_section_line = _normalized_cross_section_line(raw_cross_section_line)
            cross_section_strategy = _normalized_cross_section_strategy(
                check.get("cross_section_strategy")
            )
            reference_band_axis = _normalized_reference_band_axis(
                check.get("reference_band_axis")
            )
            if diagnostic_id == "vertical_cross_section":
                if cross_section_line is None:
                    raise ValueError(
                        "vertical_cross_section requires a valid cross_section_line with start/end "
                        "latitude/longitude and selection_reason"
                    )
                if cross_section_strategy is None:
                    raise ValueError(
                        "vertical_cross_section requires a valid cross_section_strategy"
                    )
                if reference_band_axis is None:
                    raise ValueError(
                        "vertical_cross_section requires a valid reference_band_axis"
                    )
                selected_bbox = candidate_bbox if scope == "candidate" else context_bbox
                if selected_bbox is None:
                    raise ValueError("vertical_cross_section has no resolved selected domain")
                endpoints = (
                    (cross_section_line["start_longitude"], cross_section_line["start_latitude"]),
                    (cross_section_line["end_longitude"], cross_section_line["end_latitude"]),
                )
                if any(not _point_in_bbox(lon, lat, selected_bbox) for lon, lat in endpoints):
                    raise ValueError(
                        "vertical_cross_section endpoints must be inside the selected domain_scope bbox"
                    )
                if not _segment_intersects_bbox(
                    cross_section_line["start_longitude"],
                    cross_section_line["start_latitude"],
                    cross_section_line["end_longitude"],
                    cross_section_line["end_latitude"],
                    candidate_bbox,
                ):
                    raise ValueError(
                        "vertical_cross_section line must intersect the candidate_area"
                    )
                original_angle_diff = _line_angle_difference_degrees(
                    cross_section_line, reference_band_axis
                )
                canonical_cross_section_line = _canonical_cross_section_line_orientation(
                    line=cross_section_line,
                    strategy=cross_section_strategy,
                    reference_axis=reference_band_axis,
                    selected_bbox=selected_bbox,
                    candidate_bbox=candidate_bbox,
                )
                if canonical_cross_section_line is None:
                    raise ValueError(
                        "vertical_cross_section line orientation could not be canonicalized "
                        "from cross_section_strategy and reference_band_axis"
                    )
                cross_section_line = canonical_cross_section_line
                canonical_angle_diff = _line_angle_difference_degrees(
                    cross_section_line, reference_band_axis
                )
                if (
                    cross_section_strategy == "across_precipitation_band"
                    and not (60.0 <= canonical_angle_diff <= 120.0)
                ):
                    raise ValueError(
                        "vertical_cross_section with "
                        "cross_section_strategy=across_precipitation_band must be broadly "
                        "perpendicular to reference_band_axis"
                    )
                if (
                    cross_section_strategy == "along_precipitation_axis"
                    and canonical_angle_diff > 30.0
                ):
                    raise ValueError(
                        "vertical_cross_section with "
                        "cross_section_strategy=along_precipitation_axis must be broadly "
                        "aligned with reference_band_axis"
                    )
                if abs(canonical_angle_diff - original_angle_diff) > 1.0e-6:
                    logging.warning(
                        "step04 candidate plan cross-section orientation canonicalized "
                        "candidate_id=%s check_key=%s strategy=%s "
                        "original_angle_diff=%.2f canonical_angle_diff=%.2f",
                        candidate.get("candidate_id"),
                        check.get("check_key"),
                        cross_section_strategy,
                        original_angle_diff,
                        canonical_angle_diff,
                    )
                original_length_km = _line_length_km(
                    _normalized_cross_section_line(raw_cross_section_line)
                    or cross_section_line
                )
                canonical_length_km = _line_length_km(cross_section_line)
                if canonical_length_km + 1.0 < original_length_km:
                    logging.warning(
                        "step04 candidate plan cross-section length bounded "
                        "candidate_id=%s check_key=%s strategy=%s "
                        "original_length_km=%.1f canonical_length_km=%.1f",
                        candidate.get("candidate_id"),
                        check.get("check_key"),
                        cross_section_strategy,
                        original_length_km,
                        canonical_length_km,
                    )
                if any(
                    not _point_in_bbox(lon, lat, selected_bbox)
                    for lon, lat in (
                        (cross_section_line["start_longitude"], cross_section_line["start_latitude"]),
                        (cross_section_line["end_longitude"], cross_section_line["end_latitude"]),
                    )
                ):
                    raise ValueError(
                        "canonicalized vertical_cross_section endpoints must remain inside "
                        "the selected domain_scope bbox"
                    )
                if not _segment_intersects_bbox(
                    cross_section_line["start_longitude"],
                    cross_section_line["start_latitude"],
                    cross_section_line["end_longitude"],
                    cross_section_line["end_latitude"],
                    candidate_bbox,
                ):
                    raise ValueError(
                        "canonicalized vertical_cross_section line must intersect the "
                        "candidate_area"
                    )
                check["cross_section_line"] = cross_section_line
                check["cross_section_strategy"] = cross_section_strategy
                check["reference_band_axis"] = reference_band_axis
            else:
                if raw_cross_section_line is not None:
                    raise ValueError(
                        "cross_section_line must be null unless diagnostic_id=vertical_cross_section"
                    )
                if check.get("cross_section_strategy") is not None:
                    raise ValueError("cross_section_strategy must be null unless diagnostic_id=vertical_cross_section")
                if check.get("reference_band_axis") is not None:
                    raise ValueError("reference_band_axis must be null unless diagnostic_id=vertical_cross_section")
            original_methods = list(dict.fromkeys(
                str(item) for item in check.get("analysis_methods", [])
            ))
            normalized_methods = _canonical_analysis_methods_for_diagnostic(
                diagnostic_id,
                original_methods,
            )
            check["analysis_methods"] = normalized_methods
            if normalized_methods != original_methods:
                logging.info(
                    "step04 candidate plan analysis methods canonicalized "
                    "candidate_id=%s check_key=%s diagnostic_id=%s original=%s canonical=%s",
                    candidate.get("candidate_id"),
                    check.get("check_key"),
                    diagnostic_id,
                    original_methods,
                    normalized_methods,
                )
            methods = set(normalized_methods)
            if not methods:
                raise ValueError("compact diagnostic check requires analysis_methods")
            if not methods <= (SPATIAL_ANALYSIS_METHODS | REGIONAL_ANALYSIS_METHODS):
                raise ValueError(
                    f"compact diagnostic check has unsupported analysis_methods={sorted(methods)}"
                )
            _validate_check_scope_contract(
                check_key=str(check.get("check_key", "")),
                diagnostic_id=diagnostic_id,
                analysis_methods=normalized_methods,
                check_scope=str(check.get("check_scope") or ""),
                required_signal_components=check.get("required_signal_components", []),
            )
            if support_catalog is not None:
                _validate_compact_check_execution_contract(
                    check_key=str(check.get("check_key", "")),
                    diagnostic_id=diagnostic_id,
                    required_signal_components=check.get("required_signal_components", []),
                    support_catalog=support_catalog,
                )
            _validate_same_check_narrative_contract(check)
            references = {str(item) for item in check.get("phase_keys", [])}
            referenced_hours = {
                hour for phase_key in references for hour in phase_hours_by_key.get(phase_key, set())
            }
            representative_hours_raw = check.get("representative_spatial_hours", [])
            if not isinstance(representative_hours_raw, list) or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in representative_hours_raw
            ):
                raise ValueError(
                    f"check_key={check.get('check_key')!r} representative_spatial_hours must be a non-negative integer array"
                )
            representative_hours = list(dict.fromkeys(int(value) for value in representative_hours_raw))
            check["representative_spatial_hours"] = representative_hours
            verification_mode = str(
                check.get("verification_mode")
                or _verification_mode_from_diagnostic_check(check)
            )
            spatial_support_error = _spatial_analysis_support_error(
                candidate_id=str(candidate.get("candidate_id", "")),
                check_id=str(check.get("check_key", "")),
                mode=verification_mode,
                scope=str(check.get("domain_scope") or ""),
                candidate_bbox=candidate_bbox,
                context_bbox=context_bbox,
            )
            if spatial_support_error:
                raise ValueError(spatial_support_error)
            if verification_mode == "temporal_change":
                if not representative_hours:
                    raise ValueError(
                        f"check_key={check.get('check_key')!r} temporal_change requires at least one AI-selected representative_spatial_hour"
                    )
                invalid_representative_hours = sorted(set(representative_hours) - referenced_hours)
                if invalid_representative_hours:
                    raise ValueError(
                        f"check_key={check.get('check_key')!r} representative_spatial_hours must belong to referenced phase hours; "
                        f"invalid={invalid_representative_hours} referenced={sorted(referenced_hours)}"
                    )
            elif representative_hours:
                raise ValueError(
                    f"check_key={check.get('check_key')!r} representative_spatial_hours is only allowed for temporal_change"
                )
            minimum_hours = _minimum_forecast_hours_for_check(
                diagnostic_id,
                str(check.get("check_scope") or ""),
            )
            if len(referenced_hours) < minimum_hours:
                raise ValueError(
                    "compact diagnostic check has insufficient forecast-hour coverage "
                    f"candidate_id={candidate.get('candidate_id')!r} "
                    f"chunk={expected_chunk_index} "
                    f"check_key={check.get('check_key')!r} "
                    f"verification_mode={verification_mode!r} "
                    f"check_scope={check.get('check_scope')!r} "
                    f"diagnostic_id={diagnostic_id!r} "
                    f"phase_keys={sorted(references)!r} "
                    f"referenced_hours={sorted(referenced_hours)!r} "
                    f"allowed_hours={available_hours!r} "
                    f"required={minimum_hours} actual={len(referenced_hours)}"
                )
            mentioned_hours = set()
            for field in ("purpose", "expected_signal", "decision_relevance"):
                mentioned_hours.update(
                    _mentioned_forecast_hours(str(check.get(field, "")), available_hours)
                )
            missing_hours = sorted(mentioned_hours - referenced_hours)
            if missing_hours:
                _log_step04_advisories(
                    "candidate_plan.text_phase_alignment",
                    [
                        "diagnostic check text mentions forecast hours not covered by phase_keys "
                        f"check_key={check.get('check_key')!r} missing={missing_hours}"
                    ],
                )
            if support_catalog is not None:
                _validate_compact_quantitative_atomic_claims(
                    check,
                    support_catalog=support_catalog,
                    candidate=candidate,
                    selectable_operational_regions=selectable_operational_regions,
                    referenced_hours=referenced_hours,
                    phase_hours_by_key=phase_hours_by_key,
                )

        all_alignment_checks = [
            check
            for previous in previous_chunks
            for check in _compact_plan_rows(previous, "diagnostic_checks")
        ] + list(checks)
        complementary_quantitative_errors = _validate_complementary_quantitative_checks(
            all_alignment_checks
        )
        if complementary_quantitative_errors:
            raise ValueError(" | ".join(complementary_quantitative_errors))

        positive_alignment_errors = _validate_positive_alignment_contracts(
            all_alignment_checks,
            phase_hours_by_key=phase_hours_by_key,
        )
        if positive_alignment_errors:
            raise ValueError(" | ".join(positive_alignment_errors))

        _log_step04_advisories(
            "candidate_plan.signal_coverage",
            _enforce_component_coverage_for_signal_text(checks),
        )

        _canonicalize_redundant_temporal_evolution_checks(
            checks,
            hypotheses,
            phase_hours_by_key=phase_hours_by_key,
            candidate_id=str(candidate.get("candidate_id", "")),
        )
        _canonicalize_disjoint_hypothesis_check_references(
            hypotheses,
            candidate_id=str(candidate.get("candidate_id", "")),
        )
        previous_supported_hypothesis_count = sum(
            1
            for previous in previous_chunks
            for previous_hypothesis in _compact_plan_rows(previous, "hypotheses")
            if previous_hypothesis.get("supporting_check_keys")
        )
        hypotheses = _drop_hypotheses_without_supporting_checks(
            hypotheses,
            candidate_id=str(candidate.get("candidate_id", "")),
            field_suffix="keys",
            require_one=not bool(previous_supported_hypothesis_count),
        )
        value["diagnostic_checks"] = checks
        value["hypotheses"] = hypotheses


        check_keys = _compact_local_keys(
            checks, "check_key", label="diagnostic_checks"
        )

        for hypothesis in hypotheses:
            referenced = {
                str(item)
                for field in ("supporting_check_keys", "opposing_check_keys")
                for item in hypothesis.get(field, [])
            }
            unknown = sorted(referenced - (previous_check_keys | check_keys))
            if unknown:
                raise ValueError(
                    "candidate_id="
                    f"{candidate.get('candidate_id')} chunk={expected_chunk_index} "
                    "object=hypothesis reason=unresolved check reference after normalization "
                    f"check_keys={unknown}"
                )

        all_phase_hours = {hour for row in all_phase_rows for hour in _compact_phase_hours(row)}
        for question in value.get("forecaster_questions", []):
            mentioned = _mentioned_forecast_hours(str(question), available_hours)
            if not mentioned.issubset(all_phase_hours):
                _log_step04_advisories(
                    "candidate_plan.forecaster_question_alignment",
                    [
                        "forecaster question mentions forecast hours not represented by analysis_phases "
                        f"missing={sorted(mentioned - all_phase_hours)}"
                    ],
                )

        if not previous_chunks:
            if len(phases) < 1:
                raise ValueError("first compact candidate plan chunk requires analysis_phases")
            if len(checks) < 3:
                raise ValueError("first compact candidate plan chunk requires at least 3 diagnostic_checks")
            if len(value.get("forecaster_questions", [])) < 1:
                raise ValueError("first compact candidate plan chunk requires a forecaster question")
            peak_phase_hours = {
                hour
                for phase in phases
                if str(phase.get("phase_type")) == "peak"
                for hour in _compact_phase_hours(phase)
            }
            candidate_peak_hours = {
                int(value) for value in candidate.get("peak_forecast_hours", [])
            }
            if not candidate_peak_hours.issubset(peak_phase_hours):
                raise ValueError(
                    "first compact candidate plan peak phases must cover all candidate peak_forecast_hours"
                )

            # Do not force a Python-defined split/merge of AI-declared peaks.
            # The AI may use one continuous peak analysis phase spanning multiple
            # declared peaks, or separate peak phases, as long as every declared
            # peak hour is represented.  The number of forecaster questions is also
            # an AI planning choice within the schema limits.

        # A model-declared final chunk cannot reserve a phase for an absent future
        # continuation. Surface this as a chunk-level contract error before
        # expansion assigns canonical phase IDs, so the complete chunk can be
        # regenerated from the original candidate context.
        if bool(value.get("is_complete")):
            unreferenced_phase_keys = _unreferenced_compact_phase_keys(
                [*previous_chunks, value]
            )
            if unreferenced_phase_keys:
                raise ValueError(
                    "COMPACT_PLAN_UNREFERENCED_PHASE "
                    f"phase_keys={unreferenced_phase_keys}"
                )

        if attachment_manifest is not None:
            available_phase_types = set(_compact_candidate_phase_hours(candidate, attachment_manifest))
            unsupported_phase_types = sorted(
                {
                    str(item.get("phase_type"))
                    for item in phases
                    if str(item.get("phase_type")) not in available_phase_types
                }
            )
            if unsupported_phase_types:
                raise ValueError(
                    "compact candidate plan uses unavailable phase_type(s)="
                    f"{unsupported_phase_types}; allowed={sorted(available_phase_types)}"
                )

        # Continuation exists only to close required coverage gaps.  Reject a
        # semantically valid-looking continuation before it is accepted when it
        # fails to reduce even one previously missing required role.  This sends
        # the whole chunk through the bounded candidate-chunk regeneration path
        # instead of consuming outer continuation attempts with the same gap.
        if previous_chunks:
            before_coverage = evaluate_compact_candidate_plan_coverage(
                previous_chunks,
                candidate=candidate,
            )
            before_missing = sorted(
                role
                for role, status in before_coverage.items()
                if status == "missing_but_relevant"
            )
            if before_missing:
                # Measure progress against the same post-validation continuation
                # normalization used by create_compact_candidate_plan. Exact
                # restatements must not appear to make progress merely because a
                # duplicate Check carries a newly declared non-structural role.
                trial_value = _normalize_compact_continuation_chunk(
                    copy.deepcopy(value),
                    previous_chunks,
                )
                trial_chunks = [*previous_chunks, trial_value]
                after_coverage = evaluate_compact_candidate_plan_coverage(
                    trial_chunks,
                    candidate=candidate,
                )
                after_missing = sorted(
                    role
                    for role, status in after_coverage.items()
                    if status == "missing_but_relevant"
                )
                if not (set(before_missing) - set(after_missing)):
                    raise ValueError(
                        "COMPACT_PLAN_CONTINUATION_NO_PROGRESS "
                        f"candidate_id={candidate.get('candidate_id')} "
                        f"remaining_roles={before_missing} "
                        f"after_remaining_roles={after_missing}; "
                        "repair=regenerate the continuation chunk so at least one previously missing "
                        "coverage role is satisfied by an executable verification structure"
                    )
    except (TypeError, ValueError) as exc:
        errors.append(str(exc))
    return errors

def _candidate_relevant_coverage_roles(candidate: dict[str, Any]) -> set[str]:
    relevant = {"hazard_magnitude", "temporal_evolution", "spatial_evolution", "supporting_evidence"}
    status = str(candidate.get("precipitation_signal_status", ""))
    if status in {"present", "weak", "uncertain"}:
        relevant.add("hazard_magnitude")
    category_map = {
        "moisture_supply": "moisture_supply",
        "low_level_forcing": "low_level_forcing",
        "mid_upper_forcing": "mid_level_forcing",
        "thermodynamic_support": "instability_or_stability",
        "organization_persistence": "forecast_uncertainty",
    }
    relevant.update(
        category_map[str(item)]
        for item in candidate.get("precursor_signal_categories", [])
        if str(item) in category_map
    )
    if candidate.get("opposing_reasons"):
        relevant.add("opposing_evidence")
    if candidate.get("uncertainties") or str(candidate.get("confidence")) in {"low", "medium"}:
        relevant.add("forecast_uncertainty")
    return relevant


def evaluate_compact_candidate_plan_coverage(
    chunks: list[dict[str, Any]],
    *,
    candidate: dict[str, Any],
) -> dict[str, str]:
    """Evaluate coverage from normalized diagnostic checks, not model completion claims."""
    covered: set[str] = set()
    for chunk in chunks:
        for check in _compact_plan_rows(chunk, "diagnostic_checks"):
            covered.update(_effective_compact_check_coverage_roles(check))

    # Every candidate receives a mandatory system-owned precipitation-presence
    # Check after the AI plan is accepted. Compact-plan completion must not depend
    # on the LLM reproducing that system invariant, including precursor candidates.
    if str(candidate.get("detection_basis")) in {
        "direct_precipitation",
        "precursor_environment",
        "mixed",
    }:
        covered.add("hazard_magnitude")

    required_roles = _candidate_relevant_coverage_roles(candidate)
    result: dict[str, str] = {}
    for role in COMPACT_COVERAGE_ROLES:
        if role in covered:
            result[role] = "covered"
        elif role in required_roles:
            result[role] = "missing_but_relevant"
        else:
            result[role] = "unknown"
    return result


def normalize_compact_plan_completion_state(
    chunks: list[dict[str, Any]],
    *,
    candidate: dict[str, Any],
) -> tuple[dict[str, str], list[str], bool, list[str]]:
    """Make completion fields reflect merged diagnostic-check coverage.

    ``is_complete`` and ``remaining_roles`` are model-provided transport fields.
    The canonical values are derived only after duplicate continuation checks have
    been normalized and the merged diagnostic checks are available.
    """
    if not chunks:
        raise ValueError("compact candidate plan completion requires at least one chunk")
    latest = chunks[-1]
    raw_remaining = latest.get("remaining_roles", [])
    model_remaining_roles = (
        [str(role) for role in raw_remaining] if isinstance(raw_remaining, list) else []
    )
    model_is_complete = bool(latest.get("is_complete"))
    coverage = evaluate_compact_candidate_plan_coverage(chunks, candidate=candidate)
    actual_remaining_roles = sorted(
        role for role, status in coverage.items() if status == "missing_but_relevant"
    )
    latest["is_complete"] = not actual_remaining_roles
    latest["remaining_roles"] = actual_remaining_roles
    return coverage, actual_remaining_roles, model_is_complete, model_remaining_roles


def compact_candidate_plan_made_progress(
    previous_chunks: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    *,
    candidate: dict[str, Any],
) -> bool:
    """Return whether a continuation reduced required coverage gaps.

    Continuation exists to complete candidate-plan coverage. Adding a novel
    phase or Check that does not cover any previously missing required role is
    not progress toward completion and therefore consumes the no-progress
    safety budget. This keeps the loop bounded even after removing the old
    total-continuation cap.
    """
    if len(chunks) <= len(previous_chunks):
        return False
    before_coverage = evaluate_compact_candidate_plan_coverage(
        previous_chunks,
        candidate=candidate,
    )
    after_coverage = evaluate_compact_candidate_plan_coverage(chunks, candidate=candidate)
    before_missing = {
        role for role, status in before_coverage.items() if status == "missing_but_relevant"
    }
    after_missing = {
        role for role, status in after_coverage.items() if status == "missing_but_relevant"
    }
    return bool(before_missing - after_missing)


def _candidate_temporal_feasibility_contract(
    candidate: dict[str, Any],
    attachment_manifest: dict[str, Any],
    *,
    support_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expand a one-hour verification window by one *observed* adjacent cadence.

    Use only an immutable candidate's explicitly related overview assets.  A
    before/after comparison must be visible in at least one of those assets,
    including the original peak hour.  This is a planning allowance, not a
    change to the discovered event window, precipitation anchor, or source data.
    """
    window = candidate.get("time_window") or {}
    start = int(window.get("forecast_hour_start", 0))
    end = int(window.get("forecast_hour_end", 0))
    envelope = candidate.get("analysis_envelope_request") or {}
    padding = max(0, int(envelope.get("temporal_padding_hours", 0)))
    related_ids = {str(value) for value in candidate.get("related_overview_asset_ids", [])}
    all_assets = [
        asset for asset in attachment_manifest.get("attachments", [])
        if isinstance(asset, dict)
    ]
    manifest_hours = {
        int(hour) for asset in all_assets for hour in asset.get("forecast_hours", [])
    }
    original = sorted(hour for hour in manifest_hours if start - padding <= hour <= end + padding)
    peaks = {int(hour) for hour in candidate.get("peak_forecast_hours", [])}
    # The preexisting calculation includes explicitly declared peak hours.  Do
    # not fabricate a missing peak to justify extending to another hour.
    requested = sorted(set(original) | peaks)
    extended = list(requested)
    source_asset_ids: list[str] = []
    if len(requested) < 2 and requested and related_ids:
        base_hour = requested[0]
        options: list[tuple[int, int, str]] = []
        for asset in all_assets:
            asset_id = str(asset.get("asset_id") or "")
            if asset_id not in related_ids:
                continue
            asset_hours = sorted({int(hour) for hour in asset.get("forecast_hours", [])})
            if base_hour not in asset_hours:
                continue
            # Do not extend with an overview whose product cannot be requested
            # at both times in Step05.  Missing catalog schedules use the
            # existing legacy unconstrained-product policy.
            product_hours = (
                _allowed_forecast_hours_for_product(
                    str(asset.get("product_id") or ""), support_catalog
                )
                if support_catalog is not None else None
            )
            cadence = int(asset.get("cadence_hours") or 0)
            if cadence <= 0:
                continue
            for hour in asset_hours:
                delta = abs(hour - base_hour)
                if delta == cadence and (
                    product_hours is None
                    or {base_hour, hour}.issubset(product_hours)
                ):
                    # Prefer the preceding time to preserve a pre-peak comparison.
                    options.append((delta, hour, asset_id))
        if options:
            _, neighbor, source_id = min(options, key=lambda row: (row[0], row[1], row[2]))
            extended = sorted({base_hour, neighbor})
            source_asset_ids = [source_id]
    return {
        "requested_hours": requested,
        "effective_hours": extended,
        "added_hours": sorted(set(extended) - set(requested)),
        "source_asset_ids": source_asset_ids,
        "minimum_temporal_hours": 2,
        "temporal_comparison_available": len(extended) >= 2,
    }


def _available_candidate_forecast_hours(
    candidate: dict[str, Any],
    attachment_manifest: dict[str, Any],
) -> list[int]:
    return list(_candidate_temporal_feasibility_contract(candidate, attachment_manifest)["effective_hours"])


def _compact_candidate_phase_hours(
    candidate: dict[str, Any],
    attachment_manifest: dict[str, Any],
) -> dict[str, list[int]]:
    """Return only phase types that have concrete forecast-hour coverage."""
    available_hours = _available_candidate_forecast_hours(candidate, attachment_manifest)
    peak_hours = sorted({int(item) for item in candidate.get("peak_forecast_hours", [])})
    if not available_hours or not peak_hours:
        return {}
    phase_hours = {
        "peak": peak_hours,
        "pre_peak": [hour for hour in available_hours if hour < min(peak_hours)],
        "post_peak": [hour for hour in available_hours if hour > max(peak_hours)],
        "context": available_hours,
    }
    return {
        phase_type: hours
        for phase_type, hours in phase_hours.items()
        if hours
    }


def _preferred_product_id_for_check(
    diagnostic_id: str,
    role: str,
    required_signal_components: list[str] | tuple[str, ...] | set[str],
    support_catalog: dict[str, Any],
    candidate: dict[str, Any],
) -> str:
    """Select only a catalog-compatible execution product for one check."""
    products = [
        str(item.get("product_id"))
        for item in support_catalog.get("spatial_field_products", [])
        if isinstance(item, dict) and item.get("product_id")
    ]
    execution_support = support_catalog.get("diagnostic_execution_support", {})
    contract = (
        execution_support.get(diagnostic_id, {})
        if isinstance(execution_support, dict)
        else {}
    )
    if not isinstance(contract, dict) or diagnostic_id not in execution_support:
        raise ValueError(
            "compact candidate plan diagnostic has no executable catalog contract "
            f"diagnostic_id={diagnostic_id!r}"
        )
    compatible = [
        str(value)
        for value in contract.get("compatible_product_ids", [])
        if str(value) in products
    ]
    required = {str(value) for value in required_signal_components if str(value)}
    component_compatible = [
        product_id
        for product_id in compatible
        if not _product_signal_components_from_support(product_id, support_catalog)
        or required.issubset(_product_signal_components_from_support(product_id, support_catalog))
    ]
    if component_compatible:
        compatible = component_compatible
    elif compatible:
        raise ValueError(
            "compact candidate plan diagnostic has no compatible product providing required signal components "
            f"diagnostic_id={diagnostic_id!r} required_signal_components={sorted(required)} "
            f"compatible_product_ids={compatible}"
        )
    if not compatible:
        raise ValueError(
            "compact candidate plan diagnostic has no available compatible product "
            f"diagnostic_id={diagnostic_id!r}"
        )
    preferred = str(contract.get("preferred_product_id") or "")
    if preferred and preferred in compatible:
        return preferred

    candidate_products = [str(item) for item in candidate.get("related_product_ids", [])]
    ordered = [
        item
        for item in dict.fromkeys([*candidate_products, *products])
        if item in compatible
    ]
    patterns = {
        "hazard_magnitude": ("PRECIP",),
        "moisture_supply": ("TCW", "WIND850", "MOISTURE"),
        "low_level_forcing": ("CONVERGENCE", "WIND925"),
        "midlevel_forcing": ("OMEGA",),
        "synoptic_support": ("MSLP", "JET", "MULTIVARIABLE"),
        "uncertainty": ("MULTIVARIABLE", "PRECIP", "TCW", "OMEGA", "CONVERGENCE", "MSLP"),
    }.get(role, ())
    for pattern in patterns:
        selected = next((item for item in ordered if pattern in item), None)
        if selected is not None:
            return selected
    if len(compatible) == 1:
        return compatible[0]
    raise ValueError(
        "compact candidate plan cannot resolve an unambiguous compatible product "
        f"diagnostic_id={diagnostic_id!r} variable_role={role!r} "
        f"compatible_product_ids={compatible}"
    )


def _allowed_forecast_hours_for_product(
    product_id: str,
    support_catalog: dict[str, Any],
) -> set[int] | None:
    """Return a product schedule when the catalog declares one.

    A missing schedule is intentionally treated as unconstrained for legacy
    catalog fixtures. Production spatial-field products declare the schedule.
    """
    for item in support_catalog.get("spatial_field_products", []):
        if not isinstance(item, dict) or str(item.get("product_id")) != product_id:
            continue
        allowed_hours = item.get("allowed_forecast_hours")
        if not isinstance(allowed_hours, list):
            return None
        return {int(value) for value in allowed_hours}
    return None


def _deduplicate_compact_phases(
    phase_rows: list[dict[str, Any]],
    check_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge only exact continuation phase restatements.

    Distinct forecast-hour windows remain separate even when they share the same
    phase type.
    """
    retained: list[dict[str, Any]] = []
    canonical_by_signature: dict[tuple[str, tuple[int, ...]], str] = {}
    aliases: dict[str, str] = {}
    for row in phase_rows:
        local_key = str(row.get("phase_key", "")).strip()
        if not local_key:
            retained.append(row)
            continue
        signature = _compact_phase_signature(row)
        canonical_key = canonical_by_signature.get(signature)
        if canonical_key is None:
            canonical_by_signature[signature] = local_key
            aliases[local_key] = local_key
            retained.append(row)
        else:
            aliases[local_key] = canonical_key
    for row in check_rows:
        row["phase_keys"] = list(
            dict.fromkeys(
                aliases.get(str(key), str(key))
                for key in row.get("phase_keys", [])
            )
        )
        claims = row.get("atomic_claims")
        if isinstance(claims, list):
            for claim in claims:
                if not isinstance(claim, dict):
                    continue
                claim["phase_keys"] = list(
                    dict.fromkeys(
                        aliases.get(str(key), str(key))
                        for key in claim.get("phase_keys", [])
                    )
                )
    return retained


def _stable_unique_nonempty_strings(values: Any) -> list[str]:
    """Return exact string identifiers once, preserving their first occurrence.

    This helper is intentionally conservative: it does not fuzzy-match, rename,
    or drop unknown identifiers.  It only removes representational duplicates.
    """
    if not isinstance(values, list):
        return values
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _atomic_claim_normalization_signature(claim: dict[str, Any]) -> str:
    """Fingerprint an atomic claim excluding its transport-only claim_key.

    Two claims are collapsed only when every semantic field is identical after
    deterministic normalization.  Similar or paraphrased claims remain distinct
    and are left for strict atomicity validation.
    """
    return canonical_hash({
        "claim_statement": str(claim.get("claim_statement") or "").strip(),
        "claim_components": list(claim.get("claim_components", [])),
        "phase_keys": list(claim.get("phase_keys", [])),
        "expected_state": str(claim.get("expected_state") or "").strip(),
        "scope_quantifier": str(claim.get("scope_quantifier") or "").strip(),
        "scope_description": str(claim.get("scope_description") or "").strip(),
        "quantitative_scope": copy.deepcopy(claim.get("quantitative_scope")),
    })


def _normalize_candidate_plan_representation_fields(value: dict[str, Any]) -> list[str]:
    """Normalize representation-only Step04 plan fields in either transport form.

    This helper is deliberately usable *before* JSON Schema validation and again
    after transport-to-diagnostic conversion.  It therefore only touches fields
    whose duplicate/order representation cannot change meteorological intent.
    It never invents identifiers, forecast hours, phases, regions, or comparator
    direction.
    """
    changes: list[str] = []

    phase_rows = value.get("analysis_phases")
    if isinstance(phase_rows, list):
        for phase in phase_rows:
            if not isinstance(phase, dict):
                continue
            raw_hours = phase.get("forecast_hours")
            if (
                isinstance(raw_hours, list)
                and raw_hours
                and all(
                    not isinstance(hour, bool) and isinstance(hour, int) and hour >= 0
                    for hour in raw_hours
                )
            ):
                canonical_hours = sorted(set(int(hour) for hour in raw_hours))
                if canonical_hours != raw_hours:
                    phase["forecast_hours"] = canonical_hours
                    changes.append(f"phase={phase.get('phase_key')!r}:forecast_hours")

    collection_name = None
    rows: Any = None
    if isinstance(value.get("verification_requests"), list):
        collection_name = "verification_requests"
        rows = value.get("verification_requests")
    elif isinstance(value.get("diagnostic_checks"), list):
        collection_name = "diagnostic_checks"
        rows = value.get("diagnostic_checks")

    if isinstance(rows, list):
        for check in rows:
            if not isinstance(check, dict):
                continue
            check_key = str(check.get("check_key") or "")
            set_like_fields = [
                "phase_keys",
                "analysis_methods",
                "coverage_roles",
            ]
            if collection_name == "verification_requests":
                set_like_fields.append("signal_components")
            else:
                set_like_fields.append("required_signal_components")
            for field in set_like_fields:
                original = check.get(field)
                canonical = _stable_unique_nonempty_strings(original)
                if isinstance(original, list) and canonical != original:
                    check[field] = canonical
                    changes.append(f"check={check_key!r}:{field}")

            representative = check.get("representative_spatial_hours")
            if (
                isinstance(representative, list)
                and all(
                    not isinstance(hour, bool) and isinstance(hour, int) and hour >= 0
                    for hour in representative
                )
            ):
                canonical_representative = sorted(set(int(hour) for hour in representative))
                if canonical_representative != representative:
                    check["representative_spatial_hours"] = canonical_representative
                    changes.append(f"check={check_key!r}:representative_spatial_hours")

            claims = check.get("atomic_claims")
            if not isinstance(claims, list):
                continue
            normalized_claims: list[Any] = []
            for claim in claims:
                if not isinstance(claim, dict):
                    normalized_claims.append(claim)
                    continue
                for field in ("claim_components", "phase_keys"):
                    original = claim.get(field)
                    canonical = _stable_unique_nonempty_strings(original)
                    if isinstance(original, list) and canonical != original:
                        claim[field] = canonical
                        changes.append(
                            f"check={check_key!r}:claim={claim.get('claim_key')!r}:{field}"
                        )

                scope = claim.get("quantitative_scope")
                if isinstance(scope, dict):
                    region_ids = scope.get("region_ids")
                    canonical_regions = _stable_unique_nonempty_strings(region_ids)
                    if isinstance(region_ids, list) and canonical_regions != region_ids:
                        scope["region_ids"] = canonical_regions
                        changes.append(
                            f"check={check_key!r}:claim={claim.get('claim_key')!r}:region_ids"
                        )

                    comparison = scope.get("comparison_forecast_hours")
                    if (
                        isinstance(comparison, list)
                        and all(
                            not isinstance(hour, bool)
                            and isinstance(hour, int)
                            and hour >= 0
                            for hour in comparison
                        )
                    ):
                        canonical_comparison = list(dict.fromkeys(int(hour) for hour in comparison))
                        if len(canonical_comparison) == 2 and canonical_comparison != comparison:
                            scope["comparison_forecast_hours"] = canonical_comparison
                            changes.append(
                                f"check={check_key!r}:claim={claim.get('claim_key')!r}:comparison_forecast_hours"
                            )
                normalized_claims.append(claim)

            check["atomic_claims"] = normalized_claims
            _canonicalize_compact_quantitative_atomic_claims(check)

            seen_claims: set[str] = set()
            retained_claims: list[Any] = []
            for claim in check.get("atomic_claims", []):
                if not isinstance(claim, dict):
                    retained_claims.append(claim)
                    continue
                signature = _atomic_claim_normalization_signature(claim)
                if signature in seen_claims:
                    changes.append(
                        f"check={check_key!r}:duplicate_atomic_claim={claim.get('claim_key')!r}"
                    )
                    continue
                seen_claims.add(signature)
                retained_claims.append(claim)
            check["atomic_claims"] = retained_claims

    hypotheses = value.get("hypotheses")
    if isinstance(hypotheses, list):
        for hypothesis in hypotheses:
            if not isinstance(hypothesis, dict):
                continue
            for field in ("supporting_check_keys", "opposing_check_keys"):
                original = hypothesis.get(field)
                canonical = _stable_unique_nonempty_strings(original)
                if isinstance(original, list) and canonical != original:
                    hypothesis[field] = canonical
                    changes.append(
                        f"hypothesis={hypothesis.get('hypothesis_key')!r}:{field}"
                    )

    questions = value.get("forecaster_questions")
    if isinstance(questions, list):
        retained_questions: list[Any] = []
        seen_questions: set[str] = set()
        for question in questions:
            if not isinstance(question, str):
                retained_questions.append(question)
                continue
            stripped = question.strip()
            signature = re.sub(r"\s+", " ", stripped).casefold()
            if signature in seen_questions:
                changes.append("forecaster_questions:exact_duplicate")
                continue
            seen_questions.add(signature)
            retained_questions.append(stripped)
        value["forecaster_questions"] = retained_questions

    return changes


def _normalize_compact_candidate_plan_contracts(
    value: dict[str, Any],
    *,
    all_phase_rows: Iterable[dict[str, Any]],
    available_hours: Iterable[int],
    candidate_id: str,
) -> dict[str, Any]:
    """Central deterministic normalization layer before strict Step04 validation.

    The LLM owns meteorological intent. Python owns only representation-level
    normalization that cannot change that intent. The same representation helper
    is used before provider-schema validation and here after Check repair merges,
    so recurring structural errors have one implementation path.

    In addition to representation normalization, this stage may connect an
    explicit/quantitative forecast hour to an already-declared *unique* phase.
    It never invents forecast hours/phases, changes region identity, changes
    comparator direction, or converts one meteorological hypothesis into another.
    Ambiguous cases remain visible to strict validation and bounded regeneration.
    """
    phase_rows = list(all_phase_rows)
    allowed_hours = {int(value) for value in available_hours}
    changes = _normalize_candidate_plan_representation_fields(value)
    unresolved_text_rows: list[str] = []
    unresolved_quant_rows: list[str] = []

    for check in _compact_plan_rows(value, "diagnostic_checks"):
        check_key = str(check.get("check_key") or "")
        unresolved_text = _canonicalize_check_phase_keys_for_explicit_hours(
            check,
            all_phase_rows=phase_rows,
            available_hours=allowed_hours,
            candidate_id=candidate_id,
        )
        if unresolved_text:
            unresolved_text_rows.append(
                f"check_key={check_key!r} missing={unresolved_text}"
            )

        unresolved_quant = _canonicalize_quantitative_scope_phase_keys(
            check,
            all_phase_rows=phase_rows,
            candidate_id=candidate_id,
        )
        if unresolved_quant:
            unresolved_quant_rows.append(
                f"check_key={check_key!r} missing_or_ambiguous={unresolved_quant}"
            )

    if changes:
        _log_step04_advisories(
            "candidate_plan.contract_normalization",
            list(dict.fromkeys(changes)),
        )
        logging.info(
            "step04 candidate-plan deterministic normalization candidate_id=%s count=%s version=%s",
            candidate_id,
            len(changes),
            COMPACT_CANDIDATE_PLAN_NORMALIZATION_VERSION,
        )
    if unresolved_text_rows:
        _log_step04_advisories(
            "candidate_plan.contract_normalization.text_phase_alignment",
            [
                "diagnostic check text mentions forecast hours not represented by a unique analysis phase "
                + row
                for row in unresolved_text_rows
            ],
        )
    if unresolved_quant_rows:
        _log_step04_advisories(
            "candidate_plan.contract_normalization.quantitative_phase_alignment",
            [
                "quantitative_scope forecast hours are not represented by one unique existing analysis phase "
                + row
                for row in unresolved_quant_rows
            ],
        )
    return value



def _quantitative_feature_ids_for_product(
    product_id: str,
    support_catalog: dict[str, Any],
) -> list[str]:
    support = support_catalog.get("quantitative_excerpt_support", {})
    mapping = support.get("feature_ids_by_product", {}) if isinstance(support, dict) else {}
    values = mapping.get(product_id, []) if isinstance(mapping, dict) else []
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def _quantitative_feature_ids_for_check(
    diagnostic_id: str,
    product_id: str,
    support_catalog: dict[str, Any],
) -> list[str]:
    """Prefer the diagnostic/product semantic contract over broad product features.

    A logical overview product may expose several quantitative fields, while one
    Step05 diagnostic is intended to validate only a specific observable. The
    capability contract therefore owns any narrowed feature list.
    """
    profile = _capability_profile_for_check(
        support_catalog,
        diagnostic_id=str(diagnostic_id),
        product_id=str(product_id),
    )
    values = profile.get("quantitative_feature_ids") if isinstance(profile, dict) else None
    if isinstance(values, list) and values:
        return list(dict.fromkeys(str(value) for value in values if str(value)))
    return _quantitative_feature_ids_for_product(product_id, support_catalog)


def _canonicalize_compact_quantitative_atomic_claims(check: dict[str, Any]) -> None:
    """Canonicalize only semantically redundant quantitative transport fields.

    Python may normalize fields that are fully implied by the declared claim and
    therefore cannot change the meteorological meaning. Material model choices
    (feature identity, target region, forecast-hour set, or comparator direction)
    remain visible to the semantic validator.

    Two narrow normalizations are supported:

    1. ``quantitative_scope.forecast_hours`` is a time-set contract, so valid
       non-negative integer hours may be sorted and de-duplicated when at least
       two unique hours remain.  This does not alter comparator direction;
       explicit left/right ordering is carried by ``comparison_forecast_hours``.

    2. ``increase/decrease/strengthen/weaken`` with exactly two chronological
       ``forecast_hours`` may occasionally repeat the same two hours in
       ``comparison_forecast_hours``. The latter is redundant because temporal
       change is defined by chronological ``forecast_hours``; it is normalized
       to ``null`` only when the list is exactly identical. Reversed, partial,
       or otherwise different comparison lists are not changed.

    3. ``majority + not_strengthen`` on 925 hPa convergence has a deterministic
       aggregation contract. Region scope, statistic, hour ordering, and a
       redundant same-hour comparison list are canonicalized as before.
    """
    claims = check.get("atomic_claims")
    if not isinstance(claims, list):
        return
    advisories: list[str] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        scope = claim.get("quantitative_scope")
        if not isinstance(scope, dict):
            continue

        state = str(claim.get("expected_state") or "")
        changed: list[str] = []

        # ``forecast_hours`` is an executable time-set contract: it represents
        # the forecast hours used by the claim and must be unique and
        # chronological.  Ordering/duplication is transport syntax rather than
        # meteorological intent because comparator direction, when relevant, is
        # carried separately by ``comparison_forecast_hours``.  Therefore we may
        # deterministically sort and de-duplicate a valid integer list *only*
        # when at least two unique hours remain.  A list collapsing to fewer
        # than two unique hours is semantically incomplete and is intentionally
        # left for model regeneration/validation.
        hours = scope.get("forecast_hours")
        if (
            isinstance(hours, list)
            and all(
                not isinstance(value, bool) and isinstance(value, int) and value >= 0
                for value in hours
            )
        ):
            canonical_hours = sorted(set(hours))
            if len(canonical_hours) >= 2 and canonical_hours != hours:
                scope["forecast_hours"] = canonical_hours
                hours = canonical_hours
                changed.append("forecast_hours")

        comparison_hours = scope.get("comparison_forecast_hours")
        if (
            state in {"increase", "decrease", "strengthen", "weaken"}
            and isinstance(hours, list)
            and len(hours) == 2
            and all(not isinstance(value, bool) and isinstance(value, int) and value >= 0 for value in hours)
            and hours == sorted(set(hours))
            and isinstance(comparison_hours, list)
            and all(not isinstance(value, bool) and isinstance(value, int) and value >= 0 for value in comparison_hours)
            and comparison_hours == hours
        ):
            scope["comparison_forecast_hours"] = None
            changed.append("comparison_forecast_hours")
            comparison_hours = None

        if (
            claim.get("scope_quantifier") == "majority"
            and state == "not_strengthen"
            and str(scope.get("feature_id") or "") == "convergence925_1e5_s"
        ):
            if (
                isinstance(hours, list)
                and len(hours) == 2
                and all(not isinstance(value, bool) and isinstance(value, int) and value >= 0 for value in hours)
                and len(set(hours)) == 2
            ):
                chronological_hours = sorted(hours)
                if chronological_hours != hours:
                    scope["forecast_hours"] = chronological_hours
                    hours = chronological_hours
                    changed.append("forecast_hours")

            if scope.get("region_scope") != "candidate_overlap":
                scope["region_scope"] = "candidate_overlap"
                changed.append("region_scope")
            if scope.get("region_ids") != []:
                scope["region_ids"] = []
                changed.append("region_ids")
            if scope.get("statistic") != "mean":
                scope["statistic"] = "mean"
                changed.append("statistic")

            comparison_hours = scope.get("comparison_forecast_hours")
            canonical_hours = scope.get("forecast_hours")
            if comparison_hours is not None:
                if (
                    isinstance(comparison_hours, list)
                    and len(comparison_hours) == 2
                    and isinstance(canonical_hours, list)
                    and len(canonical_hours) == 2
                    and set(comparison_hours) == set(canonical_hours)
                ):
                    scope["comparison_forecast_hours"] = None
                    changed.append("comparison_forecast_hours")
                # For this majority contract the comparison field is not part
                # of the semantics; the same two hours in either order are
                # redundant. Different hours remain visible to the validator.

        if changed:
            advisories.append(
                f"check_key={check.get('check_key')!r} claim_key={claim.get('claim_key')!r} "
                f"canonicalized={','.join(dict.fromkeys(changed))}"
            )

    if advisories:
        _log_step04_advisories("candidate_plan.contract_normalization.quantitative_scope", advisories)



def _validate_quantitative_region_identity(
    *,
    check_key: str,
    claim_key: str,
    statement: str,
    region_id: str,
    selectable_operational_regions: list[dict[str, Any]] | None,
) -> None:
    """Reject a specific quantitative claim whose named region contradicts region_id.

    The structured region_id is the executable selector.  If the natural-language
    claim explicitly names an operational region, that name must resolve to the
    same region_id.  Claims using generic wording such as ``해당 권역`` are left
    unchanged because there is no textual identity to validate.
    """
    text = str(statement or "").strip()
    if not text or not region_id:
        return
    mentioned_ids: set[str] = set()
    for item in selectable_operational_regions or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("region_id") or "")
        if not item_id:
            continue
        names = [str(item.get("name_ko") or "").strip()]
        aliases = item.get("aliases")
        if isinstance(aliases, list):
            names.extend(str(value).strip() for value in aliases if isinstance(value, str))
        for name in names:
            if len(name) >= 2 and name in text:
                mentioned_ids.add(item_id)
                break
    if mentioned_ids and mentioned_ids != {region_id}:
        region_names = {
            str(item.get("region_id") or ""): str(item.get("name_ko") or "").strip()
            for item in selectable_operational_regions or []
            if isinstance(item, dict) and str(item.get("region_id") or "")
        }
        selected_name = region_names.get(region_id, "")
        mentioned_regions = [
            {"region_id": item_id, "name_ko": region_names.get(item_id, "")}
            for item_id in sorted(mentioned_ids)
        ]
        raise ValueError(
            "QUANTITATIVE_SCOPE_REGION_IDENTITY_MISMATCH "
            f"check_key={check_key} claim_key={claim_key} "
            f"selected_region_id={region_id!r} selected_region_name_ko={selected_name!r} "
            f"natural_language_regions={mentioned_regions!r}; "
            "repair=regenerate the complete verification request so the structured region_id and every explicit operational-region name refer to one consistent region; "
            "if the structured region_id is retained, use selected_region_name_ko as its official name"
        )


def _validate_quantitative_comparison_identity(
    *,
    check_key: str,
    claim_key: str,
    statement: str,
    comparison_hours: list[int],
) -> None:
    """Reject reversed left/right F-hours in explicitly two-hour Korean comparisons.

    Handles both `F018이 F006보다 크다` and `F006보다 F018에 크다`.
    The latter was not detected by the former before-`보다`-only parser.
    No automatic comparator reversal is permitted.
    """
    expected = inconsistent_pairwise_hour_order(statement, comparison_hours)
    if expected is not None:
        raise ValueError(
            "QUANTITATIVE_SCOPE_COMPARISON_IDENTITY_MISMATCH "
            f"check_key={check_key} claim_key={claim_key} "
            f"claim_hour_order={list(expected)} comparison_forecast_hours={comparison_hours}; "
            "repair=regenerate the verification request with comparison_forecast_hours=[left_hour,right_hour] "
            "matching the two sides of the Korean proposition"
        )


def _validate_compact_quantitative_atomic_claims(
    check: dict[str, Any],
    *,
    support_catalog: dict[str, Any],
    candidate: dict[str, Any],
    selectable_operational_regions: list[dict[str, Any]] | None,
    referenced_hours: set[int],
    phase_hours_by_key: dict[str, set[int]],
) -> None:
    """Validate the Step04 quantitative claim metadata before expansion.

    The transport schema validates field types only.  This semantic preflight
    additionally guarantees that a model-declared quantitative feature is
    executable by the selected diagnostic/product, that specific claims target
    one actually overlapping operational region, and that comparison hours are
    owned by the Check.  Errors are raised during the model-repair stage rather
    than surfacing later as an unrecoverable expansion failure.
    """
    claims = check.get("atomic_claims")
    if not isinstance(claims, list) or not claims:
        return
    claims = check.get("atomic_claims")
    quantitative_claims = [
        claim for claim in claims
        if isinstance(claim, dict) and claim.get("quantitative_scope") is not None
    ]
    # Step07 *requires* an executable quantitative contract for these
    # specific states even when Step04 was formerly shadow-only. Reject during
    # AI plan generation, not after expensive Step05/07 execution. No attempt
    # is made to change the meteorological hypothesis or fabricate a scope.
    specific_states_enforced = {
        "greater_than", "less_than", "increase", "decrease", "strengthen", "weaken",
    }
    # Only one-claim Checks are subject to the existing Step07 specific
    # quantitative executor. Multi-claim spatial/image interpretation remains
    # shadow-validated; it must not be silently reinterpreted as numeric.
    enforced_single = len(claims) == 1 and isinstance(claims[0], dict)
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        state = str(claim.get("expected_state") or "")
        quantifier = str(claim.get("scope_quantifier") or "")
        if enforced_single and quantifier == "specific" and state in specific_states_enforced and not isinstance(claim.get("quantitative_scope"), dict):
            raise ValueError(
                "QUANTITATIVE_SCOPE_CONTRACT_INVALID "
                f"check_key={check.get('check_key')} claim_key={claim.get('claim_key')} "
                f"specific expected_state={state!r} requires an executable quantitative_scope; "
                "repair=regenerate the entire verification request with a supported quantitative feature, "
                "single operational region, chronological hours and statistic. "
                "If the original evidence is only visual, write a genuinely qualitative hypothesis "
                "with the appropriate spatial/area state and quantifier; do not fabricate values or alter the physical meaning."
            )
        if enforced_single and quantifier == "majority" and state == "not_strengthen":
            if any(opposite_majority_not_strengthen_wording(text) for text in (
                claim.get("claim_statement"), check.get("expected_signal")
            )):
                raise ValueError(
                    "QUANTITATIVE_MAJORITY_POLARITY_MISMATCH "
                    f"check_key={check.get('check_key')} claim_key={claim.get('claim_key')}; "
                    "the text claims majority convergence strengthening, but expected_state=not_strengthen. "
                    "repair=regenerate a coherent verification request; do not negate the original hypothesis "
                    "just to satisfy the available quantitative executor"
                )
        if enforced_single and quantifier == "majority" and state == "not_strengthen" and not isinstance(claim.get("quantitative_scope"), dict):
            raise ValueError(
                "QUANTITATIVE_SCOPE_CONTRACT_INVALID "
                f"check_key={check.get('check_key')} claim_key={claim.get('claim_key')} "
                "majority not_strengthen requires a two-hour 925 hPa convergence quantitative_scope; "
                "repair=regenerate the complete verification request"
            )
    if not quantitative_claims:
        return
    if len(claims) != 1 or len(quantitative_claims) != 1:
        raise ValueError(
            "QUANTITATIVE_ATOMICITY_INVALID "
            f"check_key={check.get('check_key')} atomic_claim_count={len(claims)} "
            f"quantitative_claim_count={len(quantitative_claims)}; "
            "repair=a verification_request with an enforced quantitative_scope must contain exactly one atomic claim; split other criteria into separate verification_requests"
        )

    methods = {str(value) for value in check.get("analysis_methods", [])}
    if not (methods & REGIONAL_ANALYSIS_METHODS):
        raise ValueError(
            "QUANTITATIVE_SCOPE_EXECUTION_UNAVAILABLE "
            f"check_key={check.get('check_key')} analysis_methods={sorted(methods)}; "
            "repair=a quantitative_scope requires a verification mode that produces operational-region quantitative evidence "
            "(regional_intensity, temporal_change, or precipitation_amount), otherwise set quantitative_scope=null"
        )

    diagnostic_id = str(check.get("diagnostic_id") or "")
    role = str(check.get("variable_role") or "")
    components = [
        str(value) for value in check.get("required_signal_components", []) if str(value)
    ]
    product_id = _preferred_product_id_for_check(
        diagnostic_id, role, components, support_catalog, candidate
    )
    allowed_features = (
        []
        if diagnostic_id in SPATIAL_ONLY_DIAGNOSTIC_IDS
        else _quantitative_feature_ids_for_check(diagnostic_id, product_id, support_catalog)
    )
    selectable_ids = {
        str(item.get("region_id"))
        for item in selectable_operational_regions or []
        if isinstance(item, dict) and item.get("region_id")
    }
    specific_states = {
        "greater_than", "less_than", "increase", "decrease",
        "strengthen", "weaken", "peak_at",
    }

    for claim in quantitative_claims:
        scope = claim.get("quantitative_scope")
        if not isinstance(scope, dict):
            raise ValueError(
                "QUANTITATIVE_SCOPE_CONTRACT_INVALID quantitative_scope must be object or null"
            )
        claim_key = str(claim.get("claim_key") or "")
        state = str(claim.get("expected_state") or "")
        quantifier = str(claim.get("scope_quantifier") or "")
        feature_id = str(scope.get("feature_id") or "")
        region_scope = str(scope.get("region_scope") or "")
        region_ids = scope.get("region_ids")
        hours = scope.get("forecast_hours")
        comparison_hours = scope.get("comparison_forecast_hours")
        statistic = str(scope.get("statistic") or "")

        if feature_id not in allowed_features:
            raise ValueError(
                "QUANTITATIVE_SCOPE_FEATURE_UNAVAILABLE "
                f"check_key={check.get('check_key')} claim_key={claim_key} "
                f"feature_id={feature_id!r} executable_features={allowed_features}; "
                "repair=use the exact quantitative feature produced by this verification request, "
                "or set quantitative_scope=null when the claim is qualitative"
            )
        if (
            not isinstance(hours, list)
            or len(hours) < 2
            or any(isinstance(hour, bool) or not isinstance(hour, int) or hour < 0 for hour in hours)
            or hours != sorted(set(hours))
        ):
            raise ValueError(
                "QUANTITATIVE_SCOPE_CONTRACT_INVALID "
                f"check_key={check.get('check_key')} claim_key={claim_key} "
                "forecast_hours must be unique chronological forecast hours"
            )
        claim_phase_keys = [
            str(value).strip()
            for value in claim.get("phase_keys", [])
            if str(value).strip()
        ]
        claim_referenced_hours = {
            hour
            for phase_key in claim_phase_keys
            for hour in phase_hours_by_key.get(phase_key, set())
        }
        missing_request_hours = sorted(set(hours) - referenced_hours)
        missing_claim_hours = sorted(set(hours) - claim_referenced_hours)
        if missing_request_hours or missing_claim_hours:
            missing_hours = sorted(set(missing_request_hours) | set(missing_claim_hours))
            covering_phase_keys = {
                hour: sorted(
                    phase_key
                    for phase_key, phase_hours in phase_hours_by_key.items()
                    if hour in phase_hours
                )
                for hour in missing_hours
            }
            raise ValueError(
                "QUANTITATIVE_SCOPE_PHASE_MISMATCH "
                f"check_key={check.get('check_key')} claim_key={claim_key} "
                f"forecast_hours={hours} request_phase_keys={list(check.get('phase_keys', []))} "
                f"claim_phase_keys={claim_phase_keys} referenced_phase_hours={sorted(referenced_hours)} "
                f"claim_referenced_phase_hours={sorted(claim_referenced_hours)} "
                f"missing_forecast_hours={missing_hours} declared_covering_phase_keys={covering_phase_keys} "
                "repair_unit=candidate_plan_chunk; "
                "repair=regenerate the candidate-plan chunk so every quantitative forecast hour is owned by "
                "the verification request and atomic claim phase_keys; do not delete a forecast hour merely to fit the current phase graph"
            )
        if statistic not in {"min", "max", "mean", "p95"}:
            raise ValueError(
                "QUANTITATIVE_SCOPE_CONTRACT_INVALID "
                f"check_key={check.get('check_key')} claim_key={claim_key} unsupported statistic={statistic!r}"
            )

        if quantifier == "specific":
            if state not in specific_states:
                raise ValueError(
                    "QUANTITATIVE_SCOPE_CONTRACT_INVALID "
                    f"check_key={check.get('check_key')} claim_key={claim_key} "
                    f"specific quantitative_scope does not support expected_state={state!r}; "
                    "repair=set quantitative_scope=null for qualitative claims"
                )
            if region_scope != "explicit_regions" or not isinstance(region_ids, list) or len(region_ids) != 1:
                raise ValueError(
                    "QUANTITATIVE_SCOPE_CONTRACT_INVALID "
                    f"check_key={check.get('check_key')} claim_key={claim_key} "
                    "specific quantitative_scope requires region_scope=explicit_regions and exactly one region_id"
                )
            region_id = str(region_ids[0] or "")
            if selectable_ids and region_id not in selectable_ids:
                raise ValueError(
                    "QUANTITATIVE_SCOPE_REGION_UNAVAILABLE "
                    f"check_key={check.get('check_key')} claim_key={claim_key} region_id={region_id!r} "
                    f"selectable_region_ids={sorted(selectable_ids)}; "
                    "repair=select one operational region that overlaps the candidate area"
                )
            _validate_quantitative_region_identity(
                check_key=str(check.get("check_key") or ""),
                claim_key=claim_key,
                statement=str(claim.get("claim_statement") or check.get("expected_signal") or ""),
                region_id=region_id,
                selectable_operational_regions=selectable_operational_regions,
            )
            if state in {"greater_than", "less_than", "increase", "decrease", "strengthen", "weaken"}:
                for proposition in dict.fromkeys((
                    str(claim.get("claim_statement") or ""),
                    str(check.get("expected_signal") or ""),
                )):
                    fixed_cutoff = absolute_threshold_in_hour_comparison(proposition)
                    if fixed_cutoff is not None:
                        raise ValueError(
                            "QUANTITATIVE_ABSOLUTE_THRESHOLD_CONTRACT_MISMATCH "
                            f"check_key={check.get('check_key')} claim_key={claim_key} "
                            f"fixed_cutoff={fixed_cutoff!r} expected_state={state!r}; "
                            "repair=relative time comparison quantitative_scope cannot represent a fixed numeric threshold; "
                            "regenerate the verification request preserving the original threshold hypothesis "
                            "and using an actually supported evidence contract; do not substitute an F-hour comparison"
                        )
            if state in {"greater_than", "less_than"}:
                if (
                    not isinstance(comparison_hours, list)
                    or len(comparison_hours) != 2
                    or any(isinstance(hour, bool) or not isinstance(hour, int) for hour in comparison_hours)
                    or len(set(comparison_hours)) != 2
                    or any(hour not in hours for hour in comparison_hours)
                ):
                    raise ValueError(
                        "QUANTITATIVE_SCOPE_COMPARISON_INVALID "
                        f"check_key={check.get('check_key')} claim_key={claim_key} "
                        "greater_than/less_than requires comparison_forecast_hours=[left_hour,right_hour] "
                        "with both hours contained in chronological forecast_hours"
                    )
                for proposition in dict.fromkeys((
                    str(claim.get("claim_statement") or ""),
                    str(check.get("expected_signal") or ""),
                )):
                    if proposition:
                        _validate_quantitative_comparison_identity(
                            check_key=str(check.get("check_key") or ""),
                            claim_key=claim_key,
                            statement=proposition,
                            comparison_hours=comparison_hours,
                        )
            elif comparison_hours is not None:
                raise ValueError(
                    "QUANTITATIVE_SCOPE_COMPARISON_INVALID "
                    f"check_key={check.get('check_key')} claim_key={claim_key} "
                    f"expected_state={state!r} requires comparison_forecast_hours=null"
                )
            continue

        if quantifier == "majority" and state == "not_strengthen":
            if (
                feature_id != "convergence925_1e5_s"
                or region_scope != "candidate_overlap"
                or region_ids != []
                or len(hours) != 2
                or comparison_hours is not None
                or statistic != "mean"
            ):
                raise ValueError(
                    "QUANTITATIVE_SCOPE_CONTRACT_INVALID "
                    f"check_key={check.get('check_key')} claim_key={claim_key}; "
                    "majority not_strengthen is currently supported only for convergence925_1e5_s, "
                    "region_scope=candidate_overlap, region_ids=[], two chronological hours, mean statistic; "
                    f"actual_feature_id={feature_id!r} actual_region_scope={region_scope!r} "
                    f"actual_region_ids={region_ids!r} actual_forecast_hours={hours!r} "
                    f"actual_comparison_forecast_hours={comparison_hours!r} actual_statistic={statistic!r}"
                )
            continue

        raise ValueError(
            "QUANTITATIVE_SCOPE_CONTRACT_INVALID "
            f"check_key={check.get('check_key')} claim_key={claim_key} "
            f"unsupported quantitative contract quantifier={quantifier!r} expected_state={state!r}; "
            "repair=set quantitative_scope=null unless the claim matches a supported deterministic contract"
        )


def _analysis_request_merge_key(request: dict[str, Any]) -> tuple[Any, ...]:
    requested_domain = request.get("requested_domain")
    domain_key = (
        json.dumps(requested_domain, sort_keys=True, separators=(",", ":"))
        if isinstance(requested_domain, dict)
        else None
    )
    cross_section_line = request.get("cross_section_line")
    line_key = (
        json.dumps(cross_section_line, sort_keys=True, separators=(",", ":"))
        if isinstance(cross_section_line, dict)
        else None
    )
    return (
        str(request.get("candidate_id")),
        str(request.get("data_type")),
        str(request.get("product_id")),
        str(request.get("target_id")),
        domain_key,
        line_key,
    )


def _merge_output_specs(first: Any, second: Any) -> dict[str, Any] | None:
    if not isinstance(first, dict) and not isinstance(second, dict):
        return None
    left = first if isinstance(first, dict) else {}
    right = second if isinstance(second, dict) else {}
    statistics = list(
        dict.fromkeys(
            [str(value) for value in left.get("statistics", [])]
            + [str(value) for value in right.get("statistics", [])]
        )
    )
    return {
        "statistics": statistics,
        "ranking_statistic": str(
            left.get("ranking_statistic") or right.get("ranking_statistic") or "p95"
        ),
        "top_n": max(int(left.get("top_n", 0)), int(right.get("top_n", 0)), 1),
        "include_time_series": bool(
            left.get("include_time_series") or right.get("include_time_series")
        ),
        "include_max_location": bool(
            left.get("include_max_location") or right.get("include_max_location")
        ),
    }


def _request_primary_rank(request: dict[str, Any]) -> tuple[int, int, str]:
    """Prefer a concrete field diagnostic over a derived temporal view."""
    diagnostic_id = str(request.get("diagnostic_id", ""))
    derived_rank = 1 if diagnostic_id == "temporal_evolution" else 0
    priority_rank = 0 if str(request.get("priority")) == "required" else 1
    return (derived_rank, priority_rank, str(request.get("check_id", "")))


def _merge_analysis_data_requests(
    requests: list[dict[str, Any]],
    *,
    candidate_number: str,
    non_mergeable_check_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Merge repeated physical outputs while preserving explicitly isolated Checks.

    ``non_mergeable_check_ids`` is execution-only state.  It is intentionally not
    written into the JSON contract so the canonical schemas remain unchanged.
    A request linked to one of these Checks receives a unique merge key and can
    therefore never be absorbed into, or absorb, another physical request.
    """
    isolated_ids = {str(value) for value in (non_mergeable_check_ids or set()) if str(value)}
    merged_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for source_index, raw in enumerate(requests):
        request = copy.deepcopy(raw)
        source_check_ids = [
            str(value)
            for value in request.get("source_check_ids", [request.get("check_id")])
            if str(value)
        ]
        request["source_check_ids"] = list(dict.fromkeys(source_check_ids))
        key = _analysis_request_merge_key(request)
        if isolated_ids.intersection(request["source_check_ids"]):
            key = (*key, "__NON_MERGEABLE_CHECK__", source_index)
        existing = merged_by_key.get(key)
        if existing is None:
            merged_by_key[key] = request
            order.append(key)
            continue
        if _request_primary_rank(request) < _request_primary_rank(existing):
            existing["check_id"] = request.get("check_id")
            existing["diagnostic_id"] = request.get("diagnostic_id")
            existing["purpose"] = request.get("purpose")
        existing["source_check_ids"] = list(
            dict.fromkeys(
                [*existing.get("source_check_ids", []), *request.get("source_check_ids", [])]
            )
        )
        existing["forecast_hours"] = sorted(
            {int(value) for value in existing.get("forecast_hours", [])}
            | {int(value) for value in request.get("forecast_hours", [])}
        )
        existing["phase_ids"] = list(
            dict.fromkeys(
                [str(value) for value in existing.get("phase_ids", [])]
                + [str(value) for value in request.get("phase_ids", [])]
            )
        )
        existing["analysis_methods"] = list(
            dict.fromkeys(
                [str(value) for value in existing.get("analysis_methods", [])]
                + [str(value) for value in request.get("analysis_methods", [])]
            )
        )
        existing["feature_ids"] = list(
            dict.fromkeys(
                [str(value) for value in existing.get("feature_ids", [])]
                + [str(value) for value in request.get("feature_ids", [])]
            )
        )
        if str(request.get("priority")) == "required":
            existing["priority"] = "required"
        merged_spec = _merge_output_specs(existing.get("output_spec"), request.get("output_spec"))
        if merged_spec is not None:
            existing["output_spec"] = merged_spec
    merged = [merged_by_key[key] for key in order]
    for index, request in enumerate(merged, start=1):
        request["request_id"] = f"REQ_CAND{candidate_number}_{index:03d}"
    return merged


def _build_check_intent_contract(
    row: dict[str, Any],
    *,
    check_id: str,
    phase_ids: list[str],
) -> dict[str, Any]:
    """Preserve the Step04 meteorological verification intent without reinterpretation."""
    verification_mode = str(
        row.get("verification_mode")
        or _verification_mode_from_diagnostic_check(row)
    )
    source_intents = row.get("_source_intents")
    if not isinstance(source_intents, list) or not source_intents:
        source_intents = [_source_check_intent_snapshot(row)]
    source_intents = [copy.deepcopy(item) for item in source_intents if isinstance(item, dict)]
    return {
        "contract_version": CHECK_INTENT_CONTRACT_VERSION,
        "check_id": check_id,
        "source_intents": source_intents,
        "merged_source_intent_count": len(source_intents),
        "source_check_key": str(row.get("check_key") or ""),
        "signal_components": list(dict.fromkeys(
            str(value)
            for value in row.get("required_signal_components", [])
            if str(value)
        )),
        "verification_mode": verification_mode,
        "domain_scope": str(row.get("domain_scope") or "candidate"),
        "spatial_relation_scope": str(row.get("spatial_relation_scope") or "not_applicable"),
        "source_phase_keys": [str(value) for value in row.get("phase_keys", []) if str(value)],
        "phase_ids": list(phase_ids),
        "representative_spatial_hours": [
            int(value)
            for value in row.get("representative_spatial_hours", [])
            if isinstance(value, int) and not isinstance(value, bool)
        ],
        "direct_precipitation_anchor": _is_system_direct_precipitation_anchor_row(row),
        "expected_signal": row.get("expected_signal"),
        "decision_relevance": row.get("decision_relevance"),
        "purpose": row.get("purpose"),
        "priority": row.get("priority"),
        "evidence_role": row.get("evidence_role"),
        "coverage_roles": _effective_compact_check_coverage_roles(row),
        "cross_section_line": _cross_section_line_signature(row.get("cross_section_line")),
        "cross_section_strategy": _normalized_cross_section_strategy(row.get("cross_section_strategy")),
        "reference_band_axis": _reference_band_axis_signature(row.get("reference_band_axis")),
    }


def _capability_profile_for_check(
    support_catalog: dict[str, Any],
    *,
    diagnostic_id: str,
    product_id: str,
) -> dict[str, Any]:
    contract = support_catalog.get("product_capability_contract", {})
    if not isinstance(contract, dict):
        return {}
    diagnostics = contract.get("diagnostics", {})
    diagnostic = diagnostics.get(diagnostic_id, {}) if isinstance(diagnostics, dict) else {}
    profiles = diagnostic.get("profiles", {}) if isinstance(diagnostic, dict) else {}
    profile = profiles.get(product_id, {}) if isinstance(profiles, dict) else {}
    if not isinstance(profile, dict):
        return {}
    resolved = copy.deepcopy(profile)
    resolved["registry_hash"] = contract.get("contract_hash")
    return resolved


def _shadow_validate_check_capability(
    *,
    check_id: str,
    intent_contract: dict[str, Any],
    capability_profile: dict[str, Any],
) -> dict[str, Any]:
    """Audit intent/execution compatibility without changing operational decisions."""
    warning_codes: list[str] = []
    if not capability_profile:
        warning_codes.append("CAPABILITY_PROFILE_MISSING")
    else:
        mode = str(intent_contract.get("verification_mode") or "")
        supported_modes = {
            str(value)
            for value in capability_profile.get("supported_verification_modes", [])
            if str(value)
        }
        if supported_modes and mode not in supported_modes:
            warning_codes.append("VERIFICATION_MODE_UNSUPPORTED_BY_CAPABILITY")
        required = {
            str(value)
            for value in intent_contract.get("signal_components", [])
            if str(value)
        }
        available = {
            str(value)
            for value in capability_profile.get("signal_components", [])
            if str(value)
        }
        if available and not required.issubset(available):
            warning_codes.append("SIGNAL_COMPONENT_UNSUPPORTED_BY_CAPABILITY")
        if not capability_profile.get("observable_ids"):
            warning_codes.append("CAPABILITY_OBSERVABLES_MISSING")
    status = "pass" if not warning_codes else "warning"
    result = {
        "mode": CAPABILITY_VALIDATION_MODE,
        "status": status,
        "warning_codes": warning_codes,
    }
    if warning_codes:
        logging.warning(
            "STEP04_CAPABILITY_WARNING check_id=%s codes=%s",
            check_id,
            ",".join(warning_codes),
        )
    return result


def expand_compact_candidate_plan(
    compact_chunks: list[dict[str, Any]],
    *,
    package_id: str,
    candidate: dict[str, Any],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
) -> dict[str, Any]:
    """Expand AI-selected candidate/context domains into canonical Step05 requests."""
    if not compact_chunks:
        raise ValueError("compact candidate plan has no chunks")
    candidate_id = str(candidate.get("candidate_id"))
    match = re.fullmatch(r"CAND_(\d{3})", candidate_id)
    if match is None:
        raise ValueError(f"candidate_id is not canonical: {candidate_id}")
    number = match.group(1)
    phase_rows: list[dict[str, Any]] = []
    check_rows: list[dict[str, Any]] = []
    hypothesis_rows: list[dict[str, Any]] = []
    questions: list[str] = []
    objective: str | None = None
    context_bbox: dict[str, float] | None = None
    context_reason: str | None = None
    for chunk in compact_chunks:
        chunk_objective = str(chunk.get("analysis_objective", "")).strip()
        if not objective:
            objective = chunk_objective
        elif chunk_objective != objective:
            logging.info(
                "step04 compact candidate plan continuation objective ignored candidate_id=%s",
                candidate_id,
            )
        chunk_context = _normalized_bbox_for_validation(chunk.get("context_domain"))
        chunk_reason_raw = chunk.get("context_domain_reason")
        chunk_reason = str(chunk_reason_raw).strip() if chunk_reason_raw is not None else None
        if context_bbox is None and chunk_context is not None:
            context_bbox = chunk_context
            context_reason = chunk_reason
        elif context_bbox != chunk_context or context_reason != chunk_reason:
            raise ValueError("compact candidate plan continuation changed context_domain")
        phase_rows.extend(_compact_plan_rows(chunk, "analysis_phases"))
        check_rows.extend(_compact_plan_rows(chunk, "diagnostic_checks"))
        hypothesis_rows.extend(_compact_plan_rows(chunk, "hypotheses"))
        questions.extend(str(item) for item in chunk.get("forecaster_questions", []))
    questions = list(dict.fromkeys(question.strip() for question in questions if question.strip()))
    hypothesis_rows = _drop_hypotheses_without_supporting_checks(
        hypothesis_rows,
        candidate_id=candidate_id,
        field_suffix="keys",
        require_one=True,
    )
    phase_rows = _deduplicate_compact_phases(phase_rows, check_rows)
    check_rows, hypothesis_rows = _canonicalize_semantic_duplicate_compact_plan(
        check_rows,
        hypothesis_rows,
        phase_rows,
        candidate_id=candidate_id,
    )
    _compact_local_keys(phase_rows, "phase_key", label="merged analysis_phases")
    _compact_local_keys(check_rows, "check_key", label="merged diagnostic_checks")
    _compact_local_keys(hypothesis_rows, "hypothesis_key", label="merged hypotheses")
    _compact_check_combinations(check_rows)

    candidate_bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
    if candidate_bbox is None:
        raise ValueError("compact candidate plan candidate_area bbox is invalid")
    if context_bbox is not None:
        if not _bbox_contains(context_bbox, candidate_bbox):
            raise ValueError("context_domain must contain candidate_area")
        available_domain = _available_context_domain(support_catalog)
        if not _bbox_contains(available_domain, context_bbox):
            raise ValueError(
                f"context_domain is outside available context domain: available={available_domain}"
            )
        if not context_reason:
            raise ValueError("context_domain requires context_domain_reason")

    has_regional = any(
        bool({str(item) for item in row.get("analysis_methods", [])} & REGIONAL_ANALYSIS_METHODS)
        for row in check_rows
    )
    targets: list[dict[str, Any]] = []
    target_id_by_scope: dict[str, str] = {}
    candidate_target_id = f"AREA_CAND{number}_01"
    target_id_by_scope["candidate"] = candidate_target_id
    targets.append({
        "target_id": candidate_target_id,
        "target_type": "candidate_domain",
        "resolution_method": "explicit_bbox",
        "requested_domain": candidate_bbox,
        "region_ids": [],
        "purpose": "후보 위험 핵심영역의 공간장과 정량 신호를 검증합니다.",
    })
    if context_bbox is not None:
        context_target_id = f"AREA_CAND{number}_{len(targets)+1:02d}"
        target_id_by_scope["context"] = context_target_id
        targets.append({
            "target_id": context_target_id,
            "target_type": "context_domain",
            "resolution_method": "explicit_bbox",
            "requested_domain": context_bbox,
            "region_ids": [],
            "purpose": context_reason,
        })
    if has_regional:
        operational_target_id = f"AREA_CAND{number}_{len(targets)+1:02d}"
        target_id_by_scope["operational_regions"] = operational_target_id
        targets.append({
            "target_id": operational_target_id,
            "target_type": "operational_regions",
            "resolution_method": "candidate_area_overlap",
            "requested_domain": None,
            "region_ids": [],
            "purpose": "후보 위험영역과 겹치는 운영지역의 통계·순위·최대값·시간변화를 검증합니다.",
        })

    available_phase_hours = set(
        _available_candidate_forecast_hours(candidate, attachment_manifest)
    )
    if not available_phase_hours:
        raise ValueError("compact candidate plan cannot derive available forecast hours")
    phase_ids: dict[str, str] = {}
    phases: list[dict[str, Any]] = []
    for index, row in enumerate(phase_rows, start=1):
        key = str(row["phase_key"])
        phase_type = str(row.get("phase_type"))
        hours = list(_compact_phase_hours(row))
        invalid_hours = sorted(set(hours) - available_phase_hours)
        if invalid_hours:
            raise ValueError(
                "compact candidate plan phase uses unavailable forecast hours "
                f"phase_key={key} invalid={invalid_hours}"
            )
        phase_ids[key] = f"PHASE_CAND{number}_{index:02d}"
        phases.append({
            "phase_id": phase_ids[key],
            "phase_type": phase_type,
            "forecast_hours": hours,
            "purpose": row.get("purpose"),
        })

    check_ids: dict[str, str] = {}
    product_ids_by_check_id: dict[str, str] = {}
    checks: list[dict[str, Any]] = []
    for index, row in enumerate(check_rows, start=1):
        local_key = str(row["check_key"])
        check_id = f"CHK_CAND{number}_{index:02d}"
        check_ids[local_key] = check_id
        role = str(row.get("variable_role"))
        diagnostic_id = str(row.get("diagnostic_id"))
        methods = _canonical_analysis_methods_for_diagnostic(
            diagnostic_id,
            row.get("analysis_methods", []),
        )
        row["analysis_methods"] = methods
        check_scope = str(row.get("check_scope") or _derived_check_scope(diagnostic_id, methods))
        required_signal_components = _normalized_required_signal_components(
            row.get("required_signal_components"),
            variable_role=role,
            diagnostic_id=diagnostic_id,
        )
        _validate_check_scope_contract(
            check_key=local_key,
            diagnostic_id=diagnostic_id,
            analysis_methods=methods,
            check_scope=check_scope,
            required_signal_components=required_signal_components,
        )
        product_id = _preferred_product_id_for_check(
            diagnostic_id, role, required_signal_components, support_catalog, candidate
        )
        product_ids_by_check_id[check_id] = product_id
        product_signal_components = _product_signal_components_from_support(
            product_id, support_catalog
        )
        if product_signal_components and not set(required_signal_components).issubset(product_signal_components):
            raise ValueError(
                f"check_key={local_key} required_signal_components={required_signal_components} "
                f"cannot be provided by product_id={product_id!r}; available={sorted(product_signal_components)}"
            )
        scope = str(row.get("domain_scope"))
        if scope == "context" and "context" not in target_id_by_scope:
            raise ValueError(f"check_key={local_key} requests context without context_domain")
        if diagnostic_id in CANDIDATE_SCOPE_ONLY_DIAGNOSTIC_IDS and scope != "candidate":
            raise ValueError(
                f"check_key={local_key} diagnostic_id={diagnostic_id} must use candidate domain"
            )
        target_values: list[str] = []
        if set(methods) & SPATIAL_ANALYSIS_METHODS:
            target_values.append(target_id_by_scope[scope])
        quantitative_feature_ids = (
            []
            if diagnostic_id in SPATIAL_ONLY_DIAGNOSTIC_IDS
            else _quantitative_feature_ids_for_check(diagnostic_id, product_id, support_catalog)
        )
        if set(methods) & REGIONAL_ANALYSIS_METHODS:
            if "operational_regions" not in target_id_by_scope:
                raise ValueError("regional analysis method requires operational_regions target")
            if not quantitative_feature_ids:
                raise ValueError(
                    "regional analysis requires quantitative feature mapping "
                    f"product_id={product_id}"
                )
            target_values.append(target_id_by_scope["operational_regions"])
        canonical_phase_ids = [phase_ids[str(key)] for key in row.get("phase_keys", [])]
        intent_contract = _build_check_intent_contract(
            row,
            check_id=check_id,
            phase_ids=canonical_phase_ids,
        )
        intent_hash = canonical_hash(intent_contract)
        if int(intent_contract.get("merged_source_intent_count") or 0) > 1:
            logging.info(
                "STEP04_INTENT_MERGE_AUDIT check_id=%s source_intents=%s",
                check_id,
                intent_contract.get("merged_source_intent_count"),
            )
        atomic_claim_contract = _build_atomic_claim_contract(
            row,
            check_id=check_id,
        )
        atomic_claim_hash = canonical_hash(atomic_claim_contract)
        atomic_claim_validation = _shadow_validate_atomic_claim_contract(
            check_id=check_id,
            row=row,
            contract=atomic_claim_contract,
        )
        for atomic_claim in atomic_claim_contract.get("canonical_claims", []):
            scope_value = atomic_claim.get("quantitative_scope") if isinstance(atomic_claim, dict) else None
            feature_value = scope_value.get("feature_id") if isinstance(scope_value, dict) else None
            if feature_value is not None and feature_value not in quantitative_feature_ids:
                raise ValueError(
                    f"check_key={local_key} atomic quantitative_scope feature_id={feature_value!r} "
                    f"is outside executable quantitative features={quantitative_feature_ids}"
                )
        capability_profile = _capability_profile_for_check(
            support_catalog,
            diagnostic_id=diagnostic_id,
            product_id=product_id,
        )
        capability_validation = _shadow_validate_check_capability(
            check_id=check_id,
            intent_contract=intent_contract,
            capability_profile=capability_profile,
        )
        checks.append({
            "check_id": check_id,
            "diagnostic_id": row.get("diagnostic_id"),
            "variable_role": role,
            "check_scope": check_scope,
            "required_signal_components": required_signal_components,
            "product_id": product_id,
            "feature_ids": quantitative_feature_ids,
            "target_ids": list(dict.fromkeys(target_values)),
            "phase_ids": canonical_phase_ids,
            "analysis_methods": methods,
            "expected_signal": row.get("expected_signal"),
            "decision_relevance": row.get("decision_relevance"),
            "priority": row.get("priority"),
            "cross_section_line": _cross_section_line_signature(row.get("cross_section_line")),
            "cross_section_strategy": _normalized_cross_section_strategy(row.get("cross_section_strategy")),
            "reference_band_axis": _reference_band_axis_signature(row.get("reference_band_axis")),
            "intent_contract": intent_contract,
            "intent_hash": intent_hash,
            "atomic_claim_contract": atomic_claim_contract,
            "atomic_claim_hash": atomic_claim_hash,
            "atomic_claim_validation": atomic_claim_validation,
            "capability_profile": capability_profile,
            "capability_validation": capability_validation,
        })

    hypotheses: list[dict[str, Any]] = []
    for index, row in enumerate(hypothesis_rows, start=1):
        def mapped(field: str) -> list[str]:
            keys = [str(item) for item in row.get(field, [])]
            unknown = sorted(set(keys) - set(check_ids))
            if unknown:
                raise ValueError(f"compact hypothesis references unknown check_keys={unknown}")
            return list(dict.fromkeys(check_ids[key] for key in keys))
        hypotheses.append({
            "hypothesis_id": f"H_CAND{number}_{index:02d}",
            "statement": row.get("statement"),
            "supporting_check_ids": mapped("supporting_check_keys"),
            "opposing_check_ids": mapped("opposing_check_keys"),
        })
    _canonicalize_disjoint_hypothesis_check_references(
        hypotheses,
        candidate_id=candidate_id,
    )
    hypotheses = _drop_hypotheses_without_supporting_checks(
        hypotheses,
        candidate_id=candidate_id,
        field_suffix="ids",
        require_one=True,
    )
    hypotheses = _limit_canonical_hypotheses(hypotheses, candidate_number=number)
    questions = _limit_canonical_questions(questions)

    phase_hours = {str(item["phase_id"]): item["forecast_hours"] for item in phases}
    requests: list[dict[str, Any]] = []
    for row in check_rows:
        check_id = check_ids[str(row["check_key"])]
        product_id = product_ids_by_check_id[check_id]
        request_phase_ids = [phase_ids[str(key)] for key in row.get("phase_keys", [])]
        forecast_hours = sorted({hour for phase_id in request_phase_ids for hour in phase_hours[phase_id]})
        allowed_hours = _allowed_forecast_hours_for_product(product_id, support_catalog)
        if allowed_hours is not None:
            forecast_hours = [hour for hour in forecast_hours if hour in allowed_hours]
        if not forecast_hours:
            raise ValueError(
                f"compact candidate plan has no supported forecast hours check_key={row['check_key']}"
            )
        minimum_hours = _minimum_forecast_hours_for_check(
            str(row.get("diagnostic_id") or ""),
            str(row.get("check_scope") or ""),
        )
        if len(set(forecast_hours)) < minimum_hours:
            raise ValueError(
                "compact candidate plan has insufficient forecast-hour coverage "
                f"check_key={row['check_key']} required={minimum_hours} actual={len(set(forecast_hours))}"
            )
        methods = list(dict.fromkeys(str(item) for item in row.get("analysis_methods", [])))
        spatial_methods = [item for item in methods if item in SPATIAL_ANALYSIS_METHODS]
        regional_methods = [item for item in methods if item in REGIONAL_ANALYSIS_METHODS]
        common = {
            "candidate_id": candidate_id,
            "plan_id": f"PLAN_CAND{number}",
            "diagnostic_id": row.get("diagnostic_id"),
            "forecast_hours": forecast_hours,
            "check_id": check_id,
            "source_check_ids": [check_id],
            "phase_ids": request_phase_ids,
            "purpose": row.get("purpose"),
            "priority": row.get("priority"),
            "cross_section_line": _cross_section_line_signature(row.get("cross_section_line")),
            "cross_section_strategy": _normalized_cross_section_strategy(row.get("cross_section_strategy")),
            "reference_band_axis": _reference_band_axis_signature(row.get("reference_band_axis")),
        }
        if spatial_methods:
            scope = str(row.get("domain_scope"))
            bbox = candidate_bbox if scope == "candidate" else context_bbox
            if bbox is None:
                raise ValueError(f"check_key={row['check_key']} has unresolved spatial domain")
            spatial_forecast_hours = forecast_hours
            if str(row.get("verification_mode") or _verification_mode_from_diagnostic_check(row)) == "temporal_change":
                representative_hours = [
                    int(value)
                    for value in row.get("representative_spatial_hours", [])
                    if isinstance(value, int) and not isinstance(value, bool)
                ]
                spatial_forecast_hours = [hour for hour in representative_hours if hour in set(forecast_hours)]
                if not spatial_forecast_hours:
                    raise ValueError(
                        f"compact temporal_change has no executable representative_spatial_hours check_key={row['check_key']}"
                    )
            requests.append({
                **common,
                "forecast_hours": spatial_forecast_hours,
                "request_id": f"REQ_CAND{number}_{len(requests)+1:03d}",
                "data_type": "spatial_field",
                "product_id": product_id,
                "feature_ids": [],
                "requested_domain": bbox,
                "target_id": target_id_by_scope[scope],
                "analysis_methods": spatial_methods,
            })
        if regional_methods:
            feature_ids = _quantitative_feature_ids_for_check(
                str(row.get("diagnostic_id") or ""),
                product_id,
                support_catalog,
            )
            statistics = ["valid_grid_count", "mean", "min", "max", "p95"]
            if "regional_max_location" in regional_methods:
                statistics.append("max_location")
            requests.append({
                **common,
                "request_id": f"REQ_CAND{number}_{len(requests)+1:03d}",
                "data_type": "quantitative_excerpt",
                "product_id": product_id,
                "feature_ids": feature_ids,
                "requested_domain": None,
                "target_id": target_id_by_scope["operational_regions"],
                "analysis_methods": regional_methods,
                "output_spec": {
                    "statistics": statistics,
                    "ranking_statistic": "p95",
                    "top_n": 20,
                    "include_time_series": "temporal_evolution" in regional_methods,
                    "include_max_location": "regional_max_location" in regional_methods,
                },
            })

    requests = _merge_analysis_data_requests(requests, candidate_number=number)

    plan = {
        "plan_id": f"PLAN_CAND{number}",
        "candidate_id": candidate_id,
        "analysis_objective": objective,
        "hypotheses": hypotheses,
        "target_areas": targets,
        "analysis_phases": phases,
        "variable_checks": checks,
        "forecaster_questions": questions,
    }
    _link_unreferenced_variable_checks(plan)
    _prune_unreferenced_context_phases(plan)
    return {
        "package_id": package_id,
        "candidate_id": candidate_id,
        "candidate_analysis_plan": plan,
        "analysis_data_requests": requests,
    }


def split_payload_diagnostics(
    *,
    model: str,
    messages: list[dict[str, Any]],
    input_value: dict[str, Any],
    response_schema: dict[str, Any],
    schema_name: str,
    max_tokens: int,
    temperature: float,
    top_p: float,
    enable_thinking: bool,
) -> dict[str, int]:
    payload = build_http_payload(
        model=model,
        messages=messages,
        response_schema=response_schema,
        schema_name=schema_name,
        structured_output=True,
        enable_thinking=enable_thinking,
        extensions=True,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
    )
    return {
        "http_json_utf8_bytes": json_utf8_bytes(payload),
        "messages_utf8_bytes": json_utf8_bytes(messages),
        "input_utf8_bytes": json_utf8_bytes(input_value),
        "response_schema_utf8_bytes": json_utf8_bytes(response_schema),
    }


def ensure_split_payload_budget(stage: str, diagnostics: dict[str, int]) -> None:
    actual = diagnostics["http_json_utf8_bytes"]
    if actual > HIERARCHICAL_FINAL_MAX_BYTES:
        raise Pass1Error(
            f"{stage} payload exceeds hard limit {HIERARCHICAL_FINAL_MAX_BYTES} UTF-8 bytes: "
            + json.dumps(diagnostics, ensure_ascii=True, sort_keys=True)
        )
    if actual > HIERARCHICAL_PAYLOAD_WARN_BYTES:
        logging.warning(
            "%s payload exceeds warning threshold %s UTF-8 bytes "
            "but is within hard limit %s; continuing: %s",
            stage,
            HIERARCHICAL_PAYLOAD_WARN_BYTES,
            HIERARCHICAL_FINAL_MAX_BYTES,
            json.dumps(diagnostics, ensure_ascii=True, sort_keys=True),
        )


def _checkpoint_contract(base: dict[str, Any]) -> dict[str, Any]:
    return {**base, "fingerprint": canonical_hash(base)}


def _load_checkpointed_json(
    *,
    value_path: Path,
    checkpoint_path: Path,
    expected_checkpoint: dict[str, Any],
    provider_stage: str,
    validator,
) -> dict[str, Any] | None:
    if not value_path.is_file() or not checkpoint_path.is_file():
        return None
    try:
        checkpoint = load_json(checkpoint_path)
        if not _checkpoint_fingerprint_matches(
            checkpoint,
            expected_checkpoint,
            provider_stage=provider_stage,
        ):
            return None
        if checkpoint.get("value_sha256") != sha256sum(value_path):
            return None
        value = load_json(value_path)
        return value if not validator(value) else None
    except Exception:  # noqa: BLE001
        return None


def create_or_resume_candidate_discovery(
    *,
    root: Path,
    output_dir: Path,
    package_id: str,
    group_summaries: list[dict[str, Any]],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    overview_data: dict[str, Any],
    schema: dict[str, Any],
    provider_schema: dict[str, Any] | None = None,
    prompt_template: str,
    prompt_sha256: str,
    client: Pass1ProviderClient,
    model: str,
    structured_output_mode: str,
    enable_thinking: bool,
    max_tokens: int,
    temperature: float,
    top_p: float,
    repair_attempts: int,
    resume: bool,
    reuse_as_immutable_input: bool = False,
    output_policy: OutputPolicy | None = None,
    checkpoint_publisher: Callable[[str, str], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
    output_policy = output_policy or OutputPolicy("operational")
    checkpoint_provider_schema = provider_schema or schema
    runtime_provider_schema = build_candidate_discovery_provider_schema(
        checkpoint_provider_schema,
        attachment_manifest=attachment_manifest,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "candidate_discovery.raw.txt"
    value_path = output_dir / "candidate_discovery.validated.json"
    provider_path = output_dir / "candidate_discovery.provider.json"
    checkpoint_path = output_dir / "candidate_discovery.checkpoint.json"
    system_precipitation_audit_path = output_dir / "system_direct_precipitation_coverage_audit.json"
    coverage_reconciliation_path = output_dir / "system_direct_precipitation_coverage_reconciliation.json"

    def persist_system_precipitation_audit(value: dict[str, Any]) -> dict[str, Any]:
        audit = build_system_direct_precipitation_coverage_audit(
            value,
            overview_data=overview_data,
            attachment_manifest=attachment_manifest,
            support_catalog=support_catalog,
        )
        write_json(system_precipitation_audit_path, audit)
        logging.info(
            "step04 SYSTEM direct precipitation coverage audit signals=%s covered=%s "
            "required_uncovered=%s advisory_uncovered=%s",
            audit.get("signal_count", 0),
            audit.get("covered_signal_count", 0),
            audit.get("required_uncovered_count", 0),
            audit.get("advisory_uncovered_count", 0),
        )
        return audit

    checkpoint = _checkpoint_contract(
        {
            "package_id": package_id,
            "group_summaries_fingerprint": canonical_hash(group_summaries),
            "candidate_discovery_input_projection_version": CANDIDATE_DISCOVERY_INPUT_PROJECTION_VERSION,
            "candidate_discovery_group_summaries_projection_fingerprint": canonical_hash(
                _candidate_discovery_group_summary_projection(group_summaries)
            ),
            "attachment_index_fingerprint": canonical_hash(
                _candidate_discovery_asset_index(attachment_manifest)
            ),
            "support_catalog_fingerprint": canonical_hash(
                _minimal_support_catalog(support_catalog).get("spatial_field_products", [])
            ),
            "precipitation_peak_guidance_fingerprint": canonical_hash(
                build_precipitation_peak_guidance(
                    overview_data, minimum_regional_max_mm=_peak_anchor_minimum_mm(support_catalog)
                )
            ),
            "system_direct_precipitation_coverage_audit_version": SYSTEM_DIRECT_PRECIPITATION_COVERAGE_AUDIT_VERSION,
            "direct_precipitation_coverage_reconciliation_version": DIRECT_PRECIPITATION_COVERAGE_RECONCILIATION_VERSION,
            "system_direct_precipitation_coverage_guidance_fingerprint": canonical_hash(
                build_system_direct_precipitation_coverage_guidance(
                    overview_data, support_catalog=support_catalog
                )
            ),
            "candidate_operational_scope_fingerprint": canonical_hash(
                _candidate_discovery_operational_scope(overview_data)
            ),
            "candidate_operational_scope_contract_version": CANDIDATE_DISCOVERY_OPERATIONAL_SCOPE_CONTRACT_VERSION,
            "prompt_sha256": prompt_sha256,
            "schema_fingerprint": canonical_hash(
                {"canonical": schema, "provider": checkpoint_provider_schema}
            ),
            "canonical_schema_sha256": canonical_hash(schema),
            "provider_configuration": _client_checkpoint_configuration(
                client,
                stage="candidate_discovery",
            ),
            "model": model,
            "contract_version": SPLIT_SYNTHESIS_CONTRACT_VERSION,
            "meteorological_qc_version": CANDIDATE_DISCOVERY_METEOROLOGICAL_QC_VERSION,
            "narrative_language": NARRATIVE_LANGUAGE,
        }
    )
    validator = lambda value: validate_candidate_discovery_response(
        value,
        schema=schema,
        package_id=package_id,
        attachment_manifest=attachment_manifest,
        support_catalog=support_catalog,
        group_summaries=group_summaries,
        overview_data=overview_data,
        enforce_system_direct_precipitation_coverage=True,
    )
    discovery_validator_without_system_coverage = lambda value: validate_candidate_discovery_response(
        value,
        schema=schema,
        package_id=package_id,
        attachment_manifest=attachment_manifest,
        support_catalog=support_catalog,
        group_summaries=group_summaries,
        overview_data=overview_data,
        enforce_system_direct_precipitation_coverage=False,
    )
    if reuse_as_immutable_input:
        if not resume:
            raise Pass1Error("candidate plan-only execution requires candidate discovery resume")
        if not value_path.is_file() or not checkpoint_path.is_file():
            raise Pass1Error(
                "candidate plan-only execution requires an existing candidate discovery checkpoint"
            )
        try:
            frozen_checkpoint = load_json(checkpoint_path)
            frozen_value = load_json(value_path)
        except Exception as exc:  # noqa: BLE001
            raise Pass1Error(
                "candidate plan-only execution could not read the candidate discovery checkpoint"
            ) from exc
        if frozen_checkpoint.get("provider_configuration") != _client_checkpoint_configuration(
            client,
            stage="candidate_discovery",
        ):
            raise Pass1Error(
                "candidate plan-only execution requires a candidate discovery checkpoint for the active provider configuration"
            )
        frozen_value, frozen_qc = _apply_candidate_discovery_meteorological_qc(
            frozen_value,
            overview_data=overview_data,
        )
        errors = validator(frozen_value)
        if errors:
            raise Pass1Error(
                "candidate plan-only execution found an invalid candidate discovery checkpoint: "
                + " | ".join(errors[:10])
            )
        system_precipitation_audit = persist_system_precipitation_audit(frozen_value)
        logging.info(
            "step05 candidate discovery reused as immutable input candidates=%s provider_call_count=0",
            len(frozen_value.get("candidates", [])),
        )
        return (
            frozen_value,
            {
                "status": "reused_as_immutable_input",
                "validated_ref": rel(root, value_path),
                "checkpoint_ref": rel(root, checkpoint_path),
                "candidate_count": len(frozen_value.get("candidates", [])),
                "meteorological_qc_candidate_count": len(frozen_qc),
                "system_direct_precipitation_coverage_audit_ref": rel(root, system_precipitation_audit_path),
                "system_direct_precipitation_required_uncovered_count": int(system_precipitation_audit.get("required_uncovered_count", 0)),
                "system_direct_precipitation_advisory_uncovered_count": int(system_precipitation_audit.get("advisory_uncovered_count", 0)),
                "elapsed_seconds": 0.0,
                "provider_call_count": 0,
            },
            {
                "http_json_utf8_bytes": 0,
                "message_utf8_bytes": 0,
                "input_utf8_bytes": 0,
                "schema_utf8_bytes": 0,
            },
        )
    existing = None
    if resume:
        existing = _load_checkpointed_json(
            value_path=value_path,
            checkpoint_path=checkpoint_path,
            expected_checkpoint=checkpoint,
            provider_stage="candidate_discovery",
            validator=validator,
        )
    messages, input_value = build_candidate_discovery_messages(
        prompt_template=prompt_template,
        package_id=package_id,
        group_summaries=group_summaries,
        attachment_manifest=attachment_manifest,
        support_catalog=support_catalog,
        overview_data=overview_data,
    )
    if structured_output_mode == "off":
        messages = add_prompt_only_schema_constraint(messages, runtime_provider_schema)
    diagnostics = split_payload_diagnostics(
        model=model,
        messages=messages,
        input_value=input_value,
        response_schema=runtime_provider_schema,
        schema_name="AIFSPass1CandidateDiscovery",
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        enable_thinking=enable_thinking,
    )
    diagnostics.update(_candidate_discovery_payload_component_bytes(input_value))
    diagnostics["original_group_summaries_utf8_bytes"] = json_utf8_bytes(group_summaries)
    diagnostics["group_summaries_projection_saved_utf8_bytes"] = (
        diagnostics["original_group_summaries_utf8_bytes"]
        - diagnostics.get("group_summaries_utf8_bytes", 0)
    )
    logging.info(
        "step04 candidate discovery payload bytes=%s input=%s group_summaries=%s "
        "asset_index=%s group_projection_saved=%s peak_guidance=%s coverage_guidance=%s "
        "operational_scope=%s schema=%s",
        diagnostics.get("http_json_utf8_bytes"),
        diagnostics.get("input_utf8_bytes"),
        diagnostics.get("group_summaries_utf8_bytes"),
        diagnostics.get("asset_index_utf8_bytes"),
        diagnostics.get("group_summaries_projection_saved_utf8_bytes"),
        diagnostics.get("precipitation_peak_guidance_utf8_bytes"),
        diagnostics.get("system_direct_precipitation_coverage_guidance_utf8_bytes"),
        diagnostics.get("candidate_operational_scope_utf8_bytes"),
        diagnostics.get("response_schema_utf8_bytes"),
    )
    ensure_split_payload_budget("candidate discovery", diagnostics)
    if existing is not None:
        existing, existing_qc = _apply_candidate_discovery_meteorological_qc(
            existing,
            overview_data=overview_data,
        )
        system_precipitation_audit = persist_system_precipitation_audit(existing)
        report = {
            "status": "reused",
            "validated_ref": rel(root, value_path),
            "checkpoint_ref": rel(root, checkpoint_path),
            "candidate_count": len(existing.get("candidates", [])),
            "meteorological_qc_candidate_count": len(existing_qc),
            "system_direct_precipitation_coverage_audit_ref": rel(root, system_precipitation_audit_path),
            "system_direct_precipitation_required_uncovered_count": int(system_precipitation_audit.get("required_uncovered_count", 0)),
            "system_direct_precipitation_advisory_uncovered_count": int(system_precipitation_audit.get("advisory_uncovered_count", 0)),
            "elapsed_seconds": 0.0,
        }
        logging.info(
            "step05 candidate discovery reused candidates=%s http_payload_bytes=%s",
            report["candidate_count"],
            diagnostics["http_json_utf8_bytes"],
        )
        return existing, report, diagnostics

    if coverage_reconciliation_path.is_file():
        coverage_reconciliation_path.unlink()

    logging.info(
        "step05 candidate discovery start group_summaries=%s http_payload_bytes=%s max_tokens=%s",
        len(group_summaries),
        diagnostics["http_json_utf8_bytes"],
        max_tokens,
    )
    started = time.monotonic()
    def run_candidate_discovery_call(
        call_messages: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]:
        return call_and_validate_json(
            client=client,
            model=model,
            messages=call_messages,
            schema=runtime_provider_schema,
            schema_name="AIFSPass1CandidateDiscovery",
            structured_output_mode=structured_output_mode,
            enable_thinking=enable_thinking,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            repair_attempts=repair_attempts,
            normalizer=lambda item: normalize_candidate_discovery_contract(
                item,
                package_id=package_id,
                attachment_count=int(attachment_manifest.get("attachment_count", 0)),
                attachment_manifest=attachment_manifest,
                stage_schema=runtime_provider_schema,
                group_summaries=group_summaries,
                overview_data=overview_data,
                support_catalog=support_catalog,
            ),
            fixed_field_expectations=lambda raw: {
                "package_id": package_id,
                "analysis_coverage.coverage_status": "complete",
                "analysis_coverage.overview_attachment_count": int(
                    attachment_manifest.get("attachment_count", 0)
                ),
                "run_summary.candidate_count": len(raw.get("candidates", []))
                if isinstance(raw.get("candidates"), list)
                else 0,
                "pass2_required": bool(raw.get("candidates"))
                if isinstance(raw.get("candidates"), list)
                else False,
            },
            semantic_validator=lambda result: [
                *discovery_validator_without_system_coverage(result),
                *_candidate_discovery_initial_compact_limit_errors(result),
            ],
            repair_context_messages=call_messages,
        )

    incomplete_retry_count = 0
    initial_incomplete_diagnostics: dict[str, Any] | None = None
    try:
        value, raw, provider, call_meta = run_candidate_discovery_call(messages)
    except ProviderIncompleteError as exc:
        diagnostics_incomplete = dict(exc.diagnostics)
        if str(diagnostics_incomplete.get("incomplete_reason", "")) != "max_output_tokens":
            raise
        initial_incomplete_diagnostics = diagnostics_incomplete
        incomplete_retry_count = 1
        logging.warning(
            "step04 candidate discovery max_output_tokens incomplete; compact retry=1/1 "
            "output_tokens=%s reasoning_tokens=%s diagnosis=%s",
            diagnostics_incomplete.get("output_tokens"),
            diagnostics_incomplete.get("reasoning_tokens"),
            diagnostics_incomplete.get("incomplete_diagnosis"),
        )
        retry_messages = build_candidate_discovery_length_retry_messages(messages)
        try:
            value, raw, provider, call_meta = run_candidate_discovery_call(retry_messages)
        except ProviderIncompleteError as retry_exc:
            retry_diagnostics = dict(retry_exc.diagnostics)
            retry_diagnostics.update(
                {
                    "candidate_discovery_incomplete_retry_attempt_count": 1,
                    "initial_incomplete_reason": diagnostics_incomplete.get("incomplete_reason"),
                    "initial_output_tokens": diagnostics_incomplete.get("output_tokens"),
                    "initial_reasoning_tokens": diagnostics_incomplete.get("reasoning_tokens"),
                    "initial_total_tokens": diagnostics_incomplete.get("total_tokens"),
                    "initial_incomplete_diagnosis": diagnostics_incomplete.get(
                        "incomplete_diagnosis"
                    ),
                }
            )
            raise ProviderIncompleteError(
                str(retry_exc), diagnostics=retry_diagnostics
            ) from retry_exc

    call_meta["candidate_discovery_incomplete_retry_attempt_count"] = incomplete_retry_count
    if initial_incomplete_diagnostics is not None:
        initial_usage = {
            key: initial_incomplete_diagnostics.get(key)
            for key in (
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "total_tokens",
            )
        }
        retry_usage = call_meta.get("usage")
        retry_attempts = (
            list(retry_usage.get("attempts", []))
            if isinstance(retry_usage, dict) and isinstance(retry_usage.get("attempts"), list)
            else []
        )
        call_meta["usage"] = _summarize_usage_attempts([initial_usage, *retry_attempts])
        call_meta["http_retry_count"] = int(call_meta.get("http_retry_count", 0)) + int(
            initial_incomplete_diagnostics.get("retry_count") or 0
        )
        call_meta["candidate_discovery_initial_incomplete"] = {
            key: initial_incomplete_diagnostics.get(key)
            for key in (
                "incomplete_reason",
                "configured_max_output_tokens",
                "output_tokens",
                "reasoning_tokens",
                "total_tokens",
                "visible_output_token_estimate",
                "incomplete_diagnosis",
            )
        }
    value, meteorological_qc = _apply_candidate_discovery_meteorological_qc(
        value,
        overview_data=overview_data,
    )
    post_qc_errors = [
        *discovery_validator_without_system_coverage(value),
        *_candidate_discovery_initial_compact_limit_errors(value),
    ]
    if post_qc_errors:
        raise Pass1Error(
            "candidate discovery meteorological QC attachment produced an invalid canonical response: "
            + " | ".join(post_qc_errors[:10])
        )

    initial_system_precipitation_audit = persist_system_precipitation_audit(value)
    required_uncovered = _required_uncovered_system_direct_precipitation_signals(
        initial_system_precipitation_audit
    )
    call_meta["coverage_reconciliation_attempt_count"] = 0
    if required_uncovered:
        original_count = len(required_uncovered)
        initial_candidate_count = len(value.get("candidates", []))
        working_value = copy.deepcopy(value)
        working_audit = initial_system_precipitation_audit
        history: list[dict[str, Any]] = []
        rejected_in_row = 0
        working_path = output_dir / "candidate_discovery.coverage_working.json"
        if working_path.exists():
            working_path.unlink()  # never reuse a partially covered discovery as a checkpoint
        # A bounded outer loop accepts partial improvements. A rejected proposal
        # never becomes the baseline for the next AI call.
        max_reconciliation_rounds = max(4, min(12, max(1, repair_attempts) * 3))
        for attempt in range(1, max_reconciliation_rounds + 1):
            missing = _required_uncovered_system_direct_precipitation_signals(working_audit)
            if not missing:
                break
            before_reconciliation = copy.deepcopy(working_value)
            worklist_offset = (rejected_in_row * 4) % len(missing)
            reconciliation_messages = build_system_direct_precipitation_coverage_reconciliation_messages(
                base_messages=messages,
                current_response=working_value,
                audit=working_audit,
                attachment_manifest=attachment_manifest,
                worklist_offset=worklist_offset,
                worklist_limit=4,
            )
            # Do not send the last provider response as a fresh original candidate
            # discovery. The current accepted baseline is explicit in the prompt.
            if history and history[-1].get("status") == "rejected":
                reconciliation_messages.append({
                    "role": "user",
                    "content": json.dumps({
                        "previous_attempt": "rejected_no_progress_or_regression",
                        "reason": history[-1].get("reason"),
                        "instruction": "기존 포괄을 유지한 채 누락된 신호를 새로 포괄하는 별도 기상학적 계획을 작성하십시오.",
                    }, ensure_ascii=False),
                })
            reconciliation_input = {
                "current_candidate_count": len(working_value.get("candidates", [])),
                "required_uncovered_count": len(missing),
                "required_uncovered_signals": missing[:12],
            }
            reconciliation_diagnostics = split_payload_diagnostics(
                model=model,
                messages=reconciliation_messages,
                input_value=reconciliation_input,
                response_schema=runtime_provider_schema,
                schema_name="AIFSPass1CandidateDiscovery",
                max_tokens=max_tokens,
                temperature=0.0,
                top_p=1.0,
                enable_thinking=enable_thinking,
            )
            ensure_split_payload_budget(
                "direct precipitation coverage reconciliation", reconciliation_diagnostics
            )

            def reconciliation_validator(repaired: dict[str, Any]) -> list[str]:
                # Partial coverage is accepted only after the SYSTEM ledger
                # confirms monotonic progress; unrelated schema contracts remain.
                return [
                    *discovery_validator_without_system_coverage(repaired),
                    *_candidate_discovery_coverage_reconciliation_preservation_errors(
                        before_reconciliation,
                        repaired,
                        required_uncovered_count=len(missing),
                    ),
                ]

            entry: dict[str, Any] = {
                "attempt": attempt,
                "before_required_uncovered_count": len(missing),
                "candidate_count_before": len(working_value.get("candidates", [])),
                "candidate_capacity_before": _coverage_reconciliation_capacity(working_value),
                "worklist": [
                    {"forecast_hour": x.get("forecast_hour"), "region_id": x.get("region_id")}
                    for x in (sorted(
                        missing,
                        key=lambda x: (-float(x.get("maximum_6h_mm") or 0), int(x["forecast_hour"]), str(x["region_id"])),
                    )[worklist_offset:] + sorted(
                        missing,
                        key=lambda x: (-float(x.get("maximum_6h_mm") or 0), int(x["forecast_hour"]), str(x["region_id"])),
                    )[:worklist_offset])[:4]
                ],
            }
            try:
                proposed, proposed_raw, proposed_provider, proposed_meta = call_and_validate_json(
                    client=client,
                    model=model,
                    messages=reconciliation_messages,
                    schema=runtime_provider_schema,
                    schema_name="AIFSPass1CandidateDiscovery",
                    structured_output_mode=structured_output_mode,
                    enable_thinking=enable_thinking,
                    max_tokens=max_tokens,
                    temperature=0.0,
                    top_p=1.0,
                    repair_attempts=min(1, repair_attempts),
                    normalizer=lambda item: _canonicalize_candidate_discovery_coverage_reconciliation_preservation(
                        before_reconciliation,
                        normalize_candidate_discovery_contract(
                            item,
                            package_id=package_id,
                            attachment_count=int(attachment_manifest.get("attachment_count", 0)),
                            attachment_manifest=attachment_manifest,
                            stage_schema=runtime_provider_schema,
                            group_summaries=group_summaries,
                            overview_data=overview_data,
                            support_catalog=support_catalog,
                        ),
                        attachment_manifest=attachment_manifest,
                    ),
                    fixed_field_expectations=lambda item: {
                        "package_id": package_id,
                        "analysis_coverage.coverage_status": "complete",
                        "analysis_coverage.overview_attachment_count": int(attachment_manifest.get("attachment_count", 0)),
                        "run_summary.candidate_count": len(item.get("candidates", []))
                        if isinstance(item.get("candidates"), list) else 0,
                        "pass2_required": bool(item.get("candidates"))
                        if isinstance(item.get("candidates"), list) else False,
                    },
                    semantic_validator=reconciliation_validator,
                    repair_context_messages=reconciliation_messages,
                )
                # Account for every provider call even if the proposal is rejected.
                for key in ("http_retry_count", "repair_attempt_count", "structured_output_fallback_count"):
                    call_meta[key] = int(call_meta.get(key, 0)) + int(proposed_meta.get(key, 0))
                previous_usage = call_meta.get("usage") if isinstance(call_meta.get("usage"), dict) else {}
                attempt_usage = proposed_meta.get("usage") if isinstance(proposed_meta.get("usage"), dict) else {}
                call_meta["usage"] = _summarize_usage_attempts([
                    *list(previous_usage.get("attempts", [])),
                    *list(attempt_usage.get("attempts", [])),
                ])
                proposed, proposed_qc = _apply_candidate_discovery_meteorological_qc(
                    proposed, overview_data=overview_data
                )
                post_errors = reconciliation_validator(proposed)
                if post_errors:
                    raise Pass1Error("reconciled candidate QC invalid: " + " | ".join(post_errors[:5]))
                proposed_audit = build_system_direct_precipitation_coverage_audit(
                    proposed,
                    overview_data=overview_data,
                    attachment_manifest=attachment_manifest,
                    support_catalog=support_catalog,
                )
                progress = _direct_precipitation_reconciliation_progress(working_audit, proposed_audit)
                entry.update(progress)
                entry["candidate_count_after"] = len(proposed.get("candidates", []))
                if not progress["accept"]:
                    entry["status"] = "rejected"
                    entry["reason"] = "coverage_regressed" if progress["regressed"] else "no_new_required_signal_covered"
                else:
                    entry["status"] = "accepted"
                    working_value, working_audit = proposed, proposed_audit
                    value, raw, provider, meteorological_qc = proposed, proposed_raw, proposed_provider, proposed_qc
                    # Working state is diagnostic only; never a validated checkpoint.
                    write_json(working_path, working_value)
            except Exception as exc:  # noqa: BLE001
                entry["status"] = "rejected"
                entry["reason"] = sanitize_failure_text(str(exc))[:1200]
            rejected_in_row = rejected_in_row + 1 if entry["status"] == "rejected" else 0
            history.append(entry)
            call_meta["coverage_reconciliation_attempt_count"] = len(history)
            write_json(coverage_reconciliation_path, {
                "contract_version": DIRECT_PRECIPITATION_COVERAGE_RECONCILIATION_VERSION,
                "status": "running",
                "initial_required_uncovered_count": original_count,
                "current_required_uncovered_count": int(working_audit["required_uncovered_count"]),
                "candidate_count_before": initial_candidate_count,
                "candidate_count_current": len(working_value.get("candidates", [])),
                "rounds": history,
                "created_at_utc": now_utc(),
            })
            logging.info(
                "STEP04_COVERAGE_INCREMENTAL attempt=%s/%s status=%s uncovered_before=%s uncovered_after=%s new=%s regressed=%s",
                attempt, max_reconciliation_rounds, entry["status"],
                entry["before_required_uncovered_count"], entry.get("after", "invalid"),
                len(entry.get("newly_covered", [])), len(entry.get("regressed", [])),
            )
            if int(working_audit.get("required_uncovered_count", 0)) == 0:
                break
            if rejected_in_row >= 3:
                logging.warning(
                    "STEP04_COVERAGE_INCREMENTAL_NONPROGRESS attempts=%s remaining=%s",
                    rejected_in_row, working_audit["required_uncovered_count"],
                )
                break
        if int(working_audit.get("required_uncovered_count", 0)) != 0:
            # Persist the LAST accepted baseline, not the initial audit.  In
            # operational mode the temporary workspace is removed on failure;
            # the Step04 wrapper publishes these diagnostics separately.
            persist_system_precipitation_audit(working_value)
            remaining_rows = _required_uncovered_system_direct_precipitation_signals(working_audit)
            write_json(coverage_reconciliation_path, {
                "contract_version": DIRECT_PRECIPITATION_COVERAGE_RECONCILIATION_VERSION,
                "status": "failed",
                "initial_required_uncovered_count": original_count,
                "final_required_uncovered_count": int(working_audit["required_uncovered_count"]),
                "remaining_signals": [
                    {"forecast_hour": row["forecast_hour"], "region_id": row["region_id"],
                     "region_role": row.get("region_role"),
                     "maximum_6h_mm": row.get("maximum_6h_mm"),
                     "candidate_match_diagnostics": row.get("candidate_match_diagnostics", [])}
                    for row in remaining_rows
                ],
                "rounds": history,
                "created_at_utc": now_utc(),
            })
            remaining_keys = [
                "F{:03d}:{}".format(int(row["forecast_hour"]), row["region_id"])
                for row in remaining_rows
            ]
            raise Pass1Error(
                "SYSTEM_DIRECT_PRECIPITATION_COVERAGE_REQUIRED incremental reconciliation exhausted "
                f"attempts={len(history)} remaining={working_audit['required_uncovered_count']} "
                f"remaining_keys={remaining_keys}; audit={coverage_reconciliation_path}"
            )
        write_json(coverage_reconciliation_path, {
            "contract_version": DIRECT_PRECIPITATION_COVERAGE_RECONCILIATION_VERSION,
            "status": "completed",
            "initial_required_uncovered_count": original_count,
            "final_required_uncovered_count": 0,
            "candidate_count_before": initial_candidate_count,
            "candidate_count_after": len(value.get("candidates", [])),
            "rounds": history,
            "created_at_utc": now_utc(),
        })
        logging.info(
            "STEP04_COVERAGE_INCREMENTAL_OK initial_uncovered=%s rounds=%s candidates=%s",
            original_count, len(history), len(value.get("candidates", [])),
        )

    final_errors = validator(value)
    if final_errors:
        raise Pass1Error(
            "candidate discovery final validation failed: " + " | ".join(final_errors[:10])
        )
    system_precipitation_audit = persist_system_precipitation_audit(value)
    elapsed = round(time.monotonic() - started, 3)
    if output_policy.should_write("provider_raw"):
        atomic_write_text(raw_path, raw + "\n")
    write_json(value_path, value)
    if output_policy.should_write("provider_metadata"):
        write_json(provider_path, {**provider, **call_meta, "elapsed_seconds": elapsed})
    retry_summary = checkpoint_retry_summary(call_meta)
    retry_summary["candidate_discovery_incomplete_retry_attempt_count"] = int(
        call_meta.get("candidate_discovery_incomplete_retry_attempt_count", 0)
    )
    retry_summary["coverage_reconciliation_attempt_count"] = int(
        call_meta.get("coverage_reconciliation_attempt_count", 0)
    )
    write_json(
        checkpoint_path,
        {
            **checkpoint,
            "value_sha256": sha256sum(value_path),
            "retry_summary": retry_summary,
            "usage": checkpoint_usage_summary(call_meta),
            "meteorological_qc": {
                "version": CANDIDATE_DISCOVERY_METEOROLOGICAL_QC_VERSION,
                "candidate_count": len(meteorological_qc),
                "candidate_ids": sorted(meteorological_qc),
            },
            "system_direct_precipitation_coverage_audit": {
                "version": SYSTEM_DIRECT_PRECIPITATION_COVERAGE_AUDIT_VERSION,
                "signal_count": int(system_precipitation_audit.get("signal_count", 0)),
                "required_uncovered_count": int(system_precipitation_audit.get("required_uncovered_count", 0)),
                "advisory_uncovered_count": int(system_precipitation_audit.get("advisory_uncovered_count", 0)),
                "audit_ref": rel(root, system_precipitation_audit_path),
            },
            "system_direct_precipitation_coverage_reconciliation": {
                "version": DIRECT_PRECIPITATION_COVERAGE_RECONCILIATION_VERSION,
                "attempt_count": int(call_meta.get("coverage_reconciliation_attempt_count", 0)),
                **(
                    {"reconciliation_ref": rel(root, coverage_reconciliation_path)}
                    if coverage_reconciliation_path.is_file()
                    else {}
                ),
            },
            "created_at_utc": now_utc(),
        },
    )
    report = {
        "status": "completed",
        "validated_ref": rel(root, value_path),
        "checkpoint_ref": rel(root, checkpoint_path),
        "candidate_count": len(value.get("candidates", [])),
        "meteorological_qc_candidate_count": len(meteorological_qc),
        "system_direct_precipitation_coverage_audit_ref": rel(root, system_precipitation_audit_path),
        "system_direct_precipitation_required_uncovered_count": int(system_precipitation_audit.get("required_uncovered_count", 0)),
        "system_direct_precipitation_advisory_uncovered_count": int(system_precipitation_audit.get("advisory_uncovered_count", 0)),
        "elapsed_seconds": elapsed,
        "candidate_discovery_incomplete_retry_attempt_count": int(
            call_meta.get("candidate_discovery_incomplete_retry_attempt_count", 0)
        ),
        "coverage_reconciliation_attempt_count": int(
            call_meta.get("coverage_reconciliation_attempt_count", 0)
        ),
        **(
            {"system_direct_precipitation_coverage_reconciliation_ref": rel(root, coverage_reconciliation_path)}
            if coverage_reconciliation_path.is_file()
            else {}
        ),
        **report_call_metadata(call_meta, output_policy),
    }
    if output_policy.should_write("provider_metadata"):
        report["provider_ref"] = rel(root, provider_path)
        report["usage"] = call_meta.get("usage")
    logging.info(
        "step05 candidate discovery completed candidates=%s elapsed=%.3fs "
        "http_retry_count=%s json_repair_count=%s structured_output_fallback_count=%s coverage_reconciliation_attempt_count=%s",
        report["candidate_count"],
        elapsed,
        call_meta.get("http_retry_count", 0),
        call_meta.get("repair_attempt_count", 0),
        call_meta.get("structured_output_fallback_count", 0),
        call_meta.get("coverage_reconciliation_attempt_count", 0),
    )
    if checkpoint_publisher is not None:
        checkpoint_publisher("candidate_discovery", "")
    return value, report, diagnostics



def _next_system_anchor_check_id(plan: dict[str, Any], *, candidate_number: str) -> str:
    used_numbers: set[int] = set()
    for check in plan.get("variable_checks", []):
        if not isinstance(check, dict):
            continue
        match = re.fullmatch(
            rf"CHK_CAND{candidate_number}_(\d{{2}})",
            str(check.get("check_id") or ""),
        )
        if match is not None:
            used_numbers.add(int(match.group(1)))
    for number in range(1, 100):
        if number not in used_numbers:
            return f"CHK_CAND{candidate_number}_{number:02d}"
    raise ValueError(
        f"CAND_{candidate_number} has no free canonical check ID "
        "for the system direct-precipitation anchor"
    )


def _system_direct_precipitation_anchor_text(
    candidate_peak_hours: list[int],
) -> tuple[str, str, str]:
    hour_text = ", ".join(f"F{hour:03d}" for hour in candidate_peak_hours)
    threshold = f"{DIRECT_PRECIPITATION_MINIMUM_MM_6H:g}"
    expected_signal = (
        f"{hour_text} 중 하나 이상의 후보 핵심시각에서 후보영역과 겹치는 운영 예보권역(육상·해상)에 "
        f"6시간 강수량 {threshold} mm 이상인 식별 가능한 모델 강수영역이 나타난다."
        if hour_text
        else f"후보 핵심 시각 중 하나 이상에서 후보영역과 겹치는 운영 예보권역(육상·해상)에 6시간 강수량 {threshold} mm 이상인 식별 가능한 모델 강수영역이 나타난다."
    )
    decision_relevance = (
        "후보 핵심시각의 직접 모델 강수 신호가 후보영역과 겹치는 운영 예보권역의 상세 6시간 강수장에서 "
        f"운영상 최소기준인 6시간 강수량 {threshold} mm 이상으로 실제 확인되는지 독립적으로 판정합니다."
    )
    purpose = (
        "모델 강수 예측 존재 사실을 이동·연속성·조직화·강도 변화와 분리하여 검증합니다. "
        "북한·일본·중국 등 운영 예보권역 밖의 강수는 환경 및 이동 맥락에는 사용할 수 있지만 이 직접강수 존재 판정에는 사용하지 않습니다. "
        f"운영 예보권역에서 6시간 강수량 {threshold} mm 미만인 강수는 모델 강수 예측 확인 근거로 사용하지 않습니다."
    )
    return expected_signal, decision_relevance, purpose


def _demote_non_system_direct_precipitation_anchor_flags(
    plan: dict[str, Any],
) -> list[str]:
    """Remove legacy AI-owned anchor flags before adding the system invariant."""
    demoted: list[str] = []
    for check in plan.get("variable_checks", []):
        if not isinstance(check, dict):
            continue
        intent = check.get("intent_contract")
        if (
            not isinstance(intent, dict)
            or intent.get("direct_precipitation_anchor") is not True
        ):
            continue
        if (
            str(intent.get("source_check_key") or "")
            == SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY
        ):
            continue
        intent["direct_precipitation_anchor"] = False
        source_intents = intent.get("source_intents")
        if isinstance(source_intents, list):
            for source_intent in source_intents:
                if isinstance(source_intent, dict):
                    source_intent["direct_precipitation_anchor"] = False
        check["intent_hash"] = canonical_hash(intent)
        demoted.append(str(check.get("check_id") or ""))
    return demoted


def ensure_system_direct_precipitation_anchor(
    value: dict[str, Any],
    *,
    candidate: dict[str, Any],
    support_catalog: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Add the mandatory system-owned direct-precipitation presence Check.

    The AI owns candidate discovery and meteorological verification hypotheses.
    Python owns this one invariant for every candidate type: independently verify
    whether a candidate-relevant 6-hour precipitation signal is present at the
    candidate core hours that the AI already selected.  For precursor candidates
    those hours remain precursor-overlap representative hours; the SYSTEM Check
    does not reinterpret them as precipitation peaks.

    This function does not change detection_basis or select a new area, time, peak,
    intensity threshold, motion, continuity, organization, or intensity comparison.
    """
    if str(candidate.get("detection_basis") or "") not in {
        "direct_precipitation",
        "precursor_environment",
        "mixed",
    }:
        return value, None
    if not isinstance(value, dict):
        raise ValueError(
            "candidate plan response must be an object before system anchor insertion"
        )

    normalized = copy.deepcopy(value)
    plan = normalized.get("candidate_analysis_plan")
    requests = normalized.get("analysis_data_requests")
    if not isinstance(plan, dict) or not isinstance(requests, list):
        raise ValueError(
            "candidate plan is missing canonical plan or analysis_data_requests"
        )

    candidate_id = str(
        candidate.get("candidate_id") or normalized.get("candidate_id") or ""
    )
    match = re.fullmatch(r"CAND_(\d{3})", candidate_id)
    if match is None:
        raise ValueError(
            "candidate_id is not canonical for the system direct-precipitation "
            f"anchor: {candidate_id!r}"
        )
    candidate_number = match.group(1)

    demoted = _demote_non_system_direct_precipitation_anchor_flags(plan)
    existing_system_anchors = [
        check
        for check in plan.get("variable_checks", [])
        if isinstance(check, dict)
        and _is_system_direct_precipitation_anchor_check(check)
    ]
    if len(existing_system_anchors) > 1:
        raise ValueError(
            f"{candidate_id} contains duplicate system direct-precipitation "
            f"anchors: {[check.get('check_id') for check in existing_system_anchors]}"
        )
    if len(existing_system_anchors) == 1:
        existing_errors = _system_direct_precipitation_anchor_errors(
            normalized,
            candidate=candidate,
        )
        if existing_errors:
            raise ValueError(" | ".join(existing_errors))
        return normalized, {
            "action": "reused",
            "check_id": existing_system_anchors[0].get("check_id"),
            "demoted_legacy_anchor_check_ids": demoted,
        }

    raw_peak_hours = candidate.get("peak_forecast_hours")
    if not isinstance(raw_peak_hours, list):
        raise ValueError(
            f"{candidate_id} candidate has no peak_forecast_hours"
        )
    candidate_peak_hours = sorted(
        {
            int(hour)
            for hour in raw_peak_hours
            if isinstance(hour, int) and not isinstance(hour, bool)
        }
    )
    if not candidate_peak_hours:
        raise ValueError(
            f"{candidate_id} candidate has empty peak_forecast_hours"
        )
    peak_hour_set = set(candidate_peak_hours)

    phase_rows = [
        item
        for item in plan.get("analysis_phases", [])
        if isinstance(item, dict)
    ]
    selected_peak_phases = [
        phase
        for phase in phase_rows
        if str(phase.get("phase_type") or "") == "peak"
        and peak_hour_set.intersection(
            {
                int(hour)
                for hour in phase.get("forecast_hours", [])
                if isinstance(hour, int) and not isinstance(hour, bool)
            }
        )
    ]
    selected_phase_ids = [
        str(phase.get("phase_id"))
        for phase in selected_peak_phases
        if str(phase.get("phase_id") or "")
    ]
    covered_peak_hours = {
        int(hour)
        for phase in selected_peak_phases
        for hour in phase.get("forecast_hours", [])
        if isinstance(hour, int) and not isinstance(hour, bool)
    }
    missing_peak_hours = sorted(peak_hour_set - covered_peak_hours)
    if not selected_phase_ids or missing_peak_hours:
        raise ValueError(
            f"{candidate_id} system direct-precipitation anchor cannot resolve "
            f"candidate peak phases; missing_peak_forecast_hours={missing_peak_hours}"
        )

    candidate_targets = [
        target
        for target in plan.get("target_areas", [])
        if isinstance(target, dict)
        and str(target.get("target_type") or "") == "candidate_domain"
    ]
    if len(candidate_targets) != 1:
        raise ValueError(
            f"{candidate_id} system direct-precipitation anchor requires "
            "exactly one candidate_domain target"
        )
    candidate_target = candidate_targets[0]
    target_id = str(candidate_target.get("target_id") or "")
    candidate_bbox = _normalized_bbox_for_validation(
        candidate_target.get("requested_domain")
    )
    if not target_id or candidate_bbox is None:
        raise ValueError(
            f"{candidate_id} candidate_domain target is incomplete for system anchor"
        )

    check_id = _next_system_anchor_check_id(
        plan,
        candidate_number=candidate_number,
    )
    diagnostic_id = "detailed_spatial_map"
    variable_role = "hazard_magnitude"
    required_signal_components = ["direct_precipitation"]
    product_id = _preferred_product_id_for_check(
        diagnostic_id,
        variable_role,
        required_signal_components,
        support_catalog,
        candidate,
    )
    if not product_id:
        raise ValueError(
            f"{candidate_id} cannot resolve direct-precipitation spatial product "
            "for system anchor"
        )
    product_components = _product_signal_components_from_support(
        product_id,
        support_catalog,
    )
    if (
        product_components
        and "direct_precipitation" not in set(product_components)
    ):
        raise ValueError(
            f"{candidate_id} system anchor product_id={product_id!r} does not "
            "provide direct_precipitation"
        )

    expected_signal, decision_relevance, purpose = (
        _system_direct_precipitation_anchor_text(candidate_peak_hours)
    )
    system_row = {
        "check_key": SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY,
        "verification_mode": "spatial_distribution",
        "variable_role": variable_role,
        "diagnostic_id": diagnostic_id,
        "domain_scope": "candidate",
        "spatial_relation_scope": "candidate_interior",
        # System-owned source phases use canonical IDs because there is no
        # provider-local phase namespace for this injected Check.
        "phase_keys": list(selected_phase_ids),
        "representative_spatial_hours": [],
        "analysis_methods": ["detailed_map"],
        "expected_signal": expected_signal,
        "decision_relevance": decision_relevance,
        "priority": "required",
        "evidence_role": "supporting",
        "coverage_roles": ["hazard_magnitude", "supporting_evidence"],
        "purpose": purpose,
        "atomic_claims": [
            {
                "claim_key": SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CLAIM_KEY,
                "claim_statement": expected_signal,
                "claim_components": ["direct_precipitation"],
                "phase_keys": list(selected_phase_ids),
                "expected_state": "present",
                "scope_quantifier": "at_least_one",
                "scope_description": "지정된 후보 핵심시각과 후보영역∩운영 예보권역(육상·해상)",
            }
        ],
        "cross_section_line": None,
        "cross_section_strategy": None,
        "reference_band_axis": None,
        "check_scope": "single_variable",
        "required_signal_components": required_signal_components,
        "_system_direct_precipitation_anchor": True,
    }

    intent_contract = _build_check_intent_contract(
        system_row,
        check_id=check_id,
        phase_ids=list(selected_phase_ids),
    )
    atomic_claim_contract = _build_atomic_claim_contract(
        system_row,
        check_id=check_id,
    )
    capability_profile = _capability_profile_for_check(
        support_catalog,
        diagnostic_id=diagnostic_id,
        product_id=product_id,
    )
    system_check = {
        "check_id": check_id,
        "variable_role": variable_role,
        "required_signal_components": required_signal_components,
        "check_scope": "single_variable",
        "diagnostic_id": diagnostic_id,
        "spatial_relation_scope": "candidate_interior",
        "product_id": product_id,
        "feature_ids": [],
        "target_ids": [target_id],
        "phase_ids": list(selected_phase_ids),
        "analysis_methods": ["detailed_map"],
        "expected_signal": expected_signal,
        "decision_relevance": decision_relevance,
        "priority": "required",
        "cross_section_line": None,
        "cross_section_strategy": None,
        "reference_band_axis": None,
        "intent_contract": intent_contract,
        "intent_hash": canonical_hash(intent_contract),
        "atomic_claim_contract": atomic_claim_contract,
        "atomic_claim_hash": canonical_hash(atomic_claim_contract),
        "atomic_claim_validation": _shadow_validate_atomic_claim_contract(
            check_id=check_id,
            row=system_row,
            contract=atomic_claim_contract,
        ),
        "capability_profile": capability_profile,
        "capability_validation": _shadow_validate_check_capability(
            check_id=check_id,
            intent_contract=intent_contract,
            capability_profile=capability_profile,
        ),
    }
    plan.setdefault("variable_checks", []).append(system_check)

    system_request = {
        # Temporary ID. _merge_analysis_data_requests rewrites canonical IDs.
        "request_id": f"REQ_CAND{candidate_number}_999",
        "candidate_id": candidate_id,
        "plan_id": str(plan.get("plan_id") or f"PLAN_CAND{candidate_number}"),
        "diagnostic_id": diagnostic_id,
        "data_type": "spatial_field",
        "product_id": product_id,
        "feature_ids": [],
        "forecast_hours": list(candidate_peak_hours),
        "check_id": check_id,
        "source_check_ids": [check_id],
        "target_id": target_id,
        "phase_ids": list(selected_phase_ids),
        "analysis_methods": ["detailed_map"],
        "purpose": purpose,
        "priority": "required",
        "requested_domain": candidate_bbox,
        "cross_section_line": None,
        "cross_section_strategy": None,
        "reference_band_axis": None,
    }
    request_count_before = len(requests)
    normalized["analysis_data_requests"] = _merge_analysis_data_requests(
        [*requests, system_request],
        candidate_number=candidate_number,
        non_mergeable_check_ids={check_id},
    )
    request_reused = (
        len(normalized["analysis_data_requests"]) == request_count_before
    )

    logging.info(
        "STEP04_SYSTEM_DIRECT_PRECIPITATION_ANCHOR "
        "candidate=%s check_id=%s peak_hours=%s product_id=%s "
        "request_asset_reused=%s demoted_legacy=%s",
        candidate_id,
        check_id,
        candidate_peak_hours,
        product_id,
        request_reused,
        demoted,
    )
    return normalized, {
        "action": "added",
        "check_id": check_id,
        "peak_forecast_hours": candidate_peak_hours,
        "product_id": product_id,
        "request_asset_reused": request_reused,
        "demoted_legacy_anchor_check_ids": demoted,
    }


def _system_direct_precipitation_anchor_errors(
    value: dict[str, Any],
    *,
    candidate: dict[str, Any],
) -> list[str]:
    """Validate the complete system-owned Check -> request invariant."""
    if str(candidate.get("detection_basis") or "") not in {
        "direct_precipitation",
        "precursor_environment",
        "mixed",
    }:
        return []
    candidate_id = str(candidate.get("candidate_id") or "")
    plan = value.get("candidate_analysis_plan")
    requests = value.get("analysis_data_requests")
    if not isinstance(plan, dict):
        return [
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID candidate plan is missing"
        ]
    if not isinstance(requests, list):
        return [
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID analysis_data_requests is missing"
        ]

    errors: list[str] = []
    anchors = [
        check
        for check in plan.get("variable_checks", [])
        if isinstance(check, dict)
        and _is_system_direct_precipitation_anchor_check(check)
    ]
    if len(anchors) != 1:
        return [
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID "
            f"candidate_id={candidate_id} expected=1 actual={len(anchors)}"
        ]
    anchor = anchors[0]
    anchor_id = str(anchor.get("check_id") or "")
    intent = anchor.get("intent_contract")
    intent = intent if isinstance(intent, dict) else {}
    claims = anchor.get("atomic_claim_contract", {}).get("canonical_claims")
    if not isinstance(claims, list):
        claims = []

    structural_expectations = {
        "required_signal_components": ["direct_precipitation"],
        "check_scope": "single_variable",
        "diagnostic_id": "detailed_spatial_map",
        "product_id": "QL_PRECIP_6H",
        "priority": "required",
    }
    for field, expected in structural_expectations.items():
        if anchor.get(field) != expected:
            errors.append(
                "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID "
                f"candidate_id={candidate_id} check_id={anchor_id} "
                f"field={field} expected={expected!r} actual={anchor.get(field)!r}"
            )
    for field, expected in (
        ("direct_precipitation_anchor", True),
        ("source_check_key", SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY),
        ("signal_components", ["direct_precipitation"]),
        ("verification_mode", "spatial_distribution"),
        ("domain_scope", "candidate"),
        ("priority", "required"),
        ("evidence_role", "supporting"),
    ):
        if intent.get(field) != expected:
            errors.append(
                "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID "
                f"candidate_id={candidate_id} check_id={anchor_id} "
                f"intent.{field} expected={expected!r} actual={intent.get(field)!r}"
            )
    if len(claims) != 1:
        errors.append(
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID "
            f"candidate_id={candidate_id} check_id={anchor_id} "
            f"atomic_claim_count expected=1 actual={len(claims)}"
        )
    else:
        claim = claims[0]
        for field, expected in (
            ("claim_components", ["direct_precipitation"]),
            ("expected_state", "present"),
            ("scope_quantifier", "at_least_one"),
        ):
            if claim.get(field) != expected:
                errors.append(
                    "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID "
                    f"candidate_id={candidate_id} check_id={anchor_id} "
                    f"claim.{field} expected={expected!r} actual={claim.get(field)!r}"
                )

    raw_peak_hours = candidate.get("peak_forecast_hours")
    expected_hours = sorted(
        {
            int(value)
            for value in raw_peak_hours
            if isinstance(value, int) and not isinstance(value, bool)
        }
    ) if isinstance(raw_peak_hours, list) else []
    expected_domain = _normalized_bbox_for_validation(candidate.get("candidate_area"))
    linked_requests = []
    for request in requests:
        if not isinstance(request, dict):
            continue
        raw_source_ids = request.get("source_check_ids")
        source_ids = (
            [str(value) for value in raw_source_ids if str(value)]
            if isinstance(raw_source_ids, list)
            else [str(request.get("check_id") or "")]
        )
        if anchor_id in source_ids:
            linked_requests.append((request, source_ids))
    if len(linked_requests) != 1:
        errors.append(
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID "
            f"candidate_id={candidate_id} check_id={anchor_id} "
            f"anchor_request_count expected=1 actual={len(linked_requests)}"
        )
        return errors

    request, source_ids = linked_requests[0]
    request_id = str(request.get("request_id") or "")
    request_expectations = {
        "source_check_ids": [anchor_id],
        "check_id": anchor_id,
        "data_type": "spatial_field",
        "diagnostic_id": "detailed_spatial_map",
        "product_id": "QL_PRECIP_6H",
        "priority": "required",
    }
    actual_values = {
        "source_check_ids": source_ids,
        **{field: request.get(field) for field in request_expectations if field != "source_check_ids"},
    }
    for field, expected in request_expectations.items():
        if actual_values[field] != expected:
            errors.append(
                "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID "
                f"candidate_id={candidate_id} request_id={request_id} "
                f"field={field} expected={expected!r} actual={actual_values[field]!r}"
            )
    actual_hours = sorted(
        {
            int(value)
            for value in request.get("forecast_hours", [])
            if isinstance(value, int) and not isinstance(value, bool)
        }
    )
    if actual_hours != expected_hours:
        errors.append(
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID "
            f"candidate_id={candidate_id} request_id={request_id} "
            f"forecast_hours expected={expected_hours} actual={actual_hours}"
        )
    actual_domain = _normalized_bbox_for_validation(request.get("requested_domain"))
    if expected_domain is None or actual_domain != expected_domain:
        errors.append(
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID "
            f"candidate_id={candidate_id} request_id={request_id} "
            f"requested_domain expected={expected_domain!r} actual={actual_domain!r}"
        )
    return errors


def system_direct_precipitation_anchor_final_errors(
    response: dict[str, Any],
) -> list[str]:
    """Validate SYSTEM Anchor invariants on a completed Step04 document."""
    candidates = [
        item for item in response.get("candidates", []) if isinstance(item, dict)
    ]
    plans = {
        str(item.get("candidate_id") or ""): item
        for item in response.get("candidate_analysis_plans", [])
        if isinstance(item, dict)
    }
    requests = [
        item for item in response.get("analysis_data_requests", []) if isinstance(item, dict)
    ]
    errors: list[str] = []
    for candidate in candidates:
        if str(candidate.get("detection_basis") or "") not in {
            "direct_precipitation",
            "precursor_environment",
            "mixed",
        }:
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        plan = plans.get(candidate_id)
        candidate_requests = [
            request for request in requests if str(request.get("candidate_id") or "") == candidate_id
        ]
        errors.extend(
            _system_direct_precipitation_anchor_errors(
                {
                    "candidate_analysis_plan": plan,
                    "analysis_data_requests": candidate_requests,
                },
                candidate=candidate,
            )
        )
    return errors



def create_or_resume_candidate_plans(
    *,
    root: Path,
    output_dir: Path,
    package_id: str,
    discovery_response: dict[str, Any],
    group_summaries: list[dict[str, Any]],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    feature_catalog: dict[str, Any],
    operational_region_catalog: dict[str, Any],
    schema: dict[str, Any],
    provider_schema: dict[str, Any] | None = None,
    final_schema: dict[str, Any],
    prompt_template: str,
    prompt_sha256: str,
    client: Pass1ProviderClient,
    model: str,
    structured_output_mode: str,
    enable_thinking: bool,
    max_tokens: int,
    temperature: float,
    top_p: float,
    repair_attempts: int,
    request_gap_seconds: float,
    resume: bool,
    candidate_ids: set[str] | None = None,
    compact_continuation_attempts: int = 4,
    output_policy: OutputPolicy | None = None,
    checkpoint_publisher: Callable[[str, str], None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, int]]]:
    output_policy = output_policy or OutputPolicy("operational")
    provider_schema = provider_schema or schema
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = [item for item in discovery_response.get("candidates", []) if isinstance(item, dict)]
    selected_candidate_ids = set(candidate_ids or ())
    if selected_candidate_ids:
        available_candidate_ids = {str(item.get("candidate_id")) for item in candidates}
        unknown_candidate_ids = sorted(selected_candidate_ids - available_candidate_ids)
        if unknown_candidate_ids:
            raise Pass1Error(f"candidate plan selection references unknown candidate_ids={unknown_candidate_ids}")
        candidates = [
            item for item in candidates if str(item.get("candidate_id")) in selected_candidate_ids
        ]
    use_compact_openai_plan = client.provider.api_family == "responses"
    runtime_provider_schema = provider_schema
    if use_compact_openai_plan:
        runtime_provider_schema = client.transport_schemas.get("candidate_plan")
        if not isinstance(runtime_provider_schema, dict):
            raise Pass1Error("OpenAI compact candidate-plan transport schema is unavailable")
    results: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    diagnostics_rows: list[dict[str, int]] = []
    for index, candidate in enumerate(candidates, start=1):
        candidate_id = str(candidate["candidate_id"])
        candidate_provider_schema = runtime_provider_schema
        candidate_operational_overlap = _candidate_operational_region_overlap(
            candidate=candidate,
            operational_region_catalog=operational_region_catalog,
        )
        if use_compact_openai_plan:
            candidate_provider_schema = _candidate_plan_transport_schema_for_operational_scope(
                candidate_provider_schema,
                candidate=candidate,
                operational_region_catalog=operational_region_catalog,
            )
        if use_compact_openai_plan:
            temporal_feasibility = _candidate_temporal_feasibility_contract(
                candidate, attachment_manifest, support_catalog=support_catalog
            )
            logging.info(
                "STEP04_TIME_FEASIBILITY candidate=%s requested=%s effective=%s added=%s source_assets=%s",
                candidate_id,
                temporal_feasibility["requested_hours"],
                temporal_feasibility["effective_hours"],
                temporal_feasibility["added_hours"],
                temporal_feasibility["source_asset_ids"],
            )
            if not temporal_feasibility["temporal_comparison_available"]:
                raise CandidatePlanError(
                    candidate_id=candidate_id,
                    cause=ValueError(
                        "STEP04_TEMPORAL_EVIDENCE_UNAVAILABLE "
                        f"candidate_id={candidate_id} "
                        f"requested_hours={temporal_feasibility['requested_hours']} "
                        f"effective_hours={temporal_feasibility['effective_hours']}; "
                        "temporal/spatial evolution requires two observed forecast hours "
                        "in a related overview asset; do not invent hours or retry an impossible plan"
                    ),
                )
        logging.info(
            "STEP04_CANDIDATE_PLAN_SCOPE candidate=%s regional_verification_available=%s "
            "selectable_region_ids=%s allowed_verification_modes=%s",
            candidate_id,
            candidate_operational_overlap.get("regional_verification_available"),
            [
                row.get("region_id")
                for row in candidate_operational_overlap.get("selectable_regions", [])
                if isinstance(row, dict)
            ],
            candidate_operational_overlap.get("allowed_verification_modes"),
        )
        evidence = _candidate_evidence_for(discovery_response, candidate_id)
        related_opportunities = _related_precursor_opportunities(discovery_response, candidate_id)
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", candidate_id)
        raw_path = output_dir / f"{safe_id}.plan.raw.txt"
        value_path = output_dir / f"{safe_id}.plan.validated.json"
        provider_path = output_dir / f"{safe_id}.plan.provider.json"
        checkpoint_path = output_dir / f"{safe_id}.plan.checkpoint.json"
        related_summaries = _related_group_summaries(candidate, group_summaries)
        checkpoint_base = {
                "package_id": package_id,
                "candidate_id": candidate_id,
                "candidate_fingerprint": canonical_hash(candidate),
                "evidence_fingerprint": canonical_hash(evidence),
                "related_group_summaries_fingerprint": canonical_hash(related_summaries),
                "related_precursor_opportunities_fingerprint": canonical_hash(related_opportunities),
                "support_catalog_fingerprint": canonical_hash(_minimal_support_catalog(support_catalog)),
                "feature_catalog_fingerprint": canonical_hash(_minimal_feature_index(feature_catalog)),
                "operational_region_catalog_fingerprint": canonical_hash(
                    _minimal_operational_region_index(operational_region_catalog)
                ),
            "prompt_sha256": prompt_sha256,
            "schema_fingerprint": canonical_hash(
                {"canonical": schema, "provider": candidate_provider_schema}
            ),
            "canonical_schema_sha256": canonical_hash(schema),
                "provider_configuration": _client_checkpoint_configuration(
                    client,
                    stage="candidate_plan",
                ),
                "model": model,
                "contract_version": SPLIT_SYNTHESIS_CONTRACT_VERSION,
                "system_direct_precipitation_anchor_execution_contract_version": SYSTEM_DIRECT_PRECIPITATION_ANCHOR_EXECUTION_CONTRACT_VERSION,
                "narrative_language": NARRATIVE_LANGUAGE,
        }
        if use_compact_openai_plan:
            if temporal_feasibility["added_hours"]:
                # Only modified one-hour candidates invalidate an existing
                # candidate-plan checkpoint; unrelated candidates keep theirs.
                checkpoint_base["candidate_temporal_feasibility"] = canonical_hash(
                    temporal_feasibility
                )
            checkpoint_base.update(
                {
                    "compact_candidate_plan_contract_version": COMPACT_CANDIDATE_PLAN_CONTRACT_VERSION,
                    "compact_candidate_plan_normalization_version": COMPACT_CANDIDATE_PLAN_NORMALIZATION_VERSION,
                    "compact_candidate_plan_expander_version": COMPACT_CANDIDATE_PLAN_EXPANDER_VERSION,
                    "compact_candidate_plan_coverage_version": COMPACT_CANDIDATE_PLAN_COVERAGE_VERSION,
                    "max_output_tokens": int(max_tokens),
                    "continuation_attempt_limit": int(compact_continuation_attempts),
                }
            )
        checkpoint = _checkpoint_contract(checkpoint_base)
        pre_anchor_validator = lambda value, _candidate=candidate, _evidence=evidence: validate_candidate_plan_response(
            value,
            schema=schema,
            final_schema=final_schema,
            package_id=package_id,
            candidate=_candidate,
            candidate_evidence=_evidence,
            attachment_manifest=attachment_manifest,
            support_catalog=support_catalog,
            feature_catalog=feature_catalog,
            operational_region_catalog=operational_region_catalog,
            require_system_direct_precipitation_anchor=False,
        )
        post_anchor_validator = lambda value, _candidate=candidate, _evidence=evidence: validate_candidate_plan_response(
            value,
            schema=schema,
            final_schema=final_schema,
            package_id=package_id,
            candidate=_candidate,
            candidate_evidence=_evidence,
            attachment_manifest=attachment_manifest,
            support_catalog=support_catalog,
            feature_catalog=feature_catalog,
            operational_region_catalog=operational_region_catalog,
            require_system_direct_precipitation_anchor=True,
        )
        existing = None
        if resume:
            existing = _load_checkpointed_json(
                value_path=value_path,
                checkpoint_path=checkpoint_path,
                expected_checkpoint=checkpoint,
                provider_stage="candidate_plan",
                validator=post_anchor_validator,
            )
        messages, input_value = build_candidate_plan_messages(
            prompt_template=prompt_template,
            package_id=package_id,
            candidate=candidate,
            candidate_evidence=evidence,
            group_summaries=group_summaries,
            attachment_manifest=attachment_manifest,
            support_catalog=support_catalog,
            feature_catalog=feature_catalog,
            operational_region_catalog=operational_region_catalog,
            related_precursor_opportunities=related_opportunities,
            compact_transport=use_compact_openai_plan,
        )
        if structured_output_mode == "off":
            messages = add_prompt_only_schema_constraint(messages, candidate_provider_schema)
        diagnostics = split_payload_diagnostics(
            model=model,
            messages=messages,
            input_value=input_value,
            response_schema=candidate_provider_schema,
            schema_name="AIFSPass1CandidatePlan",
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            enable_thinking=enable_thinking,
        )
        ensure_split_payload_budget(f"candidate plan {candidate_id}", diagnostics)
        diagnostics_rows.append({"candidate_id": candidate_id, **diagnostics})
        if existing is not None:
            results.append(existing)
            reports.append(
                {
                    "candidate_id": candidate_id,
                    "status": "reused",
                    "validated_ref": rel(root, value_path),
                    "checkpoint_ref": rel(root, checkpoint_path),
                    "request_count": len(existing.get("analysis_data_requests", [])),
                    "elapsed_seconds": 0.0,
                }
            )
            logging.info(
                "step05 candidate plan reused candidate_id=%s requests=%s",
                candidate_id,
                len(existing.get("analysis_data_requests", [])),
            )
            continue

        logging.info(
            "step05 candidate plan start index=%s/%s candidate_id=%s related_groups=%s "
            "http_payload_bytes=%s max_tokens=%s",
            index,
            len(candidates),
            candidate_id,
            len(related_summaries),
            diagnostics["http_json_utf8_bytes"],
            max_tokens,
        )
        started = time.monotonic()
        canonicalization_events: list[dict[str, Any]] = []
        try:
            if use_compact_openai_plan:
                value, provider, call_meta, diagnostics, coverage = create_compact_candidate_plan(
                    package_id=package_id,
                    candidate=candidate,
                    candidate_evidence=evidence,
                    group_summaries=group_summaries,
                    attachment_manifest=attachment_manifest,
                    support_catalog=support_catalog,
                    feature_catalog=feature_catalog,
                    operational_region_catalog=operational_region_catalog,
                    related_precursor_opportunities=related_opportunities,
                    canonical_schema=schema,
                    transport_schema=candidate_provider_schema,
                    final_schema=final_schema,
                    prompt_template=prompt_template,
                    prompt_sha256=prompt_sha256,
                    client=client,
                    model=model,
                    structured_output_mode=structured_output_mode,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    repair_attempts=repair_attempts,
                    continuation_attempts=compact_continuation_attempts,
                )
                raw = ""
                call_meta["coverage"] = coverage
                diagnostics_rows[-1] = {"candidate_id": candidate_id, **diagnostics}
            else:
                value, raw, provider, call_meta = call_and_validate_json(
                    client=client,
                    model=model,
                    messages=messages,
                    schema=provider_schema,
                    schema_name="AIFSPass1CandidatePlan",
                    structured_output_mode=structured_output_mode,
                    enable_thinking=enable_thinking,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    repair_attempts=repair_attempts,
                    normalizer=lambda item, _candidate_id=candidate_id: normalize_candidate_plan_contract(
                        item,
                        package_id=package_id,
                        candidate_id=_candidate_id,
                        stage_schema=provider_schema,
                        support_catalog=support_catalog,
                        canonicalization_events=canonicalization_events,
                    ),
                    fixed_field_expectations={
                        "package_id": package_id,
                        "candidate_id": candidate_id,
                    },
                    semantic_validator=pre_anchor_validator,
                    repair_context_messages=messages,
                )
        except Exception as exc:  # Preserve the checkpoint-safe candidate identity in the failure summary.
            raise CandidatePlanError(candidate_id=candidate_id, cause=exc) from exc

        try:
            value, system_anchor_event = ensure_system_direct_precipitation_anchor(
                value,
                candidate=candidate,
                support_catalog=support_catalog,
            )
            system_anchor_errors = _system_direct_precipitation_anchor_errors(
                value,
                candidate=candidate,
            )
            if system_anchor_errors:
                raise ValueError(" | ".join(system_anchor_errors))
            post_anchor_validation_errors = post_anchor_validator(value)
            if post_anchor_validation_errors:
                raise ValueError(
                    "system direct-precipitation anchor post-validation failed: "
                    + " | ".join(post_anchor_validation_errors[:20])
                )
            if system_anchor_event is not None:
                call_meta["system_direct_precipitation_anchor"] = system_anchor_event
        except Exception as exc:
            raise CandidatePlanError(candidate_id=candidate_id, cause=exc) from exc

        if canonicalization_events:
            call_meta["canonicalized_fields"] = [
                *call_meta.get("canonicalized_fields", []),
                *canonicalization_events,
            ]
        elapsed = round(time.monotonic() - started, 3)
        if output_policy.should_write("provider_raw") and not use_compact_openai_plan:
            atomic_write_text(raw_path, raw + "\n")
        write_json(value_path, value)
        if output_policy.should_write("provider_metadata"):
            write_json(provider_path, {**provider, **call_meta, "elapsed_seconds": elapsed})
        write_json(
            checkpoint_path,
            {
                **checkpoint,
                "value_sha256": sha256sum(value_path),
                "retry_summary": checkpoint_retry_summary(call_meta),
                "repair_history": checkpoint_repair_history(call_meta),
                "usage": checkpoint_usage_summary(call_meta),
                **(
                    {
                        "compact_candidate_plan": {
                            "chunk_count": int(call_meta.get("compact_plan_chunk_count", 0)),
                            "continuation_attempt_count": int(call_meta.get("continuation_attempt_count", 0)),
                            "coverage": call_meta.get("coverage", {}),
                        }
                    }
                    if use_compact_openai_plan
                    else {}
                ),
                "created_at_utc": now_utc(),
            },
        )
        results.append(value)
        reports.append(
            {
                "candidate_id": candidate_id,
                "status": "completed",
                "validated_ref": rel(root, value_path),
                "checkpoint_ref": rel(root, checkpoint_path),
                "request_count": len(value.get("analysis_data_requests", [])),
                "elapsed_seconds": elapsed,
                **report_call_metadata(call_meta, output_policy),
            }
        )
        if output_policy.should_write("provider_metadata"):
            reports[-1]["provider_ref"] = rel(root, provider_path)
            reports[-1]["usage"] = provider.get("usage")
        if use_compact_openai_plan:
            reports[-1]["compact_plan_chunk_count"] = int(
                call_meta.get("compact_plan_chunk_count", 0)
            )
            reports[-1]["continuation_attempt_count"] = int(
                call_meta.get("continuation_attempt_count", 0)
            )
            reports[-1]["coverage"] = call_meta.get("coverage", {})
        logging.info(
            "step05 candidate plan completed candidate_id=%s requests=%s elapsed=%.3fs "
            "http_retry_count=%s json_repair_count=%s structured_output_fallback_count=%s "
            "compact_chunk_count=%s continuation_count=%s",
            candidate_id,
            len(value.get("analysis_data_requests", [])),
            elapsed,
            call_meta.get("http_retry_count", 0),
            call_meta.get("repair_attempt_count", 0),
            call_meta.get("structured_output_fallback_count", 0),
            call_meta.get("compact_plan_chunk_count", 0),
            call_meta.get("continuation_attempt_count", 0),
        )
        if checkpoint_publisher is not None:
            checkpoint_publisher("candidate_plan", candidate_id)
        if request_gap_seconds > 0 and index < len(candidates):
            time.sleep(request_gap_seconds)
    return results, reports, diagnostics_rows


def merge_split_pass1_response(
    discovery_response: dict[str, Any],
    candidate_plan_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Deterministically combine validated discovery and per-candidate plans."""
    candidates = [item for item in discovery_response.get("candidates", []) if isinstance(item, dict)]
    result_by_candidate: dict[str, dict[str, Any]] = {}
    for result in candidate_plan_results:
        candidate_id = str(result.get("candidate_id"))
        if candidate_id in result_by_candidate:
            raise Pass1Error(f"duplicate candidate plan result: {candidate_id}")
        result_by_candidate[candidate_id] = result
    expected_ids = [str(candidate.get("candidate_id")) for candidate in candidates]
    missing = [candidate_id for candidate_id in expected_ids if candidate_id not in result_by_candidate]
    extra = sorted(set(result_by_candidate) - set(expected_ids))
    if missing or extra:
        raise Pass1Error(f"candidate plan coverage mismatch missing={missing} extra={extra}")

    plans: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    for candidate_id in expected_ids:
        result = result_by_candidate[candidate_id]
        plan = result.get("candidate_analysis_plan")
        if not isinstance(plan, dict):
            raise Pass1Error(f"candidate plan result is invalid: {candidate_id}")
        plans.append(plan)
        candidate_requests = result.get("analysis_data_requests", [])
        if not isinstance(candidate_requests, list):
            raise Pass1Error(f"candidate requests are invalid: {candidate_id}")
        requests.extend(candidate_requests)

    return {
        "package_id": discovery_response.get("package_id"),
        "analysis_coverage": discovery_response.get("analysis_coverage"),
        "run_summary": discovery_response.get("run_summary"),
        "candidates": discovery_response.get("candidates", []),
        "candidate_evidence": discovery_response.get("candidate_evidence", []),
        **(
            {"precursor_opportunities": discovery_response["precursor_opportunities"]}
            if "precursor_opportunities" in discovery_response
            else {}
        ),
        "candidate_analysis_plans": plans,
        "analysis_data_requests": requests,
        "global_uncertainties": discovery_response.get("global_uncertainties", []),
        "precursor_candidate_assessment": discovery_response.get(
            "precursor_candidate_assessment"
        ),
        "pass2_required": discovery_response.get("pass2_required", False),
    }

def build_hierarchical_synthesis_messages(
    *,
    prompt_template: str,
    package_id: str,
    group_summaries: list[dict[str, Any]],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    feature_catalog: dict[str, Any],
    operational_region_catalog: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    synthesis_input = {
        "package_id": package_id,
        "synthesis_contract_version": GROUP_SUMMARY_CONTRACT_VERSION,
        "group_summaries": group_summaries,
        "asset_index": _minimal_asset_index(attachment_manifest),
        "supported_analysis_data_requests": _minimal_support_catalog(support_catalog),
        "feature_index": _minimal_feature_index(feature_catalog),
        "operational_region_index": _minimal_operational_region_index(operational_region_catalog),
    }
    serialized = json.dumps(synthesis_input, ensure_ascii=False)
    if "source_panels" in serialized:
        raise ValueError("hierarchical synthesis input must not contain source_panels")
    if re.search(r"(?:[A-Za-z]:[\\/]|file://|\\\\\\\\)", serialized, flags=re.IGNORECASE):
        raise ValueError("hierarchical synthesis input must not contain local absolute paths")
    return [
        {
            "role": "system",
            "content": (
                "검증된 그룹 요약에서 엄격한 최종 Pass1 후보 JSON 객체 하나만 반환하십시오. "
                "JSON 키·식별자·enum 값은 유지하고 사람이 읽는 설명 문장은 모두 한국어로 작성하십시오."
            ),
        },
        {
            "role": "user",
            "content": prompt_template.strip() + "\n\n## hierarchical synthesis input\n" + serialized,
        },
    ], synthesis_input


def hierarchical_payload_diagnostics(
    *,
    model: str,
    messages: list[dict[str, Any]],
    synthesis_input: dict[str, Any],
    final_schema: dict[str, Any],
    max_tokens: int,
    temperature: float,
    top_p: float,
    enable_thinking: bool,
) -> dict[str, int]:
    payload = build_http_payload(
        model=model,
        messages=messages,
        response_schema=final_schema,
        schema_name="AIFSPass1CandidateResponse",
        structured_output=True,
        enable_thinking=enable_thinking,
        extensions=True,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
    )
    diagnostics = {
        "http_json_utf8_bytes": json_utf8_bytes(payload),
        "synthesis_input_utf8_bytes": json_utf8_bytes(synthesis_input),
        "group_summaries_utf8_bytes": sum(
            json_utf8_bytes(item) for item in synthesis_input["group_summaries"]
        ),
        "asset_index_utf8_bytes": json_utf8_bytes(synthesis_input["asset_index"]),
        "feature_index_utf8_bytes": json_utf8_bytes(synthesis_input["feature_index"]),
        "operational_region_index_utf8_bytes": json_utf8_bytes(
            synthesis_input["operational_region_index"]
        ),
        "final_schema_utf8_bytes": json_utf8_bytes(final_schema),
    }
    return diagnostics


def ensure_hierarchical_payload_budget(diagnostics: dict[str, int]) -> None:
    actual = diagnostics["http_json_utf8_bytes"]
    if actual > HIERARCHICAL_FINAL_MAX_BYTES:
        raise Pass1Error(
            f"hierarchical final payload exceeds hard limit {HIERARCHICAL_FINAL_MAX_BYTES} UTF-8 bytes: "
            + json.dumps(diagnostics, ensure_ascii=True, sort_keys=True)
        )
    if actual > HIERARCHICAL_PAYLOAD_WARN_BYTES:
        logging.warning(
            "hierarchical final payload exceeds warning threshold %s UTF-8 bytes "
            "but is within hard limit %s; continuing: %s",
            HIERARCHICAL_PAYLOAD_WARN_BYTES,
            HIERARCHICAL_FINAL_MAX_BYTES,
            json.dumps(diagnostics, ensure_ascii=True, sort_keys=True),
        )


def create_or_resume_group_summaries(
    *,
    root: Path,
    group_summary_dir: Path,
    contexts: list[dict[str, Any]],
    batches: list[dict[str, Any]],
    package_id: str,
    schema: dict[str, Any],
    prompt_template: str,
    prompt_sha256: str,
    schema_sha256: str,
    client: Pass1ProviderClient,
    model: str,
    structured_output_mode: str,
    enable_thinking: bool,
    max_tokens_group_summary: int,
    temperature: float,
    top_p: float,
    repair_attempts: int,
    compression_attempts: int,
    resume: bool,
    overwrite_group_summaries: bool,
    output_policy: OutputPolicy | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output_policy = output_policy or OutputPolicy("operational")
    if overwrite_group_summaries and group_summary_dir.exists():
        shutil.rmtree(group_summary_dir)
    group_summary_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    for context in contexts:
        summary_id = str(context["summary_id"])
        summary_path = group_summary_dir / f"{summary_id}.summary.json"
        raw_path = group_summary_dir / f"{summary_id}.raw.txt"
        provider_path = group_summary_dir / f"{summary_id}.provider.json"
        checkpoint_path = group_summary_dir / f"{summary_id}.checkpoint.json"
        checkpoint_contract = build_group_checkpoint_contract(
            package_id=package_id,
            context=context,
            batches=batches,
            prompt_sha256=prompt_sha256,
            schema_sha256=schema_sha256,
            model=model,
            provider_configuration=_client_checkpoint_configuration(
                client,
                stage="group_summary",
            ),
        )
        messages = build_group_summary_messages(
            prompt_template=prompt_template,
            package_id=package_id,
            context=context,
        )
        http_payload_bytes = json_utf8_bytes(
            build_http_payload(
                model=model,
                messages=messages,
                response_schema=schema,
                schema_name="AIFSPass1GroupSummary",
                structured_output=structured_output_mode != "off",
                enable_thinking=enable_thinking,
                extensions=True,
                max_tokens=max_tokens_group_summary,
                temperature=temperature,
                top_p=top_p,
            )
        )
        source_observation_count = len(context["source_observations"])
        source_asset_count = len(context["source_asset_ids"])
        existing = None
        if resume and not overwrite_group_summaries:
            existing = _load_existing_group_summary(
                summary_path=summary_path,
                checkpoint_path=checkpoint_path,
                expected_checkpoint=checkpoint_contract,
                schema=schema,
                package_id=package_id,
                context=context,
            )
        if existing is not None:
            logging.info(
                "step05 group summary reused tier=%s group_id=%s source_observations=%s "
                "source_assets=%s http_payload_bytes=%s max_tokens_group_summary=%s "
                "elapsed_seconds=0.000 http_retry_count=0 json_repair_count=0 compaction_count=0",
                context["analysis_tier"],
                context["group_id"],
                source_observation_count,
                source_asset_count,
                http_payload_bytes,
                max_tokens_group_summary,
            )
            summaries.append(existing)
            reports.append(
                {
                    "summary_id": summary_id,
                    "status": "reused",
                    "summary_ref": rel(root, summary_path),
                    "checkpoint_ref": rel(root, checkpoint_path),
                    "summary_utf8_bytes": json_utf8_bytes(existing),
                }
            )
            continue

        if source_observation_count == 1:
            started = time.monotonic()
            summary = build_deterministic_group_summary(
                package_id=package_id,
                context=context,
            )
            errors = validate_group_summary(
                summary,
                schema=schema,
                package_id=package_id,
                context=context,
                enforce_byte_budget=False,
            )
            if errors:
                raise Pass1Error(
                    f"deterministic group summary validation failed summary_id={summary_id}: "
                    + " | ".join(errors[:30])
                )
            if json_utf8_bytes(summary) <= GROUP_SUMMARY_MAX_BYTES:
                elapsed = round(time.monotonic() - started, 3)
                if output_policy.should_write("provider_raw"):
                    atomic_write_text(raw_path, json.dumps(summary, ensure_ascii=False) + "\n")
                write_json(summary_path, summary)
                checkpoint = {
                    **checkpoint_contract,
                    "max_tokens_group_summary": max_tokens_group_summary,
                    "summary_sha256": sha256sum(summary_path),
                    "summary_utf8_bytes": json_utf8_bytes(summary),
                    "generation_mode": "deterministic_single_observation",
                    "fixed_field_mismatches": [],
                    "created_at_utc": now_utc(),
                }
                write_json(checkpoint_path, checkpoint)
                if output_policy.should_write("provider_metadata"):
                    write_json(
                        provider_path,
                        {
                            "model": model,
                            "generation_mode": "deterministic_single_observation",
                            "provider_call_count": 0,
                            "http_retry_count": 0,
                            "repair_attempt_count": 0,
                            "compression_attempt_count": 0,
                            "elapsed_seconds": elapsed,
                        },
                    )
                logging.info(
                    "step05 group summary completed deterministically tier=%s group_id=%s "
                    "source_observations=%s source_assets=%s summary_utf8_bytes=%s "
                    "elapsed_seconds=%.3f provider_call_count=0",
                    context["analysis_tier"],
                    context["group_id"],
                    source_observation_count,
                    source_asset_count,
                    json_utf8_bytes(summary),
                    elapsed,
                )
                summaries.append(summary)
                reports.append(
                    {
                        "summary_id": summary_id,
                        "status": "completed",
                        "generation_mode": "deterministic_single_observation",
                        "summary_ref": rel(root, summary_path),
                        "checkpoint_ref": rel(root, checkpoint_path),
                        "summary_utf8_bytes": json_utf8_bytes(summary),
                        "elapsed_seconds": elapsed,
                        "compression_attempt_count": 0,
                    }
                )
                continue
            logging.warning(
                "deterministic group summary exceeds byte budget; falling back to provider compaction "
                "summary_id=%s summary_utf8_bytes=%s hard_max=%s",
                summary_id,
                json_utf8_bytes(summary),
                GROUP_SUMMARY_MAX_BYTES,
            )

        logging.info(
            "step05 group summary start tier=%s group_id=%s source_observations=%s "
            "source_assets=%s http_payload_bytes=%s max_tokens_group_summary=%s",
            context["analysis_tier"],
            context["group_id"],
            source_observation_count,
            source_asset_count,
            http_payload_bytes,
            max_tokens_group_summary,
        )
        started = time.monotonic()
        summary, raw, provider_summary, call_meta = call_and_validate_json(
            client=client,
            model=model,
            messages=messages,
            schema=schema,
            schema_name="AIFSPass1GroupSummary",
            structured_output_mode=structured_output_mode,
            enable_thinking=enable_thinking,
            max_tokens=max_tokens_group_summary,
            temperature=temperature,
            top_p=top_p,
            repair_attempts=repair_attempts,
            normalizer=lambda value, _context=context: normalize_group_summary_contract(
                value,
                package_id=package_id,
                context=_context,
            ),
            fixed_field_expectations={
                "schema_version": GROUP_SUMMARY_SCHEMA_VERSION,
                "package_id": package_id,
                "summary_id": summary_id,
                "analysis_tier": context["analysis_tier"],
                "group_id": context["group_id"],
            },
            semantic_validator=lambda value, _context=context: validate_group_summary(
                value,
                schema=schema,
                package_id=package_id,
                context=_context,
                enforce_byte_budget=False,
            ),
        )
        initial_call_meta = call_meta
        compression_reports: list[dict[str, Any]] = []
        for attempt in range(compression_attempts):
            if json_utf8_bytes(summary) <= GROUP_SUMMARY_MAX_BYTES:
                break
            compressed, compressed_raw, compressed_provider, compressed_meta = call_and_validate_json(
                client=client,
                model=model,
                messages=build_group_summary_compaction_messages(summary=summary, context=context),
                schema=schema,
                schema_name="AIFSPass1GroupSummary",
                structured_output_mode=structured_output_mode,
                enable_thinking=False,
                max_tokens=max_tokens_group_summary,
                temperature=0.0,
                top_p=1.0,
                repair_attempts=repair_attempts,
                normalizer=lambda value, _context=context: normalize_group_summary_contract(
                    value,
                    package_id=package_id,
                    context=_context,
                ),
                fixed_field_expectations={
                    "schema_version": GROUP_SUMMARY_SCHEMA_VERSION,
                    "package_id": package_id,
                    "summary_id": summary_id,
                    "analysis_tier": context["analysis_tier"],
                    "group_id": context["group_id"],
                },
                semantic_validator=lambda value, _context=context: validate_group_summary(
                    value,
                    schema=schema,
                    package_id=package_id,
                    context=_context,
                    enforce_byte_budget=False,
                ),
            )
            summary, raw, provider_summary, call_meta = (
                compressed,
                compressed_raw,
                compressed_provider,
                compressed_meta,
            )
            compression_reports.append(
                {
                    "attempt": attempt + 1,
                    "summary_utf8_bytes": json_utf8_bytes(summary),
                    "call_meta": compressed_meta,
                }
            )
        errors = validate_group_summary(
            summary,
            schema=schema,
            package_id=package_id,
            context=context,
            enforce_byte_budget=True,
        )
        if errors:
            raise Pass1Error(
                f"group summary validation failed summary_id={summary_id}: "
                + " | ".join(errors[:30])
            )
        elapsed = round(time.monotonic() - started, 3)
        if output_policy.should_write("provider_raw"):
            atomic_write_text(raw_path, raw + "\n")
        write_json(summary_path, summary)
        checkpoint = {
            **checkpoint_contract,
            "max_tokens_group_summary": max_tokens_group_summary,
            "summary_sha256": sha256sum(summary_path),
            "summary_utf8_bytes": json_utf8_bytes(summary),
            "fixed_field_mismatches": [
                *initial_call_meta.get("fixed_field_mismatches", []),
                *[
                    mismatch
                    for report in compression_reports
                    for mismatch in report["call_meta"].get("fixed_field_mismatches", [])
                ],
            ],
            "created_at_utc": now_utc(),
        }
        write_json(checkpoint_path, checkpoint)
        if output_policy.should_write("provider_metadata"):
            write_json(
                provider_path,
                {
                    **provider_summary,
                    **call_meta,
                    "initial_call_meta": initial_call_meta,
                    "elapsed_seconds": elapsed,
                    "compression_attempt_count": len(compression_reports),
                    "compression_reports": compression_reports,
                },
            )
        http_retry_count = int(initial_call_meta.get("http_retry_count", 0)) + sum(
            int(report["call_meta"].get("http_retry_count", 0))
            for report in compression_reports
        )
        json_repair_count = int(initial_call_meta.get("repair_attempt_count", 0)) + sum(
            int(report["call_meta"].get("repair_attempt_count", 0))
            for report in compression_reports
        )
        logging.info(
            "step05 group summary completed tier=%s group_id=%s source_observations=%s "
            "source_assets=%s http_payload_bytes=%s max_tokens_group_summary=%s "
            "elapsed_seconds=%.3f http_retry_count=%s json_repair_count=%s compaction_count=%s",
            context["analysis_tier"],
            context["group_id"],
            source_observation_count,
            source_asset_count,
            http_payload_bytes,
            max_tokens_group_summary,
            elapsed,
            http_retry_count,
            json_repair_count,
            len(compression_reports),
        )
        usage = provider_summary.get("usage")
        finish_reason = provider_summary.get("finish_reason")
        if isinstance(usage, dict) and finish_reason is not None:
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            logging.info(
                "step05 group summary provider usage tier=%s group_id=%s prompt_tokens=%s "
                "completion_tokens=%s finish_reason=%s",
                context["analysis_tier"],
                context["group_id"],
                prompt_tokens,
                completion_tokens,
                finish_reason,
            )
        if finish_reason == "length":
            logging.warning(
                "step05 group summary finish_reason=length tier=%s group_id=%s; output may be truncated",
                context["analysis_tier"],
                context["group_id"],
            )
        summaries.append(summary)
        reports.append(
            {
                "summary_id": summary_id,
                "status": "completed",
                "summary_ref": rel(root, summary_path),
                "checkpoint_ref": rel(root, checkpoint_path),
                "summary_utf8_bytes": json_utf8_bytes(summary),
                "elapsed_seconds": elapsed,
                "compression_attempt_count": len(compression_reports),
            }
        )
    return summaries, reports


def _reduced_attachment_manifest(attachment_manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "package_id": attachment_manifest.get("package_id"),
        "attachment_count": attachment_manifest.get("attachment_count"),
        "attachments": [
            {
                "asset_id": item.get("asset_id"),
                "product_id": item.get("product_id"),
                "analysis_tier": item.get("analysis_tier"),
                "group_id": item.get("group_id"),
                "forecast_hours": item.get("forecast_hours"),
                "valid_times_utc": item.get("valid_times_utc"),
                "render_domain_id": item.get("render_domain_id"),
                "render_domain_bbox": item.get("render_domain_bbox"),
                "source_panels": item.get("source_panels"),
            }
            for item in attachment_manifest.get("attachments", [])
        ],
    }


def build_synthesis_messages(
    *,
    final_prompt: str,
    package_manifest: dict[str, Any],
    compact_input: dict[str, Any],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    feature_catalog: dict[str, Any],
    operational_region_catalog: dict[str, Any],
    batch_observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    synthesis_input = {
        "execution_note": (
            "overview 26장은 동일 시간그룹별 다중 이미지 배치에서 먼저 판독되었습니다. "
            "아래 batch_observations는 원본 asset_id와 연결된 검증 완료 관찰기록입니다. "
            "최종 후보 근거는 반드시 원본 overview asset_id를 인용하십시오."
        ),
        "package_manifest": package_manifest,
        "compact_input": compact_input,
        "attachment_manifest_reduced": _reduced_attachment_manifest(attachment_manifest),
        "supported_request_catalog": support_catalog,
        "feature_catalog": feature_catalog,
        "operational_region_catalog": operational_region_catalog,
        "batch_observations": batch_observations,
    }
    return [
        {
            "role": "system",
            "content": (
                "당신은 AIFS 강수 감시후보 Pass 1 최종 종합기입니다. "
                "검증된 배치 관찰만 종합하고, 제공되지 않은 자료를 보았다고 주장하지 마십시오. "
                "최종 출력은 JSON만 반환하십시오."
            ),
        },
        {
            "role": "user",
            "content": final_prompt.strip() + "\n\n## 검증된 다단계 관찰 입력\n" + json.dumps(synthesis_input, ensure_ascii=False),
        },
    ]


def _load_existing_batch_observation(
    path: Path,
    *,
    schema: dict[str, Any],
    package_id: str,
    batch: dict[str, Any],
    attachment_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = load_json(path)
        errors = validate_batch_observation(
            value,
            schema=schema,
            package_id=package_id,
            batch=batch,
            attachment_by_id=attachment_by_id,
        )
        return value if not errors else None
    except Exception:  # noqa: BLE001
        return None


def step05_overwrite_paths(pass1_dir: Path, *, synthesis_mode: str) -> list[Path]:
    paths = [
        pass1_dir / "ai_candidate_response.raw.txt",
        pass1_dir / "ai_candidate_response.json",
        pass1_dir / "ai_candidate_response.validated.json",
        pass1_dir / "ai_candidate_response.validation_report.json",
    ]
    if synthesis_mode == "direct":
        return [pass1_dir / "observations", *paths]
    if synthesis_mode == "hierarchical":
        return [pass1_dir / "candidate_discovery", pass1_dir / "candidate_plans", *paths]
    raise ValueError(f"unsupported synthesis mode: {synthesis_mode}")


def _openai_transport_schema_paths(root: Path) -> dict[str, Path]:
    return {
        "batch_observation": runtime_path(root, "prompts", "schemas", "openai", "ai_pass1_batch_observation.transport.schema.json"),
        "group_summary": runtime_path(root, "prompts", "schemas", "openai", "ai_pass1_group_summary.transport.schema.json"),
        "candidate_discovery": runtime_path(root, "prompts", "schemas", "openai", "ai_pass1_candidate_discovery.transport.schema.json"),
        "candidate_plan": runtime_path(root, "prompts", "schemas", "openai", "ai_pass1_candidate_plan.transport.schema.json"),
        "candidate_response": runtime_path(root, "prompts", "schemas", "openai", "ai_discovery_candidate_response.transport.schema.json"),
    }


def main(*, checkpoint_publisher: Callable[[str, str], None] | None = None, preloaded_env_load: ProjectEnvLoad | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run Pass 1 with staged image observation and text-only synthesis."
    )
    parser.add_argument("--init", required=True, help="Initialization time YYYYMMDDHH")
    parser.add_argument("--root", default=".")
    parser.add_argument("--provider", default=None, choices=["openai", "codex_cli", "openai_compatible"])
    parser.add_argument("--base-url", default=None, help="Base URL for compatible HTTP providers.")
    parser.add_argument("--openai-base-url", default=None, help="Explicit non-default OpenAI Responses API base URL; official OpenAI uses https://api.openai.com/v1.")
    parser.add_argument("--model", default=None, help="Deprecated for AIFSHeavyRain: model selection is controlled by envi/api_keys.env only.")
    parser.add_argument("--api-key-env", default=None, help="Provider API key environment variable. Default is provider-specific.")
    parser.add_argument(
        "--env-file",
        default=None,
        help="Dotenv path. Default: <root>/envi/api_keys.env. Existing OS environment variables take precedence.",
    )
    parser.add_argument(
        "--no-env-file",
        action="store_true",
        help="Do not load a dotenv file; use OS environment variables only.",
    )
    parser.add_argument("--env-preloaded", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--max-images-per-request", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--retry-backoff-seconds", type=float, default=5.0)
    parser.add_argument("--request-gap-seconds", type=float, default=1.0)
    parser.add_argument("--max-tokens-observation", type=int, default=8000)
    parser.add_argument(
        "--max-tokens-group-summary",
        type=int,
        default=3200,
        help="Maximum output tokens for provider-based group summary fallback and repair.",
    )
    parser.add_argument(
        "--max-tokens-candidate-discovery",
        type=int,
        default=16000,
        help=(
            "Maximum combined reasoning and output-token ceiling for hierarchical candidate "
            "discovery after bounded schema output. Default: 16000."
        ),
    )
    parser.add_argument(
        "--max-tokens-candidate-plan",
        type=int,
        default=None,
        help=(
            "Maximum output-token ceiling for one candidate-plan request. "
            "OpenAI Responses compact chunks default to 8000; legacy providers retain 32000."
        ),
    )
    parser.add_argument(
        "--candidate-plan-continuation-attempts",
        type=int,
        default=4,
        help="Maximum safety limit for no-progress candidate-plan continuation calls. Default: 4.",
    )
    parser.add_argument(
        "--max-tokens-synthesis",
        type=int,
        default=20000,
        help="Legacy direct-mode final synthesis token limit; hierarchical mode does not use it.",
    )
    parser.add_argument(
        "--synthesis-mode",
        choices=SYNTHESIS_MODES,
        default="hierarchical",
        help="hierarchical (default) uses 8 validated group summaries; direct keeps the 26-observation synthesis path.",
    )
    parser.add_argument(
        "--group-summary-compression-attempts",
        type=int,
        default=1,
        help="Text-only compaction attempts after a group summary exceeds 8 KiB.",
    )
    parser.add_argument("--temperature-observation", type=float, default=0.0)
    parser.add_argument("--temperature-synthesis", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument(
        "--structured-output",
        choices=["auto", "on", "off"],
        default="auto",
        help="auto retries once without response_format after provider rejection or 503/504 gateway failure",
    )
    parser.add_argument("--enable-thinking", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--enable-synthesis-thinking",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable model reasoning for candidate discovery and per-candidate planning. Default: disabled.",
    )
    parser.add_argument("--image-detail", choices=sorted(IMAGE_DETAILS), default=None, help="OpenAI Responses image detail. Ignored by Chat Completions providers.")
    parser.add_argument("--reasoning-effort-observation", choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"], default=None)
    parser.add_argument("--reasoning-effort-group-summary", choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"], default=None)
    parser.add_argument("--reasoning-effort-candidate-discovery", choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"], default=None)
    parser.add_argument("--reasoning-effort-candidate-plan", choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"], default=None)
    parser.add_argument(
        "--multimodal-retry-attempts",
        type=int,
        default=2,
        help=(
            "Number of source-grounded retries that resend the original images "
            "before text-only JSON repair. Default: 2."
        ),
    )
    parser.add_argument("--repair-attempts", type=int, default=1)
    parser.add_argument(
        "--candidate-id",
        default=None,
        help="Run only one already-discovered candidate plan; requires --resume and does not run final merge.",
    )
    parser.add_argument(
        "--stop-after",
        choices=["group_summary", "candidate_discovery"],
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--overwrite-group-summaries",
        action="store_true",
        help="Remove only Step05 hierarchical group-summary checkpoints before rebuilding them.",
    )
    parser.add_argument("--dry-run", action="store_true")
    add_output_mode_argument(parser)
    args = parser.parse_args()

    if not re.fullmatch(r"\d{10}", args.init):
        print("step05 실패: --init은 YYYYMMDDHH 형식이어야 합니다.", file=sys.stderr)
        return 2
    if not 1 <= args.max_images_per_request <= 4:
        print("step05 실패: --max-images-per-request는 1~4 범위여야 합니다.", file=sys.stderr)
        return 2
    if (
        args.max_retries < 0
        or args.multimodal_retry_attempts < 0
        or args.repair_attempts < 0
        or args.group_summary_compression_attempts < 0
        or args.candidate_plan_continuation_attempts < 0
    ):
        print("step05 실패: retry/repair 횟수는 0 이상이어야 합니다.", file=sys.stderr)
        return 2

    if args.no_env_file and args.env_file:
        print("step05 실패: --env-file과 --no-env-file은 함께 사용할 수 없습니다.", file=sys.stderr)
        return 2
    if args.overwrite_group_summaries and args.synthesis_mode != "hierarchical":
        print("step05 failed: --overwrite-group-summaries requires --synthesis-mode hierarchical", file=sys.stderr)
        return 2
    if args.candidate_id is not None and (not args.resume or args.overwrite):
        print("step05 failed: --candidate-id requires --resume and cannot be combined with --overwrite", file=sys.stderr)
        return 2
    if args.stop_after is not None and args.synthesis_mode != "hierarchical":
        print("step05 failed: --stop-after requires --synthesis-mode hierarchical", file=sys.stderr)
        return 2

    root = Path(args.root).resolve()
    try:
        output_policy = resolve_output_mode(args.output_mode)
    except ValueError as exc:
        print(f"step05 failed: {exc}", file=sys.stderr)
        return 2
    try:
        if preloaded_env_load is not None:
            if args.env_file or args.no_env_file or args.env_preloaded:
                raise ValueError(
                    "in-process Step04 engine received both preloaded dotenv provenance and dotenv CLI controls"
                )
            env_load = preloaded_env_load
        else:
            env_load = load_project_env(
                root,
                env_file=args.env_file,
                override=False,
                enabled=not args.no_env_file and not args.env_preloaded,
            )
        env_file = env_load.path or runtime_path(root, "envi", "api_keys.env")
        loaded_env_names: list[str] = list(env_load.loaded_keys)
    except Exception as exc:  # noqa: BLE001
        print(f"step05 실패: .env 로드 오류: {exc}", file=sys.stderr)
        return 2

    try:
        if args.model is not None:
            raise ProviderConfigurationError(
                "model selection is env-file-only; edit AIFS_MODEL_STEP04 in envi/api_keys.env and do not pass --model"
            )
        settings = resolve_ai_settings(
            cli_provider=args.provider,
            cli_model=None,
            cli_image_detail=args.image_detail,
            cli_reasoning_efforts={
                "observation": args.reasoning_effort_observation,
                "group_summary": args.reasoning_effort_group_summary,
                "candidate_discovery": args.reasoning_effort_candidate_discovery,
                "candidate_plan": args.reasoning_effort_candidate_plan,
            },
            reasoning_defaults={
            "observation": "none",
            "group_summary": "none",
            "candidate_discovery": "low",
            "candidate_plan": "none",
            },
            env_load=env_load,
        )
        if settings.model.source != "env_file":
            raise ProviderConfigurationError(
                "model must come from envi/api_keys.env only; "
                f"resolved source={settings.model.source!r}. Configure AIFS_MODEL_STEP04 in the project env file and remove process-environment model overrides."
            )
    except ProviderConfigurationError as exc:
        print(f"step05 configuration failed: {exc}", file=sys.stderr)
        return 2
    args.provider = settings.provider.value
    args.model = settings.model.value
    args.image_detail = settings.image_detail.value
    args.reasoning_effort_observation = settings.reasoning_efforts["observation"].value
    args.reasoning_effort_group_summary = settings.reasoning_efforts["group_summary"].value
    args.reasoning_effort_candidate_discovery = settings.reasoning_efforts["candidate_discovery"].value
    args.reasoning_effort_candidate_plan = settings.reasoning_efforts["candidate_plan"].value
    if args.max_tokens_candidate_plan is None:
        args.max_tokens_candidate_plan = 8000 if args.provider in {"openai", "codex_cli"} else 32000
    defaults = provider_defaults(args.provider)
    api_key_env = args.api_key_env or defaults["api_key_env"]

    setup_logging(root, args.init)
    if args.env_preloaded:
        logging.info("step05 dotenv preloaded by wrapper")
    elif args.no_env_file:
        logging.info("step05 dotenv loading disabled")
    elif env_file.is_file():
        logging.info("pass1 dotenv 로드 적용변수수=%s", len(loaded_env_names))
    else:
        logging.info("step04 dotenv 파일을 찾지 못해 운영체제 환경 변수만 사용합니다.")

    logging.info(
        "step05 provider configuration provider=%s provider_source=%s model=%s model_source=%s "
        "image_detail=%s image_detail_source=%s api_key_configured=%s",
        args.provider,
        settings.provider.source,
        args.model,
        settings.model.source,
        args.image_detail,
        settings.image_detail.source,
        bool(os.getenv(api_key_env, "").strip()),
    )
    logging.info(
        "step05 reasoning configuration observation=%s(%s) group_summary=%s(%s) "
        "candidate_discovery=%s(%s) candidate_plan=%s(%s)",
        args.reasoning_effort_observation,
        settings.reasoning_efforts["observation"].source,
        args.reasoning_effort_group_summary,
        settings.reasoning_efforts["group_summary"].source,
        args.reasoning_effort_candidate_discovery,
        settings.reasoning_efforts["candidate_discovery"].source,
        args.reasoning_effort_candidate_plan,
        settings.reasoning_efforts["candidate_plan"].source,
    )

    pass1_dir = runtime_path(root, "daio", args.init, "ai_pass1")
    input_dir = pass1_dir / "input"
    observation_dir = pass1_dir / "observations"
    group_summary_dir = pass1_dir / "group_summaries"
    candidate_discovery_dir = pass1_dir / "candidate_discovery"
    candidate_plan_dir = pass1_dir / "candidate_plans"
    prompt_dir = runtime_path(root, "daio", args.init, "prompts")
    required_paths = {
        "input_manifest": input_dir / "ai_discovery_input_manifest.json",
        "input_validation": input_dir / "ai_discovery_input_validation_report.json",
        "compact_input": input_dir / "ai_discovery_input.compact.json",
        "attachment_manifest": input_dir / "ai_discovery_attachment_manifest.json",
        "batch_plan": input_dir / "ai_discovery_batch_plan.json",
        "support_catalog": input_dir / "ai_pass1_supported_request_catalog.json",
        "final_schema": input_dir / "ai_discovery_candidate_response.schema.json",
        "batch_schema": input_dir / "ai_pass1_batch_observation.schema.json",
        "batch_prompt": prompt_dir / "ai_pass1_batch_observation_prompt.ko.md",
        "final_prompt": prompt_dir / "ai_discovery_candidate_prompt.ko.md",
        "feature_catalog": runtime_path(root, "daio", args.init, "feature_package", "discovery_feature_catalog.json"),
        "operational_region_catalog": runtime_path(root, "daio", args.init, "feature_package", "operational_region_catalog.json"),
    }
    hierarchical_paths = {
        "group_summary_prompt": runtime_path(root, "prompts", "templates", "ai_pass1_group_summary_prompt.ko.md"),
        "candidate_discovery_prompt": runtime_path(root, "prompts", "templates", "ai_pass1_candidate_discovery_prompt.ko.md"),
        "candidate_plan_prompt": runtime_path(root, "prompts", "templates", "ai_pass1_candidate_plan_prompt.ko.md"),
        "group_summary_schema": runtime_path(root, "prompts", "schemas", "ai_pass1_group_summary.schema.json"),
        "candidate_discovery_schema": runtime_path(root, "prompts", "schemas", "ai_pass1_candidate_discovery.schema.json"),
        "candidate_plan_schema": runtime_path(root, "prompts", "schemas", "ai_pass1_candidate_plan.schema.json"),
    }
    if args.synthesis_mode == "hierarchical" and not required_paths["batch_prompt"].is_file():
        legacy_batch_prompt = runtime_path(root, "prompts", "templates", "ai_pass1_batch_observation_prompt.ko.md")
        if legacy_batch_prompt.is_file():
            required_paths["batch_prompt"] = legacy_batch_prompt

    failure_stage = "preflight"
    client: Pass1ProviderClient | None = None
    try:
        required_for_mode = {
            name: path
            for name, path in required_paths.items()
            if args.synthesis_mode == "direct" or name != "final_prompt"
        }
        missing = [
            rel(root, path) if path.is_absolute() and root in path.parents else str(path)
            for path in required_for_mode.values()
            if not path.is_file()
        ]
        if missing:
            raise FileNotFoundError(
                "Step04 staged Pass1 package is incomplete. Re-run Step04 after this update. Missing: "
                + ", ".join(missing)
            )
        if args.synthesis_mode == "hierarchical":
            hierarchical_missing = [
                rel(root, path) if path.is_absolute() and root in path.parents else str(path)
                for path in hierarchical_paths.values()
                if not path.is_file()
            ]
            if hierarchical_missing:
                raise FileNotFoundError(
                    "Step05 hierarchical execution contract is incomplete. Missing: "
                    + ", ".join(hierarchical_missing)
                )
        transport_paths = _openai_transport_schema_paths(root)
        if args.provider in {"openai", "codex_cli"}:
            missing_transport = [rel(root, path) for path in transport_paths.values() if not path.is_file()]
            if missing_transport:
                raise FileNotFoundError(
                    "OpenAI Responses transport schemas are incomplete. Missing: "
                    + ", ".join(missing_transport)
                )


        loaded = {name: load_json(path) for name, path in required_paths.items() if path.suffix == ".json"}
        input_manifest = loaded["input_manifest"]
        input_validation = loaded["input_validation"]
        compact_input = loaded["compact_input"]
        attachment_manifest = loaded["attachment_manifest"]
        batch_plan = loaded["batch_plan"]
        support_catalog = loaded["support_catalog"]
        final_schema = loaded["final_schema"]
        batch_schema = loaded["batch_schema"]
        feature_catalog = loaded["feature_catalog"]
        operational_region_catalog = loaded["operational_region_catalog"]
        group_summary_schema = (
            load_json(hierarchical_paths["group_summary_schema"])
            if args.synthesis_mode == "hierarchical"
            else None
        )
        candidate_discovery_provider_schema = (
            load_json(hierarchical_paths["candidate_discovery_schema"])
            if args.synthesis_mode == "hierarchical"
            else None
        )
        candidate_plan_provider_schema = (
            load_json(hierarchical_paths["candidate_plan_schema"])
            if args.synthesis_mode == "hierarchical"
            else None
        )
        package_id = str(input_manifest.get("package_id", ""))
        if not package_id:
            raise ValueError("Step04 package_id is missing")
        overview_data_ref = str(input_manifest.get("overview_data_ref", "")).strip()
        if not overview_data_ref:
            raise ValueError("Step04 input manifest overview_data_ref is missing")
        overview_data_path = resolve_logical_ref(root, overview_data_ref)
        if not overview_data_path.is_file():
            raise FileNotFoundError(f"Step02 overview_data is missing: {overview_data_ref}")
        expected_overview_sha = str(input_manifest.get("overview_data_sha256", "")).strip()
        if expected_overview_sha and sha256sum(overview_data_path) != expected_overview_sha:
            raise ValueError("Step02 overview_data hash does not match Step03 input manifest")
        overview_data = load_json(overview_data_path)
        if str(overview_data.get("init", "")) != args.init:
            raise ValueError("Step02 overview_data init does not match --init")
        precipitation_peak_guidance = build_precipitation_peak_guidance(
            overview_data, minimum_regional_max_mm=_peak_anchor_minimum_mm(support_catalog)
        )
        if not precipitation_peak_guidance.get("operational_timeline"):
            raise ValueError("Step02 overview_data has no operational 6-hour precipitation timeline")
        logging.info(
            "step04 quantitative precipitation peak guidance primary_peak_hours=%s regional_anchor_count=%s",
            precipitation_peak_guidance.get("primary_peak_hours", []),
            len(precipitation_peak_guidance.get("regional_peak_anchors", [])),
        )
        for name, value in {
            "compact_input": compact_input,
            "attachment_manifest": attachment_manifest,
            "batch_plan": batch_plan,
            "support_catalog": support_catalog,
        }.items():
            if value.get("package_id") != package_id:
                raise ValueError(f"{name} package_id does not match input manifest")
        if input_validation.get("coverage_status") != "complete" or input_validation.get("errors"):
            raise ValueError("Step04 input validation did not pass")

        attachment_by_id = verify_attachments(root, attachment_manifest)
        batches = normalize_batch_plan(
            batch_plan,
            attachment_manifest,
            max_images_per_request=args.max_images_per_request,
        )
        expected_batch_count = int(batch_plan.get("batch_count", len(batches)))
        planned_max_images = int(batch_plan.get("max_images_per_request", args.max_images_per_request))
        if (
            str(batch_plan.get("execution_mode", "")).startswith("staged")
            and args.max_images_per_request == planned_max_images
            and expected_batch_count != len(batches)
        ):
            raise ValueError(
                f"batch_plan batch_count mismatch declared={expected_batch_count} actual={len(batches)}"
            )

        logging.info(
            "step05 preflight package_id=%s attachments=%s batches=%s max_images=%s",
            package_id,
            len(attachment_by_id),
            len(batches),
            args.max_images_per_request,
        )
        if args.dry_run:
            print("AIFSHeavyRain Step04 Pass1 dry-run")
            print(f"  package_id: {package_id}")
            print(f"  provider: {args.provider}")
            print(f"  model: {args.model}")
            print(f"  overview_attachment_count: {len(attachment_by_id)}")
            print(f"  image_observation_batch_count: {len(batches)}")
            print(f"  synthesis_mode: {args.synthesis_mode}")
            print(
                "  quantitative_precipitation_peak_hours: "
                + ",".join(str(value) for value in precipitation_peak_guidance.get("primary_peak_hours", []))
            )
            for batch in batches:
                print(
                    f"  {batch['batch_id']}: {len(batch['attachment_ids'])} images "
                    f"tier={batch.get('analysis_tier')} group={batch.get('group_id')}"
                )
            return 0

        model = args.model
        reasoning_efforts = {
            "observation": args.reasoning_effort_observation,
            "group_summary": args.reasoning_effort_group_summary,
            "candidate_discovery": args.reasoning_effort_candidate_discovery,
            "candidate_plan": args.reasoning_effort_candidate_plan,
        }
        if args.provider in {"openai", "codex_cli"}:
            validate_openai_capabilities(
                model=model,
                require_images=True,
                require_structured_outputs=True,
                image_detail=args.image_detail,
                reasoning_efforts=reasoning_efforts.values(),
            )
        provider_client = build_provider(
            provider=args.provider,
            base_url=args.base_url,
            openai_base_url=args.openai_base_url,
            api_key_env=api_key_env,
            timeout_seconds=args.timeout_seconds,
            max_retries=args.max_retries,
            retry_backoff_seconds=args.retry_backoff_seconds,
        )
        client = Pass1ProviderClient(
            provider=provider_client,
            image_detail=args.image_detail,
            reasoning_efforts=reasoning_efforts,
            transport_schemas={key: load_json(path) for key, path in transport_paths.items()} if args.provider in {"openai", "codex_cli"} else {},
        )

        final_validated_path = pass1_dir / "ai_candidate_response.validated.json"
        replace_final_outputs = args.overwrite or args.overwrite_group_summaries
        if final_validated_path.exists() and not replace_final_outputs:
            raise FileExistsError("step05 validated output exists. Use --overwrite to run again.")
        if replace_final_outputs:
            for path in step05_overwrite_paths(pass1_dir, synthesis_mode=args.synthesis_mode):
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
        observation_dir.mkdir(parents=True, exist_ok=True)

        batch_prompt_template = required_paths["batch_prompt"].read_text(encoding="utf-8")
        final_prompt = (
            required_paths["final_prompt"].read_text(encoding="utf-8")
            if args.synthesis_mode == "direct"
            else ""
        )
        batch_observations: list[dict[str, Any]] = []
        batch_reports: list[dict[str, Any]] = []
        for batch_index, batch in enumerate(batches, start=1):
            failure_stage = "observation"
            batch_id = str(batch["batch_id"])
            safe_batch_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", batch_id)
            observation_path = observation_dir / f"{safe_batch_id}.observation.json"
            raw_path = observation_dir / f"{safe_batch_id}.raw.txt"
            provider_path = observation_dir / f"{safe_batch_id}.provider.json"
            existing = None
            if args.resume and not args.overwrite and observation_path.exists():
                existing = _load_existing_batch_observation(
                    observation_path,
                    schema=batch_schema,
                    package_id=package_id,
                    batch=batch,
                    attachment_by_id=attachment_by_id,
                )
                if args.synthesis_mode == "hierarchical" and existing is None:
                    raise Pass1Error(
                        "hierarchical resume found an invalid observation checkpoint; "
                        f"batch_id={batch_id} state=invalid path={rel(root, observation_path)}"
                    )
            if existing is not None:
                logging.info("step05 batch resume batch_id=%s", batch_id)
                batch_observations.append(existing)
                batch_reports.append(
                    {
                        "batch_id": batch_id,
                        "status": "reused",
                        "attachment_count": len(batch["attachment_ids"]),
                        "observation_ref": rel(root, observation_path),
                    }
                )
                continue

            attachments = [attachment_by_id[asset_id] for asset_id in batch["attachment_ids"]]
            messages = build_batch_messages(
                package_id=package_id,
                batch=batch,
                attachments=attachments,
                root=root,
                prompt_template=batch_prompt_template,
            )
            logging.info(
                "step05 image batch start index=%s/%s batch_id=%s images=%s",
                batch_index,
                len(batches),
                batch_id,
                len(attachments),
            )
            started = time.monotonic()
            provider_observation_schema = build_provider_observation_schema(
                batch_schema,
                batch=batch,
            )
            try:
                observation, raw, provider_summary, call_meta = call_and_validate_json(
                    client=client,
                    model=model,
                    messages=messages,
                    schema=provider_observation_schema,
                    schema_name="AIFSPass1BatchObservation",
                    structured_output_mode=args.structured_output,
                    enable_thinking=args.enable_thinking,
                    max_tokens=min(
                        args.max_tokens_observation,
                        COMPACT_OBSERVATION_RECOVERY_MAX_TOKENS,
                    ),
                    temperature=args.temperature_observation,
                    top_p=args.top_p,
                    repair_attempts=args.repair_attempts,
                    normalizer=lambda value, _batch=batch, _attachments=attachments: normalize_batch_observation_contract(
                        value,
                        package_id=package_id,
                        batch=_batch,
                        attachments_in_payload_order=_attachments,
                    ),
                    semantic_validator=lambda value, _batch=batch: validate_batch_observation(
                        value,
                        schema=batch_schema,
                        package_id=package_id,
                        batch=_batch,
                        attachment_by_id=attachment_by_id,
                    ),
                    source_retry_attempts=args.multimodal_retry_attempts,
                    source_retry_after_repair_reserve=1,
                    source_retry_context={
                        "required_attachment_count": len(attachments),
                        "required_asset_ids": [
                            str(item["asset_id"]) for item in attachments
                        ],
                    },
                    source_retry_schema=provider_observation_schema,
                    source_retry_schema_name="AIFSPass1BatchObservation",
                    source_retry_before_repair_on_parse_error=True,
                    compact_length_recovery={
                        "messages": build_compact_observation_recovery_messages(
                            messages,
                            batch=batch,
                        ),
                        "schema": provider_observation_schema,
                        "schema_name": "AIFSPass1BatchObservationCompactRecovery",
                        "max_tokens": min(
                            args.max_tokens_observation,
                            COMPACT_OBSERVATION_RECOVERY_MAX_TOKENS,
                        ),
                    },
                )
            except ResponseValidationError as exc:
                logging.warning(
                    "single-image observation fallback triggered batch_id=%s reason=%s",
                    batch_id,
                    str(exc),
                )
                observation, provider_summary, call_meta = run_single_image_observation_fallback(
                    client=client,
                    model=model,
                    package_id=package_id,
                    batch=batch,
                    attachments=attachments,
                    root=root,
                    prompt_template=batch_prompt_template,
                    canonical_schema=batch_schema,
                    structured_output_mode=args.structured_output,
                    max_tokens=args.max_tokens_observation,
                    temperature=args.temperature_observation,
                    top_p=args.top_p,
                )
                raw = ""
            elapsed = round(time.monotonic() - started, 3)
            write_observation_artifacts(
                observation_path=observation_path,
                raw_path=raw_path,
                provider_path=provider_path,
                observation=observation,
                raw=raw,
                provider_summary=provider_summary,
                call_meta=call_meta,
                elapsed_seconds=elapsed,
                output_policy=output_policy,
            )
            if checkpoint_publisher is not None:
                checkpoint_publisher("observation", batch_id)
            batch_observations.append(observation)
            batch_reports.append(
                {
                    "batch_id": batch_id,
                    "status": "completed",
                    "attachment_count": len(attachments),
                    "elapsed_seconds": elapsed,
                    "observation_ref": rel(root, observation_path),
                    **report_call_metadata(call_meta, output_policy),
                }
            )
            if "single_image_fallback_asset_count" in call_meta:
                batch_reports[-1]["single_image_fallback_asset_count"] = int(
                    call_meta["single_image_fallback_asset_count"]
                )
            if output_policy.should_write("provider_metadata"):
                batch_reports[-1]["provider_ref"] = rel(root, provider_path)
                batch_reports[-1]["usage"] = provider_summary.get("usage")
            logging.info("step05 image batch completed batch_id=%s elapsed=%.3fs", batch_id, elapsed)
            if args.request_gap_seconds > 0 and batch_index < len(batches):
                time.sleep(args.request_gap_seconds)

        group_summary_reports: list[dict[str, Any]] = []
        candidate_discovery_report: dict[str, Any] | None = None
        candidate_plan_reports: list[dict[str, Any]] = []
        hierarchical_diagnostics: dict[str, Any] | None = None
        synthesis_started = time.monotonic()
        if args.synthesis_mode == "direct":
            failure_stage = "direct_synthesis"
            synthesis_messages = build_synthesis_messages(
                final_prompt=final_prompt,
                package_manifest=input_manifest,
                compact_input=compact_input,
                attachment_manifest=attachment_manifest,
                support_catalog=support_catalog,
                feature_catalog=feature_catalog,
                operational_region_catalog=operational_region_catalog,
                batch_observations=batch_observations,
            )
            logging.info("step05 direct final synthesis start batches=%s", len(batch_observations))
            final_response, final_raw, final_provider_summary, final_call_meta = call_and_validate_json(
                client=client,
                model=model,
                messages=synthesis_messages,
                schema=final_schema,
                schema_name="AIFSPass1CandidateResponse",
                structured_output_mode=args.structured_output,
                enable_thinking=args.enable_thinking,
                max_tokens=args.max_tokens_synthesis,
                temperature=args.temperature_synthesis,
                top_p=args.top_p,
                repair_attempts=args.repair_attempts,
                normalizer=lambda value: normalize_final_response_contract(
                    value,
                    package_id=package_id,
                ),
                fixed_field_expectations={"package_id": package_id},
                semantic_validator=lambda value: validate_final_response(
                    value,
                    schema=final_schema,
                    package_id=package_id,
                    attachment_manifest=attachment_manifest,
                    support_catalog=support_catalog,
                    feature_catalog=feature_catalog,
                    operational_region_catalog=operational_region_catalog,
                ),
            )
        else:
            if group_summary_schema is None:
                raise RuntimeError("hierarchical group summary schema is unavailable")
            contexts = build_group_contexts(
                batch_observations=batch_observations,
                batches=batches,
                attachment_by_id=attachment_by_id,
            )
            if len(contexts) != 8:
                actual = [context["summary_id"] for context in contexts]
                raise Pass1Error(f"hierarchical synthesis requires exactly 8 groups, actual={actual}")
            group_prompt_path = hierarchical_paths["group_summary_prompt"]
            failure_stage = "group_summary"
            group_summaries, group_summary_reports = create_or_resume_group_summaries(
                root=root,
                group_summary_dir=group_summary_dir,
                contexts=contexts,
                batches=batches,
                package_id=package_id,
                schema=group_summary_schema,
                prompt_template=group_prompt_path.read_text(encoding="utf-8"),
                prompt_sha256=sha256sum(group_prompt_path),
                schema_sha256=sha256sum(hierarchical_paths["group_summary_schema"]),
                client=client,
                model=model,
                structured_output_mode=args.structured_output,
                enable_thinking=args.enable_thinking,
                max_tokens_group_summary=args.max_tokens_group_summary,
                temperature=args.temperature_synthesis,
                top_p=args.top_p,
                repair_attempts=args.repair_attempts,
                compression_attempts=args.group_summary_compression_attempts,
                resume=args.resume,
                overwrite_group_summaries=args.overwrite_group_summaries,
                output_policy=output_policy,
            )
            if args.stop_after == "group_summary":
                logging.info(
                    "step04 audit stop-after=group_summary completed summaries=%s",
                    len(group_summaries),
                )
                return 0
            discovery_schema = build_candidate_discovery_schema(final_schema)
            candidate_plan_schema = build_candidate_plan_schema(final_schema)
            if candidate_discovery_provider_schema is None or candidate_plan_provider_schema is None:
                raise RuntimeError("hierarchical stage provider schemas are unavailable")
            discovery_prompt_path = hierarchical_paths["candidate_discovery_prompt"]
            plan_prompt_path = hierarchical_paths["candidate_plan_prompt"]
            failure_stage = "candidate_discovery"
            discovery_response, candidate_discovery_report, discovery_diagnostics = (
                create_or_resume_candidate_discovery(
                    root=root,
                    output_dir=candidate_discovery_dir,
                    package_id=package_id,
                    group_summaries=group_summaries,
                    attachment_manifest=attachment_manifest,
                    support_catalog=support_catalog,
                    overview_data=overview_data,
                    schema=discovery_schema,
                    provider_schema=candidate_discovery_provider_schema,
                    prompt_template=discovery_prompt_path.read_text(encoding="utf-8"),
                    prompt_sha256=sha256sum(discovery_prompt_path),
                    client=client,
                    model=model,
                    structured_output_mode=args.structured_output,
                    enable_thinking=args.enable_synthesis_thinking,
                    max_tokens=args.max_tokens_candidate_discovery,
                    temperature=args.temperature_synthesis,
                    top_p=args.top_p,
                    repair_attempts=args.repair_attempts,
                    resume=args.resume and not args.overwrite,
                    reuse_as_immutable_input=args.candidate_id is not None,
                    output_policy=output_policy,
                    checkpoint_publisher=checkpoint_publisher,
                )
            )
            if args.stop_after == "candidate_discovery":
                logging.info(
                    "step04 audit stop-after=candidate_discovery completed candidates=%s",
                    len(discovery_response.get("candidates", [])),
                )
                return 0
            failure_stage = "candidate_plan"
            candidate_plan_results, candidate_plan_reports, candidate_plan_diagnostics = (
                create_or_resume_candidate_plans(
                    root=root,
                    output_dir=candidate_plan_dir,
                    package_id=package_id,
                    discovery_response=discovery_response,
                    group_summaries=group_summaries,
                    attachment_manifest=attachment_manifest,
                    support_catalog=support_catalog,
                    feature_catalog=feature_catalog,
                    operational_region_catalog=operational_region_catalog,
                    schema=candidate_plan_schema,
                    provider_schema=candidate_plan_provider_schema,
                    final_schema=final_schema,
                    prompt_template=plan_prompt_path.read_text(encoding="utf-8"),
                    prompt_sha256=sha256sum(plan_prompt_path),
                    client=client,
                    model=model,
                    structured_output_mode=args.structured_output,
                    enable_thinking=args.enable_synthesis_thinking,
                    max_tokens=args.max_tokens_candidate_plan,
                    temperature=args.temperature_synthesis,
                    top_p=args.top_p,
                    repair_attempts=args.repair_attempts,
                    request_gap_seconds=args.request_gap_seconds,
                    resume=args.resume and not args.overwrite,
                    candidate_ids={args.candidate_id} if args.candidate_id else None,
                    compact_continuation_attempts=args.candidate_plan_continuation_attempts,
                    output_policy=output_policy,
                    checkpoint_publisher=checkpoint_publisher,
                )
            )
            if args.candidate_id:
                logging.info(
                    "step05 candidate plan-only completed candidate_id=%s compact_chunks=%s continuations=%s",
                    args.candidate_id,
                    candidate_plan_reports[0].get("compact_plan_chunk_count", 0)
                    if candidate_plan_reports else 0,
                    candidate_plan_reports[0].get("continuation_attempt_count", 0)
                    if candidate_plan_reports else 0,
                )
                return 0
            final_response = merge_split_pass1_response(
                discovery_response,
                candidate_plan_results,
            )
            final_errors = validate_final_response(
                final_response,
                schema=final_schema,
                package_id=package_id,
                attachment_manifest=attachment_manifest,
                support_catalog=support_catalog,
                feature_catalog=feature_catalog,
                operational_region_catalog=operational_region_catalog,
                require_system_direct_precipitation_anchor=True,
            )
            if final_errors:
                raise Pass1Error(
                    "deterministic final merge validation failed: "
                    + " | ".join(final_errors[:30])
                )
            final_raw = json.dumps(final_response, ensure_ascii=False, indent=2)
            final_provider_summary = {}
            final_call_meta = {
                "merge_mode": "deterministic_python",
                "final_merge_provider_call_count": 0,
                "candidate_discovery_status": candidate_discovery_report.get("status"),
                "candidate_plan_count": len(candidate_plan_results),
            }
            hierarchical_diagnostics = {
                "candidate_discovery": discovery_diagnostics,
                "candidate_plans": candidate_plan_diagnostics,
                "final_merge_provider_call_count": 0,
            }
            logging.info(
                "step05 deterministic final merge completed candidates=%s plans=%s requests=%s "
                "provider_call_count=0",
                len(final_response.get("candidates", [])),
                len(final_response.get("candidate_analysis_plans", [])),
                len(final_response.get("analysis_data_requests", [])),
            )
        synthesis_elapsed = round(time.monotonic() - synthesis_started, 3)
        raw_final_path = pass1_dir / "ai_candidate_response.raw.txt"
        parsed_final_path = pass1_dir / "ai_candidate_response.json"
        validation_report_path = pass1_dir / "ai_candidate_response.validation_report.json"
        final_artifacts = write_final_response_artifacts(
            pass1_dir=pass1_dir,
            final_response=final_response,
            final_raw=final_raw,
            output_policy=output_policy,
        )
        raw_final_path = final_artifacts["raw"]
        parsed_final_path = final_artifacts["parsed"]
        final_validated_path = final_artifacts["validated"]

        validation_report = {
            "validation_status": "pass",
            "step": "step05",
            "script": "scripts/05_run_ai_pass1.py",
            "created_at_utc": now_utc(),
            "init": args.init,
            "package_id": package_id,
            "provider": args.provider,
            "model": model,
            "execution_mode": (
                "staged_image_observation_then_text_synthesis"
                if args.synthesis_mode == "direct"
                else (
                    "staged_image_observation_then_group_summary_then_candidate_discovery_"
                    "then_per_candidate_planning_then_deterministic_merge"
                )
            ),
            "synthesis_mode": args.synthesis_mode,
            "overview_attachment_count": len(attachment_by_id),
            "image_observation_batch_count": len(batch_observations),
            "max_images_per_request": args.max_images_per_request,
            "candidate_count": len(final_response.get("candidates", [])),
            "analysis_plan_count": len(final_response.get("candidate_analysis_plans", [])),
            "analysis_data_request_count": len(final_response.get("analysis_data_requests", [])),
            "batch_reports": batch_reports,
            "group_summary_reports": group_summary_reports,
            "candidate_discovery_report": candidate_discovery_report,
            "candidate_plan_reports": candidate_plan_reports,
            "synthesis": {
                "elapsed_seconds": synthesis_elapsed,
                **final_call_meta,
            },
            "output_refs": {
                "validated_response": rel(root, final_validated_path),
            },
            **output_policy.manifest_fields(
                [
                    "validated_response",
                    "validation_summary",
                    "provider_raw",
                    "intermediate_parsed_json",
                    "provider_metadata",
                    "payload_diagnostics",
                    "execution_snapshot",
                ]
            ),
            "checks": {
                "step04_input_validation_passed": True,
                "attachment_hashes_verified": True,
                "all_attachments_observed_once": True,
                "batch_observation_schema_valid": True,
                "candidate_discovery_schema_valid": True,
                "candidate_plan_schema_valid": True,
                "deterministic_final_merge_used": args.synthesis_mode == "hierarchical",
                "final_response_schema_valid": True,
                "final_response_reference_integrity_valid": True,
            },
        }
        if output_policy.should_write("provider_raw"):
            validation_report["output_refs"]["raw_response"] = rel(root, raw_final_path)
        if output_policy.should_write("intermediate_parsed_json"):
            validation_report["output_refs"]["parsed_response"] = rel(root, parsed_final_path)
        if output_policy.should_write("payload_diagnostics"):
            validation_report["hierarchical_payload_diagnostics"] = hierarchical_diagnostics
        if output_policy.should_write("provider_metadata"):
            validation_report["endpoint_id"] = client.provider.endpoint_id
            validation_report["synthesis"]["usage"] = final_provider_summary.get("usage")
            validation_report["synthesis"]["finish_reason"] = final_provider_summary.get("finish_reason")
        write_json(validation_report_path, validation_report)
        logging.info(
            "step05 completed package_id=%s candidates=%s requests=%s batches=%s synthesis_elapsed=%.3fs",
            package_id,
            len(final_response.get("candidates", [])),
            len(final_response.get("analysis_data_requests", [])),
            len(batch_observations),
            synthesis_elapsed,
        )
    except Exception as exc:  # noqa: BLE001
        logging.exception("step05 failed")
        write_transient_failure_summary(
            pass1_dir=pass1_dir,
            root=root,
            stage=failure_stage,
            exc=exc,
        )
        try:
            print(f"step04 실패: {exc}", file=sys.stderr)
        except OSError:
            logging.error("step05 실패 메시지를 표준 오류에 기록하지 못했습니다.")
        return 1

    print("AIFSHeavyRain step04 Pass 1")
    print(f"  package_id: {package_id}")
    print(f"  provider: {args.provider}")
    print(f"  model: {model}")
    print(f"  overview_attachment_count: {len(attachment_by_id)}")
    print(f"  image_observation_batch_count: {len(batch_observations)}")
    print(f"  candidate_count: {len(final_response.get('candidates', []))}")
    print(f"  analysis_data_request_count: {len(final_response.get('analysis_data_requests', []))}")
    print(f"  validated_response: {rel(root, final_validated_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
