"""Step10 deterministic, self-contained forecaster-product publication."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from html import escape
import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Iterable
from urllib.parse import urlsplit

import yaml

from func.shared.pass2_common import Pass2PipelineError, active_candidate_groups, load_json_object, load_schema, validate_schema
from func.shared.majority_narrative import (
    MajorityNarrativeError, assert_majority_narrative_current,
    canonicalize_majority_result, majority_digest, validate_majority_product,
)
from func.shared.pipeline_common import sha256sum
from func.shared.quantitative_consistency import format_quantitative_display_value
from func.shared.forecaster_quantitative import question_answer_errors
from func.shared.tier_narrative import (TierNarrativeError, public_reason, canonical_guidance, assert_public_tier)
from func.shared.korean_narrative import validate_forecaster_product_narrative
from func.shared.runtime_paths import logical_ref, resolve_logical_ref, resolve_step04_output_dir, runtime_path
from func.shared.signal_validation import (
    CANDIDATE_SIGNAL_STATUSES,
    CANDIDATE_TYPES,
    DISPLAY_TIERS,
    DIRECT_PRECIPITATION_MINIMUM_MM_6H,
    DECISION_EVIDENCE_EFFECTS,
    _compact_peak_precip_region_name,
    candidate_operational_display_name,
    candidate_effective_peak_forecast_hours,
    candidate_peak_precipitation_details_from_group,
    derive_signal_class,
    derive_validated_precursor_categories,
)
from func.step07.checks import (
    MINORITY_REPORT_SCHEMA,
    load_step06_package,
    load_step07_results,
    guard_unresolved_localization_narrative,
    step07_minority_report_path,
    validate_step07_minority_report_consistency,
    _single_majority_convergence_not_strengthen_claim,
)
from func.step08.synthesis import (
    _candidate_analysis_region_index,
    migrate_step08_document,
    step08_output_path,
    validate_operational_brief,
    validate_operational_guidance,
    _verified_quantitative_relations,
)
from func.step09.operational import step09_output_path
from func.step05.candidate_overview import _select_candidate_overview_assets


FORECASTER_PRODUCT_SCHEMA = "forecaster_product.schema.json"
STEP08_SCHEMA = "pass2_result.schema.json"
STEP09_SCHEMA = "operational_result.schema.json"
GENERATOR_VERSION = "step10-forecaster-product-v4"
TEMPLATE_IDENTITY = "step10-forecaster-triage-v42-question-evidence-trace"
SIGNAL_CLASS_LABELS = {
    "direct_environment_supported": "모델 강수·환경 확인",
    "direct_environment_conditional": "모델 강수 확인·환경 부분 지지",
    "direct_environment_unsupported": "모델 강수 확인·환경 미지지",
    "direct_environment_unresolved": "모델 강수 확인·환경 판단 제한",
    "precursor_watch": "강수 발달 환경 감시",
    "partially_supported": "부분 지지",
    "rejected": "기각",
    "insufficient_evidence": "근거 부족",
}
ASSESSMENT_LABELS = {
    "observed": "확인",
    "partially_observed": "부분 확인",
    "not_observed": "확인되지 않음",
    "opposite_observed": "반대 신호 확인",
    "uncertain": "불확실",
    "insufficient_evidence": "근거 부족",
}
STATISTIC_LABELS = {
    "min": "최솟값",
    "max": "최댓값",
    "mean": "평균",
    "p95": "95백분위값",
}
FEATURE_LABELS = {
    "tp_6h_increment_mm": "6시간 강수량",
    "tp_accum_mm": "초기시각부터의 누적강수량",
    "convergence925_1e5_s": "925 hPa 수렴",
    "moisture_flux850": "850 hPa 수증기 플럭스 크기",
    "omega700_pa_s": "700 hPa 기압연직속도",
    "omega700_upward": "700 hPa 상승운동 강도",
}
PHYSICAL_DIAGNOSTIC_LABELS = {
    "QL_PRECIP_6H": "6시간 강수량",
    "QL_CONVERGENCE925_WIND925": "925 hPa 수렴 + 바람",
    "QL_MOISTURE_FLUX850_WIND850": "850 hPa 수증기 플럭스 크기 + 바람",
    "QL_OMEGA700_Z500": "700 hPa 기압연직속도 + 500 hPa 지위고도",
    "omega_time_height": "시간-고도 기압연직속도",
    "specific_humidity_time_height": "시간-고도 비습",
    "wind_speed_time_height": "시간-고도 풍속",
    "along_section_wind_time_height": "시간-고도 단면평행 바람",
    "VERTICAL_WIND_SHEAR_850_500": "850–500 hPa 연직바람시어",
    "omega_cross_section": "연직단면 상승·하강운동",
    "specific_humidity_cross_section": "연직단면 비습",
    "wind_speed_cross_section": "연직단면 풍속",
    "along_section_wind_cross_section": "연직단면 평행바람",
}
ROLE_LABELS = {
    "signal_evidence": "신호 근거",
    "forecaster_question_reference": "예보관 질문 근거",
    "direct_precipitation": "모델 강수 예측",
    "moisture_supply": "수분 공급",
    "low_level_forcing": "하층 수렴",
    "mid_upper_forcing": "중층 상승운동",
    "thermodynamic_support": "열역학적 환경",
    "organization_persistence": "바람의 연직구조",
    "spatiotemporal_alignment": "시공간 일치",
    "mechanism_support": "물리과정 지지",
    "mechanism_limit": "물리과정 제한",
    "mechanism_mixed": "물리과정 혼합",
    "mechanism_context": "물리과정 맥락",
    "monitoring_support": "감시 가치 지지",
    "monitoring_limit": "감시 가치 제한",
    "monitoring_mixed": "감시 가치 혼합",
    "monitoring_context": "감시 가치 맥락",
}
PRECURSOR_CATEGORY_LABELS = {
    "moisture_supply": "수분 공급",
    "low_level_forcing": "하층 수렴",
    "mid_upper_forcing": "중층 상승운동",
    "thermodynamic_support": "열역학적 환경",
    "organization_persistence": "바람의 연직구조",
    "direct_precipitation": "모델 강수 예측",
}
INDEPENDENT_VARIABLE_ROLE_TO_SIGNAL_ROLE = {
    "moisture_supply": "moisture_supply",
    "low_level_forcing": "low_level_forcing",
    "midlevel_forcing": "mid_upper_forcing",
    "thermodynamic_support": "thermodynamic_support",
    "organization_persistence": "organization_persistence",
}
INTENT_EVIDENCE_ROLE_TOKENS = {
    "supporting": "intent_supporting",
    "opposing": "intent_opposing",
    "context": "intent_context",
}
ENVIRONMENT_STATUS_PRESENTATION = {
    "confirmed": ("확인", "observed"),
    "partial": ("부분 확인", "partially_observed"),
    "not_confirmed": ("미확인", "not_observed"),
    "insufficient_evidence": ("판단 제한", "insufficient_evidence"),
}
ENVIRONMENT_PERSISTENCE_PRESENTATION = {
    "confirmed": ("확인", "observed"),
    "limited": ("제한", "partially_observed"),
    "insufficient_evidence": ("판단 제한", "insufficient_evidence"),
    "not_applicable": ("해당 없음", "insufficient_evidence"),
}
PHYSICAL_CONSISTENCY_LABELS = {
    "coherent": "성립",
    "conditionally_coherent": "조건부 성립",
    "incoherent": "성립 어려움",
    "insufficient_evidence": "판단 제한",
}
MONITORING_VALUE_LABELS = {
    "meaningful": "있음",
    "marginal": "조건부",
    "low": "낮음",
    "undetermined": "판단 제한",
}
DECISION_EFFECT_LABELS = {
    "supporting": "지지",
    "limiting": "제한",
    "mixed": "혼합",
    "context": "참고",
}
PHYSICAL_TRANSITION_LABELS = {
    "convergence_strengthening": "수렴 강화",
    "convergence_weakening": "수렴 약화",
    "divergence_to_convergence": "발산 → 수렴",
    "convergence_to_divergence": "수렴 → 발산",
    "divergence_strengthening": "발산 강화",
    "divergence_weakening": "발산 약화",
    "neutral_to_convergence": "중립 → 수렴",
    "neutral_to_divergence": "중립 → 발산",
    "convergence_to_neutral": "수렴 → 중립",
    "divergence_to_neutral": "발산 → 중립",
    "neutral_persistence": "중립 유지",
}
ASSESSMENT_STYLE_CLASSES = {
    "observed": "ok",
    "partially_observed": "partial",
    "not_observed": "muted",
    "opposite_observed": "opp",
    "uncertain": "warn",
    "insufficient_evidence": "muted",
}
QUANTITATIVE_IMAGE_DIAGNOSTICS = {
    "tp_6h_increment_mm": ("QL_PRECIP_6H",),
    "tp_accum_mm": ("QL_PRECIP_6H",),
    "convergence925_1e5_s": ("QL_CONVERGENCE925_WIND925",),
    "moisture_flux850": ("QL_MOISTURE_FLUX850_WIND850",),
    "omega700_upward": ("QL_OMEGA700_Z500",),
    "omega700_pa_s": ("QL_OMEGA700_Z500",),
}
_LOCAL_REFERENCE = re.compile(r"(?:src|href)\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
_FORECAST_HOUR_RANGE = re.compile(r"(?<![A-Za-z0-9])F(\d{3,})\s*(?:-|~|–|—)\s*F(\d{3,})(?![A-Za-z0-9])")
_FORECAST_HOUR_TOKEN = re.compile(r"(?<![A-Za-z0-9])F(\d{3,})(?![A-Za-z0-9])")
_PRECIPITATION_MM_VALUE = re.compile(r"(?<![A-Za-z0-9_.])(-?\d+(?:\.\d+)?)\s*mm(?![A-Za-z])", re.IGNORECASE)
_PRECIPITATION_MM_RANGE = re.compile(
    r"(?<![A-Za-z0-9_.])(-?\d+(?:\.\d+)?)\s*(에서|~|–|—|→|-)\s*(-?\d+(?:\.\d+)?)\s*mm(?![A-Za-z])",
    re.IGNORECASE,
)
_DISPLAY_DECIMAL_VALUE = re.compile(r"(?<![A-Za-z0-9_.])(-?\d+\.\d+)(?![\d.])")
_HASH_ASSET_FILENAME = re.compile(r"^AST_[0-9a-f]{12}\.png$")
COLLECTION_NAMELIST = Path("daba") / "collection" / "aifs_download_namelist.yaml"
DEFAULT_FORECAST_TIMELINE = (0, 120, 6)


def _validate_step10_schema_contract(root: Path) -> None:
    """Fail fast when Step10 generator and publication schema drift apart."""
    schema = load_schema(root, FORECASTER_PRODUCT_SCHEMA)
    version_rule = (schema.get("properties") or {}).get("schema_version") or {}
    allowed_versions: set[str] = set()
    if isinstance(version_rule.get("const"), str):
        allowed_versions.add(str(version_rule["const"]))
    enum_values = version_rule.get("enum")
    if isinstance(enum_values, list):
        allowed_versions.update(str(value) for value in enum_values if isinstance(value, str))
    if GENERATOR_VERSION not in allowed_versions:
        raise Pass2PipelineError(
            "SCHEMA_LOAD_FAILED",
            f"{FORECASTER_PRODUCT_SCHEMA} does not allow current Step10 generator version: {GENERATOR_VERSION}",
        )

    # Step10 transfers the Step08 final_spatial_identity without narrowing it.
    # Keep the publication schema structurally synchronized with the Step08 schema
    # so a new spatial-grounding field cannot reach publication and fail late.
    step08_schema = load_schema(root, STEP08_SCHEMA)
    step10_defs = schema.get("$defs") or {}
    step08_defs = step08_schema.get("$defs") or {}
    for definition_name in ("bbox", "localized_region", "final_spatial_identity"):
        if step10_defs.get(definition_name) != step08_defs.get(definition_name):
            raise Pass2PipelineError(
                "SCHEMA_LOAD_FAILED",
                f"{FORECASTER_PRODUCT_SCHEMA} {definition_name} is not synchronized with {STEP08_SCHEMA}",
            )

ENVIRONMENT_LOCALIZATION_COMPONENTS = (
    "direct_precipitation",
    "moisture_supply",
    "low_level_forcing",
    "mid_upper_forcing",
)
ENVIRONMENT_LOCALIZATION_COMPONENT_LABELS = {
    "direct_precipitation": "모델 강수",
    "moisture_supply": "TCW",
    "low_level_forcing": "하층 수렴",
    "mid_upper_forcing": "중층 상승운동",
}
ENVIRONMENT_LOCALIZATION_DIAGNOSTIC_BY_COMPONENT = {
    "direct_precipitation": ("QL_PRECIP_6H", "6시간 강수량"),
    "moisture_supply": ("QL_TCW_WIND850", "대기 전층 수분량(TCW)"),
    "low_level_forcing": ("QL_CONVERGENCE925_WIND925", "925 hPa 수평수렴"),
    "mid_upper_forcing": ("QL_OMEGA700_Z500", "700 hPa 기압연직속도(ω)"),
}


def _environment_localization_components(focus: dict[str, Any] | None) -> tuple[str, ...]:
    """Return the canonical component order for one localized alignment."""
    if not isinstance(focus, dict):
        return tuple()
    requested = {
        str(value)
        for value in focus.get("signal_components", [])
        if isinstance(value, str) and value in ENVIRONMENT_LOCALIZATION_COMPONENT_LABELS
    }
    return tuple(value for value in ENVIRONMENT_LOCALIZATION_COMPONENTS if value in requested)


def _environment_localization_required_diagnostics(
    signal_components: list[str] | tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    """Map the exact localized alignment components to public evidence fields."""
    requested = {str(value) for value in signal_components if str(value)}
    return tuple(
        ENVIRONMENT_LOCALIZATION_DIAGNOSTIC_BY_COMPONENT[component]
        for component in ENVIRONMENT_LOCALIZATION_COMPONENTS
        if component in requested and component in ENVIRONMENT_LOCALIZATION_DIAGNOSTIC_BY_COMPONENT
    )
ENVIRONMENT_LOCALIZATION_SECTOR_LABELS = {
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

MINIMAP_VIEWBOX_WIDTH = 270.0
MINIMAP_VIEWBOX_HEIGHT = 225.0
MINIMAP_LON_MIN = 118.0
MINIMAP_LON_MAX = 145.0
MINIMAP_LAT_MIN = 28.0
MINIMAP_LAT_MAX = 46.0
MINIMAP_REFERENCE_LAT = 37.0
MINIMAP_COASTLINE = Path("daba") / "map" / "ne_10m_coastline" / "ne_10m_coastline.shp"
MINIMAP_COUNTRIES = Path("daba") / "map" / "ne_50m_admin_0_countries" / "ne_50m_admin_0_countries.shp"
MINIMAP_LAND_COUNTRIES = {"China", "North Korea", "South Korea", "Japan"}
REGION_GUIDE_LAND_MASK = Path("daba") / "map" / "ne_50m_land" / "ne_50m_land.shp"
REGION_GUIDE_CATALOG = Path("daba") / "ai_pass1" / "discovery_operational_regions.yaml"
REGION_GUIDE_LAND_GRID = Path("daba") / "analysis" / "aifs_operational_region_grid_map.csv"
REGION_GUIDE_ROLE_LABELS = {
    "operational_land": "육상",
    "marine_macro": "광역 해상",
    "source_corridor": "상류 통로",
}
REGION_GUIDE_ROLE_ORDER = ("operational_land", "marine_macro", "source_corridor")


def _direct_precipitation_peak_anchor_mm(root: Path) -> float:
    """Return the configured Step04 direct-precipitation peak anchor for UI explanation."""
    path = runtime_path(root, "daba", "ai_pass1", "discovery_execution_policy.yaml")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        generation_policy = payload.get("candidate_generation_policy") if isinstance(payload, dict) else None
        config = generation_policy.get("precipitation_peak_anchor") if isinstance(generation_policy, dict) else None
        value = float(config.get("minimum_regional_maximum_6h_mm")) if isinstance(config, dict) else 5.0
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        value = 5.0
    return value if value >= 0.0 else 5.0


def _forecast_timeline_from_collection_config(root: Path) -> tuple[int, int, int]:
    """Return the configured Step01 forecast horizon used for Step10 timeline presentation."""
    path = resolve_logical_ref(root, COLLECTION_NAMELIST)
    if not path.is_file():
        raise Pass2PipelineError("FORECAST_TIMELINE_CONFIG_INVALID", f"Collection namelist is missing: {path}")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise Pass2PipelineError("FORECAST_TIMELINE_CONFIG_INVALID", f"Unable to read collection namelist: {path}") from exc
    profile = payload.get("collection_profile") if isinstance(payload, dict) else None
    if not isinstance(profile, dict):
        raise Pass2PipelineError("FORECAST_TIMELINE_CONFIG_INVALID", "collection_profile is missing from the collection namelist.")
    try:
        start = int(profile["forecast_hour_start"])
        end = int(profile["forecast_hour_end"])
        interval = int(profile["forecast_hour_interval"])
    except (KeyError, TypeError, ValueError) as exc:
        raise Pass2PipelineError("FORECAST_TIMELINE_CONFIG_INVALID", "Forecast-hour range in the collection namelist is invalid.") from exc
    if start < 0 or end < start or interval <= 0:
        raise Pass2PipelineError("FORECAST_TIMELINE_CONFIG_INVALID", "Forecast-hour range in the collection namelist is invalid.")
    return start, end, interval


def step04_result_path(root: Path, init: str) -> Path:
    try:
        return resolve_step04_output_dir(root, init) / "pass1_result.json"
    except FileNotFoundError as exc:
        raise Pass2PipelineError("STEP04_RESULT_MISSING", str(exc)) from exc
    except (RuntimeError, ValueError, OSError) as exc:
        raise Pass2PipelineError("STEP04_RESULT_AMBIGUOUS", str(exc)) from exc


def step10_output_dir(root: Path, init: str) -> Path:
    return runtime_path(root, "daou", init)


def step10_work_dir(root: Path, init: str) -> Path:
    return runtime_path(root, "daio", init, "step10_work")


def _require_list(value: Any, *, code: str, message: str) -> list[Any]:
    if not isinstance(value, list):
        raise Pass2PipelineError(code, message)
    return value


def _ordered_unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value)
        if text and text not in result:
            result.append(text)
    return result


def _index_candidates(values: Any, *, active_ids: set[str], code: str, label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in _require_list(values, code=code, message=f"{label} candidates must be an array."):
        if not isinstance(item, dict):
            raise Pass2PipelineError(code, f"{label} candidate must be an object.")
        candidate_id = item.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise Pass2PipelineError(code, f"{label} candidate_id is invalid.")
        if candidate_id not in active_ids:
            raise Pass2PipelineError(code, f"{label} has an unexpected candidate {candidate_id!r}.")
        if candidate_id in indexed:
            raise Pass2PipelineError(code, f"{label} has duplicate candidate {candidate_id!r}.")
        indexed[candidate_id] = item
    if set(indexed) != active_ids:
        missing = ", ".join(sorted(active_ids - set(indexed)))
        raise Pass2PipelineError(code, f"{label} omitted active candidates: {missing}.")
    return indexed


def _index_step07_results(step07: dict[str, Any], active_ids: set[str]) -> dict[str, dict[str, dict[str, Any]]]:
    indexed: dict[str, dict[str, dict[str, Any]]] = {candidate_id: {} for candidate_id in active_ids}
    for item in _require_list(step07.get("results"), code="STEP07_RESULTS_INVALID", message="Step07 results must be an array."):
        if not isinstance(item, dict):
            raise Pass2PipelineError("STEP07_RESULTS_INVALID", "Step07 result must be an object.")
        candidate_id = item.get("candidate_id")
        check_id = item.get("check_id")
        if candidate_id not in active_ids or not isinstance(check_id, str) or not check_id:
            raise Pass2PipelineError("STEP07_RESULTS_INVALID", "Step07 contains an unexpected candidate or check.")
        if check_id in indexed[candidate_id]:
            raise Pass2PipelineError("STEP07_RESULTS_INVALID", f"Step07 has duplicate check {candidate_id}/{check_id}.")
        normalized = copy.deepcopy(item)
        consensus = normalized.get("ensemble_consensus")
        if isinstance(consensus, dict) and consensus.get("localization_agreement") == "unresolved":
            guard_unresolved_localization_narrative(normalized)
        indexed[candidate_id][check_id] = normalized
    return indexed


def _excluded_independent_roles(root: Path, init: str) -> dict[str, dict[str, set[str]]]:
    """Return excluded independent roles with their actual Step07 reason codes."""
    package = load_step06_package(root, init)
    groups = active_candidate_groups(package)
    active_ids = {str(group["candidate_id"]) for group in groups}
    step07 = load_step07_results(root, init)
    results_by_candidate = _index_step07_results(step07, active_ids)
    excluded: dict[str, dict[str, set[str]]] = {candidate_id: {} for candidate_id in active_ids}
    for group in groups:
        candidate_id = str(group["candidate_id"])
        results = results_by_candidate.get(candidate_id, {})
        for specification in group.get("check_evidence", []):
            if not isinstance(specification, dict):
                continue
            check_id = str(specification.get("check_id") or "")
            result = results.get(check_id)
            if not isinstance(result, dict):
                continue
            components = [str(value) for value in specification.get("required_signal_components", []) if str(value)]
            if len(components) != 1 or specification.get("check_scope") == "multivariable_alignment":
                continue
            if (
                result.get("evidence_status") == "complete"
                and result.get("assessment") != "insufficient_evidence"
                and result.get("usable_for_synthesis") is False
                and result.get("excluded_from_synthesis_reasons")
            ):
                role_reasons = excluded[candidate_id].setdefault(components[0], set())
                role_reasons.update(
                    str(value)
                    for value in result.get("excluded_from_synthesis_reasons", [])
                    if str(value)
                )
    return excluded


def _related_multivariable_role_assessments(root: Path, init: str) -> dict[str, dict[str, list[dict[str, str]]]]:
    """Collect usable Step07 multivariable assessments for signal-chain context.

    These checks are presentation-only supplemental context. They may exist even
    when Step08 does not validate the overall spatiotemporal-alignment gate, so
    the signal chain can show `관련 근거 부분 확인` instead of incorrectly
    reporting `개별 판독 없음`. They never change Step08/09 decisions.
    """
    package = load_step06_package(root, init)
    groups = active_candidate_groups(package)
    active_ids = {str(group["candidate_id"]) for group in groups}
    step07 = load_step07_results(root, init)
    results_by_candidate = _index_step07_results(step07, active_ids)
    supported_roles = {
        "direct_precipitation",
        "moisture_supply",
        "low_level_forcing",
        "mid_upper_forcing",
    }
    related: dict[str, dict[str, list[dict[str, str]]]] = {candidate_id: {} for candidate_id in active_ids}
    for group in groups:
        candidate_id = str(group["candidate_id"])
        results = results_by_candidate.get(candidate_id, {})
        for specification in group.get("check_evidence", []):
            if not isinstance(specification, dict) or specification.get("check_scope") != "multivariable_alignment":
                continue
            check_id = str(specification.get("check_id") or "")
            result = results.get(check_id)
            if not isinstance(result, dict):
                continue
            if result.get("evidence_status") != "complete" or result.get("usable_for_synthesis") is not True:
                continue
            assessment = str(result.get("assessment") or "")
            if assessment not in ASSESSMENT_LABELS:
                continue
            for component in specification.get("required_signal_components", []):
                role = str(component)
                if role not in supported_roles:
                    continue
                bucket = related[candidate_id].setdefault(role, [])
                item = {"check_id": check_id, "assessment": assessment}
                if item not in bucket:
                    bucket.append(item)
    return related


def _source_path(root: Path, raw_path: Any) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise Pass2PipelineError("STEP05_IMAGE_UNRESOLVED", "Selected Step05 image has no path.")
    candidate = Path(raw_path)
    path = candidate.resolve() if candidate.is_absolute() else resolve_logical_ref(root, candidate)
    try:
        logical_ref(root, path)
    except ValueError as exc:
        raise Pass2PipelineError("STEP05_IMAGE_UNRESOLVED", "Selected Step05 image is outside configured project/runtime paths.") from exc
    return path


def _number(value: Any, *, code: str, message: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Pass2PipelineError(code, message)
    return float(value)


def _area(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        raise Pass2PipelineError("EFFECTIVE_AREA_INVALID", "Effective area must be an object.")
    area = {key: _number(value.get(key), code="EFFECTIVE_AREA_INVALID", message=f"Effective area {key} is invalid.") for key in ("west", "east", "south", "north")}
    if area["west"] >= area["east"] or area["south"] >= area["north"]:
        raise Pass2PipelineError("EFFECTIVE_AREA_INVALID", "Effective area bounds are invalid.")
    return area


def _valid_time(init: str, forecast_hour: int) -> str:
    try:
        origin = datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise Pass2PipelineError("INIT_INVALID", f"init must be YYYYMMDDHH: {init!r}") from exc
    return (origin + timedelta(hours=forecast_hour)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _time_window(init: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise Pass2PipelineError("EFFECTIVE_TIME_INVALID", "Effective time window must be an object.")
    start = value.get("forecast_hour_start")
    end = value.get("forecast_hour_end")
    if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start:
        raise Pass2PipelineError("EFFECTIVE_TIME_INVALID", "Effective forecast-hour window is invalid.")
    return {
        "forecast_hour_start": start,
        "forecast_hour_end": end,
        "valid_time_start_utc": _valid_time(init, start),
        "valid_time_end_utc": _valid_time(init, end),
    }


def _effective_context(
    init: str,
    group: dict[str, Any],
    synthesis: dict[str, Any],
    operational: dict[str, Any],
) -> tuple[str, dict[str, float], dict[str, Any], list[int]]:
    context = group.get("candidate_context")
    candidate = context.get("candidate") if isinstance(context, dict) else None
    if not isinstance(candidate, dict):
        raise Pass2PipelineError("STEP06_PACKAGE_INVALID", f"{group.get('candidate_id')} has no Step04 candidate context.")
    revision = synthesis.get("revision")
    if not isinstance(revision, dict):
        raise Pass2PipelineError("STEP08_RESULT_INVALID", f"{group.get('candidate_id')} has invalid revision.")
    source_name = candidate.get("candidate_name")
    if not isinstance(source_name, str) or not source_name:
        raise Pass2PipelineError("STEP04_RESULT_INVALID", f"{group.get('candidate_id')} has no candidate name.")
    name = operational.get("display_candidate_name") if isinstance(operational, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise Pass2PipelineError("STEP09_RESULT_INVALID", f"{group.get('candidate_id')} has no final operational candidate name.")
    name = name.strip()
    area = _area(revision.get("revised_area") if revision.get("revised_area") is not None else candidate.get("candidate_area"))
    window = _time_window(init, revision.get("revised_time_window") if revision.get("revised_time_window") is not None else candidate.get("time_window"))
    peaks_raw = candidate.get("peak_forecast_hours")
    peaks = _ordered_unique(_require_list(peaks_raw, code="STEP04_RESULT_INVALID", message=f"{group.get('candidate_id')} has invalid peak forecast hours."))
    try:
        peak_hours = [int(item) for item in peaks]
    except (TypeError, ValueError) as exc:
        raise Pass2PipelineError("STEP04_RESULT_INVALID", f"{group.get('candidate_id')} has invalid peak forecast hours.") from exc
    if any(hour < 0 for hour in peak_hours):
        raise Pass2PipelineError("STEP04_RESULT_INVALID", f"{group.get('candidate_id')} has invalid peak forecast hours.")
    effective_peak_hours = [
        hour for hour in peak_hours
        if window["forecast_hour_start"] <= hour <= window["forecast_hour_end"]
    ]
    if peak_hours and not effective_peak_hours:
        raise Pass2PipelineError(
            "EFFECTIVE_PEAK_TIME_INVALID",
            f"{group.get('candidate_id')} final time window excludes every candidate peak forecast hour.",
        )
    return name, area, window, effective_peak_hours


def _finding_rows(result: dict[str, Any], field: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _require_list(result.get(field), code="STEP07_RESULTS_INVALID", message=f"{result.get('check_id')} {field} must be an array."):
        if not isinstance(item, dict) or not isinstance(item.get("statement"), str):
            raise Pass2PipelineError("STEP07_RESULTS_INVALID", f"{result.get('check_id')} has invalid {field}.")
        _require_list(item.get("evidence_refs"), code="STEP07_RESULTS_INVALID", message=f"{result.get('check_id')} finding has invalid evidence_refs.")
        rows.append(item)
    return rows


def _quantitative_evidence(result: dict[str, Any]) -> list[dict[str, Any]]:
    detailed_refs: set[tuple[str, str, int]] = set()
    request_refs: set[str] = set()
    for field in ("findings", "contradictory_findings"):
        for finding in _finding_rows(result, field):
            for ref in finding["evidence_refs"]:
                if not isinstance(ref, dict) or ref.get("evidence_type") != "quantitative":
                    continue
                request_id = ref.get("request_id")
                feature_id = ref.get("physical_diagnostic")
                forecast_hour = ref.get("forecast_hour")
                if not isinstance(request_id, str) or not request_id:
                    raise Pass2PipelineError("QUANTITATIVE_REFERENCE_INVALID", "Quantitative reference has no request_id.")
                if feature_id is None and forecast_hour is None:
                    request_refs.add(request_id)
                elif isinstance(feature_id, str) and isinstance(forecast_hour, int) and not isinstance(forecast_hour, bool):
                    detailed_refs.add((request_id, feature_id, forecast_hour))
                else:
                    raise Pass2PipelineError("QUANTITATIVE_REFERENCE_INVALID", "Quantitative reference is neither detailed nor request-level.")
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, str, str]] = set()
    claims = result.get("quantitative_claims", [])
    if claims is None:
        claims = []
    for claim in _require_list(claims, code="QUANTITATIVE_CLAIM_INVALID", message=f"{result.get('check_id')} has invalid quantitative_claims."):
        if not isinstance(claim, dict):
            raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", "Quantitative claim must be an object.")
        request_id = claim.get("request_id")
        feature_id = claim.get("physical_diagnostic")
        forecast_hour = claim.get("forecast_hour")
        if not isinstance(request_id, str) or not isinstance(feature_id, str) or not isinstance(forecast_hour, int) or isinstance(forecast_hour, bool):
            raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", "Quantitative claim has invalid identity.")
        if request_id not in request_refs and (request_id, feature_id, forecast_hour) not in detailed_refs:
            continue
        region_id = claim.get("region_id")
        region_name = claim.get("region_name")
        statistic = claim.get("statistic")
        units = claim.get("units")
        if not all(isinstance(item, str) and item for item in (region_id, region_name, statistic, units)):
            raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", "Quantitative claim has invalid region or units.")
        key = (request_id, feature_id, forecast_hour, region_id, statistic)
        if key in seen:
            continue
        seen.add(key)
        location = claim.get("resolved_location")
        if location is None:
            normalized_location = None
        elif isinstance(location, dict):
            normalized_location = {
                "latitude": _number(location.get("latitude"), code="QUANTITATIVE_CLAIM_INVALID", message="Quantitative location latitude is invalid."),
                "longitude": _number(location.get("longitude"), code="QUANTITATIVE_CLAIM_INVALID", message="Quantitative location longitude is invalid."),
            }
        else:
            raise Pass2PipelineError("QUANTITATIVE_CLAIM_INVALID", "Quantitative location is invalid.")
        output.append({
            "request_id": request_id,
            "feature_id": feature_id,
            "forecast_hour": forecast_hour,
            "region_id": region_id,
            "region_name": region_name,
            "statistic": statistic,
            "resolved_value": _number(claim.get("resolved_value"), code="QUANTITATIVE_CLAIM_INVALID", message="Quantitative resolved value is invalid."),
            "units": units,
            "resolved_location": normalized_location,
        })
    return output


def _quantitative_time_series(
    result: dict[str, Any],
    entries: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the complete Step05 operational-region mean time series for one temporal Check.

    Step07 may cite only the quantitative points needed for its prose.  The web
    chart, however, should show the complete Step05 series that the Check was
    asked to evaluate.  This helper performs no meteorological inference; it
    only republishes the stored mean values for temporal_evolution requests.
    """
    check_id = str(result.get("check_id") or "")
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, str]] = set()
    for request_id, entry in sorted(entries.items()):
        request = entry.get("request") if isinstance(entry, dict) else None
        if not isinstance(request, dict) or str(request.get("check_id") or "") != check_id:
            continue
        if request.get("data_type") != "quantitative_excerpt":
            continue
        methods = {str(value) for value in request.get("analysis_methods", [])}
        if "temporal_evolution" not in methods:
            continue
        step05 = entry.get("step05") if isinstance(entry, dict) else None
        stored = step05.get("result") if isinstance(step05, dict) else None
        if not isinstance(stored, dict) or stored.get("status") != "success":
            continue
        features = stored.get("features")
        if not isinstance(features, dict):
            continue
        for feature_id, feature in features.items():
            if not isinstance(feature_id, str) or not isinstance(feature, dict):
                continue
            units = feature.get("units")
            if not isinstance(units, str) or not units:
                continue
            per_hour = feature.get("per_forecast_hour")
            if not isinstance(per_hour, list):
                continue
            for hour_row in per_hour:
                if not isinstance(hour_row, dict):
                    continue
                forecast_hour = hour_row.get("forecast_hour")
                if isinstance(forecast_hour, bool) or not isinstance(forecast_hour, int):
                    continue
                regional = hour_row.get("regional_statistics")
                if not isinstance(regional, list):
                    continue
                for region in regional:
                    if not isinstance(region, dict):
                        continue
                    region_id = region.get("region_id")
                    region_name = region.get("region_name")
                    statistics = region.get("statistics")
                    mean_value = statistics.get("mean") if isinstance(statistics, dict) else None
                    if (
                        not isinstance(region_id, str) or not region_id
                        or not isinstance(region_name, str) or not region_name
                        or isinstance(mean_value, bool) or not isinstance(mean_value, (int, float))
                    ):
                        continue
                    key = (request_id, feature_id, forecast_hour, region_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    output.append({
                        "request_id": request_id,
                        "feature_id": feature_id,
                        "forecast_hour": forecast_hour,
                        "region_id": region_id,
                        "region_name": region_name,
                        "statistic": "mean",
                        "resolved_value": float(mean_value),
                        "units": units,
                        "resolved_location": None,
                    })
    return output


def _request_entries(group: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for entry in _require_list(group.get("analysis_data_requests"), code="STEP06_PACKAGE_INVALID", message=f"{group.get('candidate_id')} has no analysis requests."):
        request = entry.get("request") if isinstance(entry, dict) else None
        request_id = request.get("request_id") if isinstance(request, dict) else None
        if not isinstance(request_id, str) or not request_id or request_id in entries:
            raise Pass2PipelineError("STEP06_PACKAGE_INVALID", f"{group.get('candidate_id')} has invalid analysis request ids.")
        entries[request_id] = entry
    return entries


def _image_candidates(root: Path, result: dict[str, Any], entries: dict[str, dict[str, Any]], field: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    origin = "finding" if field == "findings" else "contradictory"
    for finding in _finding_rows(result, field):
        for ref in finding["evidence_refs"]:
            if not isinstance(ref, dict) or ref.get("evidence_type") != "image":
                continue
            request_id = ref.get("request_id")
            diagnostic = ref.get("physical_diagnostic")
            reference_hour = ref.get("forecast_hour")
            if not isinstance(request_id, str) or not isinstance(diagnostic, str) or not diagnostic:
                raise Pass2PipelineError("EVIDENCE_REF_INVALID", "Image evidence reference is invalid.")
            if reference_hour is not None and (isinstance(reference_hour, bool) or not isinstance(reference_hour, int) or reference_hour < 0):
                raise Pass2PipelineError("EVIDENCE_REF_INVALID", "Image evidence reference has invalid forecast_hour.")
            entry = entries.get(request_id)
            if entry is None:
                raise Pass2PipelineError("STEP05_IMAGE_UNRESOLVED", f"Image reference request is unresolved: {request_id}.")
            step05 = entry.get("step05")
            value = step05.get("result") if isinstance(step05, dict) else None
            # Step06 keeps artifact identity (including multi-time request hours
            # and SHA-256) at step05.generated_images.  The diagnostic result
            # also lists images, but it intentionally lacks that provenance.
            images = step05.get("generated_images") if isinstance(step05, dict) else None
            if not isinstance(images, list):
                images = value.get("generated_images") if isinstance(value, dict) else None
            if not isinstance(images, list):
                raise Pass2PipelineError("STEP05_IMAGE_UNRESOLVED", f"Image reference has no Step05 generated_images: {request_id}.")
            matched = False
            for image in images:
                if not isinstance(image, dict) or image.get("physical_diagnostic") != diagnostic:
                    continue
                source_hour = image.get("forecast_hour")
                request_hours = [hour for hour in image.get("request_forecast_hours", []) if isinstance(hour, int) and not isinstance(hour, bool)] if isinstance(image.get("request_forecast_hours"), list) else []
                is_match = source_hour is None if reference_hour is None else source_hour == reference_hour or (source_hour is None and reference_hour in request_hours)
                if not is_match:
                    continue
                matched = True
                hours = _ordered_unique(([reference_hour] if isinstance(reference_hour, int) else []) + ([source_hour] if isinstance(source_hour, int) else []) + request_hours)
                candidates.append({
                    "request_id": request_id,
                    "physical_diagnostic": diagnostic,
                    "forecast_hour": reference_hour,
                    "source_forecast_hour": source_hour,
                    "request_forecast_hours": request_hours,
                    "source_path": _source_path(root, image.get("path")),
                    "source_identity": str(image.get("path")),
                    "source_sha256": image.get("sha256"),
                    "hours": [int(hour) for hour in hours],
                    "origin": origin,
                })
            if not matched:
                raise Pass2PipelineError("STEP05_IMAGE_UNRESOLVED", f"Image evidence reference does not resolve to Step05 artifact: {request_id}/{diagnostic}/{reference_hour}.")
    return candidates


def _image_selection_key(candidate: dict[str, Any], window: dict[str, Any], peak_hours: list[int]) -> tuple[Any, ...]:
    start = int(window["forecast_hour_start"])
    end = int(window["forecast_hour_end"])
    hours = [int(item) for item in candidate["hours"]]
    in_window = [hour for hour in hours if start <= hour <= end]
    valid_peaks = [hour for hour in peak_hours if start <= hour <= end]
    anchors: list[float] = [float(hour) for hour in valid_peaks] if valid_peaks else [(start + end) / 2]
    distance = min((abs(hour - anchor) for hour in hours for anchor in anchors), default=float("inf"))
    return (
        0 if in_window else 1,
        distance,
        candidate["request_id"],
        candidate["physical_diagnostic"],
        min(hours) if hours else -1,
        candidate["source_identity"],
    )


def _select_representative_image(root: Path, result: dict[str, Any], entries: dict[str, dict[str, Any]], window: dict[str, Any], peak_hours: list[int]) -> dict[str, Any] | None:
    assessment = result.get("assessment")
    if assessment == "insufficient_evidence":
        return None
    findings = _image_candidates(root, result, entries, "findings")
    contradictory = _image_candidates(root, result, entries, "contradictory_findings")
    if assessment in {"observed", "partially_observed"}:
        preferred = findings or contradictory
    elif assessment in {"not_observed", "opposite_observed"}:
        preferred = contradictory or findings
    elif assessment == "uncertain":
        preferred = findings + contradictory
    else:
        raise Pass2PipelineError("STEP07_RESULTS_INVALID", f"Unsupported Step07 assessment: {assessment!r}.")
    return min(preferred, key=lambda item: _image_selection_key(item, window, peak_hours)) if preferred else None


def _select_contradiction_image(root: Path, results: dict[str, dict[str, Any]], entries: dict[str, dict[str, Any]], window: dict[str, Any], peak_hours: list[int]) -> tuple[str, dict[str, Any]] | None:
    candidates: list[tuple[str, dict[str, Any]]] = []
    for check_id, result in results.items():
        for image in _image_candidates(root, result, entries, "contradictory_findings"):
            candidates.append((check_id, image))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (_image_selection_key(item[1], window, peak_hours), item[0]))


def _candidate_same_time_generated_images(
    root: Path,
    entries: dict[str, dict[str, Any]],
    *,
    compatible_diagnostics: set[str],
    forecast_hour: int,
) -> list[dict[str, Any]]:
    """Return exact-time compatible Step05 images from the same candidate.

    Quantitative checks such as temporal evolution may be backed by a JSON-only
    request, while another request for the same candidate already produced the
    matching physical diagnostic PNG.  Keep the quantitative claim bound to its
    own Step07 evidence, but allow the UI to reuse that same-candidate PNG as a
    related visualization when both diagnostic and forecast hour match exactly.
    """
    candidates: list[dict[str, Any]] = []
    for request_id, entry in sorted(entries.items()):
        step05 = entry.get("step05") if isinstance(entry, dict) else None
        value = step05.get("result") if isinstance(step05, dict) else None
        images = step05.get("generated_images") if isinstance(step05, dict) else None
        if not isinstance(images, list):
            images = value.get("generated_images") if isinstance(value, dict) else None
        if not isinstance(images, list):
            continue
        for image in images:
            if not isinstance(image, dict):
                continue
            diagnostic = image.get("physical_diagnostic")
            source_hour = image.get("forecast_hour")
            if diagnostic not in compatible_diagnostics or source_hour != forecast_hour:
                continue
            request_hours = (
                [
                    hour
                    for hour in image.get("request_forecast_hours", [])
                    if isinstance(hour, int) and not isinstance(hour, bool)
                ]
                if isinstance(image.get("request_forecast_hours"), list)
                else []
            )
            candidates.append({
                "request_id": request_id,
                "physical_diagnostic": str(diagnostic),
                "forecast_hour": forecast_hour,
                "source_forecast_hour": source_hour,
                "request_forecast_hours": request_hours,
                "source_path": _source_path(root, image.get("path")),
                "source_identity": str(image.get("path")),
                "source_sha256": image.get("sha256"),
                "hours": [forecast_hour],
                "origin": "candidate_related",
            })
    return candidates


def _representative_spatial_image_candidates(
    root: Path,
    check_evidence: dict[str, Any],
    entries: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return Step04-AI-selected representative spatial maps for a temporal Check."""
    check_id = str(check_evidence.get("check_id") or "")
    intent = check_evidence.get("intent_contract")
    intent = intent if isinstance(intent, dict) else {}
    representative_hours = {
        int(value)
        for value in intent.get("representative_spatial_hours", [])
        if isinstance(value, int) and not isinstance(value, bool)
    }
    if not representative_hours:
        return []
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, str]] = set()
    for request_id, entry in sorted(entries.items()):
        request = entry.get("request") if isinstance(entry, dict) else None
        if (
            not isinstance(request, dict)
            or str(request.get("check_id") or "") != check_id
            or request.get("data_type") != "spatial_field"
        ):
            continue
        step05 = entry.get("step05") if isinstance(entry, dict) else None
        stored = step05.get("result") if isinstance(step05, dict) else None
        images = step05.get("generated_images") if isinstance(step05, dict) else None
        if not isinstance(images, list):
            images = stored.get("generated_images") if isinstance(stored, dict) else None
        if not isinstance(images, list):
            continue
        for image in images:
            if not isinstance(image, dict):
                continue
            hour = image.get("forecast_hour")
            diagnostic = image.get("physical_diagnostic")
            if (
                isinstance(hour, bool) or not isinstance(hour, int)
                or hour not in representative_hours
                or not isinstance(diagnostic, str) or not diagnostic
            ):
                continue
            source_path = _source_path(root, image.get("path"))
            key = (request_id, diagnostic, hour, str(image.get("path")))
            if key in seen:
                continue
            seen.add(key)
            output.append({
                "request_id": request_id,
                "physical_diagnostic": diagnostic,
                "forecast_hour": hour,
                "source_forecast_hour": hour,
                "request_forecast_hours": [
                    int(value)
                    for value in image.get("request_forecast_hours", request.get("forecast_hours", []))
                    if isinstance(value, int) and not isinstance(value, bool)
                ],
                "source_path": source_path,
                "source_identity": str(image.get("path")),
                "source_sha256": image.get("sha256"),
                "hours": [hour],
                "origin": "ai_representative_temporal_map",
            })
    return sorted(output, key=lambda item: (int(item["forecast_hour"]), item["physical_diagnostic"], item["request_id"]))


def _select_same_time_related_image(
    root: Path,
    result: dict[str, Any],
    entries: dict[str, dict[str, Any]],
    *,
    feature_id: str,
    forecast_hour: int,
) -> dict[str, Any] | None:
    compatible_diagnostics = set(QUANTITATIVE_IMAGE_DIAGNOSTICS.get(feature_id, ()))
    if not compatible_diagnostics:
        return None

    # Prefer images explicitly cited by the current Step07 check.
    direct_candidates = [
        image
        for field in ("findings", "contradictory_findings")
        for image in _image_candidates(root, result, entries, field)
        if image["physical_diagnostic"] in compatible_diagnostics
        and image.get("source_forecast_hour") == forecast_hour
    ]
    if direct_candidates:
        return min(
            direct_candidates,
            key=lambda item: (
                0 if item["origin"] == "finding" else 1,
                item["request_id"],
                item["physical_diagnostic"],
                item["source_identity"],
            ),
        )

    # A temporal/quantitative check can be JSON-only.  In that case, reuse an
    # already generated image from another request of the same candidate only
    # when physical diagnostic and forecast hour are both exact matches.
    candidate_candidates = _candidate_same_time_generated_images(
        root,
        entries,
        compatible_diagnostics=compatible_diagnostics,
        forecast_hour=forecast_hour,
    )
    if not candidate_candidates:
        return None
    return min(
        candidate_candidates,
        key=lambda item: (
            item["request_id"],
            item["physical_diagnostic"],
            item["source_identity"],
        ),
    )


def _verify_source_image(candidate: dict[str, Any]) -> tuple[Path, str]:
    source = candidate["source_path"]
    if not isinstance(source, Path) or not source.is_file():
        raise Pass2PipelineError("STEP05_IMAGE_MISSING", f"Selected Step05 image is missing: {source}")
    if source.suffix.lower() != ".png":
        raise Pass2PipelineError("STEP05_IMAGE_INVALID", f"Selected Step05 evidence is not a PNG: {source.name}")
    content_hash = sha256sum(source)
    declared_hash = candidate.get("source_sha256")
    if isinstance(declared_hash, str) and declared_hash and declared_hash.lower() != content_hash:
        raise Pass2PipelineError("STEP05_IMAGE_HASH_MISMATCH", f"Selected Step05 image hash does not match its Step06 metadata: {source.name}")
    return source, content_hash


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _asset_filename_component(value: Any) -> str:
    component = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_")
    return component or "UNKNOWN"


def _asset_forecast_info(selection: dict[str, Any]) -> str:
    source_hour = selection.get("source_forecast_hour")
    if isinstance(source_hour, int) and not isinstance(source_hour, bool) and source_hour >= 0:
        return f"F{source_hour:03d}"
    request_hours = sorted({hour for hour in selection.get("request_forecast_hours", []) if isinstance(hour, int) and not isinstance(hour, bool) and hour >= 0})
    if len(request_hours) == 1:
        return f"F{request_hours[0]:03d}"
    if request_hours:
        return f"F{request_hours[0]:03d}-F{request_hours[-1]:03d}"
    reference_hour = selection.get("forecast_hour")
    if isinstance(reference_hour, int) and not isinstance(reference_hour, bool) and reference_hour >= 0:
        return f"F{reference_hour:03d}"
    return "MULTITIME"


def _versioned_asset_path(relative_path: str, content_hash: str | None) -> str:
    """Return a cache-busted local asset URL without changing the stored relative path."""
    value = str(relative_path)
    digest = str(content_hash or "").strip().lower()
    if not digest:
        return value
    separator = "&" if "?" in value else "?"
    return f"{value}{separator}v={digest[:12]}"


def _asset_destination(stage: Path, selection: dict[str, Any], content_hash: str) -> tuple[Path, str]:
    asset_dir = stage / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    stem = "_".join((
        _asset_filename_component(selection["request_id"]),
        _asset_filename_component(selection["physical_diagnostic"]),
        _asset_forecast_info(selection),
    ))
    filename = f"{stem}.png"
    destination = asset_dir / filename
    if destination.exists():
        filename = f"{stem}__{content_hash[:8]}.png"
        destination = asset_dir / filename
        duplicate_index = 1
        while destination.exists():
            filename = f"{stem}__dup{duplicate_index:02d}_{content_hash[:8]}.png"
            destination = asset_dir / filename
            duplicate_index += 1
    return destination, f"assets/{filename}"


def _publish_asset(stage: Path, assets_by_hash: dict[str, dict[str, Any]], candidate_id: str, check_id: str, selection: dict[str, Any]) -> str:
    source, content_hash = _verify_source_image(selection)
    asset_id = f"AST_{content_hash[:12]}"
    asset = assets_by_hash.get(content_hash)
    if asset is None:
        destination, relative_path = _asset_destination(stage, selection, content_hash)
        try:
            shutil.copy2(source, destination)
        except OSError as exc:
            raise Pass2PipelineError("ASSET_COPY_FAILED", f"Unable to copy selected evidence image: {source.name}") from exc
        asset = {
            "asset_id": asset_id,
            "relative_path": relative_path,
            "media_type": "image/png",
            "sha256": content_hash,
            "candidate_ids": [],
            "check_ids": [],
            "source_refs": [],
        }
        assets_by_hash[content_hash] = asset
    _append_unique(asset["candidate_ids"], candidate_id)
    _append_unique(asset["check_ids"], check_id)
    source_ref = {
        "request_id": selection["request_id"],
        "physical_diagnostic": selection["physical_diagnostic"],
        "forecast_hour": selection["forecast_hour"],
    }
    if source_ref not in asset["source_refs"]:
        asset["source_refs"].append(source_ref)
    return asset_id


def _decision_evidence(signal: dict[str, Any], field_name: str) -> list[dict[str, str]]:
    values = _require_list(signal.get(field_name), code="PUBLICATION_CHECK_INVALID", message=f"{field_name} must be an array.")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(values):
        if not isinstance(item, dict) or set(item) != {"check_id", "effect", "reason"}:
            raise Pass2PipelineError("PUBLICATION_CHECK_INVALID", f"{field_name}[{index}] must contain check_id, effect and reason only.")
        check_id = str(item.get("check_id") or "")
        effect = str(item.get("effect") or "")
        reason = item.get("reason")
        if not check_id or check_id in seen or effect not in DECISION_EVIDENCE_EFFECTS or not isinstance(reason, str) or not reason.strip():
            raise Pass2PipelineError("PUBLICATION_CHECK_INVALID", f"{field_name}[{index}] is invalid.")
        seen.add(check_id)
        result.append({"check_id": check_id, "effect": effect, "reason": reason.strip()})
    return result



def _decision_meanings_for_check(signal: dict[str, Any], check_id: str) -> list[dict[str, str]]:
    """Return existing Step08 reasoning linked to one Step07 Check.

    This is a presentation mapping only.  It does not create new meteorological
    prose; it exposes the already validated Step08 mechanism/monitoring reasons
    next to the detailed Step07 evidence.
    """
    meanings: list[dict[str, str]] = []
    for field_name, axis_label in (
        ("mechanism_evidence", "기상학적 의미"),
        ("monitoring_evidence", "감시상 의미"),
    ):
        for item in _decision_evidence(signal, field_name):
            if item["check_id"] != check_id:
                continue
            meanings.append({
                "axis": field_name,
                "axis_label": axis_label,
                "effect": item["effect"],
                "effect_label": DECISION_EFFECT_LABELS.get(item["effect"], item["effect"]),
                "reason": item["reason"],
            })
    return meanings


def _publication_check_ids(
    signal: dict[str, Any], question_answers: list[dict[str, Any]] | None = None,
) -> list[str]:
    values: list[Any] = list(_require_list(signal.get("signal_evidence_check_ids"), code="PUBLICATION_CHECK_INVALID", message="signal_evidence_check_ids must be an array."))
    evidence = _require_list(signal.get("validated_precursor_evidence"), code="PUBLICATION_CHECK_INVALID", message="validated_precursor_evidence must be an array.")
    for item in evidence:
        if not isinstance(item, dict):
            raise Pass2PipelineError("PUBLICATION_CHECK_INVALID", "validated precursor evidence must be an object.")
        values.extend(_require_list(item.get("evidence_check_ids"), code="PUBLICATION_CHECK_INVALID", message="validated precursor evidence ids must be an array."))
    values.extend(_require_list(signal.get("spatiotemporal_alignment_evidence_check_ids"), code="PUBLICATION_CHECK_INVALID", message="spatiotemporal_alignment_evidence_check_ids must be an array."))
    values.extend(_require_list(signal.get("environment_persistence_evidence_check_ids"), code="PUBLICATION_CHECK_INVALID", message="environment_persistence_evidence_check_ids must be an array."))
    for field_name in ("mechanism_evidence", "monitoring_evidence"):
        values.extend(item["check_id"] for item in _decision_evidence(signal, field_name))
    # Question-only Step07 Checks are still publication evidence, not extra
    # Step08/09 decision support. Keep them independent of monitoring tiers.
    for answer in question_answers or []:
        values.extend(answer["evidence_check_ids"])
    result = _ordered_unique(values)
    if not result:
        raise Pass2PipelineError("PUBLICATION_CHECK_INVALID", "Surface candidate has no publication checks.")
    return result


def _require_usable_question_evidence(
    candidate_id: str,
    answers: list[dict[str, Any]],
    step07_checks: dict[str, dict[str, Any]],
) -> None:
    """Fail closed if a question references a non-usable or absent Step07 Check."""
    check_ids = {check_id for answer in answers for check_id in answer["evidence_check_ids"]}
    for check_id in sorted(check_ids):
        result = step07_checks.get(check_id)
        if (
            not isinstance(result, dict)
            or result.get("evidence_status") != "complete"
            or result.get("usable_for_synthesis") is not True
        ):
            raise Pass2PipelineError(
                "QUESTION_EVIDENCE_UNUSABLE",
                f"{candidate_id} question references a missing or excluded Step07 Check: {check_id}",
            )


def _assert_question_evidence_published(candidate: dict[str, Any]) -> None:
    """Every question citation must resolve to a published Check for this candidate."""
    block_ids = {block["check_id"] for block in candidate["evidence_blocks"]}
    for answer in candidate["forecaster_question_answers"]:
        missing_ids = set(answer["evidence_check_ids"]) - block_ids
        if missing_ids:
            raise Pass2PipelineError(
                "QUESTION_EVIDENCE_NOT_PUBLISHED",
                f"{candidate['candidate_id']}: forecaster question references non-published Checks: {sorted(missing_ids)}",
            )


def _related_non_surface_asset_ids(
    root: Path,
    stage: Path,
    assets_by_hash: dict[str, dict[str, Any]],
    candidate_id: str,
    signal: dict[str, Any],
    step07_checks: dict[str, dict[str, Any]],
    entries: dict[str, dict[str, Any]],
    window: dict[str, Any],
    peak_hours: list[int],
    *,
    limit: int = 3,
) -> list[str]:
    """Return up to ``limit`` representative Step05 image assets for a non-surfaced candidate."""
    asset_ids: list[str] = []
    for check_id in _publication_check_ids(signal):
        result = step07_checks.get(check_id)
        if not isinstance(result, dict):
            continue
        selection = _select_representative_image(root, result, entries, window, peak_hours)
        if selection is None:
            continue
        asset_id = _publish_asset(stage, assets_by_hash, candidate_id, check_id, selection)
        if asset_id not in asset_ids:
            asset_ids.append(asset_id)
        if len(asset_ids) >= limit:
            break
    return asset_ids


def _image_candidates_from_rows(
    root: Path,
    rows: Any,
    entries: dict[str, dict[str, Any]],
    *,
    origin: str,
) -> list[dict[str, Any]]:
    """Resolve minority-report finding rows to deterministic Step05 image candidates."""
    candidates: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return candidates
    for finding in rows:
        if not isinstance(finding, dict):
            continue
        evidence_refs = finding.get("evidence_refs")
        if not isinstance(evidence_refs, list):
            continue
        for ref in evidence_refs:
            if not isinstance(ref, dict) or ref.get("evidence_type") != "image":
                continue
            request_id = ref.get("request_id")
            diagnostic = ref.get("physical_diagnostic")
            reference_hour = ref.get("forecast_hour")
            if not isinstance(request_id, str) or not isinstance(diagnostic, str) or not diagnostic:
                raise Pass2PipelineError("EVIDENCE_REF_INVALID", "Minority-report image evidence reference is invalid.")
            if reference_hour is not None and (isinstance(reference_hour, bool) or not isinstance(reference_hour, int) or reference_hour < 0):
                raise Pass2PipelineError("EVIDENCE_REF_INVALID", "Minority-report image evidence reference has invalid forecast_hour.")
            entry = entries.get(request_id)
            if entry is None:
                raise Pass2PipelineError("STEP05_IMAGE_UNRESOLVED", f"Minority-report image reference request is unresolved: {request_id}.")
            step05 = entry.get("step05")
            value = step05.get("result") if isinstance(step05, dict) else None
            images = step05.get("generated_images") if isinstance(step05, dict) else None
            if not isinstance(images, list):
                images = value.get("generated_images") if isinstance(value, dict) else None
            if not isinstance(images, list):
                raise Pass2PipelineError("STEP05_IMAGE_UNRESOLVED", f"Minority-report image reference has no Step05 generated_images: {request_id}.")
            matched = False
            for image in images:
                if not isinstance(image, dict) or image.get("physical_diagnostic") != diagnostic:
                    continue
                source_hour = image.get("forecast_hour")
                request_hours = [hour for hour in image.get("request_forecast_hours", []) if isinstance(hour, int) and not isinstance(hour, bool)] if isinstance(image.get("request_forecast_hours"), list) else []
                is_match = source_hour is None if reference_hour is None else source_hour == reference_hour or (source_hour is None and reference_hour in request_hours)
                if not is_match:
                    continue
                matched = True
                hours = _ordered_unique(([reference_hour] if isinstance(reference_hour, int) else []) + ([source_hour] if isinstance(source_hour, int) else []) + request_hours)
                candidates.append({
                    "request_id": request_id,
                    "physical_diagnostic": diagnostic,
                    "forecast_hour": reference_hour,
                    "source_forecast_hour": source_hour,
                    "request_forecast_hours": request_hours,
                    "source_path": _source_path(root, image.get("path")),
                    "source_identity": str(image.get("path")),
                    "source_sha256": image.get("sha256"),
                    "hours": [int(hour) for hour in hours],
                    "origin": origin,
                })
            if not matched:
                raise Pass2PipelineError("STEP05_IMAGE_UNRESOLVED", f"Minority-report image reference does not resolve to Step05 artifact: {request_id}/{diagnostic}/{reference_hour}.")
    return candidates


def _select_minority_member_representative_image(
    root: Path,
    member_report: dict[str, Any],
    entries: dict[str, dict[str, Any]],
    window: dict[str, Any],
    peak_hours: list[int],
) -> dict[str, Any] | None:
    assessment = str(member_report.get("assessment") or "")
    findings = _image_candidates_from_rows(root, member_report.get("findings"), entries, origin="minority_finding")
    contradictory = _image_candidates_from_rows(root, member_report.get("contradictory_findings"), entries, origin="minority_contradictory")
    if assessment in {"observed", "partially_observed"}:
        preferred = findings or contradictory
    elif assessment in {"not_observed", "opposite_observed"}:
        preferred = contradictory or findings
    elif assessment == "uncertain":
        preferred = findings + contradictory
    else:
        preferred = findings + contradictory
    return min(preferred, key=lambda item: _image_selection_key(item, window, peak_hours)) if preferred else None


def _minority_report_asset_map(
    root: Path,
    minority_report: dict[str, Any],
    groups_by_id: dict[str, dict[str, Any]],
    syntheses: dict[str, dict[str, Any]],
    assets_by_hash: dict[str, dict[str, Any]],
    stage: Path,
) -> list[dict[str, Any]]:
    """Publish representative Step05 images for minority-report cards and return a render map."""
    entries_out: list[dict[str, Any]] = []
    report_entries = minority_report.get("entries") if isinstance(minority_report.get("entries"), list) else []
    for report_entry in report_entries:
        if not isinstance(report_entry, dict):
            continue
        candidate_id = str(report_entry.get("candidate_id") or "")
        check_id = str(report_entry.get("check_id") or "")
        group = groups_by_id.get(candidate_id)
        synthesis = syntheses.get(candidate_id)
        if not candidate_id or not check_id or not isinstance(group, dict) or not isinstance(synthesis, dict):
            continue
        _name, _area, window, peak_hours = _effective_context(
            str(minority_report.get("init") or ""),
            group,
            synthesis,
            {"display_candidate_name": str(((group.get("candidate_context") or {}).get("candidate") or {}).get("candidate_name") or candidate_id)},
        )
        requests = _request_entries(group)
        member_assets: list[dict[str, Any]] = []
        for member in report_entry.get("member_reports", []):
            if not isinstance(member, dict):
                continue
            member_index = member.get("member_index")
            if not isinstance(member_index, int) or isinstance(member_index, bool):
                continue
            selection = _select_minority_member_representative_image(root, member, requests, window, peak_hours)
            if selection is None:
                continue
            asset_id = _publish_asset(stage, assets_by_hash, candidate_id, check_id, selection)
            member_assets.append({
                "member_index": int(member_index),
                "asset_ids": [asset_id],
            })
        if member_assets:
            entries_out.append({
                "candidate_id": candidate_id,
                "check_id": check_id,
                "member_assets": member_assets,
            })
    return entries_out


def _environment_localization_image_selections(
    root: Path,
    result: dict[str, Any],
    entries: dict[str, dict[str, Any]],
    forecast_hour: int,
    signal_components: list[str] | tuple[str, ...],
) -> list[dict[str, Any]]:
    """Return one exact-time evidence set for the focus spatial viewer.

    Only positive Step07 finding references are eligible.  All diagnostics
    requested by this alignment Check must come from the same Step04/05 request so the UI cannot combine maps
    produced for different spatial scopes.  This function selects existing
    evidence only; it does not calculate a new meteorological overlap mask.
    """
    required_diagnostics = _environment_localization_required_diagnostics(signal_components)
    required = tuple(item[0] for item in required_diagnostics)
    if len(required) < 2:
        return []
    by_request: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for selection in _image_candidates(root, result, entries, "findings"):
        diagnostic = str(selection.get("physical_diagnostic") or "")
        if diagnostic not in required or selection.get("source_forecast_hour") != forecast_hour:
            continue
        request_id = str(selection.get("request_id") or "")
        if not request_id:
            continue
        by_request.setdefault(request_id, {}).setdefault(diagnostic, []).append(selection)
    for request_id in sorted(by_request):
        grouped = by_request[request_id]
        if any(not grouped.get(diagnostic) for diagnostic in required):
            continue
        return [
            min(
                grouped[diagnostic],
                key=lambda item: (
                    str(item.get("source_identity") or ""),
                    str(item.get("request_id") or ""),
                ),
            )
            for diagnostic in required
        ]
    return []


def _environment_localization_gallery(
    candidate: dict[str, Any],
    assets_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    focus = candidate.get("environment_localization")
    if not isinstance(focus, dict):
        return []
    required_diagnostics = _environment_localization_required_diagnostics(
        _environment_localization_components(focus)
    )
    if len(required_diagnostics) < 2:
        return []
    check_id = focus.get("evidence_check_id")
    hour = focus.get("forecast_hour")
    candidate_id = str(candidate.get("candidate_id") or "")
    if not isinstance(check_id, str) or not check_id or isinstance(hour, bool) or not isinstance(hour, int):
        return []
    by_diagnostic: dict[str, dict[str, Any]] = {}
    for asset in assets_by_id.values():
        if candidate_id not in asset.get("candidate_ids", []) or check_id not in asset.get("check_ids", []):
            continue
        for source in asset.get("source_refs", []):
            if not isinstance(source, dict) or source.get("forecast_hour") != hour:
                continue
            diagnostic = str(source.get("physical_diagnostic") or "")
            if diagnostic not in {item[0] for item in required_diagnostics}:
                continue
            current = by_diagnostic.get(diagnostic)
            if current is None or str(asset.get("asset_id")) < str(current.get("asset_id")):
                by_diagnostic[diagnostic] = asset
    if any(diagnostic not in by_diagnostic for diagnostic, _label in required_diagnostics):
        return []
    return [
        {
            "relative_path": _versioned_asset_path(
                str(by_diagnostic[diagnostic]["relative_path"]),
                str(by_diagnostic[diagnostic].get("sha256") or ""),
            ),
            "label": label,
        }
        for diagnostic, label in required_diagnostics
    ]


def _evidence_presentation_bucket(
    check_specification: dict[str, Any] | None,
    check_evidence: dict[str, Any] | None,
) -> int:
    """Return the forecaster-facing physical-process order for one Check.

    This ordering is presentation-only. It does not alter Step04 planning,
    Step07 execution, Step08 synthesis, publication provenance, or Check IDs.
    """
    specification = check_specification if isinstance(check_specification, dict) else check_evidence
    if not isinstance(specification, dict):
        return 7
    intent = specification.get("intent_contract")
    intent = intent if isinstance(intent, dict) else {}
    check_scope = str(specification.get("check_scope") or intent.get("check_scope") or "")
    verification_mode = str(specification.get("verification_mode") or intent.get("verification_mode") or "")
    variable_role = str(specification.get("variable_role") or "")
    components = {
        str(value)
        for value in (
            intent.get("signal_components")
            or specification.get("required_signal_components")
            or []
        )
        if str(value)
    }
    direct_anchor = bool(intent.get("direct_precipitation_anchor"))

    # 1) Direct-precipitation presence anchor.
    if direct_anchor:
        return 0
    # 7) Multivariable overlap is deliberately shown after the individual
    # physical-process checks, even though it may have appeared earlier in
    # Step08 publication evidence collection.
    if check_scope == "multivariable_alignment" or verification_mode == "multivariable_alignment":
        return 6
    # 2) Precipitation evolution / movement / persistence / intensity checks.
    if variable_role == "hazard_magnitude" or "direct_precipitation" in components:
        return 1
    # 3–6) Individual environment processes.
    if variable_role == "moisture_supply" or components == {"moisture_supply"}:
        return 2
    if variable_role == "low_level_forcing" or components == {"low_level_forcing"}:
        return 3
    if variable_role in {"midlevel_forcing", "mid_upper_forcing"} or components == {"mid_upper_forcing"}:
        return 4
    if variable_role == "organization_persistence" or components == {"organization_persistence"}:
        return 5
    # Thermodynamic and other supplemental diagnostics remain in "기타"
    # unless a dedicated forecaster-facing chain node is introduced later.
    return 7


def _sort_evidence_blocks_for_presentation(
    blocks: list[dict[str, Any]],
    *,
    check_specifications_by_id: dict[str, dict[str, Any]],
    check_evidence_by_id: dict[str, dict[str, Any]],
    publication_checks: list[str],
) -> None:
    """Sort detailed evidence into a meteorological reading sequence.

    The original ``publication_check_ids`` order is intentionally preserved as
    provenance. Only ``evidence_blocks`` are reordered for Step10 presentation.
    """
    publication_index = {check_id: index for index, check_id in enumerate(publication_checks)}
    plan_index = {check_id: index for index, check_id in enumerate(check_specifications_by_id)}

    def key(block: dict[str, Any]) -> tuple[int, int, int, str]:
        check_id = str(block.get("check_id") or "")
        return (
            _evidence_presentation_bucket(
                check_specifications_by_id.get(check_id),
                check_evidence_by_id.get(check_id),
            ),
            plan_index.get(check_id, 10_000),
            publication_index.get(check_id, 10_000),
            check_id,
        )

    blocks.sort(key=key)


def _roles(
    signal: dict[str, Any],
    check_id: str,
    check_evidence: dict[str, Any] | None = None,
    check_specification: dict[str, Any] | None = None,
) -> list[str]:
    roles: list[str] = []
    if check_id in {str(item) for item in signal.get("signal_evidence_check_ids", [])}:
        roles.append("signal_evidence")
    for evidence in signal.get("validated_precursor_evidence", []):
        if not isinstance(evidence, dict):
            continue
        category = evidence.get("category")
        if isinstance(category, str) and check_id in {str(item) for item in evidence.get("evidence_check_ids", [])}:
            _append_unique(roles, category)
    role_by_axis = {
        "mechanism_evidence": {
            "supporting": "mechanism_support",
            "limiting": "mechanism_limit",
            "mixed": "mechanism_mixed",
            "context": "mechanism_context",
        },
        "monitoring_evidence": {
            "supporting": "monitoring_support",
            "limiting": "monitoring_limit",
            "mixed": "monitoring_mixed",
            "context": "monitoring_context",
        },
    }
    for field_name, effect_roles in role_by_axis.items():
        for evidence in _decision_evidence(signal, field_name):
            if evidence["check_id"] == check_id:
                _append_unique(roles, effect_roles[evidence["effect"]])
    if check_id in {str(item) for item in signal.get("spatiotemporal_alignment_evidence_check_ids", [])}:
        _append_unique(roles, "spatiotemporal_alignment")
        # A positive/partial multivariable alignment check contains direct
        # evidence about each component participating in the overlap, even
        # though it is not an independent category check.  Preserve those
        # component roles so the signal-chain UI can distinguish
        # "independent check absent" from "related alignment evidence exists".
        if isinstance(check_evidence, dict) and check_evidence.get("check_scope") == "multivariable_alignment":
            for component in check_evidence.get("required_signal_components", []):
                component = str(component)
                if component in {
                    "direct_precipitation",
                    "moisture_supply",
                    "low_level_forcing",
                    "mid_upper_forcing",
                }:
                    _append_unique(roles, component)

    # Preserve the existence and polarity of independently executed environment
    # Checks even when they are not positive ``validated_precursor_evidence``.
    # This prevents an opposing/limiting Check such as "mid-level ascent does
    # not strengthen" from disappearing from the signal-chain as
    # "개별 판독 없음".  Hazard-magnitude Checks are intentionally excluded
    # here so movement/intensity Checks do not override the dedicated direct-
    # precipitation presence node.
    specification = check_specification if isinstance(check_specification, dict) else check_evidence
    if isinstance(specification, dict) and specification.get("check_scope") != "multivariable_alignment":
        intent = specification.get("intent_contract")
        intent = intent if isinstance(intent, dict) else {}
        intent_components = {
            str(value)
            for value in (
                intent.get("signal_components")
                or specification.get("required_signal_components")
                or []
            )
            if str(value)
        }
        # The system-owned direct-precipitation presence anchor is intentionally
        # variable_role=hazard_magnitude, so the generic variable-role map must
        # not be used for ordinary movement/intensity Checks.  Preserve only the
        # explicit anchor as an independent direct-precipitation readout.
        if (
            intent.get("direct_precipitation_anchor") is True
            and intent_components == {"direct_precipitation"}
        ):
            _append_unique(roles, "direct_precipitation")
        independent_role = INDEPENDENT_VARIABLE_ROLE_TO_SIGNAL_ROLE.get(
            str(specification.get("variable_role") or "")
        )
        if independent_role:
            _append_unique(roles, independent_role)
        if independent_role or "direct_precipitation" in roles:
            evidence_role = str(intent.get("evidence_role") or "")
            token = INTENT_EVIDENCE_ROLE_TOKENS.get(evidence_role)
            if token:
                _append_unique(roles, token)
    if not roles:
        raise Pass2PipelineError("PUBLICATION_ROLE_INVALID", f"Publication check has no deterministic role: {check_id}.")
    return roles



def _scope_context_for_check(
    result: dict[str, Any],
    check_evidence: dict[str, Any] | None,
    check_specification: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build deterministic time/space scope metadata for Step10 aggregation.

    This metadata does not decide meteorological polarity.  It only preserves
    the structured scope already present in Step04/06/07 so Step10 can avoid
    merging support and limitation that refer to different decision axes,
    times, or regions.
    """
    specification = check_specification if isinstance(check_specification, dict) else {}
    evidence = check_evidence if isinstance(check_evidence, dict) else {}
    intent = specification.get("intent_contract") if isinstance(specification.get("intent_contract"), dict) else {}

    check_scope = str(specification.get("check_scope") or evidence.get("check_scope") or "")
    verification_mode = str(intent.get("verification_mode") or "")
    if check_scope == "temporal_evolution" or verification_mode in {"temporal_change", "spatial_evolution"}:
        decision_axis = "temporal_evolution"
    elif check_scope in {"multivariable_alignment", "single_variable", "vertical_structure"} or verification_mode in {
        "multivariable_alignment",
        "precipitation_amount",
        "spatial_distribution",
        "vertical_structure",
    }:
        decision_axis = "core_state"
    else:
        decision_axis = check_scope or verification_mode or "unknown"

    forecast_hours: set[int] = set()
    for raw in evidence.get("allowed_quantitative_forecast_hours", []):
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
            forecast_hours.add(int(raw))
    for raw in intent.get("representative_spatial_hours", []):
        if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
            forecast_hours.add(int(raw))

    localization = result.get("spatial_localization")
    if isinstance(localization, dict):
        raw_hour = localization.get("forecast_hour")
        if isinstance(raw_hour, int) and not isinstance(raw_hour, bool) and raw_hour >= 0:
            forecast_hours.add(int(raw_hour))

    region_ids: set[str] = set()
    if isinstance(localization, dict):
        for item in localization.get("regions", []):
            if isinstance(item, dict) and str(item.get("region_id") or ""):
                region_ids.add(str(item["region_id"]))

    for claim in result.get("quantitative_claims", []):
        if not isinstance(claim, dict):
            continue
        raw_hour = claim.get("forecast_hour")
        if isinstance(raw_hour, int) and not isinstance(raw_hour, bool) and raw_hour >= 0:
            forecast_hours.add(int(raw_hour))
        region_id = str(claim.get("region_id") or "")
        if region_id:
            region_ids.add(region_id)

    for relation in result.get("quantitative_relations", []):
        if not isinstance(relation, dict):
            continue
        for point in relation.get("points", []):
            if not isinstance(point, dict):
                continue
            raw_hour = point.get("forecast_hour")
            if isinstance(raw_hour, int) and not isinstance(raw_hour, bool) and raw_hour >= 0:
                forecast_hours.add(int(raw_hour))
            region_id = str(point.get("region_id") or "")
            if region_id:
                region_ids.add(region_id)

    for field in ("findings", "contradictory_findings"):
        for finding in result.get(field, []):
            if not isinstance(finding, dict):
                continue
            for ref in finding.get("evidence_refs", []):
                if not isinstance(ref, dict):
                    continue
                raw_hour = ref.get("forecast_hour")
                if isinstance(raw_hour, int) and not isinstance(raw_hour, bool) and raw_hour >= 0:
                    forecast_hours.add(int(raw_hour))
                region_id = str(ref.get("region_id") or "")
                if region_id:
                    region_ids.add(region_id)

    return {
        "decision_axis": decision_axis,
        "check_scope": check_scope,
        "forecast_hours": sorted(forecast_hours),
        "region_ids": sorted(region_ids),
        "target_ids": sorted({str(value) for value in specification.get("target_ids", []) if str(value)}),
        "phase_ids": sorted({str(value) for value in specification.get("phase_ids", []) if str(value)}),
        "domain_scope": str(intent.get("domain_scope") or ""),
        "spatial_relation_scope": str(intent.get("spatial_relation_scope") or ""),
    }

def _evidence_block(
    result: dict[str, Any],
    signal: dict[str, Any],
    check_id: str,
    check_evidence: dict[str, Any] | None = None,
    entries: dict[str, dict[str, Any]] | None = None,
    check_specification: dict[str, Any] | None = None,
    *, question_only: bool = False,
) -> dict[str, Any]:
    if check_specification is not None and _single_majority_convergence_not_strengthen_claim(check_specification) is not None and result.get("majority_aggregation") is None:
        raise Pass2PipelineError("QUANTITATIVE_NARRATIVE_INVALID", f"{check_id}: majority aggregation missing")
    try:
        assert_majority_narrative_current(result)
        result = canonicalize_majority_result(result)
    except MajorityNarrativeError as exc:
        raise Pass2PipelineError("QUANTITATIVE_NARRATIVE_INVALID", f"{check_id}: {exc}") from exc
    expected_signal = result.get("expected_signal")
    assessment = result.get("assessment")
    if not isinstance(expected_signal, str) or not expected_signal or not isinstance(assessment, str):
        raise Pass2PipelineError("STEP07_RESULTS_INVALID", f"{check_id} has invalid evidence block fields.")
    return {
        "check_id": check_id,
        "expected_signal": expected_signal,
        "assessment": assessment,
        "ensemble_consensus": copy.deepcopy(result.get("ensemble_consensus") or {}),
        "roles": ["forecaster_question_reference"] if question_only else _roles(signal, check_id, check_evidence, check_specification),
        "scope_context": _scope_context_for_check(result, check_evidence, check_specification),
        "decision_relevance": str(result.get("decision_relevance") or ""),
        "decision_meanings": _decision_meanings_for_check(signal, check_id),
        "majority_aggregation": copy.deepcopy(result.get("majority_aggregation")) if isinstance(result.get("majority_aggregation"), dict) else None,
        "findings": [item["statement"] for item in _finding_rows(result, "findings")],
        "contradictory_findings": [item["statement"] for item in _finding_rows(result, "contradictory_findings")],
        "uncertainties": [str(item) for item in _require_list(result.get("uncertainties"), code="STEP07_RESULTS_INVALID", message=f"{check_id} uncertainties must be an array.")],
        "quantitative_evidence": _quantitative_evidence(result),
        "quantitative_time_series": _quantitative_time_series(result, entries or {}),
        "asset_ids": [],
    }


def _question_answers(value: Any) -> list[dict[str, Any]]:
    answers: list[dict[str, Any]] = []
    for item in _require_list(value, code="STEP08_RESULT_INVALID", message="forecaster_question_answers must be an array."):
        if not isinstance(item, dict):
            raise Pass2PipelineError("STEP08_RESULT_INVALID", "forecaster_question_answer must be an object.")
        try:
            answer = {
                "forecaster_question_id": item["forecaster_question_id"],
                "question": item["question"],
                "answer": item["answer"],
                "evidence_check_ids": _ordered_unique(_require_list(item["evidence_check_ids"], code="STEP08_RESULT_INVALID", message="question evidence_check_ids must be an array.")),
                "uncertainties": [str(entry) for entry in _require_list(item["uncertainties"], code="STEP08_RESULT_INVALID", message="question uncertainties must be an array.")],
            }
        except KeyError as exc:
            raise Pass2PipelineError("STEP08_RESULT_INVALID", "forecaster_question_answer is incomplete.") from exc
        if not all(isinstance(answer[key], str) and answer[key] for key in ("forecaster_question_id", "question", "answer")):
            raise Pass2PipelineError("STEP08_RESULT_INVALID", "forecaster_question_answer has invalid text.")
        answers.append(answer)
    return answers


def _surface_candidate(
    root: Path,
    init: str,
    group: dict[str, Any],
    synthesis: dict[str, Any],
    operational: dict[str, Any],
    step07_checks: dict[str, dict[str, Any]],
    operational_brief: dict[str, Any] | None,
    operational_guidance: dict[str, Any] | None,
    monitoring_emphasis: str,
    assets_by_hash: dict[str, dict[str, Any]],
    stage: Path,
) -> dict[str, Any]:
    candidate_id = str(group["candidate_id"])
    signal = synthesis.get("signal_assessment")
    if not isinstance(signal, dict):
        raise Pass2PipelineError("STEP08_RESULT_INVALID", f"{candidate_id} signal_assessment is invalid.")
    name, area, window, peak_hours = _effective_context(init, group, synthesis, operational)
    source_candidate = (group.get("candidate_context") or {}).get("candidate") or {}
    source_name = str(source_candidate.get("candidate_name") or "").strip() if isinstance(source_candidate, dict) else ""
    if not source_name:
        raise Pass2PipelineError("STEP04_RESULT_INVALID", f"{candidate_id} has no source candidate name.")
    signal_class = signal.get("signal_class")
    if signal_class not in SIGNAL_CLASS_LABELS:
        raise Pass2PipelineError("STEP08_RESULT_INVALID", f"{candidate_id} signal_class is invalid.")
    direct_status = signal.get("direct_precipitation_status")
    environment_status = signal.get("environment_support_status")
    persistence_status = signal.get("environment_persistence_status")
    persistence_ids = _require_list(
        signal.get("environment_persistence_evidence_check_ids"),
        code="STEP08_RESULT_INVALID",
        message=f"{candidate_id} environment_persistence_evidence_check_ids must be an array.",
    )
    if direct_status not in CANDIDATE_SIGNAL_STATUSES or environment_status not in CANDIDATE_SIGNAL_STATUSES:
        raise Pass2PipelineError("STEP08_RESULT_INVALID", f"{candidate_id} independent signal statuses are invalid.")
    if persistence_status not in ENVIRONMENT_PERSISTENCE_PRESENTATION:
        raise Pass2PipelineError("STEP08_RESULT_INVALID", f"{candidate_id} environment persistence status is invalid.")
    if signal_class != derive_signal_class(direct_status, environment_status):
        raise Pass2PipelineError("STEP08_RESULT_INVALID", f"{candidate_id} signal_class is not derived from independent statuses.")
    question_answers = _question_answers(synthesis.get("forecaster_question_answers"))
    _require_usable_question_evidence(candidate_id, question_answers, step07_checks)
    decision_check_ids = set(_publication_check_ids(signal))
    publication_checks = _publication_check_ids(signal, question_answers)
    question_only_ids = set(publication_checks) - decision_check_ids
    entries = _request_entries(group)
    check_evidence_by_id = {
        str(item.get("check_id")): item
        for item in group.get("check_evidence", [])
        if isinstance(item, dict) and item.get("check_id")
    }
    check_specifications_by_id = {
        str(item.get("check_id")): item
        for item in (
            (group.get("candidate_context") or {})
            .get("analysis_plan", {})
            .get("variable_checks", [])
        )
        if isinstance(item, dict) and item.get("check_id")
    }
    blocks: list[dict[str, Any]] = []
    selected: list[tuple[str, dict[str, Any]]] = []
    selected_contradictory = False
    selected_results: dict[str, dict[str, Any]] = {}
    for check_id in publication_checks:
        result = step07_checks.get(check_id)
        if result is None:
            raise Pass2PipelineError("PUBLICATION_CHECK_MISSING", f"{candidate_id} publication check is absent from Step07: {check_id}.")
        selected_results[check_id] = result
        check_evidence = check_evidence_by_id.get(check_id)
        if check_evidence is None:
            raise Pass2PipelineError(
                "PUBLICATION_CHECK_SPEC_MISSING",
                f"{candidate_id} publication check has no Step06 check_evidence specification: {check_id}.",
            )
        blocks.append(
            _evidence_block(
                result,
                signal,
                check_id,
                check_evidence,
                entries,
                check_specifications_by_id.get(check_id),
                question_only=check_id in question_only_ids,
            )
        )
        representative = _select_representative_image(root, result, entries, window, peak_hours)
        if representative is not None:
            selected.append((check_id, representative))
            selected_contradictory = selected_contradictory or representative["origin"] == "contradictory"
    contradictions = [str(item) for item in _require_list(signal.get("contradictory_evidence"), code="STEP08_RESULT_INVALID", message=f"{candidate_id} contradictory_evidence must be an array.")]
    if contradictions and not selected_contradictory:
        safeguard = _select_contradiction_image(root, selected_results, entries, window, peak_hours)
        if safeguard is not None:
            selected.append(safeguard)
    blocks_by_check = {block["check_id"]: block for block in blocks}
    for check_id, selection in selected:
        asset_id = _publish_asset(stage, assets_by_hash, candidate_id, check_id, selection)
        _append_unique(blocks_by_check[check_id]["asset_ids"], asset_id)
    for check_id, block in blocks_by_check.items():
        check_evidence = check_evidence_by_id[check_id]
        for representative in _representative_spatial_image_candidates(root, check_evidence, entries):
            asset_id = _publish_asset(stage, assets_by_hash, candidate_id, check_id, representative)
            _append_unique(block["asset_ids"], asset_id)
        result = selected_results[check_id]
        for quantitative in block["quantitative_evidence"]:
            related = _select_same_time_related_image(
                root,
                result,
                entries,
                feature_id=str(quantitative["feature_id"]),
                forecast_hour=int(quantitative["forecast_hour"]),
            )
            if related is not None:
                _publish_asset(stage, assets_by_hash, candidate_id, check_id, related)
    _sort_evidence_blocks_for_presentation(
        blocks,
        check_specifications_by_id=check_specifications_by_id,
        check_evidence_by_id=check_evidence_by_id,
        publication_checks=publication_checks,
    )
    final_assessment = synthesis.get("final_assessment")
    direct = signal.get("direct_precipitation_assessment")
    precursor = signal.get("precursor_environment_assessment")
    if not all(isinstance(value, str) and value for value in (final_assessment, direct, precursor)):
        raise Pass2PipelineError("STEP08_RESULT_INVALID", f"{candidate_id} assessment text is invalid.")
    surfaced = {
        "candidate_id": candidate_id,
        "candidate_name": name,
        "source_candidate_name": source_name,
        "final_spatial_identity": copy.deepcopy(synthesis.get("final_spatial_identity")),
        "effective_area": area,
        "effective_time_window": window,
        "peak_forecast_hours": peak_hours,
        "signal_class": signal_class,
        "candidate_type": str(operational.get("candidate_type") or ""),
        "display_tier": str(operational.get("display_tier") or ""),
        "direct_precipitation_status": direct_status,
        "environment_support_status": environment_status,
        "environment_persistence_status": str(persistence_status),
        "environment_persistence_evidence_check_ids": _ordered_unique(persistence_ids),
        "physical_consistency_status": str(signal.get("physical_consistency_status") or ""),
        "physical_mechanism_summary": str(signal.get("physical_mechanism_summary") or ""),
        "monitoring_value": str(signal.get("monitoring_value") or ""),
        "operational_surface_reason": str(operational.get("operational_surface_reason") or ""),
        "mechanism_evidence": _decision_evidence(signal, "mechanism_evidence"),
        "monitoring_evidence": _decision_evidence(signal, "monitoring_evidence"),
        "monitoring_emphasis": monitoring_emphasis,
        "should_surface": True,
        "final_assessment": final_assessment,
        "direct_precipitation_assessment": direct,
        "precursor_environment_assessment": precursor,
        "validated_precursor_categories": derive_validated_precursor_categories(signal.get("validated_precursor_evidence")),
        "validated_spatiotemporal_alignment": bool(signal.get("validated_spatiotemporal_alignment")),
        "contradictory_evidence": contradictions,
        "uncertainties": [str(item) for item in _require_list(synthesis.get("uncertainties"), code="STEP08_RESULT_INVALID", message=f"{candidate_id} uncertainties must be an array.")],
        "forecaster_question_answers": question_answers,
        "publication_check_ids": publication_checks,
        "evidence_blocks": blocks,
    }
    environment_localization = signal.get("environment_localization")
    if isinstance(environment_localization, dict) and environment_localization.get("status") == "localized":
        focus_check_id = str(environment_localization.get("evidence_check_id") or "")
        focus_hour = environment_localization.get("forecast_hour")
        focus_result = step07_checks.get(focus_check_id)
        if (
            focus_check_id
            and isinstance(focus_hour, int)
            and not isinstance(focus_hour, bool)
            and isinstance(focus_result, dict)
        ):
            signal_components = [
                str(value)
                for value in environment_localization.get("signal_components", [])
                if isinstance(value, str)
            ]
            localization_selections = _environment_localization_image_selections(
                root,
                focus_result,
                entries,
                int(focus_hour),
                signal_components,
            )
            if synthesis.get("final_spatial_identity", {}).get("status") == "localized" and not localization_selections:
                raise Pass2PipelineError(
                    "FINAL_SPATIAL_IMAGE_MISSING",
                    f"{candidate_id} final spatial identity has no same-time localized evidence image.",
                )
            for selection in localization_selections:
                _publish_asset(stage, assets_by_hash, candidate_id, focus_check_id, selection)
            surfaced["environment_localization"] = {
                "status": "localized",
                "forecast_hour": int(focus_hour),
                "confidence": str(environment_localization.get("confidence") or ""),
                "evidence_check_id": focus_check_id,
                "signal_components": signal_components,
                "regions": [
                    {
                        "region_id": str(item.get("region_id") or ""),
                        "relative_sector": str(item.get("relative_sector") or "unspecified"),
                    }
                    for item in environment_localization.get("regions", [])
                    if isinstance(item, dict)
                ],
                "source": "step07_validated_spatial_localization",
            }
    if operational_brief is not None:
        surfaced["operational_brief"] = operational_brief
    if operational_guidance is not None:
        surfaced["operational_guidance"] = operational_guidance
    return surfaced


def _non_surface_candidate(
    root: Path,
    stage: Path,
    init: str,
    group: dict[str, Any],
    synthesis: dict[str, Any],
    operational: dict[str, Any],
    step07_checks: dict[str, dict[str, Any]],
    operational_brief: dict[str, Any] | None,
    operational_guidance: dict[str, Any] | None,
    assets_by_hash: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    candidate_id = str(group["candidate_id"])
    signal = synthesis.get("signal_assessment")
    if not isinstance(signal, dict) or signal.get("signal_class") not in SIGNAL_CLASS_LABELS:
        raise Pass2PipelineError("STEP08_RESULT_INVALID", f"{candidate_id} signal_assessment is invalid.")
    direct_status = signal.get("direct_precipitation_status")
    environment_status = signal.get("environment_support_status")
    persistence_status = signal.get("environment_persistence_status")
    persistence_ids = _require_list(
        signal.get("environment_persistence_evidence_check_ids"),
        code="STEP08_RESULT_INVALID",
        message=f"{candidate_id} environment_persistence_evidence_check_ids must be an array.",
    )
    if direct_status not in CANDIDATE_SIGNAL_STATUSES or environment_status not in CANDIDATE_SIGNAL_STATUSES:
        raise Pass2PipelineError("STEP08_RESULT_INVALID", f"{candidate_id} independent signal statuses are invalid.")
    if persistence_status not in ENVIRONMENT_PERSISTENCE_PRESENTATION:
        raise Pass2PipelineError("STEP08_RESULT_INVALID", f"{candidate_id} environment persistence status is invalid.")
    if signal["signal_class"] != derive_signal_class(direct_status, environment_status):
        raise Pass2PipelineError("STEP08_RESULT_INVALID", f"{candidate_id} signal_class is not derived from independent statuses.")
    name, area, window, peak_hours = _effective_context(init, group, synthesis, operational)
    source_candidate = (group.get("candidate_context") or {}).get("candidate") or {}
    source_name = str(source_candidate.get("candidate_name") or "").strip() if isinstance(source_candidate, dict) else ""
    if not source_name:
        raise Pass2PipelineError("STEP04_RESULT_INVALID", f"{candidate_id} has no source candidate name.")
    final_assessment = synthesis.get("final_assessment")
    if not isinstance(final_assessment, str) or not final_assessment:
        raise Pass2PipelineError("STEP08_RESULT_INVALID", f"{candidate_id} final_assessment is invalid.")
    non_surfaced = {
        "candidate_id": candidate_id,
        "candidate_name": name,
        "source_candidate_name": source_name,
        "final_spatial_identity": copy.deepcopy(synthesis.get("final_spatial_identity")),
        "effective_area": area,
        "effective_time_window": window,
        "signal_class": signal["signal_class"],
        "candidate_type": str(operational.get("candidate_type") or ""),
        "display_tier": str(operational.get("display_tier") or ""),
        "direct_precipitation_status": direct_status,
        "environment_support_status": environment_status,
        "environment_persistence_status": str(persistence_status),
        "environment_persistence_evidence_check_ids": _ordered_unique(persistence_ids),
        "physical_consistency_status": str(signal.get("physical_consistency_status") or ""),
        "physical_mechanism_summary": str(signal.get("physical_mechanism_summary") or ""),
        "monitoring_value": str(signal.get("monitoring_value") or ""),
        "operational_surface_reason": str(operational.get("operational_surface_reason") or ""),
        "operational_surface_decision": str(operational.get("operational_surface_decision") or "not_applicable"),
        "validation_status": "valid",
        "should_surface": False,
        "validation_reason": operational["validation_reason"],
        "final_assessment": final_assessment,
    }
    related_image_asset_ids = _related_non_surface_asset_ids(
        root,
        stage,
        assets_by_hash,
        candidate_id,
        signal,
        step07_checks,
        _request_entries(group),
        window,
        peak_hours,
    )
    if related_image_asset_ids:
        non_surfaced["related_image_asset_ids"] = related_image_asset_ids
    if operational_brief is not None:
        non_surfaced["operational_brief"] = operational_brief
    if operational_guidance is not None:
        non_surfaced["operational_guidance"] = operational_guidance
    return non_surfaced


def _transferred_operational_brief(
    group: dict[str, Any],
    synthesis: dict[str, Any],
    operational: dict[str, Any],
    step07_checks: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Pass the Step08 concise operational brief through Step09 unchanged."""
    source = synthesis.get("operational_brief")
    transferred = operational.get("operational_brief")
    if source is None and transferred is None:
        return None
    if not isinstance(source, dict) or not isinstance(transferred, dict) or source != transferred:
        raise Pass2PipelineError(
            "STEP08_STEP09_BRIEF_MISMATCH",
            "Step08 and Step09 operational_brief differ or are incomplete.",
        )
    validate_operational_brief(group, step07_checks, synthesis)
    return source


def _transferred_operational_guidance(
    group: dict[str, Any],
    synthesis: dict[str, Any],
    operational: dict[str, Any],
    step07_checks: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Pass the Step08 object through Step09 without generating display content."""
    source = synthesis.get("operational_guidance")
    transferred = operational.get("operational_guidance")
    if source is None and transferred is None:
        # Legacy Step08/09 products remain renderable; Step10 does not add a
        # fallback message because that would be fabricated guidance.
        return None
    tier = str(operational.get("display_tier") or "")
    try:
        expected, _ = canonical_guidance(source, tier)
    except TierNarrativeError as exc:
        raise Pass2PipelineError("STEP09_TIER_NARRATIVE_INVALID", str(exc)) from exc
    if not isinstance(expected, dict) or not isinstance(transferred, dict) or expected != transferred:
        raise Pass2PipelineError("STEP08_STEP09_GUIDANCE_MISMATCH", "Step08 guidance is not canonically transferred through Step09.")
    validate_operational_guidance(group, step07_checks, synthesis)
    try:
        assert_public_tier({"candidate_id": operational.get("candidate_id"), "display_tier": tier,
                            "operational_surface_reason": operational.get("operational_surface_reason"),
                            "operational_guidance": transferred})
    except TierNarrativeError as exc:
        raise Pass2PipelineError("STEP09_TIER_NARRATIVE_INVALID", str(exc)) from exc
    return transferred


def _source_hashes(root: Path, init: str) -> list[dict[str, str]]:
    sources = [
        ("step04_pass1_result", step04_result_path(root, init)),
        ("step07_check_results", runtime_path(root, "daio", init, "step07", "pass2_check_results.json")),
        ("step07_minority_report", step07_minority_report_path(root, init)),
        ("step08_pass2_result", step08_output_path(root, init)),
        ("step09_operational_result", step09_output_path(root, init)),
    ]
    result: list[dict[str, str]] = []
    for role, path in sources:
        if not path.is_file():
            raise Pass2PipelineError("SOURCE_FILE_MISSING", f"Step10 source file is missing: {role}.")
        result.append({"role": role, "sha256": sha256sum(path)})
    return result


def _build_product(root: Path, init: str, stage: Path) -> dict[str, Any]:
    # Load and validate every source before creating a permanent-output candidate.
    load_json_object(step04_result_path(root, init), code="STEP04_RESULT_MISSING")
    package = load_step06_package(root, init)
    groups = active_candidate_groups(package)
    groups_by_id: dict[str, dict[str, Any]] = {}
    for group in groups:
        candidate_id = group.get("candidate_id") if isinstance(group, dict) else None
        if not isinstance(candidate_id, str) or not candidate_id or candidate_id in groups_by_id:
            raise Pass2PipelineError("STEP06_PACKAGE_INVALID", "Step06 active candidate ids are invalid.")
        groups_by_id[candidate_id] = group
    active_ids = set(groups_by_id)
    step07 = load_step07_results(root, init)
    step07_by_candidate = _index_step07_results(step07, active_ids)
    minority_report = load_json_object(step07_minority_report_path(root, init), code="STEP07_MINORITY_REPORT_MISSING")
    validate_schema(root, MINORITY_REPORT_SCHEMA, minority_report, code="STEP07_MINORITY_REPORT_INVALID")
    validate_step07_minority_report_consistency(step07, minority_report)
    step08 = migrate_step08_document(load_json_object(step08_output_path(root, init), code="STEP08_RESULT_MISSING"))
    validate_schema(root, STEP08_SCHEMA, step08)
    step09 = load_json_object(step09_output_path(root, init), code="STEP09_RESULT_MISSING")
    validate_schema(root, STEP09_SCHEMA, step09)
    if step08.get("init") != init or step09.get("init") != init or package.get("init") != init:
        raise Pass2PipelineError("INIT_MISMATCH", "Step06--09 init values do not match Step10 init.")
    syntheses = _index_candidates(step08.get("candidates"), active_ids=active_ids, code="STEP08_RESULT_INVALID", label="Step08")
    operational = _index_candidates(step09.get("candidates"), active_ids=active_ids, code="STEP09_RESULT_INVALID", label="Step09")
    # Independent publication gate: a valid Step08 schema alone does not
    # ensure that an answer includes the verified quantitative comparison.
    # Recheck the original Step07 relation before rendering either JSON or HTML.
    for question_candidate_id, synthesis in syntheses.items():
        verified = _verified_quantitative_relations(step07_by_candidate[question_candidate_id])
        quant_errors = question_answer_errors(synthesis.get("forecaster_question_answers", []), verified)
        if quant_errors:
            raise Pass2PipelineError(
                "FORECASTER_QUANTITATIVE_ANSWER_INVALID",
                f"{question_candidate_id}: " + "; ".join(quant_errors[:8]),
            )
    for candidate_id, record in operational.items():
        if record.get("validation_status") != "valid":
            raise Pass2PipelineError("PUBLICATION_GATE_BLOCKED", f"Step09 candidate is not valid: {candidate_id}.")
        if not isinstance(record.get("should_surface"), bool) or not isinstance(record.get("validation_reason"), str) or not record["validation_reason"]:
            raise Pass2PipelineError("STEP09_RESULT_INVALID", f"Step09 operational record is invalid: {candidate_id}.")
        candidate_type = str(record.get("candidate_type") or "")
        display_tier = str(record.get("display_tier") or "")
        if candidate_type not in CANDIDATE_TYPES or display_tier not in DISPLAY_TIERS:
            raise Pass2PipelineError("STEP09_RESULT_INVALID", f"Step09 candidate type/display tier is invalid: {candidate_id}.")
        if bool(record.get("should_surface")) != (display_tier != "hidden"):
            raise Pass2PipelineError("STEP09_RESULT_INVALID", f"Step09 display tier and should_surface disagree: {candidate_id}.")
        signal = syntheses[candidate_id].get("signal_assessment")
        candidate_context = (groups_by_id[candidate_id].get("candidate_context") or {})
        source_candidate = candidate_context.get("candidate") if isinstance(candidate_context, dict) else None
        final_spatial_identity = syntheses[candidate_id].get("final_spatial_identity")
        region_index = _candidate_analysis_region_index(root, groups_by_id[candidate_id])
        region_names = {
            str(item.get("region_id") or ""): str(item.get("name_ko") or "")
            for item in region_index.get("regions", [])
            if isinstance(item, dict) and str(item.get("region_id") or "")
        }
        effective_peak_hours = candidate_effective_peak_forecast_hours(
            source_candidate,
            syntheses[candidate_id].get("revision"),
        )
        peak_precipitation_details = candidate_peak_precipitation_details_from_group(
            groups_by_id[candidate_id],
            effective_peak_hours,
        )
        analysis_plan = candidate_context.get("analysis_plan") if isinstance(candidate_context.get("analysis_plan"), dict) else {}
        display_check_specifications = {
            str(item.get("check_id")): item
            for item in analysis_plan.get("variable_checks", [])
            if isinstance(item, dict) and str(item.get("check_id") or "")
        }
        expected_display_name = candidate_operational_display_name(
            source_candidate,
            syntheses[candidate_id].get("revision"),
            final_spatial_identity,
            region_names,
            direct_precipitation_status=(
                signal.get("direct_precipitation_status") if isinstance(signal, dict) else None
            ),
            peak_precipitation_details=peak_precipitation_details,
            check_specifications=display_check_specifications,
            check_results=step07_by_candidate.get(candidate_id, {}),
        )
        transferred_display_name = record.get("display_candidate_name")
        if not isinstance(transferred_display_name, str) or not transferred_display_name.strip():
            raise Pass2PipelineError(
                "STEP09_RESULT_INVALID",
                f"Step09 omitted the final candidate display name: {candidate_id}.",
            )
        if transferred_display_name != expected_display_name:
            raise Pass2PipelineError(
                "STEP08_STEP09_NAME_MISMATCH",
                f"Step08 final spatial identity and Step09 candidate display name differ: {candidate_id}.",
            )
        if record.get("final_spatial_identity") != final_spatial_identity:
            raise Pass2PipelineError(
                "STEP08_STEP09_SPATIAL_IDENTITY_MISMATCH",
                f"Step08 and Step09 final spatial identities differ: {candidate_id}.",
            )
        if (
            not isinstance(signal, dict)
            or record.get("signal_class") != signal.get("signal_class")
            or record.get("direct_precipitation_status") != signal.get("direct_precipitation_status")
            or record.get("environment_support_status") != signal.get("environment_support_status")
            or record.get("environment_persistence_status") != signal.get("environment_persistence_status")
            or record.get("environment_persistence_evidence_check_ids") != signal.get("environment_persistence_evidence_check_ids")
            or record.get("operational_surface_decision") != signal.get("operational_surface_decision")
            or record.get("physical_consistency_status") != signal.get("physical_consistency_status")
            or record.get("monitoring_value") != signal.get("monitoring_value")
            or record.get("operational_surface_reason") != public_reason(str(signal.get("operational_surface_reason") or ""), str(record.get("display_tier") or ""))[0]
        ):
            raise Pass2PipelineError("STEP08_STEP09_MISMATCH", f"Step08 and Step09 signal statuses differ: {candidate_id}.")
    assets_by_hash: dict[str, dict[str, Any]] = {}
    surfaced: list[dict[str, Any]] = []
    non_surfaced: list[dict[str, Any]] = []
    for candidate_id in sorted(active_ids):
        brief = _transferred_operational_brief(
            groups_by_id[candidate_id],
            syntheses[candidate_id],
            operational[candidate_id],
            step07_by_candidate[candidate_id],
        )
        guidance = _transferred_operational_guidance(
            groups_by_id[candidate_id],
            syntheses[candidate_id],
            operational[candidate_id],
            step07_by_candidate[candidate_id],
        )
        if operational[candidate_id]["should_surface"]:
            surfaced_candidate = _surface_candidate(
                root,
                init,
                groups_by_id[candidate_id],
                syntheses[candidate_id],
                operational[candidate_id],
                step07_by_candidate[candidate_id],
                brief,
                guidance,
                str(operational[candidate_id].get("monitoring_emphasis") or "standard"),
                assets_by_hash,
                stage,
            )
            minority_question_links = _derive_minority_question_links(
                groups_by_id[candidate_id],
                syntheses[candidate_id],
                minority_report,
            )
            if minority_question_links:
                surfaced_candidate["minority_question_links"] = minority_question_links
            surfaced.append(surfaced_candidate)
        else:
            non_surfaced.append(
                _non_surface_candidate(
                    root,
                    stage,
                    init,
                    groups_by_id[candidate_id],
                    syntheses[candidate_id],
                    operational[candidate_id],
                    step07_by_candidate[candidate_id],
                    brief,
                    guidance,
                    assets_by_hash,
                )
            )
    surfaced.sort(key=lambda item: (item["effective_time_window"]["forecast_hour_start"], item["candidate_id"]))
    non_surfaced.sort(key=lambda item: (item["effective_time_window"]["forecast_hour_start"], item["candidate_id"]))
    minority_report_assets = _minority_report_asset_map(
        root,
        minority_report,
        groups_by_id,
        syntheses,
        assets_by_hash,
        stage,
    )
    assets = []
    for asset in sorted(assets_by_hash.values(), key=lambda item: item["asset_id"]):
        asset["candidate_ids"].sort()
        asset["check_ids"].sort()
        asset["source_refs"].sort(key=lambda item: (item["request_id"], item["physical_diagnostic"], -1 if item["forecast_hour"] is None else item["forecast_hour"]))
        assets.append(asset)
    product_status = "published" if surfaced else "no_surface_candidates"
    schema_hash = sha256sum(runtime_path(root, "prompts", "schemas", FORECASTER_PRODUCT_SCHEMA))
    return {
        "schema_version": GENERATOR_VERSION,
        "init": init,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "product_status": product_status,
        "summary": {
            "candidate_count": len(active_ids),
            "surfaced_candidate_count": len(surfaced),
            "non_surfaced_candidate_count": len(non_surfaced),
        },
        "surfaced_candidates": surfaced,
        "non_surfaced_candidates": non_surfaced,
        "assets": assets,
        "minority_report_assets": minority_report_assets,
        "provenance": {
            "source_files": _source_hashes(root, init),
            "schema_sha256": schema_hash,
            "generator_version": GENERATOR_VERSION,
            "template_sha256": hashlib.sha256(TEMPLATE_IDENTITY.encode("utf-8")).hexdigest(),
        },
    }


def _forecast_valid_datetimes(init: str, forecast_hour: int) -> tuple[datetime, datetime]:
    """Return the valid UTC and KST datetimes for a forecast hour."""
    if isinstance(forecast_hour, bool) or not isinstance(forecast_hour, int) or forecast_hour < 0:
        raise ValueError("forecast_hour must be a non-negative integer")
    try:
        origin = datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"init must be YYYYMMDDHH: {init!r}") from exc
    valid_utc = origin + timedelta(hours=forecast_hour)
    valid_kst = valid_utc.astimezone(timezone(timedelta(hours=9)))
    return valid_utc, valid_kst


def format_valid_time_kst(init: str, forecast_hour: int) -> str:
    """Return a forecaster-facing KST valid time without a model lead label."""
    _valid_utc, valid_kst = _forecast_valid_datetimes(init, forecast_hour)
    return valid_kst.strftime("%Y-%m-%d %H:%M KST")


def format_forecast_hour(init: str, forecast_hour: int) -> str:
    """Return KST plus F-hour when the source explicitly refers to a forecast hour."""
    return f"{format_valid_time_kst(init, forecast_hour)}(F{forecast_hour:03d})"


def format_forecast_hour_dual(init: str, forecast_hour: int) -> str:
    """Backward-compatible timeline helper; the UI itself is KST-only."""
    return format_valid_time_kst(init, forecast_hour)


def _format_init_header(init: str) -> str:
    try:
        parsed = datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"init must be YYYYMMDDHH: {init!r}") from exc
    return parsed.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")


def _format_generated_at_header(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "확인할 수 없음"
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")


def _format_timeline_clock(init: str, forecast_hour: int) -> str:
    """Return a compact KST label for the timeline axis."""
    _valid_utc, valid_kst = _forecast_valid_datetimes(init, forecast_hour)
    return f"{valid_kst.month}.{valid_kst.day}. {valid_kst.hour:02d}KST"


def _format_timeline_watch_summary(init: str, forecast_hour: int) -> str:
    """Return a compact KST watch-summary label."""
    return _format_timeline_clock(init, forecast_hour)


def _format_compact_kst_range(init: str, forecast_hour_start: int, forecast_hour_end: int) -> str:
    """Return a compact KST range for space-constrained UI labels."""
    return f"{_format_timeline_clock(init, forecast_hour_start)}–{_format_timeline_clock(init, forecast_hour_end)}"


def format_forecast_hours_in_text(text: str, init: str) -> str:
    """Present embedded F-hours without changing the stored source text."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    replacements: list[str] = []

    def replace_range(match: re.Match[str]) -> str:
        replacements.append(f"{format_forecast_hour(init, int(match.group(1)))} ~ {format_forecast_hour(init, int(match.group(2)))}")
        return f"\x00RANGE_{len(replacements) - 1}\x00"

    def replace_single(match: re.Match[str]) -> str:
        return format_forecast_hour(init, int(match.group(1)))

    rendered = _FORECAST_HOUR_TOKEN.sub(replace_single, _FORECAST_HOUR_RANGE.sub(replace_range, text))
    for index, replacement in enumerate(replacements):
        rendered = rendered.replace(f"\x00RANGE_{index}\x00", replacement)
    return rendered


def _format_nonprecip_display_value(value: float) -> str:
    """Format one non-precipitation user-facing value without collapsing small non-zero values to zero."""
    if value == 0:
        return "0.00"
    if 0 < value < 0.005:
        return "<0.01"
    if -0.005 < value < 0:
        return ">-0.01"
    return f"{value:.2f}"


def format_meteorological_values_in_text(text: str) -> str:
    """Apply Step10 display precision to forecaster-facing meteorological values in prose.

    Precipitation values expressed in mm are displayed with exactly one decimal place.
    Other decimal-valued meteorological quantities are displayed with at most two-decimal
    precision, with a small-value safeguard so a non-zero quantity is not rendered as 0.00.
    Integer tokens such as pressure levels, forecast hours, dates, and identifiers are not
    decimalized by this prose formatter.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")

    precipitation_values: list[str] = []

    def protect_precipitation_range(match: re.Match[str]) -> str:
        separator = match.group(2)
        separator_text = f"{separator} " if separator == "에서" else separator
        precipitation_values.append(
            f"{float(match.group(1)):.1f}{separator_text}{float(match.group(3)):.1f} mm"
        )
        return f"\x00PRECIP_{len(precipitation_values) - 1}\x00"

    def protect_precipitation(match: re.Match[str]) -> str:
        precipitation_values.append(f"{float(match.group(1)):.1f} mm")
        return f"\x00PRECIP_{len(precipitation_values) - 1}\x00"

    rendered = _PRECIPITATION_MM_RANGE.sub(protect_precipitation_range, text)
    rendered = _PRECIPITATION_MM_VALUE.sub(protect_precipitation, rendered)
    rendered = _DISPLAY_DECIMAL_VALUE.sub(
        lambda match: _format_nonprecip_display_value(float(match.group(1))), rendered
    )
    for index, value in enumerate(precipitation_values):
        rendered = rendered.replace(f"\x00PRECIP_{index}\x00", value)
    return rendered


def format_precipitation_values_in_text(text: str) -> str:
    """Backward-compatible wrapper for the Step10 user-facing numeric precision formatter."""
    return format_meteorological_values_in_text(text)


def _render_text(value: str, init: str) -> str:
    rendered = format_forecast_hours_in_text(value, init)
    return escape(format_meteorological_values_in_text(rendered))


def _render_list(values: list[str], init: str) -> str:
    return "<ul>" + "".join(f"<li>{_render_text(value, init)}</li>" for value in values) + "</ul>" if values else "<p>없음</p>"


def _render_time(window: dict[str, Any], init: str) -> str:
    return f"{format_valid_time_kst(init, int(window['forecast_hour_start']))} ~ {format_valid_time_kst(init, int(window['forecast_hour_end']))}"


def _render_coordinate_range(first: float, second: float, *, positive_label: str, negative_label: str) -> str:
    if first >= 0 and second >= 0:
        return f"{positive_label} {abs(first):.2f}°–{abs(second):.2f}°"
    if first <= 0 and second <= 0:
        return f"{negative_label} {abs(first):.2f}°–{abs(second):.2f}°"
    first_label = positive_label if first >= 0 else negative_label
    second_label = positive_label if second >= 0 else negative_label
    return f"{first_label} {abs(first):.2f}°–{second_label} {abs(second):.2f}°"


def _render_area(area: dict[str, Any]) -> str:
    longitude = _render_coordinate_range(float(area["west"]), float(area["east"]), positive_label="동경", negative_label="서경")
    latitude = _render_coordinate_range(float(area["south"]), float(area["north"]), positive_label="북위", negative_label="남위")
    return f"{longitude}, {latitude}"


def _minimap_project(lon: float, lat: float) -> tuple[float, float]:
    import math

    x_span = (MINIMAP_LON_MAX - MINIMAP_LON_MIN) * math.cos(math.radians(MINIMAP_REFERENCE_LAT))
    x_value = (lon - MINIMAP_LON_MIN) * math.cos(math.radians(MINIMAP_REFERENCE_LAT))
    x = x_value / x_span * MINIMAP_VIEWBOX_WIDTH
    y = (MINIMAP_LAT_MAX - lat) / (MINIMAP_LAT_MAX - MINIMAP_LAT_MIN) * MINIMAP_VIEWBOX_HEIGHT
    return x, y


def _minimap_shape_segments(path: Path) -> list[list[tuple[float, float]]]:
    if not path.is_file():
        raise Pass2PipelineError("MINIMAP_MAP_DATA_MISSING", f"Minimap map data is missing: {path}")
    try:
        import shapefile  # type: ignore
    except ModuleNotFoundError as exc:
        raise Pass2PipelineError("MINIMAP_MAP_READER_MISSING", "The shapefile module required for the Step10 minimap is unavailable.") from exc
    try:
        reader = shapefile.Reader(str(path))
    except Exception as exc:
        raise Pass2PipelineError("MINIMAP_MAP_DATA_INVALID", f"Unable to read minimap map data: {path}") from exc
    segments: list[list[tuple[float, float]]] = []
    for shape in reader.shapes():
        points = shape.points
        if not points:
            continue
        parts = list(shape.parts) + [len(points)]
        for index in range(len(parts) - 1):
            raw = points[parts[index] : parts[index + 1]]
            if len(raw) >= 2:
                segments.append([(float(point[0]), float(point[1])) for point in raw])
    return segments


def _minimap_clip_chunks(points: list[tuple[float, float]], *, margin: float = 1.0) -> list[list[tuple[float, float]]]:
    west = MINIMAP_LON_MIN - margin
    east = MINIMAP_LON_MAX + margin
    south = MINIMAP_LAT_MIN - margin
    north = MINIMAP_LAT_MAX + margin

    def inside(point: tuple[float, float]) -> bool:
        lon, lat = point
        return west <= lon <= east and south <= lat <= north

    chunks: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for index, point in enumerate(points):
        if inside(point):
            if not current and index > 0:
                current.append(points[index - 1])
            current.append(point)
        elif current:
            current.append(point)
            if len(current) >= 2:
                chunks.append(current)
            current = []
    if len(current) >= 2:
        chunks.append(current)
    return chunks


def _minimap_path_data(points: list[tuple[float, float]]) -> str:
    projected = [_minimap_project(lon, lat) for lon, lat in points]
    if len(projected) < 2:
        return ""
    head_x, head_y = projected[0]
    commands = [f"M {head_x:.2f} {head_y:.2f}"]
    commands.extend(f"L {x:.2f} {y:.2f}" for x, y in projected[1:])
    return " ".join(commands)


def _minimap_simplify(points: list[tuple[float, float]], tolerance: float = 0.035) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points

    def perpendicular_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
        if start == end:
            return ((point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2) ** 0.5
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        numerator = abs(dy * point[0] - dx * point[1] + end[0] * start[1] - end[1] * start[0])
        return numerator / ((dx * dx + dy * dy) ** 0.5)

    max_distance = 0.0
    split_index = 0
    for index in range(1, len(points) - 1):
        distance = perpendicular_distance(points[index], points[0], points[-1])
        if distance > max_distance:
            max_distance = distance
            split_index = index
    if max_distance <= tolerance:
        return [points[0], points[-1]]
    left = _minimap_simplify(points[: split_index + 1], tolerance)
    right = _minimap_simplify(points[split_index:], tolerance)
    return left[:-1] + right


def _minimap_country_paths(path: Path) -> list[str]:
    if not path.is_file():
        raise Pass2PipelineError("MINIMAP_MAP_DATA_MISSING", f"Minimap country data is missing: {path}")
    try:
        import shapefile  # type: ignore
    except ModuleNotFoundError as exc:
        raise Pass2PipelineError("MINIMAP_MAP_READER_MISSING", "The shapefile module required for the Step10 minimap is unavailable.") from exc
    try:
        reader = shapefile.Reader(str(path))
    except Exception as exc:
        raise Pass2PipelineError("MINIMAP_MAP_DATA_INVALID", f"Unable to read minimap country data: {path}") from exc
    fields = [field[0] for field in reader.fields[1:]]
    paths: list[str] = []
    for shape_record in reader.iterShapeRecords():
        record = dict(zip(fields, shape_record.record))
        if str(record.get("ADMIN") or "") not in MINIMAP_LAND_COUNTRIES:
            continue
        shape = shape_record.shape
        points = shape.points
        parts = list(shape.parts) + [len(points)]
        commands: list[str] = []
        for index in range(len(parts) - 1):
            ring = [(float(point[0]), float(point[1])) for point in points[parts[index] : parts[index + 1]]]
            if len(ring) < 3:
                continue
            closed = ring[0] == ring[-1]
            working = ring[:-1] if closed and len(ring) > 3 else ring
            simplified = _minimap_simplify(working, 0.025)
            if len(simplified) < 3:
                continue
            projected = [_minimap_project(lon, lat) for lon, lat in simplified]
            x0, y0 = projected[0]
            commands.append(f"M {x0:.2f} {y0:.2f}")
            commands.extend(f"L {x:.2f} {y:.2f}" for x, y in projected[1:])
            commands.append("Z")
        if commands:
            paths.append(" ".join(commands))
    return paths


def _load_minimap_geography(root: Path) -> dict[str, str]:
    coastline_path = resolve_logical_ref(root, MINIMAP_COASTLINE)
    country_path = resolve_logical_ref(root, MINIMAP_COUNTRIES)
    coast_paths: list[str] = []
    for segment in _minimap_shape_segments(coastline_path):
        for chunk in _minimap_clip_chunks(segment):
            simplified = _minimap_simplify(chunk, 0.02)
            path_data = _minimap_path_data(simplified)
            if path_data:
                coast_paths.append(path_data)
    land_paths = _minimap_country_paths(country_path)
    if not coast_paths or not land_paths:
        raise Pass2PipelineError("MINIMAP_MAP_DATA_INVALID", "Local minimap geography could not be prepared.")
    return {
        "land_paths": "".join(f'<path d="{path_data}" />' for path_data in land_paths),
        "coastline_paths": "".join(f'<path d="{path_data}" />' for path_data in coast_paths),
    }



def _region_guide_compact_name(name: str, role: str) -> str:
    value = str(name).strip()
    if role == "marine_macro" and value.endswith(" 광역"):
        value = value[:-3].rstrip()
    return value


def _region_guide_focus_viewbox(bbox: dict[str, float]) -> tuple[float, float, float, float]:
    west = max(MINIMAP_LON_MIN, min(MINIMAP_LON_MAX, float(bbox["west"])))
    east = max(MINIMAP_LON_MIN, min(MINIMAP_LON_MAX, float(bbox["east"])))
    south = max(MINIMAP_LAT_MIN, min(MINIMAP_LAT_MAX, float(bbox["south"])))
    north = max(MINIMAP_LAT_MIN, min(MINIMAP_LAT_MAX, float(bbox["north"])))
    x1, y1 = _minimap_project(west, north)
    x2, y2 = _minimap_project(east, south)
    raw_w = max(8.0, abs(x2 - x1))
    raw_h = max(8.0, abs(y2 - y1))
    width = min(MINIMAP_VIEWBOX_WIDTH, max(74.0, raw_w * 2.1))
    height = min(MINIMAP_VIEWBOX_HEIGHT, max(66.0, raw_h * 2.1))
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    x = max(0.0, min(MINIMAP_VIEWBOX_WIDTH - width, cx - width / 2.0))
    y = max(0.0, min(MINIMAP_VIEWBOX_HEIGHT - height, cy - height / 2.0))
    return x, y, width, height


def _region_guide_grid_axis(values: list[float]) -> tuple[float, float]:
    """Infer the regular AIFS lattice step and an anchor from fixed land-grid points."""
    cleaned = sorted({round(float(value), 8) for value in values})
    diffs = [round(b - a, 8) for a, b in zip(cleaned, cleaned[1:]) if b - a > 1.0e-8]
    if not cleaned or not diffs:
        raise Pass2PipelineError("REGION_GUIDE_CONFIG_INVALID", "Unable to infer the AIFS grid lattice.")
    step = min(diffs)
    if step <= 0.0:
        raise Pass2PipelineError("REGION_GUIDE_CONFIG_INVALID", "AIFS grid lattice step must be positive.")
    return step, cleaned[0]


def _region_guide_axis_values(lower: float, upper: float, *, step: float, anchor: float) -> list[float]:
    import math

    start = math.ceil((float(lower) - anchor) / step - 1.0e-9)
    stop = math.floor((float(upper) - anchor) / step + 1.0e-9)
    return [round(anchor + index * step, 8) for index in range(start, stop + 1)]


def _region_guide_display_bbox(
    bbox: dict[str, float],
    *,
    lat_step: float,
    lon_step: float,
    lat_anchor: float,
    lon_anchor: float,
) -> dict[str, float]:
    """Return a display-only bbox snapped to AIFS cell boundaries.

    The configured bbox remains the analysis contract used to select grid centres.
    For the map only, the first/last AIFS centres admitted by that bbox are expanded
    by half a grid step so the definition outline consistently follows cell edges.
    """
    latitudes = _region_guide_axis_values(bbox["south"], bbox["north"], step=lat_step, anchor=lat_anchor)
    longitudes = _region_guide_axis_values(bbox["west"], bbox["east"], step=lon_step, anchor=lon_anchor)
    if not latitudes or not longitudes:
        raise Pass2PipelineError("REGION_GUIDE_CONFIG_INVALID", "Region bbox contains no AIFS grid centres.")
    return {
        "west": float(longitudes[0]) - lon_step / 2.0,
        "east": float(longitudes[-1]) + lon_step / 2.0,
        "south": float(latitudes[0]) - lat_step / 2.0,
        "north": float(latitudes[-1]) + lat_step / 2.0,
    }


def _load_region_guide_land_geometry(root: Path) -> Any:
    """Load the same Natural Earth land geometry used by operational sea masks."""
    path = resolve_logical_ref(root, REGION_GUIDE_LAND_MASK)
    if not path.is_file():
        raise Pass2PipelineError("REGION_GUIDE_CONFIG_MISSING", f"Region-guide land mask is missing: {path}")
    try:
        import shapefile  # type: ignore
        from shapely.geometry import shape as shapely_shape
        from shapely.ops import unary_union
    except ModuleNotFoundError as exc:
        raise Pass2PipelineError(
            "MINIMAP_MAP_READER_MISSING",
            "The shapefile/shapely modules required for the Step10 region guide are unavailable.",
        ) from exc
    try:
        reader = shapefile.Reader(str(path))
    except Exception as exc:
        raise Pass2PipelineError("MINIMAP_MAP_DATA_INVALID", f"Unable to read region-guide land mask: {path}") from exc
    geometries = []
    for raw_shape in reader.shapes():
        try:
            geometry = shapely_shape(raw_shape.__geo_interface__)
        except Exception:
            continue
        if not geometry.is_empty:
            geometries.append(geometry)
    if not geometries:
        raise Pass2PipelineError("MINIMAP_MAP_DATA_INVALID", f"Region-guide land mask has no usable geometry: {path}")
    land = unary_union(geometries)
    if not land.is_valid:
        land = land.buffer(0)
    return land


def _region_guide_dynamic_grid_points(
    *,
    bbox: dict[str, float],
    mask_type: str,
    lat_step: float,
    lon_step: float,
    lat_anchor: float,
    lon_anchor: float,
    land_geometry: Any,
) -> list[tuple[float, float]]:
    """Recreate the full-region AIFS grid membership used by operational statistics."""
    try:
        from shapely import intersects_xy
    except ModuleNotFoundError as exc:
        raise Pass2PipelineError("MINIMAP_MAP_READER_MISSING", "Shapely is required for region-grid rendering.") from exc

    latitudes = _region_guide_axis_values(bbox["south"], bbox["north"], step=lat_step, anchor=lat_anchor)
    longitudes = _region_guide_axis_values(bbox["west"], bbox["east"], step=lon_step, anchor=lon_anchor)
    points: list[tuple[float, float]] = []
    for lat in latitudes:
        for lon in longitudes:
            if mask_type == "sea" and bool(intersects_xy(land_geometry, lon, lat)):
                continue
            points.append((lat, lon))
    return points


def _load_region_guide_regions(root: Path) -> list[dict[str, Any]]:
    """Load region-guide geometry from the same definitions used by analysis.

    Every region is shown as ``definition bbox + actual AIFS analysis grid``.
    Operational land uses the fixed mapping file. Marine/source regions rebuild
    the regular AIFS grid inside their bbox and apply the same sea/mixed mask
    semantics as the operational statistics code.
    """
    catalog_path = resolve_logical_ref(root, REGION_GUIDE_CATALOG)
    grid_path = resolve_logical_ref(root, REGION_GUIDE_LAND_GRID)
    if not catalog_path.is_file():
        raise Pass2PipelineError("REGION_GUIDE_CONFIG_MISSING", f"Region catalog is missing: {catalog_path}")
    if not grid_path.is_file():
        raise Pass2PipelineError("REGION_GUIDE_CONFIG_MISSING", f"Operational land grid map is missing: {grid_path}")
    try:
        payload = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise Pass2PipelineError("REGION_GUIDE_CONFIG_INVALID", f"Unable to read region catalog: {catalog_path}") from exc
    raw_regions = payload.get("regions") if isinstance(payload, dict) else None
    if not isinstance(raw_regions, list):
        raise Pass2PipelineError("REGION_GUIDE_CONFIG_INVALID", "Region catalog has no regions list.")

    land_points: dict[str, list[tuple[float, float]]] = {}
    all_land_latitudes: list[float] = []
    all_land_longitudes: list[float] = []
    try:
        with grid_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                region_id = str(row.get("region_id") or "").strip()
                if not region_id:
                    continue
                try:
                    lat = float(row["latitude"])
                    lon = float(row["longitude"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise Pass2PipelineError("REGION_GUIDE_CONFIG_INVALID", "Operational land grid map contains invalid coordinates.") from exc
                land_points.setdefault(region_id, []).append((lat, lon))
                all_land_latitudes.append(lat)
                all_land_longitudes.append(lon)
    except OSError as exc:
        raise Pass2PipelineError("REGION_GUIDE_CONFIG_INVALID", f"Unable to read operational land grid map: {grid_path}") from exc

    lat_step, lat_anchor = _region_guide_grid_axis(all_land_latitudes)
    lon_step, lon_anchor = _region_guide_grid_axis(all_land_longitudes)
    if abs(lat_step - lon_step) > 1.0e-8:
        raise Pass2PipelineError(
            "REGION_GUIDE_CONFIG_INVALID",
            f"Region guide requires a square AIFS grid: lat_step={lat_step} lon_step={lon_step}",
        )
    land_geometry = _load_region_guide_land_geometry(root)

    regions: list[dict[str, Any]] = []
    for raw in raw_regions:
        if not isinstance(raw, dict) or not bool(raw.get("enabled_in_pass1")):
            continue
        role = str(raw.get("region_role") or "")
        if role not in REGION_GUIDE_ROLE_LABELS:
            continue
        region_id = str(raw.get("region_id") or "").strip()
        name_ko = str(raw.get("name_ko") or "").strip()
        bbox_raw = raw.get("bbox")
        if not region_id or not name_ko or not isinstance(bbox_raw, dict):
            raise Pass2PipelineError("REGION_GUIDE_CONFIG_INVALID", "Region guide entry has incomplete identity or bbox.")
        try:
            bbox = {
                "west": float(bbox_raw["lon_min"]),
                "east": float(bbox_raw["lon_max"]),
                "south": float(bbox_raw["lat_min"]),
                "north": float(bbox_raw["lat_max"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise Pass2PipelineError("REGION_GUIDE_CONFIG_INVALID", f"{region_id} has an invalid bbox.") from exc

        mask_type = str(raw.get("mask_type") or "")
        if role == "operational_land":
            points = sorted(land_points.get(region_id, []))
            expected_count = raw.get("expected_aifs_grid_count")
            if not points:
                raise Pass2PipelineError("REGION_GUIDE_CONFIG_INVALID", f"{region_id} has no fixed AIFS grid points.")
            if isinstance(expected_count, int) and len(points) != expected_count:
                raise Pass2PipelineError(
                    "REGION_GUIDE_CONFIG_INVALID",
                    f"{region_id} grid count mismatch: expected={expected_count} actual={len(points)}",
                )
            grid_source = "fixed_aifs_grid_points"
        else:
            if mask_type not in {"sea", "mixed"}:
                raise Pass2PipelineError(
                    "REGION_GUIDE_CONFIG_INVALID",
                    f"{region_id} has unsupported non-land mask_type={mask_type!r}.",
                )
            points = _region_guide_dynamic_grid_points(
                bbox=bbox,
                mask_type=mask_type,
                lat_step=lat_step,
                lon_step=lon_step,
                lat_anchor=lat_anchor,
                lon_anchor=lon_anchor,
                land_geometry=land_geometry,
            )
            if not points:
                raise Pass2PipelineError("REGION_GUIDE_CONFIG_INVALID", f"{region_id} has no AIFS analysis grid points.")
            grid_source = "bbox_plus_sea_mask" if mask_type == "sea" else "bbox_only_mixed"

        display_bbox = _region_guide_display_bbox(
            bbox,
            lat_step=lat_step,
            lon_step=lon_step,
            lat_anchor=lat_anchor,
            lon_anchor=lon_anchor,
        )
        definition = f"AIFS 격자 {len(points)}개"
        regions.append(
            {
                "region_id": region_id,
                "name": _region_guide_compact_name(name_ko, role),
                "role": role,
                "role_label": REGION_GUIDE_ROLE_LABELS[role],
                "bbox": bbox,
                "display_bbox": display_bbox,
                "mask_type": mask_type,
                "definition": definition,
                "analysis_purpose": str(raw.get("analysis_purpose") or "").strip(),
                "grid_points": points,
                "grid_source": grid_source,
                "grid_step_degrees": lat_step,
                "focus_viewbox": _region_guide_focus_viewbox(bbox),
            }
        )
    order = {role: index for index, role in enumerate(REGION_GUIDE_ROLE_ORDER)}
    regions.sort(key=lambda item: (order[item["role"]], item["name"], item["region_id"]))
    return regions


def _region_guide_bbox_rect(bbox: dict[str, float]) -> tuple[float, float, float, float]:
    x1, y1 = _minimap_project(float(bbox["west"]), float(bbox["north"]))
    x2, y2 = _minimap_project(float(bbox["east"]), float(bbox["south"]))
    return min(x1, x2), min(y1, y2), max(2.0, abs(x2 - x1)), max(2.0, abs(y2 - y1))


def _render_region_guide_grid_cells(region: dict[str, Any]) -> str:
    parts: list[str] = []
    half_step = float(region.get("grid_step_degrees") or 0.25) / 2.0
    for lat, lon in region.get("grid_points", []):
        west, east = lon - half_step, lon + half_step
        south, north = lat - half_step, lat + half_step
        x1, y1 = _minimap_project(west, north)
        x2, y2 = _minimap_project(east, south)
        parts.append(
            f'<rect x="{min(x1, x2):.2f}" y="{min(y1, y2):.2f}" width="{abs(x2 - x1):.2f}" '
            f'height="{abs(y2 - y1):.2f}" />'
        )
    return "".join(parts)


def _render_region_guide_dialog(regions: list[dict[str, Any]], geography: dict[str, str]) -> str:
    if not regions:
        return ""
    default_region = next((item for item in regions if item["region_id"] == "MARINE_WEST_SOUTH"), regions[0])
    counts = {role: sum(1 for item in regions if item["role"] == role) for role in REGION_GUIDE_ROLE_ORDER}
    role_tabs = "".join(
        f'<button class="region-guide-role-tab{" active" if role == default_region["role"] else ""}" type="button" '
        f'data-region-guide-role="{escape(role, quote=True)}">{escape(REGION_GUIDE_ROLE_LABELS[role])} <b>{counts[role]}</b></button>'
        for role in REGION_GUIDE_ROLE_ORDER
        if counts[role]
    )
    region_buttons = "".join(
        f'<button class="region-guide-region-button{" active" if item["region_id"] == default_region["region_id"] else ""}" '
        f'type="button" data-region-guide-id="{escape(item["region_id"], quote=True)}" '
        f'data-region-guide-role-name="{escape(item["role"], quote=True)}"'
        f'{"" if item["role"] == default_region["role"] else " hidden"}>{escape(item["name"])}</button>'
        for item in regions
    )

    analysis_grid_groups: list[str] = []
    bbox_outlines: list[str] = []
    for item in regions:
        region_id = escape(item["region_id"], quote=True)
        role_class = escape(item["role"], quote=True)
        active = " active" if item["region_id"] == default_region["region_id"] else ""
        analysis_grid_groups.append(
            f'<g class="region-guide-overlay region-guide-analysis-grid region-guide-{role_class}{active}" '
            f'data-region-guide-shape="{region_id}">{_render_region_guide_grid_cells(item)}</g>'
        )
        x, y, width, height = _region_guide_bbox_rect(item["display_bbox"])
        bbox_outlines.append(
            f'<g class="region-guide-overlay region-guide-bbox-outline region-guide-{role_class}{active}" '
            f'data-region-guide-shape="{region_id}"><rect x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" height="{height:.2f}" /></g>'
        )

    grid_lines: list[str] = []
    for lon in range(120, 146, 5):
        x, _ = _minimap_project(float(lon), MINIMAP_LAT_MIN)
        grid_lines.append(f'<line x1="{x:.2f}" y1="0" x2="{x:.2f}" y2="{MINIMAP_VIEWBOX_HEIGHT:.2f}" />')
    for lat in range(30, 47, 5):
        _, y = _minimap_project(MINIMAP_LON_MIN, float(lat))
        grid_lines.append(f'<line x1="0" y1="{y:.2f}" x2="{MINIMAP_VIEWBOX_WIDTH:.2f}" y2="{y:.2f}" />')
    korea_x, korea_y = _minimap_project(127.4, 38.3)
    west_sea_x, west_sea_y = _minimap_project(123.4, 36.7)
    east_sea_x, east_sea_y = _minimap_project(132.2, 38.0)
    south_sea_x, south_sea_y = _minimap_project(128.0, 31.5)
    map_svg = (
        f'<svg id="region-guide-svg" class="region-guide-svg" viewBox="0 0 {MINIMAP_VIEWBOX_WIDTH:.0f} {MINIMAP_VIEWBOX_HEIGHT:.0f}" '
        f'data-default-viewbox="0 0 {MINIMAP_VIEWBOX_WIDTH:.0f} {MINIMAP_VIEWBOX_HEIGHT:.0f}" role="img" aria-label="분석 지역 벡터 지도">'
        '<rect class="region-guide-water" x="0" y="0" width="100%" height="100%" rx="10" ry="10" />'
        '<g class="region-guide-grid">' + "".join(grid_lines) + '</g>'
        + '<g class="region-guide-land-base">' + geography["land_paths"] + '</g>'
        + '<g class="region-guide-coastline">' + geography["coastline_paths"] + '</g>'
        + "".join(analysis_grid_groups)
        + "".join(bbox_outlines)
        + f'<text class="region-guide-map-label region-guide-map-label-korea" x="{korea_x:.2f}" y="{korea_y:.2f}">한반도</text>'
        + f'<text class="region-guide-map-label" x="{west_sea_x:.2f}" y="{west_sea_y:.2f}">서해</text>'
        + f'<text class="region-guide-map-label" x="{east_sea_x:.2f}" y="{east_sea_y:.2f}">동해</text>'
        + f'<text class="region-guide-map-label" x="{south_sea_x:.2f}" y="{south_sea_y:.2f}">남해</text>'
        + '</svg>'
    )
    catalog = {
        item["region_id"]: {
            "name": item["name"],
            "role": item["role"],
            "role_label": item["role_label"],
            "definition": item["definition"],
            "analysis_purpose": item["analysis_purpose"],
            "viewbox": [round(value, 2) for value in item["focus_viewbox"]],
        }
        for item in regions
    }
    catalog_json = json.dumps(catalog, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (
        '<dialog id="region-guide-modal" aria-labelledby="region-guide-title"><div class="region-guide-content">'
        '<div class="region-guide-header"><div><span class="section-kicker">분석에 사용된 지역</span><h2 id="region-guide-title">분석 지역</h2></div>'
        '<button class="region-guide-close" type="button">닫기</button></div>'
        '<div class="region-guide-body"><aside class="region-guide-sidebar">'
        f'<div class="region-guide-role-tabs">{role_tabs}</div><div class="region-guide-region-list">{region_buttons}</div>'
        '</aside><section class="region-guide-map-panel"><div class="region-guide-selected">'
        f'<strong id="region-guide-selected-name">{escape(default_region["name"])}</strong>'
        f'<span id="region-guide-selected-role">{escape(default_region["role_label"])}</span>'
        f'<span id="region-guide-selected-definition">{escape(default_region["definition"])}</span>'
        '</div><div class="region-guide-map-toolbar"><div id="region-guide-map-legend" class="region-guide-map-legend">'
        '<span title="설정 파일에 정의된 bbox 범위"><i class="region-guide-legend-outline"></i>정의범위</span>'
        '<span title="통계에 실제 사용되는 AIFS 격자"><i class="region-guide-legend-fill"></i>실제 분석영역</span>'
        '</div><div class="region-guide-map-controls" aria-label="분석 지역 지도 조작">'
        '<button type="button" data-region-guide-zoom="in" title="지도 확대" aria-label="지도 확대">+</button>'
        '<button type="button" data-region-guide-zoom="out" title="지도 축소" aria-label="지도 축소">−</button>'
        '<button type="button" data-region-guide-zoom="reset" title="전체 지도 보기">전체</button>'
        '</div></div><div class="region-guide-map-stage">'
        f'{map_svg}</div><p id="region-guide-selected-purpose" class="region-guide-purpose">{escape(default_region["analysis_purpose"])}</p>'
        '</section></div><script id="region-guide-data" type="application/json">'
        f'{catalog_json}</script></div></dialog>'
    )


def _render_candidate_minimap(area: dict[str, Any], label: str, geography: dict[str, str]) -> str:
    west = max(MINIMAP_LON_MIN, min(MINIMAP_LON_MAX, float(area["west"])))
    east = max(MINIMAP_LON_MIN, min(MINIMAP_LON_MAX, float(area["east"])))
    south = max(MINIMAP_LAT_MIN, min(MINIMAP_LAT_MAX, float(area["south"])))
    north = max(MINIMAP_LAT_MIN, min(MINIMAP_LAT_MAX, float(area["north"])))
    if west >= east or south >= north:
        raise Pass2PipelineError("MINIMAP_AREA_INVALID", "Candidate minimap area bounds are invalid.")
    x1, y1 = _minimap_project(west, north)
    x2, y2 = _minimap_project(east, south)
    rect_x = min(x1, x2)
    rect_y = min(y1, y2)
    rect_w = max(6.0, abs(x2 - x1))
    rect_h = max(6.0, abs(y2 - y1))
    center_x = rect_x + rect_w / 2.0
    center_y = rect_y + rect_h / 2.0
    grid_lines = []
    for lon in range(120, 146, 5):
        x, _ = _minimap_project(float(lon), MINIMAP_LAT_MIN)
        grid_lines.append(f'<line x1="{x:.2f}" y1="0" x2="{x:.2f}" y2="{MINIMAP_VIEWBOX_HEIGHT:.2f}" />')
    for lat in range(30, 47, 5):
        _, y = _minimap_project(MINIMAP_LON_MIN, float(lat))
        grid_lines.append(f'<line x1="0" y1="{y:.2f}" x2="{MINIMAP_VIEWBOX_WIDTH:.2f}" y2="{y:.2f}" />')
    korea_x, korea_y = _minimap_project(127.4, 38.3)
    west_sea_x, west_sea_y = _minimap_project(123.4, 36.7)
    east_sea_x, east_sea_y = _minimap_project(132.2, 38.0)
    south_sea_x, south_sea_y = _minimap_project(128.0, 31.5)
    return (
        f'<svg class="candidate-minimap-svg" viewBox="0 0 {MINIMAP_VIEWBOX_WIDTH:.0f} {MINIMAP_VIEWBOX_HEIGHT:.0f}" '
        f'data-default-viewbox="0 0 {MINIMAP_VIEWBOX_WIDTH:.0f} {MINIMAP_VIEWBOX_HEIGHT:.0f}" role="img" aria-label="{escape(label, quote=True)} 위치도">'
        f'<title>{escape(label)}</title>'
        '<rect class="minimap-water" x="0" y="0" width="100%" height="100%" rx="12" ry="12" />'
        '<g class="minimap-grid">' + ''.join(grid_lines) + '</g>'
        '<g class="minimap-land">' + geography["land_paths"] + '</g>'
        '<g class="minimap-coastline">' + geography["coastline_paths"] + '</g>'
        f'<text class="minimap-label minimap-label-korea" x="{korea_x:.2f}" y="{korea_y:.2f}">한반도</text>'
        f'<text class="minimap-label minimap-label-west" x="{west_sea_x:.2f}" y="{west_sea_y:.2f}">서해</text>'
        f'<text class="minimap-label minimap-label-east" x="{east_sea_x:.2f}" y="{east_sea_y:.2f}">동해</text>'
        f'<text class="minimap-label minimap-label-south" x="{south_sea_x:.2f}" y="{south_sea_y:.2f}">남해</text>'
        f'<rect class="minimap-bbox" x="{rect_x:.2f}" y="{rect_y:.2f}" width="{rect_w:.2f}" height="{rect_h:.2f}" rx="4" ry="4" />'
        f'<circle class="minimap-center" cx="{center_x:.2f}" cy="{center_y:.2f}" r="3.4" />'
        '</svg>'
    )


def _render_minimap_trigger(candidate: dict[str, Any], init: str, geography: dict[str, str]) -> str:
    area = candidate["effective_area"]
    area_text = _render_area(area)
    period_text = _render_time(candidate["effective_time_window"], init)
    map_svg = _render_candidate_minimap(area, candidate["candidate_name"], geography)
    return (
        f'<button class="inline-map-trigger" type="button" data-map-title="{escape(candidate["candidate_name"], quote=True)}" '
        f'data-map-area="{escape(area_text, quote=True)}" data-map-time="{escape(period_text, quote=True)}" aria-label="{escape(candidate["candidate_name"], quote=True)} 위치도 확인">'
        '<span class="inline-map-trigger-icon" aria-hidden="true">🗺</span>'
        '<span class="inline-map-trigger-label">지도확인</span>'
        f'<span class="map-template" hidden>{map_svg}</span>'
        '</button>'
    )


def _feature_label(feature_id: str) -> str:
    return FEATURE_LABELS.get(feature_id, "기타 물리량")


def _diagnostic_label(physical_diagnostic: str) -> str:
    return PHYSICAL_DIAGNOSTIC_LABELS.get(physical_diagnostic, "상세 예측장")


def _source_ref_key(source: dict[str, Any]) -> tuple[str, str, int]:
    forecast_hour = source.get("forecast_hour")
    return (
        str(source.get("request_id") or ""),
        str(source.get("physical_diagnostic") or ""),
        forecast_hour if isinstance(forecast_hour, int) and not isinstance(forecast_hour, bool) else -1,
    )


def _popup_image_entry(asset: dict[str, Any], source: dict[str, Any], selection: str, init: str) -> dict[str, Any]:
    source_hour = source.get("forecast_hour")
    diagnostic = str(source.get("physical_diagnostic") or "")
    return {
        "relative_path": _versioned_asset_path(asset["relative_path"], asset.get("sha256")),
        "asset_id": asset["asset_id"],
        "time": format_valid_time_kst(init, source_hour) if isinstance(source_hour, int) and not isinstance(source_hour, bool) else "다중시각 대표 이미지",
        "physical_diagnostic": diagnostic,
        "diagnostic_label": _diagnostic_label(diagnostic),
        "selection": selection,
    }


def _select_quantitative_popup_image(
    asset_ids: list[str],
    assets_by_id: dict[str, dict[str, Any]],
    *,
    candidate_id: str,
    check_id: str,
    feature_id: str,
    forecast_hour: int,
    init: str,
) -> dict[str, Any] | None:
    compatible_diagnostics = set(QUANTITATIVE_IMAGE_DIAGNOSTICS.get(feature_id, ()))
    same_time: list[tuple[dict[str, Any], dict[str, Any]]] = []
    if compatible_diagnostics:
        for asset in assets_by_id.values():
            if candidate_id not in asset.get("candidate_ids", []) or check_id not in asset.get("check_ids", []):
                continue
            for source in asset.get("source_refs", []):
                if not isinstance(source, dict):
                    continue
                if source.get("physical_diagnostic") in compatible_diagnostics and source.get("forecast_hour") == forecast_hour:
                    same_time.append((asset, source))
    if same_time:
        asset, source = min(same_time, key=lambda item: (_source_ref_key(item[1]), item[0]["asset_id"]))
        return _popup_image_entry(asset, source, "same_time_related", init)

    # Never attach a different forecast hour merely because it is the
    # representative image for the Check.  Quantitative values may open only an
    # exact-time, physically compatible spatial field.
    del asset_ids
    return None


def _render_point(location: dict[str, Any] | None) -> str:
    if not isinstance(location, dict):
        return ""
    latitude = location.get("latitude")
    longitude = location.get("longitude")
    if not isinstance(latitude, (int, float)) or isinstance(latitude, bool) or not isinstance(longitude, (int, float)) or isinstance(longitude, bool):
        return ""
    lon_label = "동경" if longitude >= 0 else "서경"
    lat_label = "북위" if latitude >= 0 else "남위"
    return f"{lon_label} {abs(float(longitude)):.2f}°, {lat_label} {abs(float(latitude)):.2f}°"


def _format_quantitative_value(feature_id: str, value: float) -> str:
    return format_quantitative_display_value(feature_id, value)


def _display_unit(units: str) -> str:
    """Convert machine-friendly unit strings to forecaster-facing typography."""
    mapping = {
        "1e-5 s-1": "×10⁻⁵ s⁻¹",
        "s-1": "s⁻¹",
        "m s-1": "m s⁻¹",
        "Pa s-1": "Pa s⁻¹",
        "g kg-1": "g kg⁻¹",
        "g kg-1 m s-1": "g kg⁻¹ m s⁻¹",
        "kg m-2": "kg m⁻²",
        "degC": "°C",
    }
    return mapping.get(str(units), str(units))


def _render_quantitative_series(
    rows: list[dict[str, Any]],
    asset_ids: list[str],
    assets_by_id: dict[str, dict[str, Any]],
    init: str,
    *,
    candidate_id: str,
    check_id: str,
) -> str:
    """Render complete Step05 temporal means as lightweight interactive SVG charts."""
    if not rows:
        return ""
    by_feature: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        feature_id = str(row.get("feature_id") or "")
        if feature_id:
            by_feature.setdefault(feature_id, []).append(row)
    charts: list[str] = []
    for feature_id, feature_rows in by_feature.items():
        hours = sorted({
            int(row["forecast_hour"])
            for row in feature_rows
            if isinstance(row.get("forecast_hour"), int) and not isinstance(row.get("forecast_hour"), bool)
        })
        if len(hours) < 2:
            continue
        region_order: list[str] = []
        region_names: dict[str, str] = {}
        rows_by_region: dict[str, list[dict[str, Any]]] = {}
        for row in feature_rows:
            region_id = str(row.get("region_id") or "")
            region_name = str(row.get("region_name") or region_id)
            if not region_id:
                continue
            if region_id not in rows_by_region:
                region_order.append(region_id)
                rows_by_region[region_id] = []
                region_names[region_id] = region_name
            rows_by_region[region_id].append(row)
        regions: list[dict[str, Any]] = []
        units = str(feature_rows[0].get("units") or "")
        for region_id in region_order:
            points: list[dict[str, Any]] = []
            for row in sorted(rows_by_region[region_id], key=lambda item: int(item["forecast_hour"])):
                hour = int(row["forecast_hour"])
                value = float(row["resolved_value"])
                related = _select_quantitative_popup_image(
                    asset_ids, assets_by_id, candidate_id=candidate_id, check_id=check_id,
                    feature_id=feature_id, forecast_hour=hour, init=init,
                )
                points.append({
                    "hour": hour,
                    "time": format_valid_time_kst(init, hour),
                    "value": value,
                    "display_value": _format_quantitative_value(feature_id, value),
                    "related_image": related,
                })
            if points:
                regions.append({"region_id": region_id, "region_name": region_names[region_id], "points": points})
        if not regions:
            continue
        payload = {
            "feature_id": feature_id,
            "feature_label": _feature_label(feature_id),
            "unit": _display_unit(units),
            "regions": regions,
        }
        encoded = escape(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), quote=True)
        options = ''.join(
            f'<option value="{escape(region["region_id"], quote=True)}">{escape(region["region_name"])}</option>'
            for region in regions
        )
        selector = (
            '<label class="quant-series-region-label">영역 '            f'<select class="quant-series-region">{options}</select></label>'
            if len(regions) > 1 else f'<span class="quant-series-region-single">{escape(regions[0]["region_name"])}</span>'
        )
        charts.append(
            f'<div class="quant-series-card" data-quant-series="{encoded}">'
            '<div class="quant-series-head">'
            f'<div><strong>{escape(_feature_label(feature_id))} 시간변화</strong><small>운영권역별 평균</small></div>{selector}'
            '</div>'
            '<div class="quant-series-stage"><svg class="quant-series-svg" viewBox="0 0 720 260" role="img" '
            f'aria-label="{escape(_feature_label(feature_id), quote=True)} 시간변화 그래프"></svg>'
            '<div class="quant-series-tooltip" hidden></div></div>'
            '<p class="quant-series-note">점에 마우스를 올리면 값을 확인할 수 있습니다. 대표 공간장이 있는 시각의 점은 클릭할 수 있습니다.</p>'
            '</div>'
        )
    if not charts:
        return ""
    return '<div class="quant-series-wrap"><h5>정량 시간변화</h5>' + ''.join(charts) + '</div>'


def _render_quantitative(
    rows: list[dict[str, Any]],
    asset_ids: list[str],
    assets_by_id: dict[str, dict[str, Any]],
    init: str,
    *,
    candidate_id: str,
    check_id: str,
) -> str:
    if not rows:
        return '<p class="empty-inline">제공할 정량 분석값 없음</p>'
    body: list[str] = []
    for row in rows:
        feature_id = str(row["feature_id"])
        feature_label = _feature_label(feature_id)
        related_image = _select_quantitative_popup_image(
            asset_ids,
            assets_by_id,
            candidate_id=candidate_id,
            check_id=check_id,
            feature_id=feature_id,
            forecast_hour=int(row["forecast_hour"]),
            init=init,
        )
        if related_image is not None:
            metadata = escape(json.dumps(related_image, ensure_ascii=False, separators=(",", ":")), quote=True)
            feature = (
                f'<button class="quant-feature-button" type="button" data-related-image="{metadata}" '
                f'data-feature-label="{escape(feature_label, quote=True)}" data-feature-id="{escape(feature_id, quote=True)}" '
                f'data-quantitative-time="{escape(format_valid_time_kst(init, int(row["forecast_hour"])), quote=True)}">'
                f'{escape(feature_label)}</button>'
            )
        else:
            feature = f'<span class="feature-label">{escape(feature_label)}</span>'
        location = _render_point(row.get("resolved_location"))
        display_value = _format_quantitative_value(feature_id, float(row["resolved_value"]))
        value = f'{display_value} {escape(_display_unit(str(row["units"])))}'
        if location:
            value += f'<small class="resolved-location">{escape(location)}</small>'
        body.append(
            '<tr>'
            f'<td data-label="시각" class="nowrap">{escape(format_valid_time_kst(init, int(row["forecast_hour"])))}</td>'
            f'<td data-label="물리량">{feature}</td>'
            f'<td data-label="영역">{escape(row["region_name"])}</td>'
            f'<td data-label="통계">{escape(STATISTIC_LABELS.get(str(row["statistic"]), "기타"))}</td>'
            f'<td data-label="값" class="num">{value}</td>'
            '</tr>'
        )
    return (
        '<div class="quant-table-wrap"><table class="quant-table"><thead><tr>'
        '<th>시각</th><th>물리량</th><th>영역</th><th>통계</th><th>값</th>'
        '</tr></thead><tbody>' + ''.join(body) + '</tbody></table></div>'
    )


def _render_asset(asset_id: str, assets_by_id: dict[str, dict[str, Any]], candidate_name: str, init: str) -> str:
    asset = assets_by_id[asset_id]
    source_refs = [item for item in asset.get("source_refs", []) if isinstance(item, dict)]
    source = source_refs[0] if source_refs else {}
    hour = source.get("forecast_hour")
    time = format_valid_time_kst(init, hour) if isinstance(hour, int) and not isinstance(hour, bool) else "다중시각 대표 이미지"
    diagnostic = str(source.get("physical_diagnostic") or "")
    caption = f"{time} · {_diagnostic_label(diagnostic)}"
    image_url = _versioned_asset_path(asset["relative_path"], asset.get("sha256"))
    return (
        '<figure class="evidence-figure">'
        f'<button class="evidence-image-button" type="button" data-image-path="{escape(image_url, quote=True)}" '
        f'data-image-caption="{escape(caption, quote=True)}">'
        f'<img src="{escape(image_url, quote=True)}" loading="lazy" decoding="async" alt="{escape(candidate_name, quote=True)} 관련 대표 근거 이미지">'
        '</button>'
        f'<figcaption>{escape(caption)}</figcaption>'
        '</figure>'
    )


def _role_label(role: str) -> str:
    return ROLE_LABELS.get(role, "기타 판단 근거")


def _render_role_chips(roles: list[str]) -> str:
    return ''.join(f'<span class="role-chip">{escape(_role_label(role))}</span>' for role in roles)


def _assessment_badge(assessment: str) -> str:
    label = ASSESSMENT_LABELS.get(assessment, assessment)
    style = ASSESSMENT_STYLE_CLASSES.get(assessment, "muted")
    return f'<span class="assessment-badge {style}">{escape(label)}</span>'


def _environment_categories(categories: list[str]) -> list[str]:
    """Return validated categories that describe the precursor environment only."""
    return [category for category in categories if category != "direct_precipitation"]


def _render_precursor_chips(categories: list[str], environment_support_status: str | None = None) -> str:
    """Render environment-category chips without contradicting the aggregate environment status."""
    environment_categories = _environment_categories(categories)
    if not environment_categories:
        empty_label = {
            "partial": "강수 발달 환경 부분 확인",
            "insufficient_evidence": "강수 발달 환경 판단 제한",
            "confirmed": "강수 발달 환경 확인",
            "not_confirmed": "확인된 강수 발달 환경 없음",
        }.get(environment_support_status, "확인된 강수 발달 환경 없음")
        return f'<span class="category-chip muted">{escape(empty_label)}</span>'
    return ''.join(
        f'<span class="category-chip">{escape(PRECURSOR_CATEGORY_LABELS.get(category, category))}</span>'
        for category in environment_categories
    )


def _published_evidence_hours(
    candidate: dict[str, Any],
    assets_by_id: dict[str, dict[str, Any]],
    *,
    forecast_start: int,
    forecast_end: int,
) -> set[int]:
    hours: set[int] = set()
    check_ids = set(candidate["publication_check_ids"])
    for block in candidate["evidence_blocks"]:
        for row in block["quantitative_evidence"]:
            hour = row.get("forecast_hour")
            if isinstance(hour, int) and not isinstance(hour, bool) and forecast_start <= hour <= forecast_end:
                hours.add(hour)
    for asset in assets_by_id.values():
        if candidate["candidate_id"] not in asset.get("candidate_ids", []):
            continue
        if not check_ids.intersection(str(check_id) for check_id in asset.get("check_ids", [])):
            continue
        for source in asset.get("source_refs", []):
            if not isinstance(source, dict):
                continue
            hour = source.get("forecast_hour")
            if isinstance(hour, int) and not isinstance(hour, bool) and forecast_start <= hour <= forecast_end:
                hours.add(hour)
    return hours


def _timeline_tick_hours(
    candidate: dict[str, Any],
    assets_by_id: dict[str, dict[str, Any]],
    forecast_timeline: tuple[int, int, int] = DEFAULT_FORECAST_TIMELINE,
) -> tuple[list[int], set[int], set[int]]:
    forecast_start, forecast_end, interval = forecast_timeline
    hours = list(range(forecast_start, forecast_end + 1, interval))
    if not hours or hours[-1] != forecast_end:
        hours.append(forecast_end)
    peaks = {int(hour) for hour in candidate["peak_forecast_hours"] if forecast_start <= int(hour) <= forecast_end}
    evidence = _published_evidence_hours(
        candidate, assets_by_id, forecast_start=forecast_start, forecast_end=forecast_end
    )
    for hour in sorted(peaks | evidence | {forecast_start, forecast_end}):
        if hour not in hours:
            hours.append(hour)
    return sorted(set(hours)), evidence, peaks


def _system_direct_precipitation_anchor_peak_details(
    group: dict[str, Any],
    peak_forecast_hours: Iterable[int],
) -> dict[int, dict[str, Any]]:
    """Return peak-hour region maxima already computed by the SYSTEM direct-precipitation map.

    Step05 adds ``operational_scope_region_summaries`` only to the dedicated
    SYSTEM direct-precipitation presence request.  These values are deterministic
    operational-region maxima from the same 6-hour precipitation field shown to
    the forecaster.  They are suitable as a publication fallback when Step04 did
    not separately request a quantitative precipitation excerpt.
    """
    peak_set = {
        int(hour)
        for hour in peak_forecast_hours
        if isinstance(hour, int) and not isinstance(hour, bool)
    }
    if not peak_set:
        return {}

    context = group.get("candidate_context")
    plan = context.get("analysis_plan") if isinstance(context, dict) else None
    checks = plan.get("variable_checks") if isinstance(plan, dict) else None
    if not isinstance(checks, list):
        return {}

    anchor_ids: list[str] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        intent = check.get("intent_contract")
        if not isinstance(intent, dict):
            continue
        if (
            intent.get("direct_precipitation_anchor") is True
            and str(intent.get("source_check_key") or "") == "SYS_DIRECT_PRECIPITATION_PRESENCE"
        ):
            check_id = str(check.get("check_id") or "").strip()
            if check_id:
                anchor_ids.append(check_id)
    if len(anchor_ids) != 1:
        return {}
    anchor_id = anchor_ids[0]

    best_by_hour: dict[int, tuple[float, str, str]] = {}
    requests = group.get("analysis_data_requests")
    if not isinstance(requests, list):
        return {}
    for entry in requests:
        if not isinstance(entry, dict):
            continue
        request = entry.get("request")
        if not isinstance(request, dict):
            continue
        source_ids = request.get("source_check_ids")
        if not isinstance(source_ids, list) or anchor_id not in {str(value) for value in source_ids}:
            continue
        if (
            request.get("data_type") != "spatial_field"
            or request.get("product_id") != "QL_PRECIP_6H"
        ):
            continue
        step05 = entry.get("step05")
        stored = step05.get("result") if isinstance(step05, dict) else None
        if not isinstance(stored, dict) or stored.get("status") != "success":
            continue
        images = stored.get("generated_images")
        if not isinstance(images, list):
            continue
        for image in images:
            if not isinstance(image, dict) or image.get("physical_diagnostic") != "QL_PRECIP_6H":
                continue
            hour = image.get("forecast_hour")
            if isinstance(hour, bool) or not isinstance(hour, int) or hour not in peak_set:
                continue
            summaries = image.get("operational_scope_region_summaries")
            if not isinstance(summaries, list):
                continue
            for row in summaries:
                if not isinstance(row, dict):
                    continue
                region_id = row.get("region_id")
                region_name = row.get("region_name")
                value = row.get("maximum_6h_mm")
                if (
                    not isinstance(region_id, str)
                    or not region_id
                    or not isinstance(region_name, str)
                    or not region_name
                    or isinstance(value, bool)
                    or not isinstance(value, (int, float))
                ):
                    continue
                resolved = (float(value), region_id, region_name)
                previous = best_by_hour.get(hour)
                if (
                    previous is None
                    or resolved[0] > previous[0]
                    or (resolved[0] == previous[0] and resolved[1] < previous[1])
                ):
                    best_by_hour[hour] = resolved

    return {
        hour: {
            "forecast_hour": hour,
            "region_name": _compact_peak_precip_region_name(best[2]),
            "value_mm": best[0],
        }
        for hour, best in best_by_hour.items()
    }


def _peak_six_hour_precip_details_from_group(
    group: dict[str, Any],
    peak_forecast_hours: Iterable[int],
    *,
    allow_system_anchor_fallback: bool = False,
) -> list[dict[str, Any]]:
    """Resolve timeline peak details without changing Step09 naming evidence.

    Explicit Step05 quantitative excerpts remain authoritative.  For a confirmed
    direct-precipitation candidate, unresolved peak hours may fall back to the
    operational-region maxima already embedded in the SYSTEM direct-precipitation
    spatial request.
    """
    validated_requests = _require_list(
        group.get("analysis_data_requests"),
        code="STEP06_PACKAGE_INVALID",
        message=f"{group.get('candidate_id')} has no analysis requests.",
    )
    normalized_group = dict(group)
    normalized_group["analysis_data_requests"] = validated_requests
    details = candidate_peak_precipitation_details_from_group(
        normalized_group,
        peak_forecast_hours,
    )
    fallback = (
        _system_direct_precipitation_anchor_peak_details(normalized_group, peak_forecast_hours)
        if allow_system_anchor_fallback
        else {}
    )
    result: list[dict[str, Any]] = []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        hour = detail.get("forecast_hour")
        region_name = detail.get("region_name")
        value_mm = detail.get("value_mm")
        resolved = (
            isinstance(region_name, str)
            and bool(region_name.strip())
            and isinstance(value_mm, (int, float))
            and not isinstance(value_mm, bool)
        )
        if not resolved and isinstance(hour, int) and not isinstance(hour, bool):
            fallback_detail = fallback.get(hour)
            if isinstance(fallback_detail, dict):
                result.append(dict(fallback_detail))
                continue
        result.append(
            {
                "forecast_hour": hour,
                "region_name": region_name,
                "value_mm": value_mm,
            }
        )
    return result


def _peak_six_hour_precip_details_by_candidate(
    root: Path,
    init: str,
    product: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    package = load_step06_package(root, init)
    groups = {
        str(group.get("candidate_id")): group
        for group in active_candidate_groups(package)
        if isinstance(group, dict) and isinstance(group.get("candidate_id"), str)
    }
    result: dict[str, list[dict[str, Any]]] = {}
    for candidate in product.get("surfaced_candidates", []):
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        group = groups.get(candidate_id)
        if group is None:
            continue
        result[candidate_id] = _peak_six_hour_precip_details_from_group(
            group,
            candidate.get("peak_forecast_hours", []),
            allow_system_anchor_fallback=(
                candidate.get("candidate_type") == "direct_precipitation"
                and candidate.get("direct_precipitation_status") == "confirmed"
            ),
        )
    return result


def _resolved_peak_precip_details(details: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only peak-precipitation entries that are meaningful to render.

    A peak marker is a user-facing direct-precipitation statement, so both the
    operational region and the 6-hour precipitation value must be resolved and
    the value must meet the 1.0 mm / 6 h operational presence threshold.
    Sub-threshold and unresolved entries remain in upstream/detail data, but
    Step10 does not promote them into the top timeline summary/markers.
    """
    resolved: list[dict[str, Any]] = []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        forecast_hour = detail.get("forecast_hour")
        region_name = detail.get("region_name")
        value_mm = detail.get("value_mm")
        if not isinstance(forecast_hour, int) or isinstance(forecast_hour, bool):
            continue
        if not isinstance(region_name, str) or not region_name.strip():
            continue
        if not isinstance(value_mm, (int, float)) or isinstance(value_mm, bool):
            continue
        if float(value_mm) < DIRECT_PRECIPITATION_MINIMUM_MM_6H:
            continue
        resolved.append(detail)
    return resolved


def _render_peak_precip_summary(details: Iterable[dict[str, Any]], init: str) -> str:
    resolved_details = _resolved_peak_precip_details(details)
    if not resolved_details:
        return ''

    items: list[str] = []
    for index, detail in enumerate(resolved_details, start=1):
        region_name = str(detail["region_name"])
        value_mm = float(detail["value_mm"])
        label = f"{region_name} ({value_mm:.1f}mm)"
        label_html = (
            f'<button class="timeline-peak-region-link" type="button" '
            f'data-region-guide-name="{escape(region_name, quote=True)}">{escape(label)}</button>'
        )
        items.append(
            f'<span class="timeline-peak-summary-item" data-peak-index="{index}">'
            f'<i class="timeline-peak-summary-number" aria-hidden="true">{index}</i>'
            f'{label_html}</span>'
        )
    return (
        '<div class="timeline-peak-region" aria-label="6시간 최대 강수">'
        '<span>6시간 최대 강수</span>'
        f'<strong>{"".join(items)}</strong>'
        '</div>'
    )


def _render_peak_precip_markers(
    peaks: Iterable[int],
    details: Iterable[dict[str, Any]],
    init: str,
    forecast_start: int,
    forecast_end: int,
    peak_label: str,
) -> str:
    # ``peaks`` is retained in the signature for compatibility with the
    # timeline call site.  User-facing peak markers are rendered only when
    # the corresponding operational region and precipitation value are both
    # resolved; unresolved peak hours remain internal.
    _ = peaks
    parts: list[str] = []
    for index, detail in enumerate(_resolved_peak_precip_details(details), start=1):
        hour = int(detail["forecast_hour"])
        region_name = str(detail["region_name"])
        value_mm = float(detail["value_mm"])
        tooltip = " · ".join(
            [
                f"{index}",
                format_forecast_hour_dual(init, hour),
                region_name,
                f"6시간 강수 {value_mm:.1f}mm",
            ]
        )
        parts.append(
            f'<span class="timeline-peak-marker" style="left:{_timeline_percent(hour, forecast_start, forecast_end):.4f}%" '
            f'data-peak-index="{index}" data-tooltip="{escape(tooltip, quote=True)}" tabindex="0" '
            f'aria-label="{escape(peak_label, quote=True)} {escape(tooltip, quote=True)}">{index}</span>'
        )
    return ''.join(parts)


def _timeline_percent(hour: int, forecast_start: int, forecast_end: int) -> float:
    if forecast_end == forecast_start:
        return 0.0
    return max(0.0, min(100.0, (hour - forecast_start) * 100.0 / (forecast_end - forecast_start)))


def _timeline_axis_hours(forecast_start: int, forecast_end: int, interval: int) -> list[int]:
    span = forecast_end - forecast_start
    if span <= 0:
        return [forecast_start]
    target_step = span / 5.0
    step_units = max(1, round(target_step / interval))
    label_step = step_units * interval
    labels = list(range(forecast_start, forecast_end + 1, label_step))
    if labels[-1] != forecast_end:
        labels.append(forecast_end)
    return sorted(set(labels))


def _render_timeline(
    candidate: dict[str, Any],
    assets_by_id: dict[str, dict[str, Any]],
    init: str,
    forecast_timeline: tuple[int, int, int] = DEFAULT_FORECAST_TIMELINE,
    peak_precip_details: Iterable[dict[str, Any]] = (),
) -> str:
    forecast_start, forecast_end, interval = forecast_timeline
    hours, evidence, peaks = _timeline_tick_hours(candidate, assets_by_id, forecast_timeline)
    window = candidate["effective_time_window"]
    watch_start = int(window["forecast_hour_start"])
    watch_end = int(window["forecast_hour_end"])
    if watch_start < forecast_start or watch_end > forecast_end:
        raise Pass2PipelineError(
            "FORECAST_TIMELINE_RANGE_INVALID",
            f"감시후보 {candidate['candidate_id']}의 감시구간이 설정된 예측기간을 벗어났습니다.",
        )

    minor_ticks = ''.join(
        f'<span class="timeline-minor-tick" style="left:{_timeline_percent(hour, forecast_start, forecast_end):.4f}%" aria-hidden="true"></span>'
        for hour in hours
    )
    axis_parts: list[str] = []
    for hour in _timeline_axis_hours(forecast_start, forecast_end, interval):
        edge_class = " axis-start" if hour == forecast_start else (" axis-end" if hour == forecast_end else "")
        time_label = _format_timeline_clock(init, hour)
        full_label = escape(format_valid_time_kst(init, hour), quote=True)
        axis_parts.append(
            f'<span class="timeline-axis-label{edge_class}" style="left:{_timeline_percent(hour, forecast_start, forecast_end):.4f}%" '
            f'title="{full_label}" aria-label="{full_label}">'
            f'<span class="timeline-axis-utc">{escape(time_label)}</span>'
            '</span>'
        )
    axis_labels = ''.join(axis_parts)
    evidence_markers = ''.join(
        f'<span class="timeline-evidence-marker" style="left:{_timeline_percent(hour, forecast_start, forecast_end):.4f}%" '
        f'title="최종 판단 근거 · {escape(format_valid_time_kst(init, hour), quote=True)}" '
        f'aria-label="최종 판단 근거 · {escape(format_valid_time_kst(init, hour), quote=True)}"></span>'
        for hour in sorted(evidence)
    )
    peak_label = _peak_display_label(candidate)
    resolved_peak_details = [
        detail
        for detail in _resolved_peak_precip_details(peak_precip_details)
        if int(detail["forecast_hour"]) in peaks
    ]
    peak_markers = _render_peak_precip_markers(
        peaks, resolved_peak_details, init, forecast_start, forecast_end, peak_label
    )
    peak_summary = _render_peak_precip_summary(resolved_peak_details, init)
    peak_legend = (
        f'<span><i class="legend-peak" aria-hidden="true"></i>{escape(peak_label)}</span>'
        if resolved_peak_details
        else ''
    )
    watch_left = _timeline_percent(watch_start, forecast_start, forecast_end)
    watch_right = _timeline_percent(watch_end, forecast_start, forecast_end)
    watch_width = max(0.0, watch_right - watch_left)
    return (
        '<section class="timeline-section" aria-label="전체 예측기간 기준 감시 시간축">'
        '<div class="section-heading"><div><h3>감시 시간축</h3></div>'
        f'{peak_summary}</div>'
        '<div class="timeline-legend" aria-label="시간축 범례">'
        '<span><i class="legend-line full" aria-hidden="true"></i>전체 예측기간</span>'
        '<span><i class="legend-line watch" aria-hidden="true"></i>감시구간</span>'
        f'{peak_legend}'
        '<span><i class="legend-evidence" aria-hidden="true"></i>최종 판단 근거</span>'
        '</div>'
        '<div class="timeline-scale" role="img" '
        f'aria-label="전체 예측기간 {escape(format_valid_time_kst(init, forecast_start), quote=True)}부터 {escape(format_valid_time_kst(init, forecast_end), quote=True)}, 감시구간 {escape(format_valid_time_kst(init, watch_start), quote=True)}부터 {escape(format_valid_time_kst(init, watch_end), quote=True)}">'
        '<div class="timeline-base" aria-hidden="true"></div>'
        f'<div class="timeline-watch-band" style="left:{watch_left:.4f}%;width:{watch_width:.4f}%" aria-hidden="true"></div>'
        f'{minor_ticks}{peak_markers}{evidence_markers}{axis_labels}'
        '</div>'
        f'<div class="timeline-range-summary"><span>전체 {_format_timeline_watch_summary(init, forecast_start)} ~ {_format_timeline_watch_summary(init, forecast_end)}</span>'
        f'<strong>감시 {_format_timeline_watch_summary(init, watch_start)} ~ {_format_timeline_watch_summary(init, watch_end)}</strong></div>'
        '<div class="timeline-notes">'
        '<p class="timeline-note">전체 예측기간에서 감시후보 구간의 위치를 표시합니다. '
        '6시간 최대강수 권역은 정량 최댓값이 확인된 권역이며, 후보명의 대표 강수 객체나 환경 결합 위치와 다를 수 있습니다.</p>'
        '</div>'
        '</section>'
    )



def _render_ensemble_consensus_badge(consensus: Any) -> str:
    if not isinstance(consensus, dict) or consensus.get("mode") != "llm_ensemble":
        return ""
    agreement = str(consensus.get("assessment_agreement") or "")
    localization_agreement = str(consensus.get("localization_agreement") or "")
    dimensions = {str(value) for value in consensus.get("disagreement_dimensions", []) if isinstance(value, str)}
    if agreement == "unanimous":
        label = "판정 합의 3/3"
        if "localization" in dimensions:
            if localization_agreement == "majority":
                label += " · 위치 합의 2/3"
            elif localization_agreement == "unresolved":
                label += " · 위치 합의 미성립"
            else:
                label += " · 위치 이견"
        cls = "unanimous"
    elif agreement == "majority":
        label = "판정 합의 2/3 · 이견"
        if "localization" in dimensions:
            if localization_agreement == "majority":
                label += " · 위치 합의 2/3"
            elif localization_agreement == "unresolved":
                label += " · 위치 합의 미성립"
        cls = "majority"
    else:
        label = "판정 합의 미성립"
        cls = "unresolved"
    return f'<span class="ensemble-consensus-badge {cls}" title="동일 근거에 대한 Step07 독립 판독 3회의 구조화 판정 일치도입니다. 발생확률이나 위험도 등급이 아닙니다.">{escape(label)}</span>'


def _minority_finding_texts(rows: Any) -> list[str]:
    values: list[str] = []
    if not isinstance(rows, list):
        return values
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("statement"), str) and row["statement"].strip():
            values.append(row["statement"].strip())
    return values


def _question_match_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"F\d{3}", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\d+(?:\.\d+)?", "", text)
    return re.sub(r"[^가-힣A-Za-z]+", "", text).lower()


def _derive_minority_question_links(
    group: dict[str, Any],
    synthesis: dict[str, Any],
    minority_report: dict[str, Any],
) -> list[dict[str, Any]]:
    """Link Step08 forecaster questions to Step07 minority-report Checks conservatively.

    Adopted question evidence already carries its Check IDs directly.  A Check
    with no Step07 majority consensus is intentionally absent from those IDs,
    so a zero-count question would otherwise look like it had never been
    evaluated.  This helper preserves that traceability without making the
    minority opinion part of the adopted evidence.
    """
    candidate_id = str(group.get("candidate_id") or synthesis.get("candidate_id") or "")
    entries = [
        entry for entry in minority_report.get("entries", [])
        if isinstance(entry, dict) and str(entry.get("candidate_id") or "") == candidate_id
    ] if isinstance(minority_report, dict) else []
    if not candidate_id or not entries:
        return []

    entry_by_check = {str(entry.get("check_id") or ""): entry for entry in entries if str(entry.get("check_id") or "")}
    variable_checks = (group.get("candidate_context") or {}).get("analysis_plan", {}).get("variable_checks", [])
    expected_by_check = {
        str(item.get("check_id")): str(item.get("expected_signal") or "")
        for item in variable_checks
        if isinstance(item, dict) and str(item.get("check_id") or "")
    } if isinstance(variable_checks, list) else {}
    spec_by_check = {
        str(item.get("check_id")): item
        for item in variable_checks
        if isinstance(item, dict) and str(item.get("check_id") or "")
    } if isinstance(variable_checks, list) else {}
    request_ids_by_check = {
        str(item.get("check_id")): {
            str(value) for value in item.get("request_ids", [])
            if isinstance(value, str) and value
        }
        for item in group.get("check_evidence", [])
        if isinstance(item, dict) and str(item.get("check_id") or "")
    } if isinstance(group.get("check_evidence"), list) else {}
    questions = synthesis.get("forecaster_question_answers")
    if not isinstance(questions, list):
        return []

    links_by_question: dict[str, dict[str, Any]] = {}
    unresolved_checks = {
        check_id for check_id, entry in entry_by_check.items()
        if str(entry.get("assessment_agreement") or "") == "unresolved"
    }
    used_unresolved: set[str] = set()

    # Direct link: an adopted Check can still have a minority dissent when the
    # ensemble reached a 2/3 majority.
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = str(question.get("forecaster_question_id") or "")
        evidence_ids = [str(value) for value in question.get("evidence_check_ids", []) if isinstance(value, str)]
        direct = [check_id for check_id in evidence_ids if check_id in entry_by_check]
        if direct:
            links_by_question[question_id] = {
                "forecaster_question_id": question_id,
                "check_ids": direct,
                "link_type": "unresolved" if any(check_id in unresolved_checks for check_id in direct) else "dissent",
            }
            used_unresolved.update(check_id for check_id in direct if check_id in unresolved_checks)

    zero_questions = [
        question for question in questions
        if isinstance(question, dict)
        and not question.get("evidence_check_ids")
        and str(question.get("forecaster_question_id") or "") not in links_by_question
    ]
    remaining_unresolved = sorted(unresolved_checks - used_unresolved)

    # A question may already have adopted evidence while another Check from
    # the same evidence request failed to reach ensemble consensus.  Preserve
    # that omitted dissent when the two Checks are genuinely sibling
    # diagnostics: same Step05 request, variable role, diagnostic, and scope.
    # This is stronger and less ambiguous than natural-language matching.
    assigned_siblings: set[str] = set()
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = str(question.get("forecaster_question_id") or "")
        if not question_id or question_id in links_by_question:
            continue
        evidence_ids = [
            str(value) for value in question.get("evidence_check_ids", [])
            if isinstance(value, str) and value
        ]
        if not evidence_ids:
            continue
        sibling_matches: list[str] = []
        for unresolved_id in remaining_unresolved:
            if unresolved_id in assigned_siblings:
                continue
            unresolved_spec = spec_by_check.get(unresolved_id)
            unresolved_requests = request_ids_by_check.get(unresolved_id, set())
            if not isinstance(unresolved_spec, dict) or not unresolved_requests:
                continue
            for adopted_id in evidence_ids:
                adopted_spec = spec_by_check.get(adopted_id)
                adopted_requests = request_ids_by_check.get(adopted_id, set())
                if not isinstance(adopted_spec, dict) or not adopted_requests:
                    continue
                if not (unresolved_requests & adopted_requests):
                    continue
                if any(
                    unresolved_spec.get(key) != adopted_spec.get(key)
                    for key in ("diagnostic_id", "variable_role", "check_scope")
                ):
                    continue
                sibling_matches.append(unresolved_id)
                break
        sibling_matches = list(dict.fromkeys(sibling_matches))
        if sibling_matches:
            links_by_question[question_id] = {
                "forecaster_question_id": question_id,
                "check_ids": sibling_matches,
                "link_type": "unresolved",
            }
            assigned_siblings.update(sibling_matches)

    if assigned_siblings:
        remaining_unresolved = [
            check_id for check_id in remaining_unresolved
            if check_id not in assigned_siblings
        ]

    # Unambiguous one-to-one case: one zero-evidence question and one unresolved
    # Check.  This is the common path when Step07 intentionally excluded the
    # Check from Step08 synthesis because no majority was formed.
    if len(zero_questions) == 1 and len(remaining_unresolved) == 1:
        question_id = str(zero_questions[0].get("forecaster_question_id") or "")
        links_by_question[question_id] = {
            "forecaster_question_id": question_id,
            "check_ids": [remaining_unresolved[0]],
            "link_type": "unresolved",
        }
    elif zero_questions and remaining_unresolved:
        # For multiple unresolved items, link only a strong, clearly separated
        # textual match between the immutable Step04 question and Check intent.
        assigned: set[str] = set()
        for question in zero_questions:
            question_id = str(question.get("forecaster_question_id") or "")
            question_text = _question_match_text(question.get("question"))
            scores: list[tuple[float, str]] = []
            for check_id in remaining_unresolved:
                if check_id in assigned:
                    continue
                expected_text = _question_match_text(expected_by_check.get(check_id))
                if not question_text or not expected_text:
                    continue
                scores.append((SequenceMatcher(None, question_text, expected_text).ratio(), check_id))
            scores.sort(reverse=True)
            if not scores:
                continue
            best_score, best_check = scores[0]
            second_score = scores[1][0] if len(scores) > 1 else 0.0
            if best_score >= 0.45 and best_score - second_score >= 0.12:
                links_by_question[question_id] = {
                    "forecaster_question_id": question_id,
                    "check_ids": [best_check],
                    "link_type": "unresolved",
                }
                assigned.add(best_check)

    return [
        links_by_question[str(question.get("forecaster_question_id"))]
        for question in questions
        if isinstance(question, dict) and str(question.get("forecaster_question_id")) in links_by_question
    ]


def _apply_minority_report_assets(
    report: dict[str, Any] | None,
    minority_report_assets: Any,
) -> dict[str, Any] | None:
    if not isinstance(report, dict):
        return report
    enriched = copy.deepcopy(report)
    asset_index: dict[tuple[str, str, int], list[str]] = {}
    if isinstance(minority_report_assets, list):
        for row in minority_report_assets:
            if not isinstance(row, dict):
                continue
            candidate_id = str(row.get("candidate_id") or "")
            check_id = str(row.get("check_id") or "")
            for member_row in row.get("member_assets", []):
                if not isinstance(member_row, dict):
                    continue
                member_index = member_row.get("member_index")
                if not isinstance(member_index, int) or isinstance(member_index, bool):
                    continue
                asset_ids = [str(value) for value in member_row.get("asset_ids", []) if isinstance(value, str) and value]
                if asset_ids:
                    asset_index[(candidate_id, check_id, member_index)] = asset_ids
    for entry in enriched.get("entries", []):
        if not isinstance(entry, dict):
            continue
        candidate_id = str(entry.get("candidate_id") or "")
        check_id = str(entry.get("check_id") or "")
        for member in entry.get("member_reports", []):
            if not isinstance(member, dict):
                continue
            member_index = member.get("member_index")
            if not isinstance(member_index, int) or isinstance(member_index, bool):
                continue
            related = asset_index.get((candidate_id, check_id, member_index))
            if related:
                member["related_asset_ids"] = related
    return enriched


def _render_step07_minority_report(report: dict[str, Any] | None, product: dict[str, Any], assets_by_id: dict[str, dict[str, Any]], init: str) -> str:
    if not isinstance(report, dict):
        return ""
    entries = [item for item in report.get("entries", []) if isinstance(item, dict)]
    if not entries:
        return ""
    candidate_names = {
        str(item.get("candidate_id")): str(item.get("candidate_name") or item.get("candidate_id"))
        for item in [*product.get("surfaced_candidates", []), *product.get("non_surfaced_candidates", [])]
        if isinstance(item, dict)
    }
    cards: list[str] = []
    for entry in entries:
        candidate_id = str(entry.get("candidate_id") or "")
        check_id = str(entry.get("check_id") or "")
        vote_counts = entry.get("vote_counts") if isinstance(entry.get("vote_counts"), list) else []
        vote_text = " / ".join(
            f"{ASSESSMENT_LABELS.get(str(row.get('assessment')), str(row.get('assessment')))} {row.get('count')}"
            for row in vote_counts if isinstance(row, dict)
        )
        agreement = str(entry.get("assessment_agreement") or "")
        localization_agreement = str(entry.get("localization_agreement") or "")
        consensus = ASSESSMENT_LABELS.get(str(entry.get("consensus_assessment")), str(entry.get("consensus_assessment")))
        dimensions = {str(value) for value in entry.get("disagreement_dimensions", []) if isinstance(value, str)}
        if agreement == "majority":
            agreement_text = "판정 2/3 다수 합의"
            if "localization" in dimensions:
                if localization_agreement == "majority":
                    agreement_text += " · 위치 2/3 합의"
                elif localization_agreement == "unresolved":
                    agreement_text += " · 위치 합의 미성립"
        elif agreement == "unanimous":
            agreement_text = "판정 3/3"
            if "localization" in dimensions:
                if localization_agreement == "majority":
                    agreement_text += " · 위치 2/3 합의"
                elif localization_agreement == "unresolved":
                    agreement_text += " · 위치 합의 미성립"
        else:
            agreement_text = "판정 합의 미성립"
        member_html: list[str] = []
        for member in entry.get("member_reports", []):
            if not isinstance(member, dict):
                continue
            member_index = member.get("member_index")
            assessment = ASSESSMENT_LABELS.get(str(member.get("assessment")), str(member.get("assessment")))
            findings = _minority_finding_texts(member.get("findings"))
            contradictions = _minority_finding_texts(member.get("contradictory_findings"))
            uncertainties = [str(value) for value in member.get("uncertainties", []) if isinstance(value, str) and value.strip()]
            related_asset_ids = [asset_id for asset_id in member.get("related_asset_ids", []) if asset_id in assets_by_id]
            image_html = ""
            if related_asset_ids:
                image_html = '<section class="minority-member-images"><h4>관련 이미지</h4><div class="images">' + ''.join(
                    _render_asset(asset_id, assets_by_id, candidate_names.get(candidate_id, candidate_id), init)
                    for asset_id in related_asset_ids
                ) + '</div></section>'
            member_html.append(
                '<div class="minority-member">'
                f'<div class="minority-member-head"><strong>Run {escape(str(member_index))}</strong><span>{escape(assessment)}</span></div>'
                f'<div class="minority-member-grid"><section><h4>확인된 내용</h4>{_render_list(findings, init)}</section>'
                f'<section><h4>상반된 근거</h4>{_render_list(contradictions, init)}</section></div>'
                f'{image_html}'
                f'<section><h4>판독·근거 불확실성</h4>{_render_list(uncertainties, init)}</section>'
                '</div>'
            )
        dimension_text = []
        if "assessment" in dimensions:
            dimension_text.append("판정")
        if "localization" in dimensions:
            dimension_text.append("위치")
        cards.append(
            f'<article class="minority-card" id="{escape(f"minority-{candidate_id}-{check_id}", quote=True)}">'
            f'<div class="minority-card-head"><div><span class="section-kicker">{escape(candidate_id)} · {escape(check_id)}</span>'
            f'<h3>{escape(candidate_names.get(candidate_id, candidate_id))}</h3></div>'
            f'<span class="minority-agreement">{escape(agreement_text)}</span></div>'
            f'<div class="minority-summary"><span><b>최종 합의</b> {escape(consensus)}</span><span><b>투표</b> {escape(vote_text)}</span>'
            f'<span><b>이견 축</b> {escape(", ".join(dimension_text) or "-")}</span></div>'
            f'{"".join(member_html)}'
            '</article>'
        )
    return (
        '<details class="minority-report-panel">'
        f'<summary><span>LLM Wiki · 마이너리티 리포트</span><b>모델 판독 이견 {len(entries)}건</b></summary>'
        '<div class="minority-report-body"><p class="minority-report-note">동일한 입력 근거를 독립적으로 판독한 결과 중 최종 합의에 채택되지 않은 의견을 보존합니다. 이는 기상학적 ‘상반된 근거’와 다른 개념이며, 소수 판독 자체가 최종 판정을 직접 변경하지는 않지만, 판정 합의 미성립 Check는 종합판단 근거에서 제외되므로 최종 상태에 영향을 줄 수 있습니다.</p>'
        f'{"".join(cards)}</div></details>'
    )


def _render_majority_aggregation(aggregation: dict[str, Any] | None, init: str) -> str:
    if not isinstance(aggregation, dict):
        return ""
    total = aggregation.get("total_regions")
    not_count = aggregation.get("not_strengthening_count")
    strengthening_count = aggregation.get("strengthening_count")
    hours = aggregation.get("forecast_hours")
    regions = aggregation.get("regions")
    if (
        not isinstance(total, int) or not isinstance(not_count, int) or not isinstance(strengthening_count, int)
        or not isinstance(hours, list) or len(hours) != 2
        or not isinstance(regions, list)
    ):
        return ""
    not_regions = [str(value) for value in aggregation.get("not_strengthening_regions", []) if isinstance(value, str)]
    strengthening_regions = [str(value) for value in aggregation.get("strengthening_regions", []) if isinstance(value, str)]
    feature_id = str(aggregation.get("feature_id") or "")
    first_hour, second_hour = int(hours[0]), int(hours[1])
    rows: list[str] = []
    for row in regions:
        if not isinstance(row, dict):
            continue
        values = row.get("values")
        if not isinstance(values, list) or len(values) != 2:
            continue
        value_map = {
            int(item["forecast_hour"]): float(item["resolved_value"])
            for item in values
            if isinstance(item, dict)
            and isinstance(item.get("forecast_hour"), int) and not isinstance(item.get("forecast_hour"), bool)
            and isinstance(item.get("resolved_value"), (int, float)) and not isinstance(item.get("resolved_value"), bool)
        }
        if first_hour not in value_map or second_hour not in value_map:
            continue
        strengthening = bool(row.get("strengthening"))
        transition = PHYSICAL_TRANSITION_LABELS.get(str(row.get("physical_transition") or ""), str(row.get("physical_transition") or "-"))
        rows.append(
            '<tr>'
            f'<td>{escape(str(row.get("region_name") or row.get("region_id") or "-"))}</td>'
            f'<td class="num">{escape(format_quantitative_display_value(feature_id, value_map[first_hour]))}</td>'
            f'<td class="num">{escape(format_quantitative_display_value(feature_id, value_map[second_hour]))}</td>'
            f'<td><span class="majority-state {"strengthening" if strengthening else "not-strengthening"}">{"수렴 강화" if strengthening else "수렴 강화 아님"}</span></td>'
            f'<td>{escape(transition)}</td>'
            '</tr>'
        )
    try:
        digest = majority_digest(aggregation)
    except MajorityNarrativeError as exc:
        raise Pass2PipelineError("QUANTITATIVE_NARRATIVE_INVALID", str(exc)) from exc
    return (
        f'<section class="majority-aggregation" data-majority-sha256="{digest}" aria-label="운영권역 집계">'
        '<div class="majority-aggregation-head"><div><span class="mini-label">정량 재계산</span><h4>운영권역 집계</h4></div>'
        '<span class="majority-contract-badge">Python 검증값</span></div>'
        '<div class="majority-metrics">'
        f'<div><span>공통 운영권역</span><strong>{total}</strong></div>'
        f'<div class="not-strengthening"><span>수렴 강화 아님</span><strong>{not_count}</strong></div>'
        f'<div class="strengthening"><span>수렴 강화</span><strong>{strengthening_count}</strong></div>'
        '</div>'
        '<div class="majority-region-groups">'
        f'<div><strong>수렴 강화 아님 · {not_count}개</strong><p>{escape(", ".join(not_regions) or "없음")}</p></div>'
        f'<div><strong>수렴 강화 · {strengthening_count}개</strong><p>{escape(", ".join(strengthening_regions) or "없음")}</p></div>'
        '</div>'
        '<div class="majority-table-wrap"><table class="majority-table"><thead><tr>'
        '<th>운영권역</th>'
        f'<th>{escape(format_forecast_hour(init, first_hour))}</th>'
        f'<th>{escape(format_forecast_hour(init, second_hour))}</th>'
        '<th>판정</th><th>물리 상태 변화</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        '<p class="majority-note">음수는 발산, 양수는 수렴입니다. 음수가 덜 음수가 되는 변화는 발산 약화이며, 수렴 강화로 분류하지 않습니다.</p>'
        '</section>'
    )


def _render_evidence_detail_body(
    block: dict[str, Any],
    candidate: dict[str, Any],
    assets_by_id: dict[str, dict[str, Any]],
    init: str,
) -> str:
    asset_ids = block["asset_ids"]
    images = ''.join(_render_asset(asset_id, assets_by_id, candidate["candidate_name"], init) for asset_id in asset_ids)
    if len(asset_ids) == 1:
        image_gallery = f'<div class="images single">{images}</div>'
    elif len(asset_ids) >= 2:
        image_gallery = f'<div class="images stacked">{images}</div>'
    else:
        image_gallery = ''

    meanings = block.get("decision_meanings") if isinstance(block.get("decision_meanings"), list) else []
    meaning_html = ''
    if meanings:
        cards: list[str] = []
        for meaning in meanings:
            if not isinstance(meaning, dict):
                continue
            effect = str(meaning.get("effect") or "context")
            cards.append(
                f'<div class="decision-meaning-card {escape(effect, quote=True)}">'
                f'<div><strong>{escape(str(meaning.get("axis_label") or "판단 의미"))}</strong>'
                f'<span>{escape(str(meaning.get("effect_label") or ""))}</span></div>'
                f'<p>{_render_text(str(meaning.get("reason") or ""), init)}</p></div>'
            )
        if cards:
            meaning_html = '<section class="decision-meanings"><h4>기상학적·운영상 의미</h4>' + ''.join(cards) + '</section>'

    decision_relevance = str(block.get("decision_relevance") or "").strip()
    relevance_html = (
        '<section class="decision-relevance"><span>이 확인항목을 왜 보는가</span>'
        f'<p>{_render_text(decision_relevance, init)}</p></section>'
        if decision_relevance else ''
    )
    majority_html = _render_majority_aggregation(block.get("majority_aggregation"), init)
    quantitative_table = '' if majority_html else _render_quantitative(
        block["quantitative_evidence"], block["asset_ids"], assets_by_id, init,
        candidate_id=candidate["candidate_id"], check_id=block["check_id"]
    )
    return (
        '<div class="evidence-body">'
        f'{relevance_html}{meaning_html}'
        '<div class="evidence-columns">'
        f'<section><h4>확인된 사실</h4>{_render_list(block["findings"], init)}</section>'
        f'<section class="counter-evidence"><h4>상반된 근거</h4>{_render_list(block["contradictory_findings"], init)}</section>'
        '</div>'
        f'<section class="check-uncertainty"><h4>판독·근거 불확실성</h4>{_render_list(block["uncertainties"], init)}</section>'
        f'{majority_html}'
        f'<section class="quantitative-detail"><h4>정량 분석값</h4>'
        f'{_render_quantitative_series(block.get("quantitative_time_series", []), block["asset_ids"], assets_by_id, init, candidate_id=candidate["candidate_id"], check_id=block["check_id"])}'
        f'{quantitative_table}</section>'
        f'{image_gallery}'
        '</div>'
    )


def _render_evidence_matrix(
    candidate: dict[str, Any],
    assets_by_id: dict[str, dict[str, Any]],
    init: str,
) -> str:
    counts = _assessment_counts(candidate)
    master_items: list[str] = []
    detail_panels: list[str] = []
    attention_count = 0
    role_filter_order = (
        ("direct_precipitation", "모델 강수"),
        ("moisture_supply", "수분"),
        ("low_level_forcing", "수렴"),
        ("mid_upper_forcing", "상승"),
        ("organization_persistence", "바람"),
        ("spatiotemporal_alignment", "일치"),
    )
    available_roles = {
        role for block in candidate["evidence_blocks"] for role in block.get("roles", [])
    }
    first_check_id = str(candidate["evidence_blocks"][0]["check_id"]) if candidate["evidence_blocks"] else ""
    for block_index, block in enumerate(candidate["evidence_blocks"], start=1):
        expected = _render_text(block["expected_signal"], init)
        role_tokens = " ".join(str(role) for role in block.get("roles", []))
        assessment = str(block.get("assessment", "insufficient_evidence"))
        needs_attention = (
            assessment != "observed"
            or bool(block.get("contradictory_findings"))
            or bool(block.get("uncertainties"))
            or any(
                isinstance(item, dict) and item.get("effect") in {"limiting", "mixed"}
                for item in block.get("decision_meanings", [])
            )
        )
        if needs_attention:
            attention_count += 1
        target_id = _evidence_target_id(candidate["candidate_id"], block["check_id"])
        active = str(block["check_id"]) == first_check_id
        master_items.append(
            f'<button class="evidence-master-item{" active" if active else ""}" type="button" '
            f'data-evidence-check="{escape(str(block["check_id"]), quote=True)}" '
            f'data-evidence-target="{escape(target_id, quote=True)}" data-evidence-roles="{escape(role_tokens, quote=True)}" '
            f'data-attention="{"true" if needs_attention else "false"}" aria-selected="{"true" if active else "false"}">'
            '<div class="evidence-master-head">'
            f'<span class="check-id">확인 항목 {block_index}</span>{_assessment_badge(block["assessment"])}'
            f'{_render_ensemble_consensus_badge(block.get("ensemble_consensus"))}</div>'
            f'<div class="role-chips">{_render_role_chips(block["roles"])}</div>'
            f'<p>{expected}</p></button>'
        )
        detail_panels.append(
            f'<article class="evidence-detail-panel{" active" if active else ""}" id="{escape(target_id, quote=True)}" '
            f'data-evidence-check="{escape(str(block["check_id"]), quote=True)}" data-evidence-index="{block_index}" '
            f'{"" if active else "hidden"}>'
            '<div class="evidence-detail-head">'
            '<div><div class="evidence-detail-meta">'
            f'<span class="check-id">확인 항목 {block_index}</span>{_assessment_badge(block["assessment"])}'
            f'{_render_ensemble_consensus_badge(block.get("ensemble_consensus"))}</div>'
            f'<h4>{expected}</h4></div>'
            '<div class="evidence-detail-nav">'
            '<button type="button" data-evidence-nav="prev" aria-label="이전 확인항목">← 이전</button>'
            '<button type="button" data-evidence-nav="next" aria-label="다음 확인항목">다음 →</button>'
            '</div></div>'
            f'{_render_evidence_detail_body(block, candidate, assets_by_id, init)}'
            '</article>'
        )
    status_items = []
    for key in ("observed", "partially_observed", "uncertain", "opposite_observed", "not_observed", "insufficient_evidence"):
        if counts.get(key):
            status_items.append(f'<span>{escape(ASSESSMENT_LABELS[key])} <b>{counts[key]}</b></span>')
    role_filters = ['<button class="evidence-role-filter active" type="button" data-evidence-role-filter="all">전체</button>']
    for role, label in role_filter_order:
        if role in available_roles:
            role_filters.append(
                f'<button class="evidence-role-filter" type="button" data-evidence-role-filter="{escape(role, quote=True)}">{escape(label)}</button>'
            )
    return (
        '<section class="matrix-section" aria-label="판단 근거 및 상세 분석">'
        '<div class="section-heading evidence-section-heading"><div><span class="section-kicker">판단 근거</span><h3>물리과정별 판독</h3></div>'
        f'<div class="evidence-status-summary">{"".join(status_items)}</div></div>'
        '<div class="evidence-filter-toolbar">'
        f'<div class="evidence-role-filters">{"".join(role_filters)}</div>'
        f'<button class="attention-filter-toggle" type="button" aria-pressed="false" data-attention-toggle="{escape(candidate["candidate_id"], quote=True)}">'
        f'주의·제한만 보기 <b>{attention_count}</b></button></div>'
        '<div class="evidence-master-detail">'
        f'<nav class="evidence-master-list" aria-label="확인항목 목록">{"".join(master_items)}</nav>'
        f'<div class="evidence-detail-pane">{"".join(detail_panels)}</div>'
        '</div></section>'
    )


def _render_evidence_block(
    block: dict[str, Any],
    candidate: dict[str, Any],
    assets_by_id: dict[str, dict[str, Any]],
    init: str,
) -> str:
    return (
        '<details class="evidence">'
        '<summary>'
        '<span class="evidence-check">확인 항목</span>'
        f'<span class="evidence-roles">{_render_role_chips(block["roles"])}</span>'
        f'{_assessment_badge(block["assessment"])}'
        '</summary>'
        f'{_render_evidence_detail_body(block, candidate, assets_by_id, init)}'
        '</details>'
    )


def _render_questions(candidate: dict[str, Any], init: str) -> str:
    if not candidate["forecaster_question_answers"]:
        return '<p class="empty-inline">없음</p>'
    link_rows = candidate.get("minority_question_links") if isinstance(candidate.get("minority_question_links"), list) else []
    links_by_question = {
        str(row.get("forecaster_question_id")): row
        for row in link_rows
        if isinstance(row, dict) and str(row.get("forecaster_question_id") or "")
    }
    questions: list[str] = []
    candidate_id = str(candidate.get("candidate_id") or "")
    for question_index, question in enumerate(candidate["forecaster_question_answers"], start=1):
        question_id = str(question.get("forecaster_question_id") or "")
        evidence_count = len(question["evidence_check_ids"])
        minority_link = links_by_question.get(question_id)
        trace_label = "관련 판단 근거" if evidence_count else "최종 채택 근거"
        presentation_index = {
            str(block["check_id"]): index
            for index, block in enumerate(candidate["evidence_blocks"], start=1)
        }
        check_buttons = "".join(
            f'<button type="button" class="alignment-check-link question-check-link" '
            f'data-evidence-target="{escape(_evidence_target_id(candidate_id, check_id), quote=True)}">'
            f'확인 항목 {presentation_index[check_id]}</button>'
            for check_id in question["evidence_check_ids"]
            if check_id in presentation_index
        )
        trace_extra = ""
        if isinstance(minority_link, dict):
            check_ids = [str(value) for value in minority_link.get("check_ids", []) if isinstance(value, str) and value]
            link_type = str(minority_link.get("link_type") or "")
            if check_ids:
                minority_label = "판정 합의 미성립" if link_type == "unresolved" else "소수 판독 이견"
                buttons = ''.join(
                    f'<button type="button" class="minority-jump" data-minority-target="{escape("minority-" + candidate_id + "-" + check_id, quote=True)}">마이너리티 리포트에서 확인</button>'
                    for check_id in check_ids
                )
                trace_extra = f'<span class="question-minority-count">{escape(minority_label)} {len(check_ids)}건</span>{buttons}'
        questions.append(
            '<details class="forecaster-question">'
            '<summary>'
            f'<span class="question-id">확인사항 {question_index}</span>'
            f'<span>{_render_text(question["question"], init)}</span>'
            '</summary>'
            '<div class="question-body">'
            f'<p class="question-answer">{_render_text(question["answer"], init)}</p>'
            f'<p class="question-trace"><strong>{trace_label}</strong> {evidence_count}건 {check_buttons}{trace_extra}</p>'
            f'<h4>판독·근거 불확실성</h4>{_render_list(question["uncertainties"], init)}'
            '</div></details>'
        )
    return ''.join(questions)


def _format_six_hour_precip_interval(init: str, forecast_hour: int) -> str:
    end_hour = int(forecast_hour)
    start_hour = max(0, end_hour - 6)
    return f"{format_valid_time_kst(init, start_hour)} ~ {format_valid_time_kst(init, end_hour)}"


def _render_peak_hours(candidate: dict[str, Any], init: str) -> str:
    hours = candidate["peak_forecast_hours"]
    if not hours:
        return "없음"
    if str(candidate.get("direct_precipitation_status", "")) == "confirmed":
        return " · ".join(_format_six_hour_precip_interval(init, int(hour)) for hour in hours)
    return " · ".join(format_valid_time_kst(init, int(hour)) for hour in hours)


def _peak_display_label(candidate: dict[str, Any]) -> str:
    if str(candidate.get("direct_precipitation_status", "")) == "confirmed":
        return "최대 6시간 강수 구간"
    if str(candidate.get("environment_support_status", "")) == "confirmed":
        return "강수 발달 환경 대표 시각"
    return "감시 핵심 시각"


def _candidate_status_label(candidate: dict[str, Any]) -> str:
    """Return the compact user-facing label without exposing internal enum names."""
    direct = str(candidate.get("direct_precipitation_status") or "")
    environment = str(candidate.get("environment_support_status") or "")
    if direct == "confirmed" and environment == "confirmed":
        return "모델 강수 예측 확인 · 강수 발달 환경 동반"
    if direct == "confirmed":
        return "모델 강수 예측 확인"
    if environment == "confirmed":
        return "강수 발달 환경 감시"
    return SIGNAL_CLASS_LABELS[str(candidate["signal_class"])]


def _alignment_check_ids(candidate: dict[str, Any]) -> list[str]:
    """Return publication checks explicitly carrying the Step08 alignment-evidence role."""
    result: list[str] = []
    for block in candidate["evidence_blocks"]:
        if "spatiotemporal_alignment" in block.get("roles", []):
            check_id = str(block["check_id"])
            if check_id not in result:
                result.append(check_id)
    return result


def _evidence_target_id(candidate_id: str, check_id: str) -> str:
    return f"evidence-{candidate_id}-{check_id}"


def _render_alignment_provenance(candidate: dict[str, Any]) -> str:
    check_ids = _alignment_check_ids(candidate)
    if check_ids:
        presentation_index = {
            str(block["check_id"]): index
            for index, block in enumerate(candidate["evidence_blocks"], start=1)
        }
        check_text = " ".join(
            f'<button class="alignment-check-link" type="button" data-evidence-target="{escape(_evidence_target_id(candidate["candidate_id"], check_id), quote=True)}">확인 항목 {presentation_index.get(check_id, index)}</button>'
            for index, check_id in enumerate(check_ids, start=1)
        )
    else:
        check_text = "연결된 확인 항목 없음"
    return (
        '<details class="alignment-provenance">'
        '<summary>판단 근거</summary>'
        '<div class="alignment-provenance-body">'
        '<span><b>종합판단</b> 상세분석 결과</span>'
        '<span><b>근거 판독</b> 상세 예측자료 판독 결과</span>'
        f'<span><b>관련 확인 항목</b> {check_text}</span>'
        '</div>'
        '</details>'
    )


def _mechanism_effect_by_check(candidate: dict[str, Any]) -> dict[str, str]:
    """Return Step08 physical-mechanism effects keyed by Check ID."""
    result: dict[str, str] = {}
    for item in candidate.get("mechanism_evidence", []):
        if not isinstance(item, dict):
            continue
        check_id = str(item.get("check_id") or "")
        effect = str(item.get("effect") or "")
        if check_id and effect in DECISION_EVIDENCE_EFFECTS:
            result[check_id] = effect
    return result


def _role_block_effect(
    candidate: dict[str, Any],
    block: dict[str, Any],
    role: str,
    mechanism_effects: dict[str, str],
) -> str:
    """Resolve the intended physical direction of one role-linked Check.

    Step08 ``mechanism_evidence`` is the preferred source because it already
    expresses whether a Check supports, limits, mixes, or contextualizes the
    physical mechanism. Intent roles and validated precursor/alignment state are
    deterministic fallbacks for Checks that do not appear on that axis.
    """
    check_id = str(block.get("check_id") or "")
    effect = mechanism_effects.get(check_id)
    if effect:
        return effect
    roles = {str(value) for value in block.get("roles", [])}
    if "mechanism_support" in roles:
        return "supporting"
    if "mechanism_limit" in roles:
        return "limiting"
    if "mechanism_mixed" in roles:
        return "mixed"
    if "mechanism_context" in roles:
        return "context"
    if "intent_opposing" in roles:
        return "limiting"
    if "intent_supporting" in roles:
        return "supporting"
    if "intent_context" in roles:
        return "context"
    if "spatiotemporal_alignment" in roles:
        return "supporting" if candidate.get("validated_spatiotemporal_alignment") else "context"
    if role in {str(value) for value in candidate.get("validated_precursor_categories", [])}:
        return "supporting"
    if role == "direct_precipitation" and candidate.get("direct_precipitation_status") == "confirmed":
        return "supporting"
    return ""


def _confirmed_role_directions(effect: str, assessment: str) -> set[str]:
    """Translate a fallback hypothesis assessment into direction tokens.

    This function is used only when Step08 did not provide a final
    ``mechanism_evidence.effect`` for the Check.  In that fallback path the
    Step07 assessment is still relative to the original hypothesis, so an
    opposite result can legitimately invert the intent polarity.
    """
    if effect == "supporting":
        if assessment == "observed":
            return {"support"}
        if assessment == "partially_observed":
            return {"partial_support"}
        if assessment == "opposite_observed":
            return {"opposing"}
        return set()
    if effect == "limiting":
        if assessment == "observed":
            return {"limit"}
        if assessment == "partially_observed":
            return {"partial_limit"}
        if assessment == "opposite_observed":
            return {"limit_contradicted"}
        return set()
    if effect == "mixed":
        if assessment in {"observed", "partially_observed", "opposite_observed"}:
            return {"mixed"}
        return set()
    if effect == "context":
        if assessment in {"observed", "partially_observed", "opposite_observed"}:
            return {"context"}
        return set()
    return set()


def _authoritative_mechanism_directions(effect: str, assessment: str) -> set[str]:
    """Use Step08 mechanism effect as the final physical polarity.

    Step08 has already interpreted the Step07 hypothesis result.  Reapplying
    ``opposite_observed`` here would invert the meaning a second time.  The
    Step07 assessment is retained only to distinguish a partial observation.
    """
    if assessment in {"uncertain", "insufficient_evidence"}:
        return set()
    partial = assessment == "partially_observed"
    if effect == "supporting":
        return {"partial_support" if partial else "support"}
    if effect == "limiting":
        return {"partial_limit" if partial else "limit"}
    if effect == "mixed":
        return {"mixed"}
    if effect == "context":
        return {"context"}
    return set()


def _scope_values(block: dict[str, Any], key: str) -> set[Any]:
    scope = block.get("scope_context")
    if not isinstance(scope, dict):
        return set()
    raw = scope.get(key)
    if not isinstance(raw, list):
        return set()
    return {value for value in raw if value not in (None, "")}


def _scope_compatible(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Return True only when two evidence scopes are directly comparable."""
    first_scope = first.get("scope_context")
    second_scope = second.get("scope_context")
    if not isinstance(first_scope, dict) or not isinstance(second_scope, dict):
        return False

    first_axis = str(first_scope.get("decision_axis") or "")
    second_axis = str(second_scope.get("decision_axis") or "")
    if not first_axis or not second_axis or first_axis != second_axis:
        return False

    first_hours = _scope_values(first, "forecast_hours")
    second_hours = _scope_values(second, "forecast_hours")
    if first_hours and second_hours and not (first_hours & second_hours):
        return False

    first_regions = {str(value) for value in _scope_values(first, "region_ids")}
    second_regions = {str(value) for value in _scope_values(second, "region_ids")}
    if first_regions and second_regions:
        return bool(first_regions & second_regions)

    first_targets = {str(value) for value in _scope_values(first, "target_ids")}
    second_targets = {str(value) for value in _scope_values(second, "target_ids")}
    if first_targets and second_targets and (first_targets & second_targets):
        return True

    first_domain = str(first_scope.get("domain_scope") or "")
    second_domain = str(second_scope.get("domain_scope") or "")
    first_relation = str(first_scope.get("spatial_relation_scope") or "")
    second_relation = str(second_scope.get("spatial_relation_scope") or "")
    return bool(
        first_domain
        and second_domain
        and first_domain == second_domain
        and first_relation
        and second_relation
        and first_relation == second_relation
    )


def _core_scope_score(candidate: dict[str, Any], block: dict[str, Any]) -> int:
    """Score how directly one Check represents the candidate's core environment."""
    scope = block.get("scope_context") if isinstance(block.get("scope_context"), dict) else {}
    hours = {int(value) for value in scope.get("forecast_hours", []) if isinstance(value, int) and not isinstance(value, bool)}
    regions = {str(value) for value in scope.get("region_ids", []) if str(value)}
    score = 0

    localization = candidate.get("environment_localization")
    if isinstance(localization, dict) and localization.get("status") == "localized":
        if str(block.get("check_id") or "") == str(localization.get("evidence_check_id") or ""):
            score += 100
        local_hour = localization.get("forecast_hour")
        if isinstance(local_hour, int) and not isinstance(local_hour, bool) and local_hour in hours:
            score += 20
        local_regions = {
            str(item.get("region_id") or "")
            for item in localization.get("regions", [])
            if isinstance(item, dict) and str(item.get("region_id") or "")
        }
        if local_regions and regions and (local_regions & regions):
            score += 20
        if str(scope.get("decision_axis") or "") != "temporal_evolution":
            score += 5
        return score

    peak_hours = {
        int(value)
        for value in candidate.get("peak_forecast_hours", [])
        if isinstance(value, int) and not isinstance(value, bool)
    }
    if peak_hours:
        if hours and (hours & peak_hours):
            score += 20
        if str(scope.get("decision_axis") or "") != "temporal_evolution":
            score += 5
    return score


def _role_assessment(
    candidate: dict[str, Any],
    role: str,
    excluded_independent_roles: dict[str, set[str]] | None = None,
    related_multivariable_assessments: dict[str, list[dict[str, str]]] | None = None,
) -> tuple[str, str]:
    """Aggregate role-linked Checks without mixing incompatible scopes."""
    alignment_assessments: list[str] = []
    role_blocks: list[dict[str, Any]] = []
    for block in candidate["evidence_blocks"]:
        roles = {str(value) for value in block.get("roles", [])}
        if "spatiotemporal_alignment" in roles:
            alignment_assessments.append(str(block.get("assessment", "insufficient_evidence")))
        if role in roles:
            role_blocks.append(block)

    if role == "spatiotemporal_alignment":
        if candidate["validated_spatiotemporal_alignment"]:
            return "observed", "연계 확인"
        if alignment_assessments:
            return _dominant_assessment(alignment_assessments), "연계 미확정"
        return "insufficient_evidence", "연계 미확정"

    mechanism_effects = _mechanism_effect_by_check(candidate)
    directions: set[str] = set()
    role_assessments: list[str] = []
    support_entries: list[tuple[dict[str, Any], set[str]]] = []
    limit_entries: list[tuple[dict[str, Any], set[str]]] = []

    for block in role_blocks:
        assessment = str(block.get("assessment", "insufficient_evidence"))
        role_assessments.append(assessment)
        check_id = str(block.get("check_id") or "")
        mechanism_effect = mechanism_effects.get(check_id)
        if mechanism_effect:
            tokens = _authoritative_mechanism_directions(mechanism_effect, assessment)
        else:
            fallback_effect = _role_block_effect(candidate, block, role, {})
            tokens = _confirmed_role_directions(fallback_effect, assessment)
        directions.update(tokens)
        if tokens & {"support", "partial_support"}:
            support_entries.append((block, tokens))
        if tokens & {"limit", "partial_limit"}:
            limit_entries.append((block, tokens))

    has_support = bool(support_entries)
    has_limit = bool(limit_entries)
    has_full_support = any("support" in tokens for _block, tokens in support_entries)
    has_full_limit = any("limit" in tokens for _block, tokens in limit_entries)

    if has_support and has_limit:
        if any(_scope_compatible(support_block, limit_block) for support_block, _ in support_entries for limit_block, _ in limit_entries):
            assessment = "observed" if has_full_support and has_full_limit else "partially_observed"
            return assessment, "지지+제한"
        # Different decision axes / time windows / spatial scopes are not
        # directly comparable.  Do not let a core-scope score hide a valid
        # limitation from another scope; expose the scope conflict explicitly.
        return "partially_observed", "범위별 상반"

    if "mixed" in directions:
        assessment = "observed" if "observed" in role_assessments else "partially_observed"
        return assessment, "혼합"
    if has_support:
        if has_full_support:
            return "observed", "지지"
        return "partially_observed", "부분 지지"
    if has_limit:
        if has_full_limit:
            return "observed", "제한 근거 확인"
        return "partially_observed", "제한 근거 부분 확인"
    if "opposing" in directions:
        return "opposite_observed", "반대 신호 확인"
    if "context" in directions:
        assessment = _dominant_assessment(role_assessments)
        return assessment, "참고 근거 확인"
    if "limit_contradicted" in directions:
        return "opposite_observed", "제한 가설 반대 신호"

    exclusion_reasons = set((excluded_independent_roles or {}).get(role, set()))
    if exclusion_reasons:
        if exclusion_reasons == {"ENSEMBLE_NO_CONSENSUS"}:
            return "insufficient_evidence", "판정 합의 미성립"
        if any(reason.startswith("QUANTITATIVE_") for reason in exclusion_reasons):
            return "insufficient_evidence", "정량 검증 제외"
        return "insufficient_evidence", "판독 제외"

    related_labels = {
        "observed": "관련 근거 확인",
        "partially_observed": "관련 근거 부분 확인",
        "not_observed": "관련 근거 미확인",
        "opposite_observed": "관련 근거 상반",
        "uncertain": "관련 근거 불확실",
        "insufficient_evidence": "관련 근거 판단 제한",
    }
    supplemental = list((related_multivariable_assessments or {}).get(role, []))
    if supplemental:
        supplemental_directions: set[str] = set()
        supplemental_assessments: list[str] = []
        for item in supplemental:
            if isinstance(item, dict):
                assessment = str(item.get("assessment") or "")
                check_id = str(item.get("check_id") or "")
            else:  # backward-compatible with older in-memory callers/tests
                assessment = str(item or "")
                check_id = ""
            if assessment not in ASSESSMENT_LABELS:
                continue
            supplemental_assessments.append(assessment)
            mechanism_effect = mechanism_effects.get(check_id) if check_id else None
            if mechanism_effect:
                supplemental_directions.update(
                    _authoritative_mechanism_directions(mechanism_effect, assessment)
                )
        if supplemental_directions:
            has_support = bool(supplemental_directions & {"support", "partial_support"})
            has_limit = bool(supplemental_directions & {"limit", "partial_limit"})
            if "mixed" in supplemental_directions or (has_support and has_limit):
                return "partially_observed", "관련 근거 혼합"
            if "support" in supplemental_directions:
                return "observed", "관련 근거 지지"
            if "partial_support" in supplemental_directions:
                return "partially_observed", "관련 근거 부분 지지"
            if "limit" in supplemental_directions:
                return "observed", "관련 근거 제한"
            if "partial_limit" in supplemental_directions:
                return "partially_observed", "관련 근거 부분 제한"
            if "context" in supplemental_directions:
                return _dominant_assessment(supplemental_assessments), "관련 근거 참고"
        if supplemental_assessments:
            assessment = _dominant_assessment(supplemental_assessments)
            return assessment, related_labels.get(assessment, "관련 근거 있음")

    if role_assessments:
        assessment = _dominant_assessment(role_assessments)
        if role == "direct_precipitation":
            direct_labels = {
                "not_observed": "확인되지 않음",
                "uncertain": "불확실",
                "insufficient_evidence": "근거 부족",
            }
            if assessment in direct_labels:
                return assessment, direct_labels[assessment]
        if assessment == "not_observed":
            return assessment, "관련 근거 미확인"
        if assessment == "uncertain":
            return assessment, "관련 근거 불확실"
        if assessment == "insufficient_evidence":
            return assessment, "관련 근거 판단 제한"

    return "insufficient_evidence", "개별 판독 없음"

def _dominant_assessment(assessments: list[str]) -> str:
    """Return a conservative categorical summary; this is presentation-only and not a ranking score."""
    precedence = (
        "opposite_observed",
        "not_observed",
        "uncertain",
        "insufficient_evidence",
        "partially_observed",
        "observed",
    )
    for key in precedence:
        if key in assessments:
            return key
    return assessments[0] if assessments else "insufficient_evidence"


def _signal_chain_style(assessment: str, state_label: str) -> str:
    """Map a signal-chain summary to its forecaster-facing semantic class."""
    if state_label in {"개별 판독 없음", "판독 제외", "판정 합의 미성립", "정량 검증 제외", "연계 미확정"}:
        return "signal-neutral"
    if state_label == "지지+제한":
        return "signal-dual"
    if state_label in {"혼합", "부분 지지", "범위별 상반"}:
        return "signal-mixed"
    if state_label == "지지" or state_label == "연계 확인":
        return "signal-support"
    if state_label.startswith("제한 근거"):
        return "signal-limit" if assessment in {"observed", "partially_observed"} else "signal-neutral"
    if state_label == "제한 가설 반대 신호":
        return "signal-mixed"
    if state_label.startswith("참고 근거"):
        return "signal-context"
    if state_label.startswith("관련 근거"):
        if state_label in {"관련 근거 지지", "관련 근거 부분 지지"}:
            return "signal-support" if assessment == "observed" else "signal-mixed"
        if state_label in {"관련 근거 제한", "관련 근거 부분 제한"}:
            return "signal-limit"
        if state_label == "관련 근거 혼합":
            return "signal-mixed"
        if assessment == "opposite_observed":
            return "signal-opposing"
        if assessment in {"observed", "partially_observed"}:
            return "signal-context"
        return "signal-neutral"
    if state_label == "반대 신호 확인":
        return "signal-opposing"
    return {
        "observed": "signal-support",
        "partially_observed": "signal-mixed",
        "opposite_observed": "signal-opposing",
        "not_observed": "signal-neutral",
        "uncertain": "signal-neutral",
        "insufficient_evidence": "signal-neutral",
    }.get(assessment, "signal-neutral")


def _render_signal_chain(
    candidate: dict[str, Any],
    excluded_independent_roles: dict[str, set[str]] | None = None,
    related_multivariable_assessments: dict[str, list[dict[str, str]]] | None = None,
) -> str:
    nodes = [
        ("direct_precipitation", "모델 강수 예측", "강수"),
        ("moisture_supply", "수분 공급", "수분"),
        ("low_level_forcing", "하층 수렴", "수렴"),
        ("mid_upper_forcing", "중층 상승운동", "상승"),
        ("organization_persistence", "바람의 연직구조", "바람"),
        ("spatiotemporal_alignment", "시공간 일치", "일치"),
    ]
    html: list[str] = []
    for index, (role, label, short) in enumerate(nodes):
        assessment, state_label = _role_assessment(
            candidate,
            role,
            excluded_independent_roles,
            related_multivariable_assessments,
        )
        style = _signal_chain_style(assessment, state_label)
        clickable = any(role in {str(value) for value in block.get("roles", [])} for block in candidate["evidence_blocks"])
        if state_label == "판정 합의 미성립":
            title_suffix = "동일 근거를 독립적으로 판독했으나 다수 합의가 성립하지 않아 종합판단 근거에서 제외되었습니다."
        elif state_label == "정량 검증 제외":
            title_suffix = "상세 판독은 수행했으나 정량 일관성 검증을 통과하지 못해 종합판단 근거에서 제외되었습니다."
        elif state_label == "판독 제외":
            title_suffix = "상세 판독은 수행했으나 내부 검증을 통과하지 못해 종합판단 근거에서 제외되었습니다."
        elif state_label == "지지+제한":
            title_suffix = "이 물리과정에는 강수 발달을 지지하는 근거와 제한하는 근거가 각각 확인되었습니다. 클릭하면 양쪽 관련 확인 항목으로 이동합니다."
        elif state_label == "혼합":
            title_suffix = "하나 이상의 관련 판독에서 지지·제한 방향을 명확히 분리하기 어려운 혼합 결과가 확인되었습니다."
        elif state_label == "범위별 상반":
            title_suffix = "지지와 제한 근거가 모두 있으나 시간·공간 또는 판단축이 달라 하나의 지지+제한으로 직접 병합하지 않았습니다."
        elif state_label.startswith("관련 근거") and not clickable:
            title_suffix = "독립 판독은 없지만 사용 가능한 복합 확인항목의 판독 결과를 요약한 보조 표시입니다."
        elif state_label.startswith("제한 근거"):
            title_suffix = "강수 발달을 제한하는 확인항목의 판독입니다. 클릭하면 관련 판단 근거로 이동합니다."
        elif state_label.startswith("참고 근거"):
            title_suffix = "강수 발달의 직접 지지 여부가 아닌 참고 목적의 확인항목입니다."
        else:
            title_suffix = "클릭하면 관련 판단 근거로 이동합니다."
        body = (
            f'<span class="signal-node-short">{escape(short)}</span>'
            f'<strong>{escape(label)}</strong><small>{escape(state_label)}</small>'
        )
        if clickable:
            html.append(
                f'<button class="signal-node signal-node-button {style}" type="button" '
                f'data-signal-role="{escape(role, quote=True)}" data-signal-candidate="{escape(candidate["candidate_id"], quote=True)}" '
                f'title="{escape(label, quote=True)} · {escape(state_label, quote=True)} · {escape(title_suffix, quote=True)}">'
                f'{body}</button>'
            )
        else:
            html.append(
                f'<div class="signal-node {style}" '
                f'title="{escape(label, quote=True)} · {escape(state_label, quote=True)} · {escape(title_suffix, quote=True)}">'
                f'{body}</div>'
            )
        if index < len(nodes) - 1:
            html.append('<span class="signal-arrow" aria-hidden="true">→</span>')
    return (
        '<section class="signal-chain-section" aria-label="강수 감시 물리과정 신호체인">'
        '<div class="section-heading"><div><span class="section-kicker">물리과정</span><h3>강수 감시 신호체인</h3></div></div>'
        f'<div class="signal-chain">{"".join(html)}</div></section>'
    )


def _overview_product_short(product_id: str) -> str:
    labels = {
        "QL_PRECIP_6H": "6시간 강수량",
        "QL_TCW_WIND850": "대기 전층 수분량(TCW)·850 hPa 바람",
        "QL_CONVERGENCE925_WIND925": "925 hPa 수평수렴·바람",
        "QL_OMEGA700_Z500": "700 hPa 기압연직속도(ω)·500 hPa 지위고도",
        "QL_MSLP_WIND10": "해면기압·10 m 바람",
        "QL_JET300_Z300": "300 hPa 제트·지위고도",
    }
    return labels.get(product_id, "기타 개요 예측장")


def _prepare_overview_assets(root: Path, init: str, product: dict[str, Any], stage: Path) -> dict[str, list[dict[str, Any]]]:
    """Copy candidate-linked overview images for the Step10 web viewer.

    Preference order:
    1. Step05 candidate-specific overview images with highlighted candidate domain.
    2. Fallback to Step02 overview images when Step05 candidate overviews are absent.

    This is a presentation-only bridge for index.html. forecaster_product.json and
    its schema remain unchanged.
    """
    if not product.get("surfaced_candidates"):
        return {}

    surfaced_ids = {str(item["candidate_id"]) for item in product["surfaced_candidates"]}
    overview_dir = stage / "assets" / "overview"
    priority = {
        "QL_PRECIP_6H": 0,
        "QL_TCW_WIND850": 1,
        "QL_CONVERGENCE925_WIND925": 2,
        "QL_OMEGA700_Z500": 3,
        "QL_MSLP_WIND10": 4,
        "QL_JET300_Z300": 5,
    }

    step05_manifest_path = runtime_path(root, "daio", init, "step05", "candidate_overview_data.json")
    if step05_manifest_path.is_file():
        step05 = load_json_object(step05_manifest_path, code="STEP05_OVERVIEW_INVALID")
        result: dict[str, list[dict[str, Any]]] = {candidate_id: [] for candidate_id in surfaced_ids}
        copied: set[str] = set()
        for asset in step05.get("candidate_overview_assets", []):
            if not isinstance(asset, dict):
                continue
            candidate_id = str(asset.get("candidate_id") or "")
            if candidate_id not in surfaced_ids:
                continue
            raw_path = asset.get("image_path")
            if not isinstance(raw_path, str) or not raw_path:
                continue
            source = resolve_logical_ref(root, raw_path)
            if not source.is_file():
                raise Pass2PipelineError("STEP05_OVERVIEW_MISSING", f"Step05 candidate overview image is missing: {raw_path}.")
            file_name = source.name
            if file_name not in copied:
                overview_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, overview_dir / file_name)
                copied.add(file_name)
            result.setdefault(candidate_id, []).append({
                "asset_id": str(asset.get("asset_id") or file_name),
                "relative_path": f"assets/overview/{file_name}",
                "product_id": str(asset.get("product_id") or ""),
                "product_name_ko": str(asset.get("product_name_ko") or asset.get("asset_id") or file_name),
                "forecast_hour_start": asset.get("forecast_hour_start"),
                "forecast_hour_end": asset.get("forecast_hour_end"),
                "sha256": sha256sum(source),
            })
        for candidate_id in result:
            result[candidate_id].sort(key=lambda row: (priority.get(row["product_id"], 99), row["asset_id"]))
        if any(result.values()):
            return result

    step04 = load_json_object(step04_result_path(root, init), code="STEP04_RESULT_MISSING")
    step02 = load_json_object(runtime_path(root, "daio", init, "step02", "overview_data.json"), code="STEP02_OVERVIEW_MISSING")
    candidate_rows = {
        str(item.get("candidate_id")): item
        for item in step04.get("candidates", [])
        if isinstance(item, dict) and isinstance(item.get("candidate_id"), str)
    }
    asset_rows = {
        str(item.get("asset_id")): item
        for item in step02.get("overview_assets", [])
        if isinstance(item, dict) and isinstance(item.get("asset_id"), str)
    }
    result = {}
    copied: set[str] = set()
    for candidate_id in sorted(surfaced_ids):
        source_candidate = candidate_rows.get(candidate_id)
        if source_candidate is None:
            continue
        rows: list[dict[str, Any]] = []
        selected_assets = _select_candidate_overview_assets(source_candidate, list(asset_rows.values()))
        for asset in selected_assets:
            asset_id = str(asset.get("asset_id") or "")
            if not asset_id:
                continue
            raw_path = asset.get("image_path")
            if not isinstance(raw_path, str) or not raw_path:
                continue
            source = resolve_logical_ref(root, raw_path)
            if not source.is_file():
                raise Pass2PipelineError("STEP02_OVERVIEW_MISSING", f"Overview image is missing: {asset_id}.")
            file_name = source.name
            if file_name not in copied:
                overview_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, overview_dir / file_name)
                copied.add(file_name)
            rows.append({
                "asset_id": str(asset_id),
                "relative_path": f"assets/overview/{file_name}",
                "product_id": str(asset.get("product_id") or ""),
                "product_name_ko": str(asset.get("product_name_ko") or asset_id),
                "forecast_hour_start": asset.get("forecast_hour_start"),
                "forecast_hour_end": asset.get("forecast_hour_end"),
                "sha256": sha256sum(source),
            })
        rows.sort(key=lambda row: (priority.get(row["product_id"], 99), row["asset_id"]))
        result[candidate_id] = rows
    return result


def _render_overview_gallery(
    candidate: dict[str, Any],
    overview_assets: list[dict[str, Any]],
    init: str,
    *,
    load_initial_image: bool = False,
) -> str:
    if not overview_assets:
        return (
            '<section class="overview-empty"><span class="section-kicker">개요 자료</span>'
            '<h3>연결된 개요이미지 없음</h3><p>이 감시후보에 연결된 개요 예측장이 없습니다. 상세 근거는 하단에서 확인할 수 있습니다.</p></section>'
        )
    buttons: list[str] = []
    figures: list[str] = []
    for index, asset in enumerate(overview_assets):
        active = " active" if index == 0 else ""
        hidden = "" if index == 0 else " hidden"
        label = _overview_product_short(asset["product_id"])
        range_text = ""
        if isinstance(asset.get("forecast_hour_start"), int) and isinstance(asset.get("forecast_hour_end"), int):
            range_text = _format_compact_kst_range(
                init, int(asset["forecast_hour_start"]), int(asset["forecast_hour_end"])
            )
        target_id = f'{candidate["candidate_id"]}-overview-{index}'
        buttons.append(
            f'<button class="overview-tab{active}" type="button" role="tab" aria-selected="{"true" if index == 0 else "false"}" '
            f'aria-controls="{escape(target_id, quote=True)}" data-overview-target="{escape(target_id, quote=True)}">'
            f'<strong>{escape(label)}</strong><small>{escape(range_text)}</small></button>'
        )
        image_url = _versioned_asset_path(asset["relative_path"], asset.get("sha256"))
        should_load = load_initial_image and index == 0
        image_source_attr = (
            f'src="{escape(image_url, quote=True)}" loading="eager"'
            if should_load
            else f'data-src="{escape(image_url, quote=True)}" loading="lazy"'
        )
        figures.append(
            f'<figure id="{escape(target_id, quote=True)}" class="overview-figure{active}"{hidden}>'
            f'<button class="overview-image-button" type="button" data-image-path="{escape(image_url, quote=True)}" '
            f'data-image-caption="{escape(label, quote=True)} · {escape(range_text, quote=True)}">'
            f'<span class="overview-image-frame"><img {image_source_attr} decoding="async" '
            f'alt="{escape(candidate["candidate_name"], quote=True)} 관련 {escape(label, quote=True)} 개요 이미지">'
            '<span class="overview-load-error" aria-hidden="true"><b>개요이미지를 불러오지 못했습니다.</b><small>이미지 경로를 확인하십시오.</small></span></span></button>'
            f'<figcaption><strong>{escape(label)}</strong><span>{escape(range_text)}</span></figcaption>'
            '</figure>'
        )
    return (
        '<section class="overview-viewer" aria-label="감시후보 탐색 개요이미지">'
        '<div class="overview-toolbar"><div><span class="section-kicker">개요 자료</span><h3>후보 시간대 주요 기상장</h3></div>'
        '<div class="overview-tabs" role="tablist">' + ''.join(buttons) + '</div></div>'
        '<div class="overview-stage">' + ''.join(figures) + '</div></section>'
    )


def _render_comprehensive_assessment(text: str, init: str) -> str:
    """Render the complete Step08 forecast-analysis draft once, without duplication."""
    value = text.strip()
    if not value:
        return '<p class="empty-inline">종합판단 없음</p>'
    return f'<p>{_render_text(value, init)}</p>'


def _render_operational_guidance(candidate: dict[str, Any], init: str) -> str:
    """Render only persisted guidance; legacy candidates intentionally render nothing."""
    guidance = candidate.get("operational_guidance")
    if guidance is None:
        return ""
    if not isinstance(guidance, dict):
        raise Pass2PipelineError("STEP10_OPERATIONAL_GUIDANCE_INVALID", "operational_guidance must be an object.")
    try:
        current_action = guidance["current_action"]
        next_check = guidance["next_check"]
        interpretation_limit = guidance["interpretation_limit"]
    except KeyError as exc:
        raise Pass2PipelineError("STEP10_OPERATIONAL_GUIDANCE_INVALID", "operational_guidance is incomplete.") from exc
    if not all(isinstance(value, str) and value.strip() for value in (current_action, next_check, interpretation_limit)):
        raise Pass2PipelineError("STEP10_OPERATIONAL_GUIDANCE_INVALID", "operational_guidance contains invalid text.")
    return (
        '<section class="operational-guidance" aria-label="예보관 확인">'
        '<span class="section-kicker">예보관 확인</span><h3>예보관 확인</h3>'
        '<div class="operational-guidance-grid">'
        f'<div><span>다음 확인</span><p>{_render_text(next_check.strip(), init)}</p></div>'
        f'<div><span>해석 유의</span><p>{_render_text(interpretation_limit.strip(), init)}</p></div>'
        '</div></section>'
    )


def _assessment_counts(candidate: dict[str, Any]) -> dict[str, int]:
    counts = {key: 0 for key in ASSESSMENT_LABELS}
    for block in candidate.get("evidence_blocks", []):
        key = str(block.get("assessment", "insufficient_evidence"))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _environment_status_presentation(candidate: dict[str, Any]) -> tuple[str, str]:
    """Render environment status from its Step08/09 source-of-truth enum only."""
    status = candidate.get("environment_support_status")
    presentation = ENVIRONMENT_STATUS_PRESENTATION.get(status)
    if presentation is None:
        raise Pass2PipelineError("STEP10_ENVIRONMENT_STATUS_INVALID", f"invalid environment_support_status: {status!r}")
    return presentation


def _environment_persistence_presentation(candidate: dict[str, Any]) -> tuple[str, str]:
    status = candidate.get("environment_persistence_status")
    presentation = ENVIRONMENT_PERSISTENCE_PRESENTATION.get(status)
    if presentation is None:
        raise Pass2PipelineError(
            "STEP10_ENVIRONMENT_PERSISTENCE_STATUS_INVALID",
            f"invalid environment_persistence_status: {status!r}",
        )
    return presentation


def _render_decision_status(candidate: dict[str, Any]) -> str:
    direct_assessment, direct_label = _role_assessment(candidate, "direct_precipitation")
    precursor_label, precursor_assessment = _environment_status_presentation(candidate)
    persistence_label, persistence_assessment = _environment_persistence_presentation(candidate)
    alignment_assessment = "observed" if candidate["validated_spatiotemporal_alignment"] else "insufficient_evidence"
    alignment_label = "확인" if candidate["validated_spatiotemporal_alignment"] else "미확정"
    counts = _assessment_counts(candidate)
    caution_count = len(candidate["contradictory_evidence"]) + len(candidate["uncertainties"])
    cells = (
        ("모델 강수 예측", direct_label, direct_assessment),
        ("강수 발달 환경", precursor_label, precursor_assessment),
        ("환경 지속성", persistence_label, persistence_assessment),
        ("시공간 일치", alignment_label, alignment_assessment),
        ("주의 신호", f"{caution_count}건", "opposite_observed" if caution_count else "observed"),
    )
    return '<div class="decision-status-strip">' + ''.join(
        f'<span class="decision-status-chip {ASSESSMENT_STYLE_CLASSES.get(state, "muted")}"><small>{escape(label)}</small><b>{escape(value)}</b></span>'
        for label, value, state in cells
    ) + '</div>'


def _render_compact_watch_facts(candidate: dict[str, Any], init: str, geography: dict[str, str]) -> str:
    return (
        '<div class="watch-facts">'
        f'<div><span>영역</span><strong class="watch-fact-value">{_render_area(candidate["effective_area"])}{_render_minimap_trigger(candidate, init, geography)}</strong></div>'
        f'<div><span>시간</span><strong>{_render_time(candidate["effective_time_window"], init)}</strong></div>'
        f'<div><span>{escape(_peak_display_label(candidate))}</span><strong>{escape(_render_peak_hours(candidate, init))}</strong></div>'
        '</div>'
    )


def _render_priority_watch_badge(candidate: dict[str, Any], *, large: bool = False) -> str:
    """Render the deterministic priority-emphasis badge."""
    if candidate.get("display_tier") != "priority":
        return ""
    extra = " priority-watch-badge-large" if large else ""
    return (
        f'<i class="priority-watch-badge{extra}" '
        'title="모델 강수가 확인되고 핵심시각·공간에서 강수·대기 전층 수분량(TCW)·하층 수렴·중층 상승운동의 완전한 결합이 검증된 중점 감시 대상입니다. 환경 지속성이 제한되어도 핵심시각의 완전한 결합이 확인되면 중점 감시가 될 수 있으며, 환경 확인만으로 자동 중점 감시가 되는 것은 아닙니다.">중점 감시</i>'
    )



def _render_priority_watch_context(candidate: dict[str, Any], init: str) -> str:
    """Render concise LLM-authored priority context without repeating long prose."""
    if candidate.get("display_tier") != "priority":
        return ""
    brief = candidate.get("operational_brief")
    if not isinstance(brief, dict):
        return ""
    key_signal = str(brief.get("key_signal") or "").strip()
    key_limitation = str(brief.get("key_limitation") or "").strip()
    if not key_signal or not key_limitation:
        return ""
    return (
        '<div class="priority-watch-context">'
        '<div class="priority-watch-line priority-watch-reason"><span>중점 감시 신호</span>'
        f'<strong>{_render_text(key_signal, init)}</strong></div>'
        '<div class="priority-watch-line priority-watch-limit"><span>제한 요인</span>'
        f'<strong>{_render_text(key_limitation, init)}</strong></div>'
        '</div>'
    )


def _render_priority_check_badge(candidate: dict[str, Any]) -> str:
    """Render the elevated forecaster-action tier below deterministic priority."""
    if candidate.get("display_tier") != "elevated":
        return ""
    return '<i class="priority-check-badge" title="중점 감시 조건에는 해당하지 않지만 지지·제한 근거를 종합할 때 운영상 우선 확인할 가치가 있는 후보">우선 확인</i>'


def _render_general_watch_badge(candidate: dict[str, Any]) -> str:
    # General is the default visible monitoring state.  No badge is rendered;
    # the candidate being visible already communicates general monitoring.
    return ""


def _render_precursor_badge(candidate: dict[str, Any]) -> str:
    if candidate.get("candidate_type") != "precursor_environment":
        return ""
    return '<i class="precursor-watch-badge" title="직접 모델 강수보다 수분·수렴·상승 등 선행 강수 발달 환경을 중심으로 감시하는 후보입니다.">선행환경</i>'


def _render_internal_judgment_item(candidate: dict[str, Any], init: str) -> str:
    try:
        assert_public_tier(candidate)
    except TierNarrativeError as exc:
        raise Pass2PipelineError("STEP10_TIER_NARRATIVE_INVALID", str(exc)) from exc
    physical_status = str(candidate.get("physical_consistency_status") or "")
    monitoring_value = str(candidate.get("monitoring_value") or "")
    summary = str(candidate.get("physical_mechanism_summary") or "").strip()
    reason = str(candidate.get("operational_surface_reason") or "").strip()
    if physical_status not in PHYSICAL_CONSISTENCY_LABELS:
        raise Pass2PipelineError("STEP10_PHYSICAL_STATUS_INVALID", f"invalid physical_consistency_status: {physical_status!r}")
    if monitoring_value not in MONITORING_VALUE_LABELS:
        raise Pass2PipelineError("STEP10_MONITORING_VALUE_INVALID", f"invalid monitoring_value: {monitoring_value!r}")
    if not summary or not reason:
        raise Pass2PipelineError("STEP10_INTERNAL_JUDGMENT_INVALID", "internal judgment text must be non-empty.")
    disposition = {
        "priority": "중점 감시",
        "elevated": "우선 확인",
        "general": "일반 감시",
        "hidden": "비표출",
    }.get(str(candidate.get("display_tier") or ""), "표출" if candidate.get("should_surface") else "비표출")
    return (
        '<article class="internal-judgment-item">'
        '<div class="internal-judgment-head">'
        f'<div><span class="internal-disposition">{disposition}</span><h3>{_render_text(candidate["candidate_name"], init)}</h3></div>'
        '<div class="internal-judgment-badges">'
        f'<span><small>물리적 성립성</small><b>{escape(PHYSICAL_CONSISTENCY_LABELS[physical_status])}</b></span>'
        f'<span><small>감시 가치</small><b>{escape(MONITORING_VALUE_LABELS[monitoring_value])}</b></span>'
        '</div></div>'
        f'<p>{_render_text(summary, init)}</p>'
        f'<p class="internal-judgment-reason"><strong>감시 판단 이유</strong> {_render_text(reason, init)}</p>'
        '</article>'
    )


def _render_internal_judgment_summary(candidates: list[dict[str, Any]], init: str) -> str:
    if not candidates:
        return ""
    rows = ''.join(_render_internal_judgment_item(candidate, init) for candidate in candidates)
    return (
        '<details class="internal-judgment-summary">'
        f'<summary><span><b>AI 내부 판단 상세</b><small>물리과정 성립성과 운영상 감시 가치 · {len(candidates)}건</small></span>'
        '<span class="internal-summary-note">참고용</span></summary>'
        '<div class="internal-judgment-body">'
        '<p class="internal-judgment-note">이 항목은 AI가 표출 여부를 결정할 때 사용하는 내부 판단축입니다. '
        '예보관의 별도 필수 판정 항목이 아니며, 운영 화면에서는 종합판단·예보관 확인·상세 근거를 우선 확인합니다.</p>'
        f'{rows}</div></details>'
    )


def _environment_localization_region_label(region: dict[str, Any], sector: str) -> str:
    name = str(region.get("name") or region.get("region_id") or "").strip()
    sector_label = ENVIRONMENT_LOCALIZATION_SECTOR_LABELS.get(str(sector), "")
    return f"{name} {sector_label}".strip()



def _render_environment_localization_map(
    candidate: dict[str, Any],
    localization: dict[str, Any],
    region_guide_regions: list[dict[str, Any]],
    geography: dict[str, str],
) -> tuple[str, str]:
    catalog = {str(item.get("region_id")): item for item in region_guide_regions if isinstance(item, dict)}
    selected: list[tuple[dict[str, Any], str]] = []
    labels: list[str] = []
    for item in localization.get("regions", []):
        if not isinstance(item, dict):
            continue
        region = catalog.get(str(item.get("region_id") or ""))
        if not isinstance(region, dict):
            continue
        sector = str(item.get("relative_sector") or "unspecified")
        selected.append((region, sector))
        labels.append(_environment_localization_region_label(region, sector))
    if not selected:
        return "", ""

    area = candidate.get("effective_area") if isinstance(candidate.get("effective_area"), dict) else None
    if not isinstance(area, dict):
        return "", ""
    combined = {
        "west": float(area["west"]), "east": float(area["east"]),
        "south": float(area["south"]), "north": float(area["north"]),
    }
    for region, _sector in selected:
        bbox = region.get("bbox") if isinstance(region.get("bbox"), dict) else None
        if not isinstance(bbox, dict):
            continue
        combined["west"] = min(combined["west"], float(bbox["west"]))
        combined["east"] = max(combined["east"], float(bbox["east"]))
        combined["south"] = min(combined["south"], float(bbox["south"]))
        combined["north"] = max(combined["north"], float(bbox["north"]))
    viewbox = _region_guide_focus_viewbox(combined)

    grid_lines: list[str] = []
    for lon in range(120, 146, 5):
        x, _ = _minimap_project(float(lon), MINIMAP_LAT_MIN)
        grid_lines.append(f'<line x1="{x:.2f}" y1="0" x2="{x:.2f}" y2="{MINIMAP_VIEWBOX_HEIGHT:.2f}" />')
    for lat in range(30, 47, 5):
        _, y = _minimap_project(MINIMAP_LON_MIN, float(lat))
        grid_lines.append(f'<line x1="0" y1="{y:.2f}" x2="{MINIMAP_VIEWBOX_WIDTH:.2f}" y2="{y:.2f}" />')

    candidate_x, candidate_y, candidate_w, candidate_h = _region_guide_bbox_rect({
        "west": float(area["west"]), "east": float(area["east"]),
        "south": float(area["south"]), "north": float(area["north"]),
    })
    region_layers: list[str] = []
    for region, _sector in selected:
        region_layers.append(
            '<g class="environment-localization-region-grid">'
            + _render_region_guide_grid_cells(region) + '</g>'
        )
    korea_x, korea_y = _minimap_project(127.4, 38.3)
    west_sea_x, west_sea_y = _minimap_project(123.4, 36.7)
    east_sea_x, east_sea_y = _minimap_project(132.2, 38.0)
    south_sea_x, south_sea_y = _minimap_project(128.0, 31.5)
    svg = (
        f'<svg class="candidate-minimap-svg environment-localization-svg" viewBox="{viewbox[0]:.2f} {viewbox[1]:.2f} {viewbox[2]:.2f} {viewbox[3]:.2f}" '
        f'data-default-viewbox="{viewbox[0]:.2f} {viewbox[1]:.2f} {viewbox[2]:.2f} {viewbox[3]:.2f}" role="img" aria-label="핵심 발달환경 위치도">'
        '<rect class="minimap-water" x="0" y="0" width="100%" height="100%" />'
        '<g class="minimap-grid">' + ''.join(grid_lines) + '</g>'
        '<g class="minimap-land">' + geography["land_paths"] + '</g>'
        '<g class="minimap-coastline">' + geography["coastline_paths"] + '</g>'
        + ''.join(region_layers)
        + f'<rect class="environment-localization-candidate" x="{candidate_x:.2f}" y="{candidate_y:.2f}" width="{candidate_w:.2f}" height="{candidate_h:.2f}" />'
        + f'<text class="minimap-label minimap-label-korea" x="{korea_x:.2f}" y="{korea_y:.2f}">한반도</text>'
        + f'<text class="minimap-label" x="{west_sea_x:.2f}" y="{west_sea_y:.2f}">서해</text>'
        + f'<text class="minimap-label" x="{east_sea_x:.2f}" y="{east_sea_y:.2f}">동해</text>'
        + f'<text class="minimap-label" x="{south_sea_x:.2f}" y="{south_sea_y:.2f}">남해</text>'
        + '</svg>'
    )
    return svg, ' · '.join(labels)


def _render_environment_localization(
    candidate: dict[str, Any],
    assets_by_id: dict[str, dict[str, Any]],
    init: str,
    region_guide_regions: list[dict[str, Any]],
    geography: dict[str, str],
) -> str:
    focus = candidate.get("environment_localization")
    if not isinstance(focus, dict) or focus.get("status") != "localized":
        return ""
    hour = focus.get("forecast_hour")
    check_id = focus.get("evidence_check_id")
    regions = focus.get("regions")
    if (
        isinstance(hour, bool) or not isinstance(hour, int) or hour < 0
        or not isinstance(check_id, str) or not check_id
        or not isinstance(regions, list) or not regions
    ):
        return ""
    component_labels = [
        ENVIRONMENT_LOCALIZATION_COMPONENT_LABELS[value]
        for value in _environment_localization_components(focus)
    ]
    chips = ''.join(f'<span>{escape(label)}</span>' for label in component_labels)
    target = _evidence_target_id(str(candidate["candidate_id"]), check_id)
    map_svg, display_area = _render_environment_localization_map(candidate, focus, region_guide_regions, geography)
    if not display_area:
        return ""
    map_button = ""
    if map_svg:
        map_button = (
            f'<button type="button" class="key-environment-map-button inline-map-trigger" '
            f'data-map-title="핵심 발달환경 위치" data-map-area="{escape("주요 결합 위치: " + display_area, quote=True)}" '
            f'data-map-time="{escape(format_forecast_hour(init, hour), quote=True)}">'
            '<span class="inline-map-trigger-label">지도에서 보기</span>'
            f'<span class="map-template" hidden>{map_svg}</span></button>'
        )
    gallery = _environment_localization_gallery(candidate, assets_by_id)
    gallery_button = ""
    if gallery:
        payload = escape(json.dumps(gallery, ensure_ascii=False, separators=(",", ":")), quote=True)
        gallery_button = (
            f'<button type="button" class="key-environment-location-button" data-focus-images="{payload}" '
            f'data-focus-time="{escape(format_forecast_hour(init, hour), quote=True)}" '
            f'data-focus-area="{escape(display_area, quote=True)}">원본 기상장</button>'
        )
    overall_label, _ = _environment_status_presentation(candidate)
    persistence_label, _ = _environment_persistence_presentation(candidate)
    return (
        '<aside class="key-environment-focus" aria-label="핵심 발달환경">'
        '<div class="key-environment-focus-head"><div><span class="section-kicker">핵심 발달환경</span>'
        '<strong>환경장 결합 위치 확인</strong></div><span class="key-environment-focus-state">위치 판독 완료</span></div>'
        '<div class="key-environment-focus-summary-grid">'
        f'<div class="key-environment-focus-summary-item"><span>핵심시각</span><strong>{escape(format_forecast_hour(init, hour))}</strong></div>'
        f'<div class="key-environment-focus-summary-item"><span>주요 결합 위치</span><strong>{escape(display_area)}</strong></div>'
        f'<div class="key-environment-focus-summary-item"><span>핵심시각 환경</span><strong>{escape(overall_label)}</strong></div>'
        f'<div class="key-environment-focus-summary-item"><span>환경 지속성</span><strong>{escape(persistence_label)}</strong></div>'
        '</div>'
        '<details class="key-environment-focus-detail">'
        '<summary>상세 보기</summary>'
        '<div class="key-environment-focus-detail-body">'
        f'<div class="key-environment-focus-components">{chips}</div>'
        '<div class="key-environment-focus-actions">'
        f'{map_button}{gallery_button}'
        f'<button type="button" class="alignment-check-link" data-evidence-target="{escape(target, quote=True)}">판독 근거</button>'
        '</div></div></details></aside>'
    )



def _render_spatial_roles_note(
    candidate: dict[str, Any], init: str,
    region_guide_regions: list[dict[str, Any]],
) -> str:
    """Present spatial *roles*, never promote a checked region into candidate identity.

    No name or priority decision is made here.  We intentionally avoid inferring
    overlap/nonoverlap of two operational region polygons from their identifiers.
    """
    identity = candidate.get("final_spatial_identity")
    if not isinstance(identity, dict) or identity.get("status") != "precipitation_object":
        return ""
    hour = identity.get("forecast_hour")
    regions = identity.get("regions")
    if not isinstance(hour, int) or isinstance(hour, bool) or hour < 0 or not isinstance(regions, list):
        return ""
    catalog = {
        str(item.get("region_id")): str(item.get("name") or item.get("region_id"))
        for item in region_guide_regions if isinstance(item, dict)
    }
    rain_ids = [str(item.get("region_id")) for item in regions if isinstance(item, dict) and item.get("region_id")]
    if not rain_ids:
        return ""
    rain_names = " · ".join(dict.fromkeys(catalog.get(rid, rid) for rid in rain_ids))
    focus = candidate.get("environment_localization")
    if isinstance(focus, dict) and focus.get("status") == "localized":
        env_ids = [str(item.get("region_id")) for item in focus.get("regions", [])
                   if isinstance(item, dict) and item.get("region_id")]
        env_hour = focus.get("forecast_hour")
        if env_ids and isinstance(env_hour, int) and not isinstance(env_hour, bool) and env_hour >= 0:
            env_names = " · ".join(dict.fromkeys(catalog.get(rid, rid) for rid in env_ids))
            env_summary = f"{format_forecast_hour(init, env_hour)} · {env_names}"
            interpretation = (
                "두 위치의 운영권역 표기가 다르며, 실제 격자 중첩 여부는 원본 기상장으로 확인해야 합니다."
                if not set(rain_ids).intersection(env_ids) else
                "공통 운영권역은 존재하지만 동일한 격자에서 결합했다고 단정할 수 없습니다."
            )
        else:
            env_summary = "검증된 세부 위치 정보 없음"
            interpretation = "환경 결합 위치를 추정하여 대표 강수 권역에 할당하지 않습니다."
    else:
        env_summary = "운영권역 단위 위치 미확정"
        interpretation = "환경 결합의 구조화 위치가 확정되지 않아 대표 강수 권역과 동일하다고 간주하지 않습니다."
    return (
        '<details class="spatial-roles-note"><summary>강수 대표영역과 환경 결합 위치 구분</summary>'
        '<div class="spatial-roles-content">'
        f'<p><b>대표 강수 객체</b> {escape(format_forecast_hour(init, hour))} · {escape(rain_names)}</p>'
        f'<p><b>환경 결합 위치</b> {escape(env_summary)}</p>'
        f'<p>{escape(interpretation)} 후보명은 전체 강수·환경 결합 위치를 모두 뜻하지 않습니다.</p>'
        '</div></details>'
    )


def _render_terminal_forecast_note(candidate: dict[str, Any], forecast_end: int, init: str) -> str:
    """Warn on the data horizon, not the intensity or future weather."""
    window = candidate.get("effective_time_window")
    if not isinstance(window, dict):
        return ""
    end_hour = window.get("forecast_hour_end")
    if not isinstance(end_hour, int) or isinstance(end_hour, bool) or end_hour != forecast_end:
        return ""
    return (
        '<p class="forecast-horizon-note">'
        f'예측 종료시각 {escape(format_forecast_hour(init, forecast_end))}까지의 자료만 분석했습니다. '
        '그 이후 강수의 유지·강화·약화는 이 초기자료로 판단할 수 없으며 다음 초기자료에서 재확인해야 합니다.'
        '</p>'
    )

def _render_operational_brief_panel(
    candidate: dict[str, Any],
    assets_by_id: dict[str, dict[str, Any]],
    init: str,
    region_guide_regions: list[dict[str, Any]],
    geography: dict[str, str],
    forecast_end: int = DEFAULT_FORECAST_TIMELINE[1],
) -> str:
    brief = candidate.get("operational_brief")
    if not isinstance(brief, dict):
        return _render_priority_watch_context(candidate, init)
    key_signal = str(brief.get("key_signal") or "").strip()
    key_limitation = str(brief.get("key_limitation") or "").strip()
    next_check = str(brief.get("next_check") or "").strip()
    if not all((key_signal, key_limitation, next_check)):
        return _render_priority_watch_context(candidate, init)

    focus = candidate.get("environment_localization")
    location_html = ""
    actions_html = ""
    components_html = ""
    if isinstance(focus, dict) and focus.get("status") == "localized":
        hour = focus.get("forecast_hour")
        check_id = focus.get("evidence_check_id")
        regions = focus.get("regions")
        if (
            isinstance(hour, int) and not isinstance(hour, bool) and hour >= 0
            and isinstance(check_id, str) and check_id
            and isinstance(regions, list) and regions
        ):
            map_svg, display_area = _render_environment_localization_map(
                candidate, focus, region_guide_regions, geography
            )
            if display_area:
                location_html = (
                    '<div class="operational-brief-location">'
                    f'<span><small>핵심시각</small><b>{escape(format_forecast_hour(init, hour))}</b></span>'
                    f'<span><small>결합 위치</small><b>{escape(display_area)}</b></span>'
                    '</div>'
                )
                component_labels = [
                    ENVIRONMENT_LOCALIZATION_COMPONENT_LABELS[value]
                    for value in _environment_localization_components(focus)
                ]
                components_html = '<div class="operational-brief-components">' + ''.join(
                    f'<span>{escape(label)}</span>' for label in component_labels
                ) + '</div>'
                buttons: list[str] = []
                if map_svg:
                    buttons.append(
                        f'<button type="button" class="key-environment-map-button inline-map-trigger" '
                        f'data-map-title="핵심 발달환경 위치" data-map-area="{escape("주요 결합 위치: " + display_area, quote=True)}" '
                        f'data-map-time="{escape(format_forecast_hour(init, hour), quote=True)}">'
                        '<span class="inline-map-trigger-label">지도</span>'
                        f'<span class="map-template" hidden>{map_svg}</span></button>'
                    )
                gallery = _environment_localization_gallery(candidate, assets_by_id)
                if gallery:
                    payload = escape(json.dumps(gallery, ensure_ascii=False, separators=(",", ":")), quote=True)
                    buttons.append(
                        f'<button type="button" class="key-environment-location-button" data-focus-images="{payload}" '
                        f'data-focus-time="{escape(format_forecast_hour(init, hour), quote=True)}" '
                        f'data-focus-area="{escape(display_area, quote=True)}">원본 기상장</button>'
                    )
                target = _evidence_target_id(str(candidate["candidate_id"]), check_id)
                buttons.append(
                    f'<button type="button" class="alignment-check-link" data-evidence-target="{escape(target, quote=True)}">판독 근거</button>'
                )
                actions_html = '<div class="operational-brief-actions">' + ''.join(buttons) + '</div>'

    return (
        '<section class="operational-brief-panel" aria-label="운영 요약">'
        '<div class="operational-brief-head"><div><span class="section-kicker">운영 요약</span><h3>핵심 신호</h3></div>'
        f'{location_html}</div>'
        f'<p class="operational-brief-signal">{_render_text(key_signal, init)}</p>'
        '<div class="operational-brief-rows">'
        '<div class="operational-brief-row limit"><span>제한 요인</span>'
        f'<strong>{_render_text(key_limitation, init)}</strong></div>'
        '<div class="operational-brief-row next"><span>다음 확인</span>'
        f'<strong>{_render_text(next_check, init)}</strong></div>'
        '</div>'
        f'<div class="operational-brief-foot">{components_html}{actions_html}</div>'
        f'{_render_spatial_roles_note(candidate, init, region_guide_regions)}'
        f'{_render_terminal_forecast_note(candidate, forecast_end, init)}'
        '</section>'
    )


def _render_analysis_detail_disclosure(candidate: dict[str, Any], init: str) -> str:
    categories = candidate.get("validated_precursor_categories", [])
    category_summary = _render_precursor_chips(categories, candidate.get("environment_support_status"))
    environment_status_label, _ = _environment_status_presentation(candidate)
    persistence_status_label, _ = _environment_persistence_presentation(candidate)
    return (
        '<section class="analysis-core-panel" aria-label="종합판단">'
        '<div class="core-assessment"><span class="section-kicker">예보 분석</span><h3>종합판단</h3>'
        f'{_render_comprehensive_assessment(candidate["final_assessment"], init)}</div>'
        '</section>'
        '<details class="analysis-detail-disclosure">'
        '<summary><span><b>상세 분석 보기</b><small>모델 강수 예측·강수 발달 환경·예보관 확인</small></span>'
        f'<span class="analysis-detail-summary"><i>환경 {escape(environment_status_label)}</i><i>지속성 {escape(persistence_status_label)}</i></span></summary>'
        '<div class="analysis-detail-body">'
        '<div class="analysis-detail-stack">'
        '<article class="analysis-detail-item"><span class="mini-label">모델 강수 예측</span><h4>모델 강수 예측</h4>'
        f'<p>{_render_text(candidate["direct_precipitation_assessment"], init)}</p></article>'
        '<article class="analysis-detail-item"><span class="mini-label">강수 발달 환경</span><h4>강수 발달 환경</h4>'
        f'<p>{_render_text(candidate["precursor_environment_assessment"], init)}</p>'
        f'<div class="category-chips">{category_summary}</div></article>'
        f'{_render_operational_guidance(candidate, init)}'
        '</div>'
        '</div></details>'
    )


def _render_surface_candidate(
    candidate: dict[str, Any],
    assets_by_id: dict[str, dict[str, Any]],
    overview_assets: list[dict[str, Any]],
    init: str,
    forecast_timeline: tuple[int, int, int] = DEFAULT_FORECAST_TIMELINE,
    minimap_geography: dict[str, str] | None = None,
    excluded_independent_roles: dict[str, set[str]] | None = None,
    related_multivariable_assessments: dict[str, list[dict[str, str]]] | None = None,
    region_guide_regions: list[dict[str, Any]] | None = None,
    is_initial_candidate: bool = False,
) -> str:
    signal_label = _candidate_status_label(candidate)
    contradictions = len(candidate["contradictory_evidence"])
    uncertainties = len(candidate["uncertainties"])
    evidence_count = len(candidate["evidence_blocks"])
    image_count = sum(len(block.get("asset_ids", [])) for block in candidate["evidence_blocks"])
    geography = minimap_geography or {"land_paths": "", "coastline_paths": ""}
    return (
        f'<section class="candidate-panel" id="panel-{escape(candidate["candidate_id"], quote=True)}" role="tabpanel" '
        f'aria-labelledby="tab-{escape(candidate["candidate_id"], quote=True)}" data-candidate-panel="{escape(candidate["candidate_id"], quote=True)}">'
        '<article class="candidate-workbench">'
        '<div class="hero-grid">'
        f'{_render_overview_gallery(candidate, overview_assets, init, load_initial_image=is_initial_candidate)}'
        '<aside class="decision-rail">'
        f'<div class="candidate-title-row"><div><h2>{_render_text(candidate["candidate_name"], init)}'
        f' {_render_priority_watch_badge(candidate, large=True)}{_render_priority_check_badge(candidate)}{_render_general_watch_badge(candidate)}{_render_precursor_badge(candidate)}</h2></div><span class="signal-badge">{escape(signal_label)}</span></div>'
        f'{_render_decision_status(candidate)}'
        f'{_render_operational_brief_panel(candidate, assets_by_id, init, region_guide_regions or [], geography, forecast_timeline[1])}'
        f'{_render_compact_watch_facts(candidate, init, geography)}'
        f'{_render_analysis_detail_disclosure(candidate, init)}'
        '</aside></div>'
        f'{_render_signal_chain(candidate, excluded_independent_roles, related_multivariable_assessments)}'
        '<details class="detail-zone-disclosure"><summary class="detail-zone-summary"><span><span class="section-kicker">상세분석</span><b>후속 상세분석</b><small>정량 분석값과 상세 예측장 판독 결과</small></span>'
        f'<span class="detail-summary-metrics"><i>확인 항목 {evidence_count}</i><i>이미지 {image_count}</i><i>주의사항 {contradictions + uncertainties}</i></span></summary>'
        '<div class="detail-zone-body">'
        f'{_render_evidence_matrix(candidate, assets_by_id, init)}'
        '<details class="caution-disclosure"><summary><span><b>상반된 근거 · 판독·근거 불확실성</b><small>판단을 약화시키거나 남아 있는 한계</small></span>'
        f'<span class="summary-counts"><i>{contradictions}</i> 상반 · <i>{uncertainties}</i> 불확실</span></summary><div class="caution-grid">'
        f'<section class="contradiction"><div class="section-heading compact"><div><span class="section-kicker">상반된 근거</span><h3>상반된 근거</h3></div><span class="count-badge">{contradictions}</span></div>{_render_list(candidate["contradictory_evidence"], init)}</section>'
        f'<section class="uncertainty"><div class="section-heading compact"><div><span class="section-kicker">판독·근거 불확실성</span><h3>판독·근거 불확실성</h3></div><span class="count-badge">{uncertainties}</span></div>{_render_list(candidate["uncertainties"], init)}</section>'
        '</div></details>'
        '<details class="questions-disclosure"><summary><span><b>예보관 확인사항</b><small>분석된 질문과 답변</small></span>'
        f'<span class="summary-counts"><i>{len(candidate["forecaster_question_answers"])}</i> 문항</span></summary><div class="questions-body">{_render_questions(candidate, init)}</div></details>'
        '</div></details>'
        '</article></section>'
    )


def _render_non_surface_candidate(candidate: dict[str, Any], assets_by_id: dict[str, dict[str, Any]], init: str) -> str:
    related_asset_ids = [asset_id for asset_id in candidate.get("related_image_asset_ids", []) if asset_id in assets_by_id]
    gallery_html = ""
    if related_asset_ids:
        gallery_html = (
            '<section class="non-surface-gallery"><h4>관련 이미지</h4>'
            '<div class="images">'
            + ''.join(_render_asset(asset_id, assets_by_id, str(candidate.get("candidate_name") or candidate.get("candidate_id") or "감시후보"), init) for asset_id in related_asset_ids)
            + '</div></section>'
        )
    return (
        '<article class="non-surface">'
        '<div class="non-surface-head">'
        f'<div><h3>{_render_text(candidate["candidate_name"], init)}</h3></div>'
        f'<span class="signal-badge quiet">{escape(_candidate_status_label(candidate))}</span>'
        '</div>'
        f'<p class="non-surface-time">{_render_time(candidate["effective_time_window"], init)}</p>'
        f'<p>{_render_text(candidate["final_assessment"], init)}</p>'
        f'<p class="validation-reason"><strong>표출 판단</strong> {_render_text(candidate["validation_reason"], init)}</p>'
        f'{gallery_html}'
        '</article>'
    )


def _render_html(
    product: dict[str, Any],
    forecast_timeline: tuple[int, int, int] = DEFAULT_FORECAST_TIMELINE,
    overview_by_candidate: dict[str, list[dict[str, Any]]] | None = None,
    direct_precipitation_peak_anchor_mm: float = 5.0,
    minimap_geography: dict[str, str] | None = None,
    excluded_roles_by_candidate: dict[str, dict[str, set[str]]] | None = None,
    related_roles_by_candidate: dict[str, dict[str, list[str]]] | None = None,
    peak_precip_details_by_candidate: dict[str, list[dict[str, Any]]] | None = None,
    region_guide_regions: list[dict[str, Any]] | None = None,
    step07_minority_report: dict[str, Any] | None = None,
) -> str:
    init = product["init"]
    assets_by_id = {asset["asset_id"]: asset for asset in product["assets"]}
    surfaced = product["surfaced_candidates"]
    non_surfaced = product["non_surfaced_candidates"]
    overview_by_candidate = overview_by_candidate or {}
    peak_precip_details_by_candidate = peak_precip_details_by_candidate or {}
    region_guide_regions = region_guide_regions or []
    region_guide_dialog = _render_region_guide_dialog(
        region_guide_regions, minimap_geography or {"land_paths": "", "coastline_paths": ""}
    )
    peak_anchor_text = f"{direct_precipitation_peak_anchor_mm:g}"
    surface_body = '<p class="empty">현재 분석에서 운영 표출 대상 없음</p>' if not surfaced else ''.join(
        _render_surface_candidate(
            candidate,
            assets_by_id,
            overview_by_candidate.get(candidate["candidate_id"], []),
            init,
            forecast_timeline,
            minimap_geography,
            (excluded_roles_by_candidate or {}).get(candidate["candidate_id"], {}),
            (related_roles_by_candidate or {}).get(candidate["candidate_id"], {}),
            region_guide_regions,
            is_initial_candidate=index == 0,
        )
        for index, candidate in enumerate(surfaced)
    )
    timeline_body = ''.join(
        f'<div class="top-timeline-panel{" active" if index == 0 else ""}" '
        f'data-candidate-timeline="{escape(candidate["candidate_id"], quote=True)}">'
        f'{_render_timeline(candidate, assets_by_id, init, forecast_timeline, peak_precip_details_by_candidate.get(candidate["candidate_id"], []))}</div>'
        for index, candidate in enumerate(surfaced)
    )
    non_surface_rows = ''.join(_render_non_surface_candidate(candidate, assets_by_id, init) for candidate in non_surfaced) or '<p class="empty-inline">없음</p>'
    internal_judgment_html = _render_internal_judgment_summary([*surfaced, *non_surfaced], init)
    minority_report_html = _render_step07_minority_report(step07_minority_report, product, assets_by_id, init)
    candidate_tabs = ''.join(
        f'<button id="tab-{escape(candidate["candidate_id"], quote=True)}" class="candidate-tab{" active" if index == 0 else ""}" '
        f'type="button" role="tab" aria-selected="{"true" if index == 0 else "false"}" aria-controls="panel-{escape(candidate["candidate_id"], quote=True)}" '
        f'data-candidate-target="{escape(candidate["candidate_id"], quote=True)}"><span>감시후보 {index + 1}</span>'
        f'<strong>{_render_text(candidate["candidate_name"], init)}'
        f'{_render_priority_watch_badge(candidate)}'
        f'{_render_priority_check_badge(candidate)}{_render_general_watch_badge(candidate)}{_render_precursor_badge(candidate)}</strong>'
        f'<small><em>{escape(_candidate_status_label(candidate))}</em>'
        f'<b class="candidate-tab-time">{escape(_format_compact_kst_range(init, int(candidate["effective_time_window"]["forecast_hour_start"]), int(candidate["effective_time_window"]["forecast_hour_end"])))}</b></small></button>'
        for index, candidate in enumerate(surfaced)
    )
    first_candidate = surfaced[0]["candidate_id"] if surfaced else ""
    return f'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light">
<title>AIFS 강수 감시 분석</title>
<style>
.spatial-roles-note{{margin:8px 0 5px;padding:8px 10px;background:#f7fafb;border:1px solid #d5e2e8;border-radius:9px;font-size:.69rem;color:#425968}}
.spatial-roles-note summary{{cursor:pointer;font-weight:800;color:#2a526b}}
.spatial-roles-content{{margin-top:7px;border-top:1px solid #e0e9ee;padding-top:7px}}
.spatial-roles-content p{{margin:3px 0;line-height:1.48}}
.spatial-roles-content b{{margin-right:6px;color:#25445b}}
.forecast-horizon-note{{margin:8px 0 0;padding:7px 9px;background:#fff7e5;border-left:3px solid #c58c4f;border-radius:5px;color:#715a2e;font-size:.68rem;line-height:1.55}}
:root{{--bg:#eaf0f4;--panel:#fff;--panel-2:#f7fafc;--ink:#142331;--muted:#627482;--line:#d4dfe7;--line-strong:#bccbd6;--navy:#173a56;--navy-deep:#0b2639;--blue:#2d709c;--blue-2:#e9f3f9;--teal:#177059;--ok:#12684a;--ok-bg:#e7f4ee;--partial:#755900;--partial-bg:#fff4ce;--warn:#8a4b0c;--warn-bg:#fff0df;--opp:#9b3831;--opp-bg:#fdecea;--shadow:0 12px 34px rgba(18,45,65,.08);--shadow-soft:0 5px 18px rgba(18,45,65,.06);--radius:17px}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Malgun Gothic",Arial,sans-serif;line-height:1.56;overflow-x:hidden}}button,a,summary{{-webkit-tap-highlight-color:transparent}}button{{font:inherit}}main{{width:calc(100% - 32px);max-width:2100px;margin:auto;padding:14px 0 58px}}header{{background:linear-gradient(125deg,var(--navy-deep),var(--navy));color:#fff;border-radius:18px;padding:16px 22px;box-shadow:var(--shadow);display:flex;justify-content:space-between;gap:22px;align-items:flex-end;position:relative;overflow:hidden}}header::after{{content:"";position:absolute;width:330px;height:330px;border:1px solid rgba(255,255,255,.1);border-radius:50%;right:-120px;top:-240px;pointer-events:none}}.brand{{font-size:.7rem;letter-spacing:.16em;font-weight:850;opacity:.68}}.header-copy{{flex:1 1 auto;min-width:0}}header h1{{font-size:clamp(1.45rem,2.5vw,1.9rem);margin:.18rem 0 .35rem}}.header-sub{{font-size:.86rem;color:#ccdae4;margin:0;max-width:none}}.header-actions{{display:flex;flex:0 0 auto;max-width:48%;flex-direction:column;align-items:flex-end;gap:8px;position:relative;z-index:1}}.header-meta{{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:7px;margin:0;color:#dbe7ef}}.header-meta span{{border:1px solid rgba(255,255,255,.17);border-radius:999px;padding:5px 10px;font-size:.78rem;white-space:nowrap}}.watch-condition-button{{border:1px solid rgba(255,255,255,.34);background:rgba(255,255,255,.1);color:#fff;border-radius:9px;padding:6px 10px;font-size:.75rem;font-weight:800;cursor:pointer;backdrop-filter:blur(2px)}}.watch-condition-button:hover{{background:rgba(255,255,255,.17)}}.header-guide-buttons{{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}}
.top-timeline-deck{{margin:8px 0 2px}}.top-timeline-panel{{display:none}}.top-timeline-panel.active{{display:block}}.top-timeline-panel .timeline-section{{margin:0;background:#fff;border:1px solid var(--line);border-radius:14px;padding:8px 13px 7px;box-shadow:var(--shadow-soft)}}.top-timeline-panel .section-heading{{margin-bottom:4px;display:flex;justify-content:space-between;align-items:center;gap:8px 14px;flex-wrap:wrap}}.top-timeline-panel .section-heading>div:first-child{{flex:0 0 auto}}.top-timeline-panel .section-heading h3{{margin:0}}.top-timeline-panel .timeline-legend{{margin-top:1px}}
.candidate-tabs-wrap{{position:sticky;top:0;z-index:30;padding:6px 0 6px;background:linear-gradient(var(--bg) 80%,rgba(234,240,244,0));margin-bottom:2px}}.candidate-tabs-order{{font-size:.68rem;color:var(--muted);font-weight:800;margin:0 2px 3px}}.candidate-tabs{{display:flex;gap:8px;overflow-x:auto;padding:2px;scrollbar-width:thin;scroll-snap-type:x proximity}}.candidate-tab{{flex:0 0 auto;display:grid;grid-template-columns:auto minmax(120px,1fr);grid-template-areas:"id name" "id status";align-items:center;column-gap:9px;text-align:left;border:1px solid var(--line);background:#fff;color:#314c5f;border-radius:12px;padding:7px 11px;box-shadow:0 2px 8px rgba(22,53,75,.04);cursor:pointer;scroll-snap-align:start;transition:.16s ease}}.candidate-tab>span{{grid-area:id;display:grid;place-items:center;min-width:62px;height:38px;border-radius:9px;background:#edf3f7;color:#365b73;font-size:.72rem;font-weight:900;letter-spacing:.05em}}.candidate-tab>strong{{grid-area:name;font-size:.82rem;line-height:1.25;max-width:260px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}}.priority-watch-badge{{display:inline-flex;align-items:center;border-radius:999px;padding:2px 6px;background:#fff1c2;color:#775400;border:1px solid #e5c666;font-size:.6rem;font-style:normal;font-weight:900;white-space:nowrap}}.candidate-tab.active .priority-watch-badge{{background:#ffe9a2;color:#5d4100;border-color:#f3d77a}}.priority-watch-badge-large{{font-size:.62rem;vertical-align:middle;margin-left:4px}}.priority-check-badge{{display:inline-flex;align-items:center;border-radius:999px;padding:2px 6px;background:#edf4f8;color:#234f69;border:1px solid #b9cfdb;font-size:.6rem;font-style:normal;font-weight:900;white-space:nowrap}}.candidate-tab.active .priority-check-badge{{background:rgba(255,255,255,.14);color:#f3f8fb;border-color:rgba(255,255,255,.32)}}.precursor-watch-badge{{display:inline-flex;align-items:center;border-radius:999px;padding:2px 6px;background:#f4f0f8;color:#654d78;border:1px solid #d2c4df;font-size:.6rem;font-style:normal;font-weight:900;white-space:nowrap;margin-left:3px}}.candidate-tab.active .precursor-watch-badge{{background:rgba(255,255,255,.10);color:#f5effa;border-color:rgba(255,255,255,.25)}}.candidate-tab>small{{grid-area:status;color:var(--muted);font-size:.67rem;margin-top:2px;display:flex;align-items:center;gap:6px;min-width:0}}.candidate-tab>small em{{font-style:normal;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.candidate-tab-time{{flex:0 0 auto;font-size:.61rem;line-height:1;color:#4b6a7e;background:#edf3f7;border-radius:999px;padding:3px 5px;font-variant-numeric:tabular-nums}}.candidate-tab.active .candidate-tab-time{{color:#e5f0f6;background:rgba(255,255,255,.13)}}.candidate-tab:hover{{border-color:#a7bbc9;transform:translateY(-1px)}}.candidate-tab.active{{background:var(--navy);border-color:var(--navy);color:#fff;box-shadow:0 8px 22px rgba(18,52,77,.18)}}.candidate-tab.active>span{{background:rgba(255,255,255,.13);color:#fff}}.candidate-tab.active>small{{color:#d6e3eb}}
.candidate-panel{{display:none}}.candidate-panel.active{{display:block}}.candidate-workbench{{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:18px 18px 24px}}.hero-grid{{display:grid;grid-template-columns:minmax(0,1.9fr) minmax(320px,.8fr);gap:14px;align-items:stretch}}.overview-viewer,.decision-rail{{border:1px solid var(--line);border-radius:14px;background:#fff;min-width:0;overflow:hidden}}.overview-toolbar{{display:flex;justify-content:space-between;gap:14px;align-items:flex-end;padding:12px 14px 9px;background:#f7fafc;border-bottom:1px solid var(--line)}}.overview-toolbar h3{{margin:.08rem 0 0;font-size:1rem}}.overview-note{{display:flex;align-items:center;gap:7px;margin:.34rem 0 0;font-size:.7rem;color:#5a6f7d;font-weight:700;line-height:1.35}}.overview-note-line{{display:inline-block;width:18px;height:0;border-top:2px solid #d62828;border-radius:999px;flex:0 0 auto}}.overview-tabs{{display:flex;gap:6px;overflow-x:auto;max-width:72%;padding-bottom:2px}}.overview-tab{{border:1px solid var(--line);background:#fff;color:#3f5c70;border-radius:9px;padding:6px 9px;cursor:pointer;white-space:nowrap;text-align:left}}.overview-tab strong{{display:block;font-size:.72rem}}.overview-tab small{{display:block;font-size:.61rem;color:var(--muted);margin-top:1px}}.overview-tab.active{{background:#e8f2f8;border-color:#95b5c9;color:#174f74}}.overview-stage{{background:#f1f5f7;min-height:420px;display:grid;place-items:center}}.overview-figure{{margin:0;width:100%;height:100%;min-width:0}}.overview-figure[hidden]{{display:none!important}}.overview-image-button{{display:block;width:100%;border:0;padding:0;background:#f1f5f7;cursor:zoom-in}}.overview-figure img{{display:block;width:100%;height:auto;max-height:69vh;object-fit:contain;margin:auto;background:#fff}}.overview-figure figcaption{{display:flex;justify-content:space-between;gap:12px;padding:5px 10px;background:#fff;border-top:1px solid var(--line);font-size:.68rem;color:var(--muted)}}.overview-figure figcaption strong{{color:#334f63}}.overview-empty{{display:flex;flex-direction:column;justify-content:center;min-height:420px;padding:28px;background:#f7fafc;text-align:center}}
.decision-rail{{padding:17px;display:flex;flex-direction:column}}.candidate-title-row{{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}}.candidate-id{{font-size:.69rem;letter-spacing:.07em;color:var(--muted);font-weight:900}}h2{{font-size:clamp(1.3rem,2vw,1.62rem);line-height:1.28;margin:.12rem 0 0}}h3{{margin:.1rem 0 .42rem;font-size:1.02rem}}h4{{margin:10px 0 4px;font-size:.9rem}}p{{margin:.4rem 0}}.signal-badge{{display:inline-flex;align-items:center;background:#e7f1f7;color:#174f74;border:1px solid #c5dce9;border-radius:999px;padding:5px 9px;font-weight:850;font-size:.72rem;white-space:nowrap}}.signal-badge.quiet{{background:#f1f3f4;color:#64737d;border-color:#dce3e8}}.core-assessment{{background:linear-gradient(135deg,#edf5fa,#f8fbfd);border-left:4px solid var(--blue);border-radius:10px;padding:13px 14px;margin:15px 0 12px}}.core-assessment p{{font-size:.9rem;line-height:1.62;margin-bottom:0}}.section-kicker,.mini-label{{display:block;font-size:.66rem;letter-spacing:.085em;color:#37637f;font-weight:900}}.decision-metrics{{display:grid;grid-template-columns:1fr;gap:7px;margin-bottom:10px}}.decision-metrics>div{{border:1px solid var(--line);border-radius:9px;padding:9px 10px;background:#fbfcfd}}.decision-metrics span{{display:block;font-size:.64rem;color:var(--muted);font-weight:850;letter-spacing:.04em}}.decision-metrics strong{{display:block;font-size:.79rem;margin-top:2px;overflow-wrap:anywhere}}.alignment-card{{margin-top:auto;border:1px solid var(--line);border-radius:10px;padding:10px 11px;background:#f7fafb}}.alignment-card>span{{font-size:.65rem;color:var(--muted);font-weight:850}}.alignment-card>strong{{display:block;font-size:.83rem;margin:4px 0;color:#46677b}}.alignment-card.ok>strong{{color:var(--ok)}}.alignment-card>small{{display:block;font-size:.7rem;color:var(--muted)}}.alignment-provenance{{margin-top:8px;border-top:1px solid var(--line)}}.alignment-provenance>summary{{padding:6px 0 0;font-size:.68rem;color:#45657a;font-weight:800}}.alignment-provenance-body{{display:flex;flex-direction:column;gap:2px;padding-top:4px;font-size:.66rem;color:var(--muted)}}.alignment-provenance-body b{{color:#405969;margin-right:4px}}.alignment-check-link{{border:0;background:transparent;padding:0 2px;color:#135f8e;font-size:inherit;font-weight:750;text-decoration:underline;text-underline-offset:2px;cursor:pointer}}
.section-heading{{display:flex;justify-content:space-between;align-items:flex-end;gap:14px;margin-bottom:8px}}.section-heading>p,.detail-zone-heading>p{{font-size:.72rem;color:var(--muted);margin:0;text-align:right}}.timeline-peak-region{{display:flex;align-items:center;justify-content:flex-end;gap:6px 8px;min-width:0;max-width:100%;margin-left:auto;text-align:right;white-space:normal;flex:1 1 520px;flex-wrap:wrap}}.timeline-peak-region>span{{font-size:.66rem;color:var(--muted);font-weight:750;flex:0 0 auto}}.timeline-peak-region strong{{display:flex;gap:6px 8px;align-items:center;justify-content:flex-end;flex-wrap:wrap;min-width:0;max-width:100%;font-size:.76rem;color:#1f648d;font-weight:900;overflow:visible}}.timeline-peak-summary-item{{position:relative;display:inline-flex;align-items:center;gap:4px;white-space:nowrap;outline:none;max-width:100%}}.timeline-peak-summary-number{{display:inline-flex;width:16px;height:16px;border-radius:50%;align-items:center;justify-content:center;background:#b67d12;color:#fff;font-size:.55rem;font-style:normal;font-weight:900;line-height:1;box-shadow:0 0 0 1px #fff,0 1px 2px rgba(80,59,18,.18)}}.timeline-peak-region-link{{appearance:none;border:0;background:transparent;padding:0;margin:0;color:inherit;font:inherit;font-weight:inherit;line-height:inherit;cursor:pointer;text-decoration:none}}.timeline-peak-region-link:hover,.timeline-peak-region-link:focus-visible{{text-decoration:underline;text-underline-offset:2px;color:#174f74}}.timeline-peak-summary-item.peak-linked-hover .timeline-peak-summary-number{{background:#8d5d08;box-shadow:0 0 0 2px rgba(182,125,18,.22),0 1px 3px rgba(80,59,18,.24)}}.section-heading.compact{{align-items:center}}.timeline-section{{margin:15px 2px 0;border-top:1px solid var(--line);padding:14px 5px 0}}.timeline-legend{{display:flex;flex-wrap:wrap;gap:7px 13px;align-items:center;margin:3px 0 0;color:#526b7c;font-size:.7rem}}.timeline-legend>span{{display:inline-flex;align-items:center;gap:5px}}.legend-line{{display:inline-block;width:25px;height:7px;border-radius:999px;background:#cfd8df}}.legend-line.watch{{background:var(--blue)}}.legend-peak{{display:inline-flex;width:15px;height:15px;border-radius:50%;align-items:center;justify-content:center;background:#b67d12;color:#fff;font-size:.5rem;font-style:normal;font-weight:900;line-height:1}}.legend-peak::before{{content:"1"}}.legend-evidence{{display:inline-block;width:8px;height:8px;border-radius:50%;background:#2f6d93}}.timeline-scale{{position:relative;height:82px;margin:3px 3px 0;overflow:visible}}.timeline-base,.timeline-watch-band{{position:absolute;top:18px;height:8px;border-radius:999px}}.timeline-base{{left:0;right:0;background:#d5dde3;box-shadow:inset 0 0 0 1px #c6d0d7}}.timeline-watch-band{{background:linear-gradient(90deg,#2d6f99,#4590ba);min-width:3px;z-index:2}}.timeline-minor-tick{{position:absolute;top:15px;width:1px;height:14px;background:#9faeb9;transform:translateX(-.5px);z-index:3;opacity:.7}}.timeline-axis-label{{position:absolute;top:34px;transform:translateX(-50%);display:flex;flex-direction:column;align-items:center;gap:0;width:74px;color:#657887;font-variant-numeric:tabular-nums;line-height:1.12;text-align:center;white-space:nowrap}}.timeline-axis-label.axis-start{{transform:none;align-items:flex-start;text-align:left}}.timeline-axis-label.axis-end{{transform:translateX(-100%);align-items:flex-end;text-align:right}}.timeline-axis-fhour{{font-size:.61rem;font-weight:850;color:#526b7c}}.timeline-axis-utc{{font-size:.63rem;color:#234e68}}.timeline-axis-kst{{font-size:.61rem;color:#6a7d8a}}.timeline-peak-marker{{position:absolute;top:2px;transform:translateX(-50%);z-index:6;width:18px;height:18px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:#b67d12;color:#fff;font-size:.61rem;font-weight:900;line-height:1;box-shadow:0 0 0 2px #fff,0 1px 3px rgba(80,59,18,.22);cursor:help}}.timeline-peak-marker::after{{content:attr(data-tooltip);position:absolute;left:50%;bottom:calc(100% + 7px);transform:translateX(-50%);background:#17384f;color:#fff;padding:5px 7px;border-radius:6px;font-size:.62rem;font-weight:700;line-height:1.25;white-space:nowrap;opacity:0;pointer-events:none;transition:opacity .12s ease;box-shadow:0 3px 10px rgba(18,45,63,.18)}}.timeline-peak-marker:hover::after,.timeline-peak-marker:focus::after{{opacity:1}}.timeline-peak-marker.peak-linked-hover{{background:#8d5d08;box-shadow:0 0 0 3px rgba(182,125,18,.22),0 1px 4px rgba(80,59,18,.28)}}.timeline-evidence-marker{{position:absolute;top:23px;width:9px;height:9px;border:2px solid #fff;background:#2f6d93;border-radius:50%;transform:translateX(-50%);box-shadow:0 0 0 1px #2f6d93;z-index:4}}.timeline-range-summary{{display:flex;flex-wrap:wrap;justify-content:space-between;gap:4px 12px;margin:1px 3px 0;font-size:.73rem;color:var(--muted)}}.timeline-range-summary strong{{color:#1f648d;overflow-wrap:anywhere}}.timeline-notes{{margin-top:3px}}.timeline-note{{font-size:.66rem;color:var(--muted);margin:1px 3px 0;line-height:1.35}}
.signal-chain-section{{margin-top:14px;padding:14px;border:1px solid var(--line);border-radius:12px;background:#f8fafb}}.signal-chain{{display:grid;grid-template-columns:repeat(11,minmax(0,auto));align-items:center;justify-content:space-between;gap:6px}}.signal-node{{min-width:104px;min-height:82px;border:1px solid var(--line);background:#fff;border-radius:11px;padding:8px;display:flex;flex-direction:column;align-items:center;text-align:center;justify-content:center;box-shadow:0 2px 8px rgba(20,45,62,.03)}}.signal-node-button{{font:inherit;color:inherit;cursor:pointer;transition:transform .14s ease,box-shadow .14s ease,border-color .14s ease}}.signal-node-button:hover{{transform:translateY(-1px);box-shadow:0 6px 14px rgba(20,45,62,.09)}}.signal-node-button.active-filter{{outline:2px solid #4e8eb4;outline-offset:2px;box-shadow:0 5px 14px rgba(25,84,117,.12)}}.signal-node-button:focus-visible{{outline:3px solid #d59b1b;outline-offset:2px}}.signal-node-short{{display:grid;place-items:center;width:31px;height:31px;border-radius:50%;background:#eef2f4;color:#61717d;font-size:.65rem;font-weight:900;margin-bottom:5px}}.signal-node strong{{font-size:.73rem;line-height:1.25}}.signal-node small{{font-size:.62rem;color:var(--muted);margin-top:2px}}.signal-node.signal-support{{border-color:#b9dccb;background:var(--ok-bg)}}.signal-node.signal-support .signal-node-short{{background:#d4ecdf;color:var(--ok)}}.signal-node.signal-dual{{border-color:#d8bf95;background:linear-gradient(135deg,var(--ok-bg) 0 49%,var(--warn-bg) 51% 100%)}}.signal-node.signal-dual .signal-node-short{{background:linear-gradient(135deg,#d4ecdf 0 49%,#f8d7b6 51% 100%);color:#5d5a37;border:1px solid #d7c09a}}.signal-node.signal-mixed{{border-color:#e6d79e;background:var(--partial-bg)}}.signal-node.signal-mixed .signal-node-short{{background:#f3e7ae;color:var(--partial)}}.signal-node.signal-limit{{border-color:#e3b485;background:var(--warn-bg)}}.signal-node.signal-limit .signal-node-short{{background:#f8d7b6;color:var(--warn)}}.signal-node.signal-context{{border-color:#bdd7ea;background:#eaf3fa}}.signal-node.signal-context .signal-node-short{{background:#d8eaf6;color:#245f8f}}.signal-node.signal-opposing{{border-color:#e7bbb6;background:var(--opp-bg)}}.signal-node.signal-opposing .signal-node-short{{background:#f4d5d1;color:var(--opp)}}.signal-node.signal-neutral{{border-color:#d5dee4;background:#f3f5f6}}.signal-node.signal-neutral .signal-node-short{{background:#e5eaed;color:#65747e}}.signal-arrow{{color:#8aa0ae;font-size:1rem;font-weight:800}}.signal-chain-legend{{display:flex;align-items:center;justify-content:flex-end;gap:5px 10px;flex-wrap:wrap;margin-top:8px;padding-top:7px;border-top:1px solid #dfe7ec}}.signal-legend-item{{display:inline-flex;align-items:center;gap:5px;font-size:.62rem;font-weight:800;color:#5c6f7c;white-space:nowrap}}.signal-legend-item i{{display:inline-block;width:9px;height:9px;border-radius:50%;border:1px solid transparent}}.signal-legend-item.signal-support i{{background:#d4ecdf;border-color:#9dceb7}}.signal-legend-item.signal-dual i{{background:linear-gradient(135deg,#d4ecdf 0 49%,#f8d7b6 51% 100%);border-color:#d0b88e}}.signal-legend-item.signal-mixed i{{background:#f3e7ae;border-color:#dccb75}}.signal-legend-item.signal-limit i{{background:#f8d7b6;border-color:#dda86f}}.signal-legend-item.signal-context i{{background:#d8eaf6;border-color:#9fc7df}}.signal-legend-item.signal-opposing i{{background:#f4d5d1;border-color:#dda7a2}}.signal-legend-item.signal-neutral i{{background:#e5eaed;border-color:#c8d2d8}}
.signal-summary-section{{margin-top:14px}}.signal-summary-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.signal-summary-grid>article{{border:1px solid var(--line);border-radius:11px;background:#fff;padding:12px 14px;min-width:0}}.signal-summary-grid p{{font-size:.84rem}}.category-chips,.role-chips,.evidence-roles{{display:flex;flex-wrap:wrap;gap:5px}}.category-chips{{margin-top:8px}}.category-chip,.role-chip{{display:inline-flex;border-radius:999px;padding:3px 7px;font-size:.68rem;background:#e9f0f4;color:#38566a}}.category-chip.muted{{background:#f0f2f3;color:#697783}}
.detail-zone{{margin-top:18px;border-top:4px solid #d9e6ee;padding-top:18px}}.detail-zone-heading{{display:flex;justify-content:space-between;align-items:flex-end;gap:14px;margin-bottom:10px}}.detail-zone-heading h3{{font-size:1.12rem}}.matrix-section{{margin-top:0}}.evidence-matrix{{border:1px solid var(--line);border-radius:10px;overflow:hidden}}.matrix-row{{display:grid;grid-template-columns:145px 220px 125px minmax(260px,1fr);min-width:0}}.matrix-row>*{{padding:8px 9px;min-width:0;overflow-wrap:anywhere}}.matrix-header{{background:#edf3f6;color:#40596b;font-size:.69rem;font-weight:850;letter-spacing:.03em;border-bottom:1px solid var(--line)}}.matrix-evidence{{border-top:0;border-bottom:1px solid var(--line)}}.matrix-evidence:last-child{{border-bottom:0}}.matrix-summary{{list-style:none;cursor:pointer;background:#fff;transition:background .15s ease}}.matrix-summary::-webkit-details-marker{{display:none}}.matrix-summary::marker{{content:""}}.matrix-summary:hover{{background:#f7fafc}}.matrix-evidence[open]>.matrix-summary{{background:#f1f7fa}}.matrix-chevron{{display:inline-block;margin-right:6px;font-size:1rem;line-height:1;transition:transform .15s ease}}.matrix-evidence[open] .matrix-chevron{{transform:rotate(90deg)}}.matrix-evidence>.evidence-body{{margin:0 10px 10px}}.matrix-cell{{font-size:.78rem}}.check-id{{font-size:.71rem;color:#526c7e;font-weight:780}}.expected-cell{{line-height:1.45}}.assessment-badge{{display:inline-flex;align-items:center;border-radius:999px;padding:3px 7px;font-size:.68rem;font-weight:850;white-space:nowrap}}.assessment-badge.ok{{background:var(--ok-bg);color:var(--ok)}}.assessment-badge.partial{{background:var(--partial-bg);color:var(--partial)}}.assessment-badge.warn{{background:var(--warn-bg);color:var(--warn)}}.assessment-badge.opp{{background:var(--opp-bg);color:var(--opp)}}.assessment-badge.muted{{background:#eef1f3;color:#66747f}}
.evidence-body{{background:#f8fafc;border:1px solid var(--line);border-radius:11px;padding:14px;margin:0 0 10px}}.expected-signal{{border-left:3px solid #91aabc;padding-left:11px}}.expected-signal>span{{font-size:.68rem;color:var(--muted);font-weight:850;letter-spacing:.06em}}.expected-signal p{{margin:3px 0 7px;font-size:.82rem}}.assessment-line{{font-size:.8rem;color:#405a6c}}.evidence-columns{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.evidence-columns>section{{min-width:0}}.counter-evidence{{background:#fff8f7;border-radius:9px;padding:0 10px}}.check-uncertainty{{margin-top:6px}}ul{{padding-left:19px;margin:5px 0}}li{{margin:4px 0;font-size:.82rem}}.empty-inline{{color:#768591;font-size:.8rem}}.quant-series-wrap{{margin:6px 0 12px}}.quant-series-wrap>h5{{margin:0 0 7px;font-size:.76rem;color:#40596b}}.quant-series-card{{border:1px solid var(--line);border-radius:10px;background:#fff;padding:10px;margin:0 0 9px}}.quant-series-head{{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:5px}}.quant-series-head strong{{display:block;font-size:.78rem}}.quant-series-head small{{display:block;color:var(--muted);font-size:.66rem;margin-top:1px}}.quant-series-region-label,.quant-series-region-single{{font-size:.7rem;color:#40596b;font-weight:750}}.quant-series-region{{margin-left:5px;max-width:230px;border:1px solid var(--line);border-radius:7px;background:#fff;padding:5px 7px;font:inherit;color:#1d3444}}.quant-series-stage{{position:relative;width:100%;min-height:210px}}.quant-series-svg{{display:block;width:100%;height:auto;min-height:210px;overflow:visible}}.quant-series-tooltip{{position:absolute;z-index:3;pointer-events:none;background:#173244;color:#fff;border-radius:7px;padding:6px 8px;font-size:.68rem;line-height:1.35;box-shadow:0 4px 14px rgba(20,45,60,.2);max-width:230px}}.quant-series-note{{margin:2px 0 0;color:var(--muted);font-size:.66rem}}.quant-series-grid{{stroke:#dbe4e9;stroke-width:1}}.quant-series-axis{{stroke:#8196a4;stroke-width:1}}.quant-series-line{{fill:none;stroke:#286d99;stroke-width:2.5;stroke-linejoin:round;stroke-linecap:round}}.quant-series-point{{fill:#fff;stroke:#286d99;stroke-width:2.3}}.quant-series-point.has-image{{cursor:pointer;fill:#dcecf5}}.quant-series-label{{fill:#627786;font-size:11px}}.quant-table-wrap{{overflow-x:auto;border-radius:9px}}.quant-table{{border-collapse:separate;border-spacing:0;width:100%;min-width:690px;font-size:.77rem;border:1px solid var(--line);border-radius:9px;overflow:hidden}}.quant-table th,.quant-table td{{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}.quant-table th{{background:#edf3f6;color:#40596b;font-size:.68rem}}.quant-table tr:last-child td{{border-bottom:0}}.nowrap{{white-space:nowrap}}.num{{font-variant-numeric:tabular-nums}}.quant-feature-button{{border:0;padding:0;background:transparent;color:#135f8e;font:inherit;font-weight:750;text-decoration:underline;text-underline-offset:2px;cursor:pointer;text-align:left;min-height:28px}}.feature-label small,.quant-feature-button small,.resolved-location{{display:block;font-size:.68rem;font-weight:400;color:var(--muted)}}.images{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin-top:11px}}.evidence-figure{{margin:0;min-width:0}}.evidence-image-button{{display:block;width:100%;border:0;padding:0;background:transparent;cursor:zoom-in;border-radius:8px}}.evidence-figure img{{display:block;width:100%;height:auto;border:1px solid var(--line);border-radius:8px;background:#fff}}figcaption{{font-size:.7rem;color:var(--muted);margin-top:3px}}
.caution-disclosure,.questions-disclosure{{margin-top:10px;border:1px solid var(--line);border-radius:11px;background:#fff;overflow:hidden}}.caution-disclosure>summary,.questions-disclosure>summary{{display:flex;justify-content:space-between;gap:14px;align-items:center;padding:11px 13px;cursor:pointer;list-style:none;background:#f8fafb}}.caution-disclosure>summary::-webkit-details-marker,.questions-disclosure>summary::-webkit-details-marker{{display:none}}.caution-disclosure>summary span:first-child,.questions-disclosure>summary span:first-child{{display:flex;flex-direction:column}}.caution-disclosure>summary b,.questions-disclosure>summary b{{font-size:.84rem}}.caution-disclosure>summary small,.questions-disclosure>summary small{{font-size:.68rem;color:var(--muted)}}.summary-counts{{font-size:.69rem;color:var(--muted);white-space:nowrap}}.summary-counts i{{font-style:normal;font-weight:850;color:#405e73}}.caution-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:10px}}.caution-grid>section{{border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}}.caution-grid .contradiction{{background:#fff8f7;border-color:#ecd7d3}}.caution-grid .uncertainty{{background:#fffaf2;border-color:#e9ddcc}}.count-badge{{display:inline-grid;place-items:center;min-width:26px;height:26px;padding:0 6px;background:#fff;border:1px solid currentColor;border-radius:999px;font-size:.7rem;font-weight:850}}.questions-body{{padding:2px 12px 10px}}.forecaster-question{{border-top:1px solid var(--line)}}.forecaster-question:first-child{{border-top:0}}.forecaster-question summary{{display:grid;grid-template-columns:140px minmax(0,1fr);gap:9px;padding:11px 3px;font-weight:750;align-items:start;cursor:pointer}}.question-id{{font-size:.7rem;color:var(--muted);font-weight:800}}.question-body{{background:#f7fafc;border:1px solid var(--line);border-radius:9px;padding:12px 14px;margin:0 0 9px}}.question-answer{{margin-top:0;font-size:.82rem}}.question-trace{{display:flex;align-items:center;gap:7px;flex-wrap:wrap;font-size:.71rem;color:var(--muted)}}.question-minority-count{{font-weight:800;color:#7a5313;background:#fff4ce;border-radius:999px;padding:3px 6px}}.minority-jump{{border:1px solid #d8c485;background:#fffaf0;color:#6d4f12;border-radius:999px;padding:4px 7px;font-size:.66rem;font-weight:850;cursor:pointer}}.minority-jump:hover{{background:#fff2c7}}
.non-surface-summary{{background:#fff;border:1px solid var(--line);border-radius:13px;padding:0 16px;margin-top:18px;box-shadow:var(--shadow-soft)}}.non-surface-summary>summary{{padding:13px 0;font-weight:850;cursor:pointer}}.non-surface{{border-top:1px solid var(--line);padding:14px 0}}.non-surface-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:14px}}.non-surface-head h3{{display:inline;margin-left:7px;font-size:.96rem}}.non-surface-time,.validation-reason{{font-size:.77rem;color:var(--muted)}}.empty{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:20px}}
.internal-judgment-summary{{margin-top:12px;border:1px solid #d8e1e7;border-radius:13px;background:#f8fafb;box-shadow:var(--shadow-soft);overflow:hidden}}.internal-judgment-summary>summary{{display:flex;justify-content:space-between;gap:12px;align-items:center;list-style:none;cursor:pointer;padding:12px 15px;color:#526976}}.internal-judgment-summary>summary::-webkit-details-marker{{display:none}}.internal-judgment-summary>summary>span:first-child{{display:flex;flex-direction:column}}.internal-judgment-summary>summary b{{font-size:.82rem;color:#3a596c}}.internal-judgment-summary>summary small{{font-size:.65rem;color:#7b8b95}}.internal-summary-note{{font-size:.64rem;font-weight:850;color:#71838f;background:#edf2f5;border-radius:999px;padding:4px 8px}}.internal-judgment-body{{padding:0 15px 14px}}.internal-judgment-note{{margin:0 0 10px;padding:9px 10px;border-radius:9px;background:#eef3f6;color:#647784;font-size:.7rem;line-height:1.5}}.internal-judgment-item{{padding:11px 0;border-top:1px solid #dce4e9}}.internal-judgment-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}}.internal-judgment-head h3{{display:inline;margin:0 0 0 6px;font-size:.84rem;color:#34566b}}.internal-disposition{{font-size:.62rem;font-weight:850;color:#607784;background:#edf2f5;border-radius:999px;padding:3px 6px}}.internal-judgment-badges{{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}}.internal-judgment-badges span{{display:flex;gap:5px;align-items:center;border:1px solid #d8e1e7;border-radius:8px;background:#fff;padding:4px 7px}}.internal-judgment-badges small{{font-size:.6rem;color:#74858f}}.internal-judgment-badges b{{font-size:.66rem;color:#3d5b6d}}.internal-judgment-item p{{margin:7px 0 0;font-size:.73rem;line-height:1.52;color:#526a78}}.internal-judgment-reason strong{{color:#405f72}}
dialog{{width:min(1680px,98vw);height:96dvh;max-width:98vw;max-height:96dvh;border:0;border-radius:13px;padding:0;overflow:hidden;box-shadow:0 24px 80px #0008}}dialog::backdrop{{background:#071724b5}}.modal-content{{box-sizing:border-box;height:100%;padding:10px 12px 12px;display:block;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;background:#fff;scrollbar-gutter:stable}}.modal-header{{position:sticky;top:-10px;display:flex;justify-content:space-between;gap:14px;align-items:center;background:#fff;padding:12px 0 8px;z-index:3}}.modal-header h2{{font-size:1.05rem;margin:0}}.modal-header .question-trace{{margin:2px 0 0}}.modal-close{{min-width:44px;min-height:40px;border:1px solid #8499aa;background:#fff;border-radius:8px;padding:6px 12px;cursor:pointer}}.modal-note{{margin:0 0 6px;background:#eef5f8;padding:6px 9px;border-radius:8px;color:#405969;font-size:.74rem}}#modal-time{{margin:0 0 6px;font-size:.72rem}}.modal-image-grid{{display:block;width:100%;min-height:0;overflow:visible;background:#f7f9fa;border-radius:8px}}.modal-image-grid img{{display:block;width:100%;height:auto;max-width:100%;max-height:none;object-fit:contain;border-radius:6px;cursor:zoom-out}}.modal-image-grid.focus-environment-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding:10px;box-sizing:border-box;align-items:start}}.focus-environment-figure{{margin:0;border:1px solid #d5e0e7;border-radius:10px;background:#fff;overflow:hidden;min-width:0}}.focus-environment-figure img{{cursor:default;background:#fff}}.focus-environment-figure figcaption{{padding:7px 9px;border-top:1px solid #e2e9ed;font-size:.7rem;font-weight:850;color:#294f66;background:#f8fafb}}
.candidate-tab:focus-visible,.overview-tab:focus-visible,.alignment-check-link:focus-visible,.quant-feature-button:focus-visible,.overview-image-button:focus-visible,.evidence-image-button:focus-visible,.attention-filter-toggle:focus-visible,.watch-condition-button:focus-visible,.watch-condition-close:focus-visible,.system-guide-close:focus-visible,.modal-close:focus-visible,summary:focus-visible{{outline:3px solid #d59b1b;outline-offset:2px}}
@media(max-width:1050px){{.hero-grid{{grid-template-columns:1fr}}.overview-stage,.overview-empty{{min-height:360px}}.decision-metrics{{grid-template-columns:repeat(3,1fr)}}.alignment-card{{margin-top:4px}}.signal-chain{{grid-template-columns:repeat(6,minmax(110px,1fr));gap:7px}}.signal-arrow{{display:none}}}}
@media(max-width:760px){{main{{width:100%;padding:10px 9px 40px}}header{{border-radius:13px;padding:17px 15px;display:block}}.header-meta{{justify-content:flex-start;margin-top:9px}}.header-sub{{font-size:.78rem}}.top-timeline-deck{{margin-top:9px}}.top-timeline-panel .timeline-section{{padding:8px 9px 7px}}.candidate-tabs-wrap{{padding-top:7px}}.candidate-tab{{grid-template-columns:auto minmax(110px,1fr);padding:7px 9px}}.candidate-workbench{{border-radius:13px;padding:10px}}.map-modal-controls{{flex-direction:column;align-items:flex-start}}.map-modal-stage{{min-height:240px}}.overview-toolbar{{align-items:flex-start;flex-direction:column;gap:7px}}.overview-tabs{{max-width:100%;width:100%}}.overview-stage,.overview-empty{{min-height:240px}}.overview-figure img{{max-height:none}}.overview-figure figcaption{{flex-direction:column;gap:1px}}.decision-rail{{padding:13px}}.candidate-title-row{{flex-direction:column}}.decision-metrics{{grid-template-columns:1fr}}.section-heading,.detail-zone-heading{{align-items:flex-start;flex-direction:column;gap:2px}}.section-heading>p,.detail-zone-heading>p{{text-align:left}}.top-timeline-panel .section-heading{{align-items:stretch;gap:4px}}.top-timeline-panel .timeline-peak-region{{flex:none;width:100%;margin-left:0;justify-content:flex-start;text-align:left}}.timeline-peak-region{{text-align:left;white-space:normal;flex-wrap:wrap;gap:3px 6px}}.timeline-peak-region strong{{max-width:100%;flex-wrap:wrap;gap:4px 7px}}.top-timeline-panel .timeline-peak-region strong{{justify-content:flex-start}}.timeline-scale{{height:78px;margin:2px 1px 0}}.timeline-axis-label{{width:59px;top:33px;gap:0}}.timeline-axis-fhour{{font-size:.56rem}}.timeline-axis-utc{{font-size:.56rem}}.timeline-axis-kst{{font-size:.54rem}}.timeline-range-summary{{font-size:.68rem}}.signal-chain{{grid-template-columns:repeat(3,1fr)}}.signal-node{{min-width:0;min-height:76px}}.signal-summary-grid,.caution-grid,.evidence-columns{{grid-template-columns:1fr}}.evidence-matrix{{border:0;border-radius:0;overflow:visible}}.matrix-header{{display:none}}.matrix-evidence{{border:0;margin:0 0 7px}}.matrix-summary.matrix-row{{display:block;border:1px solid var(--line);border-radius:10px;margin:0;background:#fbfcfd}}.matrix-evidence[open]>.matrix-summary{{border-radius:10px 10px 0 0}}.matrix-row>*{{display:grid;grid-template-columns:84px minmax(0,1fr);gap:7px;padding:7px 9px;border-bottom:1px solid #e7edf1;font-size:.78rem}}.matrix-row>*:last-child{{border-bottom:0}}.matrix-row>*::before{{content:attr(data-label);font-size:.64rem;color:var(--muted);font-weight:850}}.matrix-evidence>.evidence-body{{margin:0;border-top:0;border-radius:0 0 10px 10px}}.quant-series-head{{align-items:flex-start;flex-direction:column}}.quant-series-region{{max-width:100%}}.quant-series-stage{{min-height:190px}}.quant-table-wrap{{overflow:visible}}.quant-table{{display:block;min-width:0;border:0;background:transparent}}.quant-table thead{{display:none}}.quant-table tbody{{display:block}}.quant-table tr{{display:block;border:1px solid var(--line);border-radius:9px;margin:0 0 8px;background:#fff;padding:4px 0}}.quant-table td{{display:grid;grid-template-columns:78px minmax(0,1fr);gap:7px;border-bottom:1px solid #e7edf1;padding:7px 8px;white-space:normal}}.quant-table td:last-child{{border-bottom:0}}.quant-table td::before{{content:attr(data-label);font-size:.64rem;color:var(--muted);font-weight:850}}.forecaster-question summary{{grid-template-columns:1fr;gap:4px}}dialog{{width:99vw;height:97dvh;max-width:99vw;max-height:97dvh;border-radius:11px}}.modal-content{{height:100%;padding:8px;overflow-y:auto}}.modal-header{{top:-8px}}.modal-note{{font-size:.68rem}}.modal-image-grid.focus-environment-grid{{grid-template-columns:1fr;padding:6px;gap:7px}}}}
@media(max-width:430px){{body{{font-size:.94rem}}main{{padding-left:6px;padding-right:6px}}.candidate-tab>span{{min-width:54px}}.signal-chain{{grid-template-columns:repeat(2,1fr)}}.overview-tab{{padding:5px 7px}}.timeline-axis-label{{width:53px}}.matrix-row>*{{grid-template-columns:73px minmax(0,1fr)}}.quant-table td{{grid-template-columns:70px minmax(0,1fr)}}}}

/* v12 progressive-disclosure UI overrides */
.candidate-workbench{{padding:14px 14px 18px}}.hero-grid{{grid-template-columns:minmax(0,1.55fr) minmax(440px,1fr);gap:10px;align-items:start}}.overview-toolbar{{padding:8px 10px 7px;align-items:center}}.overview-toolbar h3{{font-size:.96rem}}.overview-note{{font-size:.66rem;margin-top:.26rem}}.overview-note-line{{width:16px}}.overview-tabs{{max-width:72%}}.overview-stage{{min-height:0;display:block;background:#fff;padding:0}}.overview-image-frame{{position:relative;display:block;width:100%;min-height:0;background:#fff;overflow:hidden;padding:0}}.overview-figure img{{display:block;width:100%;height:auto;max-height:none;object-fit:contain;background:#fff}}.overview-load-error{{display:none;position:absolute;inset:0;place-items:center;align-content:center;gap:3px;background:#f4f7f9;color:#6b7c88;text-align:center;padding:20px}}.overview-load-error b{{font-size:.86rem}}.overview-load-error small{{font-size:.7rem}}.overview-image-frame.load-error .overview-load-error{{display:grid}}.overview-image-frame.load-error img{{visibility:hidden}}.decision-rail{{padding:14px}}.decision-status-grid{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:7px;margin:12px 0}}.decision-status{{border:1px solid var(--line);border-radius:9px;padding:8px 9px;background:#f7fafb}}.decision-status span{{display:block;font-size:.63rem;color:var(--muted);font-weight:800}}.decision-status strong{{display:block;font-size:.82rem;margin-top:2px}}.decision-status.ok{{background:var(--ok-bg);border-color:#c7e3d7}}.decision-status.partial{{background:var(--partial-bg);border-color:#eadca9}}.decision-status.warn,.decision-status.opp{{background:var(--warn-bg);border-color:#edd0ad}}.key-environment-focus{{margin:2px 0 9px;border:1px solid #bfd8c9;border-left:4px solid var(--ok);border-radius:11px;background:#f4faf7;padding:10px 11px}}.key-environment-focus-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}}.key-environment-focus-head strong{{display:block;margin-top:2px;font-size:.86rem;color:#1f5c43}}.key-environment-focus-state{{font-size:.62rem;font-weight:850;color:#3f6d59;background:#e3f1e8;border-radius:999px;padding:4px 7px;white-space:nowrap}}.key-environment-focus-summary-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;margin-top:8px}}.key-environment-focus-summary-item{{border:1px solid #d7e6dc;border-radius:9px;background:#fff;padding:8px 9px}}.key-environment-focus-summary-item span{{display:block;font-size:.61rem;font-weight:850;color:#648073}}.key-environment-focus-summary-item strong{{display:block;font-size:.76rem;line-height:1.38;color:#27493a;margin-top:2px}}.key-environment-focus-detail{{margin-top:8px;border-top:1px solid #dcebe2;padding-top:7px}}.key-environment-focus-detail>summary{{list-style:none;cursor:pointer;font-size:.66rem;font-weight:850;color:#2c6b4c}}.key-environment-focus-detail>summary::-webkit-details-marker{{display:none}}.key-environment-focus-detail-body{{display:flex;flex-direction:column;gap:8px;padding-top:7px}}.key-environment-focus-components{{display:flex;gap:5px;flex-wrap:wrap}}.key-environment-focus-components span{{font-size:.63rem;font-weight:800;color:#315846;background:#fff;border:1px solid #d4e5db;border-radius:999px;padding:4px 7px}}.key-environment-focus-actions{{display:flex;gap:6px;flex-wrap:wrap}}.key-environment-focus-actions button{{border-radius:8px;padding:5px 8px;font-size:.64rem;font-weight:850;cursor:pointer}}.key-environment-location-button{{border:1px solid #7eaa93;background:#e7f3ec;color:#245c42}}.key-environment-location-button:hover{{background:#dceee4}}.key-environment-map-button.inline-map-trigger{{border:1px solid #5f9878;background:#dff0e7;color:#1f5b3d;border-radius:8px;padding:5px 8px;font-size:.64rem;font-weight:850}}.key-environment-map-button.inline-map-trigger:hover{{background:#d2e9dd;border-color:#4b8468}}.key-environment-focus-note{{margin:7px 0 0;font-size:.62rem;line-height:1.42;color:#63766d}}.environment-localization-region-grid rect{{fill:rgba(239,151,44,.34);stroke:#b96f17;stroke-width:.5;vector-effect:non-scaling-stroke}}.environment-localization-candidate{{fill:none;stroke:#2b6f99;stroke-width:1.8;stroke-dasharray:5 3;vector-effect:non-scaling-stroke}}.alignment-check-link{{border:1px solid #c7d5de;background:#fff;color:#285977}}.alignment-check-link:hover{{background:#f1f7fa}}.core-assessment{{margin:8px 0;padding:12px 13px}}.core-assessment p{{font-size:.86rem;line-height:1.58}}.watch-facts{{display:grid;grid-template-columns:1fr;gap:6px}}.watch-facts>div{{display:grid;grid-template-columns:46px minmax(0,1fr);gap:8px;align-items:start;border-top:1px solid var(--line);padding-top:7px}}.watch-facts span{{font-size:.64rem;color:var(--muted);font-weight:850}}.watch-facts strong{{font-size:.74rem;line-height:1.42}}.signal-chain-section{{margin-top:12px}}.signal-chain-section .section-heading p{{display:none}}.signal-chain{{padding:8px}}.signal-node{{min-height:68px}}.signal-node-short{{width:28px;height:28px}}.signal-node strong{{font-size:.69rem}}.signal-node small{{font-size:.59rem}}.watch-fact-value{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}.inline-map-trigger{{display:inline-flex;align-items:center;gap:4px;border:1px solid #cfdce5;background:#f5f9fc;color:#225270;border-radius:999px;padding:4px 8px;font-size:.67rem;font-weight:850;line-height:1;cursor:pointer;vertical-align:middle}}.inline-map-trigger:hover{{background:#ebf4fa;border-color:#a9c3d3}}.inline-map-trigger-icon{{font-size:.8rem;line-height:1}}.inline-map-trigger-label{{letter-spacing:.01em}}.map-template{{display:none}}.candidate-minimap-svg{{display:block;width:100%;height:auto;aspect-ratio:6 / 5}}.candidate-minimap-svg .minimap-water{{fill:#dfeef7}}.candidate-minimap-svg .minimap-grid line{{stroke:rgba(77,110,132,.18);stroke-width:.9}}.candidate-minimap-svg .minimap-land path{{fill:#f8fafb;stroke:#7b919f;stroke-width:.7;stroke-linejoin:round;fill-rule:evenodd}}.candidate-minimap-svg .minimap-coastline path{{fill:none;stroke:#526f82;stroke-width:1.05;stroke-linecap:round;stroke-linejoin:round}}.candidate-minimap-svg .minimap-bbox{{fill:rgba(33,110,166,.24);stroke:#1e5f90;stroke-width:2;stroke-dasharray:5 3}}.candidate-minimap-svg .minimap-center{{fill:#124e7a;stroke:#ffffff;stroke-width:1.2}}.candidate-minimap-svg .minimap-label{{fill:#5d7688;font-size:10px;font-weight:700;letter-spacing:.02em;pointer-events:none}}.minimap-label-korea{{font-size:11px;fill:#355468}}.map-modal-content{{display:flex;flex-direction:column;gap:8px;min-width:0;height:100%}}.map-modal-meta{{display:flex;flex-wrap:wrap;gap:8px 14px;font-size:.78rem;color:#4f6778}}.map-modal-meta span{{display:inline-flex;gap:6px;align-items:center}}.map-modal-meta b{{color:#1d3f56}}.map-modal-controls{{display:flex;justify-content:space-between;align-items:center;gap:10px}}.map-zoom-controls{{display:flex;gap:6px;align-items:center}}.map-zoom-controls button{{border:1px solid var(--line);background:#fff;color:#22485f;border-radius:9px;padding:6px 10px;font-size:.76rem;font-weight:800;cursor:pointer}}.map-zoom-controls button:hover{{background:#f2f7fa}}.map-modal-tip{{font-size:.7rem;color:var(--muted)}}.map-modal-stage{{flex:1 1 auto;min-height:320px;border:1px solid var(--line);border-radius:14px;background:#e8f2f8;overflow:hidden;position:relative;touch-action:none}}.map-modal-stage .candidate-minimap-svg{{width:100%;height:100%;aspect-ratio:auto;cursor:grab}}.map-modal-stage.dragging .candidate-minimap-svg{{cursor:grabbing}}.map-modal-note{{font-size:.72rem;color:var(--muted);margin:0}}.why-watch-disclosure,.detail-zone-disclosure{{margin-top:10px;border:1px solid var(--line);border-radius:12px;background:#fff;overflow:hidden}}.why-watch-disclosure>summary,.detail-zone-summary{{display:flex;justify-content:space-between;align-items:center;gap:12px;list-style:none;cursor:pointer;padding:11px 13px;background:#f8fafb}}.why-watch-disclosure>summary::-webkit-details-marker,.detail-zone-summary::-webkit-details-marker{{display:none}}.why-watch-disclosure>summary>span:first-child,.detail-zone-summary>span:first-child{{display:flex;flex-direction:column}}.why-watch-disclosure>summary b,.detail-zone-summary b{{font-size:.9rem}}.why-watch-disclosure>summary small,.detail-zone-summary small{{font-size:.67rem;color:var(--muted)}}.why-watch-summary,.detail-summary-metrics{{display:flex;gap:5px;flex-wrap:wrap;justify-content:flex-end}}.why-watch-summary i,.detail-summary-metrics i{{font-style:normal;font-size:.65rem;font-weight:800;color:#466276;background:#edf3f6;border-radius:999px;padding:4px 7px}}.why-watch-disclosure .signal-summary-grid{{padding:10px}}.detail-zone-disclosure{{border-top:3px solid #bfd3df}}.detail-zone-body{{padding:12px}}.matrix-section{{margin:0}}.evidence-status-summary{{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}}.evidence-status-summary span{{font-size:.66rem;color:#506979;background:#eef3f6;border-radius:999px;padding:4px 7px}}.evidence-status-summary b{{color:#203f53}}.evidence-filter-toolbar{{display:flex;justify-content:flex-end;align-items:center;gap:10px;margin:-2px 0 8px;padding:0 1px}}.evidence-filter-toolbar>span{{font-size:.66rem;color:var(--muted)}}.attention-filter-toggle{{border:1px solid #d4dfe7;background:#fff;color:#35566c;border-radius:999px;padding:5px 9px;font-size:.68rem;font-weight:850;cursor:pointer;white-space:nowrap}}.attention-filter-toggle b{{margin-left:3px;color:#8a4b0c}}.attention-filter-toggle:hover{{background:#f5f9fb;border-color:#a9bfcd}}.attention-filter-toggle[aria-pressed="true"]{{background:#fff0df;border-color:#e1b77d;color:#7a430d}}.evidence-card.attention-hidden{{display:none!important}}.evidence-card.signal-focus{{border-color:#2d709c;box-shadow:0 0 0 3px rgba(45,112,156,.14),0 8px 18px rgba(20,45,62,.09)}}.evidence-card-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;align-items:start}}.evidence-card{{border:1px solid var(--line);border-radius:10px;background:#fff;overflow:hidden;min-width:0}}.evidence-card[open]{{grid-column:auto;width:100%;max-width:100%}}.evidence-card-summary{{list-style:none;cursor:pointer;padding:10px 11px;min-height:126px;background:#fbfcfd}}.evidence-card-summary::-webkit-details-marker{{display:none}}.evidence-card[open]>.evidence-card-summary{{background:#eef5f8;border-bottom:1px solid var(--line)}}.evidence-card-head{{display:flex;justify-content:flex-start;align-items:center;gap:7px;flex-wrap:wrap}}.evidence-card-head .check-id{{margin-right:auto}}.ensemble-consensus-badge{{display:inline-flex;align-items:center;border:1px solid #cbd8e0;background:#f4f7f9;color:#496475;border-radius:999px;padding:3px 6px;font-size:.6rem;font-weight:850;white-space:nowrap}}.ensemble-consensus-badge.majority{{background:#fff7df;border-color:#e6d39b;color:#705716}}.ensemble-consensus-badge.unresolved{{background:#fff0e8;border-color:#e4bda8;color:#824821}}.evidence-card-summary .role-chips{{margin-top:6px}}.evidence-card-summary p{{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-size:.74rem;line-height:1.45;color:#415b6d;margin:7px 0 4px}}.evidence-open-label{{font-size:.65rem;color:#23688f;font-weight:800}}.evidence-card[open] .evidence-open-label{{display:none}}.evidence-card>.evidence-body{{margin:0;border:0;border-radius:0;padding:11px}}.evidence-card .images{{gap:10px;margin-top:12px}}.evidence-card .images.single{{grid-template-columns:1fr}}.evidence-card .images.stacked{{grid-template-columns:1fr}}.evidence-card .images.single .evidence-figure img{{width:100%;height:auto;max-height:640px;object-fit:contain}}.evidence-card .images.stacked .evidence-figure img{{width:100%;height:auto;max-height:520px;object-fit:contain}}.evidence-card .evidence-figure img{{height:auto;max-height:360px;object-fit:contain}}.detail-zone-body .caution-disclosure,.detail-zone-body .questions-disclosure{{margin-top:9px}}.non-surface-summary{{margin-top:12px}}.non-surface-gallery{{margin-top:10px}}.non-surface-gallery h4{{font-size:.74rem;margin:0 0 6px;color:#40596b}}.non-surface-gallery .images{{grid-template-columns:repeat(auto-fit,minmax(220px,1fr));margin-top:0}}.minority-report-panel{{margin-top:14px;border:1px solid var(--line);border-radius:13px;background:#fff;overflow:hidden}}.minority-report-panel>summary{{display:flex;justify-content:space-between;gap:12px;align-items:center;cursor:pointer;padding:11px 13px;background:#f8fafb;font-size:.79rem;font-weight:850;color:#294b61}}.minority-report-panel>summary b{{font-size:.66rem;color:#785c19;background:#fff4ce;border-radius:999px;padding:4px 7px}}.minority-report-body{{padding:12px}}.minority-report-note{{font-size:.7rem;color:var(--muted);margin:0 0 10px}}.minority-card{{border:1px solid var(--line);border-radius:10px;padding:10px;margin-top:8px;background:#fcfdfd}}.minority-card-head{{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}}.minority-card-head h3{{font-size:.86rem;margin:2px 0}}.minority-agreement{{font-size:.63rem;font-weight:850;color:#705716;background:#fff4ce;border-radius:999px;padding:4px 7px;white-space:nowrap}}.minority-summary{{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 8px;font-size:.66rem;color:#556d7d}}.minority-summary span{{border-right:1px solid var(--line);padding-right:8px}}.minority-summary span:last-child{{border-right:0}}.minority-member{{border-top:1px solid var(--line);padding-top:8px;margin-top:8px}}.minority-member-head{{display:flex;gap:8px;align-items:center;font-size:.69rem}}.minority-member-head span{{font-weight:800;color:#6d4f12}}.minority-member-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}.minority-member h4{{font-size:.72rem;margin:7px 0 2px}}.minority-member li,.minority-member p{{font-size:.68rem}}.minority-member-images{{margin-top:6px}}.minority-member-images .images{{grid-template-columns:repeat(auto-fit,minmax(220px,1fr));margin-top:6px}}.minority-member-images .evidence-figure img{{max-height:260px;object-fit:contain}}.minority-card.minority-focus{{border-color:#d3a638;box-shadow:0 0 0 3px rgba(211,166,56,.18)}}
.decision-status-strip{{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 9px}}.decision-status-chip{{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--line);border-radius:999px;background:#f7fafb;padding:4px 7px;white-space:nowrap}}.decision-status-chip small{{font-size:.58rem;color:var(--muted);font-weight:800}}.decision-status-chip b{{font-size:.68rem;color:#29485d}}.decision-status-chip.ok{{background:var(--ok-bg);border-color:#c7e3d7}}.decision-status-chip.ok b{{color:var(--ok)}}.decision-status-chip.partial{{background:var(--partial-bg);border-color:#eadca9}}.decision-status-chip.partial b{{color:var(--partial)}}.decision-status-chip.warn,.decision-status-chip.opp{{background:var(--warn-bg);border-color:#edd0ad}}.decision-status-chip.warn b,.decision-status-chip.opp b{{color:var(--warn)}}
.operational-brief-panel{{margin:2px 0 10px;border:1px solid #c7d8e3;border-left:4px solid var(--blue);border-radius:12px;background:#fbfdfe;padding:11px 12px}}.operational-brief-head{{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}}.operational-brief-head h3{{margin:.08rem 0 0;font-size:.92rem;color:#234f6b}}.operational-brief-location{{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}}.operational-brief-location span{{display:inline-flex;align-items:center;gap:4px;border:1px solid #d7e2e9;border-radius:999px;background:#fff;padding:4px 7px}}.operational-brief-location small{{font-size:.56rem;color:#6c7f8b;font-weight:850}}.operational-brief-location b{{font-size:.65rem;color:#304f63}}.operational-brief-signal{{margin:8px 0 9px;font-size:.86rem;line-height:1.5;font-weight:850;color:#1f4055}}.operational-brief-rows{{border-top:1px solid #e0e8ed}}.operational-brief-row{{display:grid;grid-template-columns:70px minmax(0,1fr);gap:8px;align-items:start;padding:7px 0;border-bottom:1px solid #e8eef2}}.operational-brief-row span{{font-size:.62rem;font-weight:900;color:#5c7280}}.operational-brief-row strong{{font-size:.72rem;line-height:1.45;color:#344f61}}.operational-brief-row.limit span{{color:#8a5b16}}.operational-brief-row.next span{{color:#2d6688}}.operational-brief-foot{{display:flex;justify-content:space-between;gap:8px;align-items:center;margin-top:7px;flex-wrap:wrap}}.operational-brief-components{{display:flex;gap:4px;flex-wrap:wrap}}.operational-brief-components span{{font-size:.58rem;font-weight:800;color:#486b59;background:#f3f8f5;border:1px solid #d6e5dc;border-radius:999px;padding:3px 6px}}.operational-brief-actions{{display:flex;gap:5px;flex-wrap:wrap}}.operational-brief-actions button{{border-radius:8px;padding:4px 7px;font-size:.61rem;font-weight:850;cursor:pointer}}
.analysis-core-panel{{margin:9px 0 0;border-top:1px solid var(--line);padding-top:9px}}.analysis-core-panel .core-assessment{{margin:0}}.analysis-detail-disclosure{{margin:8px 0 0;border-top:1px solid var(--line);padding-top:7px}}.analysis-detail-disclosure>summary{{display:flex;justify-content:space-between;align-items:center;gap:8px;list-style:none;cursor:pointer;padding:5px 0;color:#31566d}}.analysis-detail-disclosure>summary::-webkit-details-marker{{display:none}}.analysis-detail-disclosure>summary>span:first-child{{display:flex;flex-direction:column}}.analysis-detail-disclosure>summary b{{font-size:.72rem}}.analysis-detail-disclosure>summary small{{font-size:.59rem;color:var(--muted)}}.analysis-detail-summary{{display:flex;gap:4px;flex-wrap:wrap}}.analysis-detail-summary i{{font-style:normal;font-size:.58rem;font-weight:800;color:#526d7d;background:#edf3f6;border-radius:999px;padding:3px 6px}}.analysis-detail-body{{padding-top:7px}}.analysis-detail-stack{{display:grid;grid-template-columns:1fr;gap:8px}}.analysis-detail-item{{border:1px solid var(--line);border-radius:11px;background:#fff;padding:12px 14px;min-width:0}}.analysis-detail-item p{{font-size:.84rem}}.analysis-detail-stack .operational-guidance{{margin-top:0}}
.priority-watch-context{{display:grid;gap:6px;margin:3px 0 8px}}.priority-watch-line{{display:grid;grid-template-columns:88px minmax(0,1fr);gap:8px;align-items:start;padding:6px 7px;border-radius:8px}}.priority-watch-line span{{font-size:.61rem;font-weight:900}}.priority-watch-line strong{{font-size:.7rem;line-height:1.42}}.priority-watch-reason{{background:#f3faf6;border-left:3px solid var(--ok)}}.priority-watch-limit{{background:#fffaf0;border-left:3px solid #c28a2a}}
.operational-guidance{{margin:8px 0 11px;padding:10px 11px;border:1px solid #cbdbe5;border-left:4px solid #4a7895;border-radius:10px;background:#f7fafc}}.operational-guidance h3{{font-size:.88rem;margin:.08rem 0 .45rem}}.operational-guidance-grid{{display:grid;gap:6px}}.operational-guidance-grid>div{{display:grid;grid-template-columns:58px minmax(0,1fr);gap:7px;align-items:start}}.operational-guidance-grid span{{font-size:.64rem;color:var(--muted);font-weight:850}}.operational-guidance-grid p{{margin:0;font-size:.76rem;line-height:1.45}}
@media(max-width:1180px){{.hero-grid{{grid-template-columns:1fr}}.overview-image-frame{{min-height:300px}}.operational-brief-location{{justify-content:flex-start}}.evidence-card-grid{{grid-template-columns:1fr}}}}
@media(max-width:760px){{.overview-image-frame{{min-height:220px}}.decision-status-strip{{gap:4px}}.operational-brief-head{{flex-direction:column}}.operational-brief-row{{grid-template-columns:62px minmax(0,1fr)}}.operational-brief-foot{{align-items:flex-start;flex-direction:column}}.analysis-detail-disclosure>summary{{align-items:flex-start;flex-direction:column}}.watch-facts>div{{grid-template-columns:42px minmax(0,1fr)}}.evidence-card-grid{{grid-template-columns:1fr}}.why-watch-disclosure>summary,.detail-zone-summary{{align-items:flex-start;flex-direction:column}}.why-watch-summary,.detail-summary-metrics{{justify-content:flex-start}}.detail-zone-body{{padding:8px}}.evidence-filter-toolbar{{justify-content:space-between;align-items:flex-start;flex-wrap:wrap}}.evidence-filter-toolbar>span{{display:none}}.candidate-tab>small{{gap:4px}}.candidate-tab-time{{font-size:.58rem}}.evidence-card-summary{{min-height:0}}.evidence-card .images.single .evidence-figure img,.evidence-card .images.stacked .evidence-figure img,.evidence-card .evidence-figure img{{height:auto;max-height:240px}}.internal-judgment-head{{flex-direction:column}}.internal-judgment-badges{{justify-content:flex-start}}}}

#system-guide-modal{{width:min(1080px,calc(100vw - 28px));height:auto;max-width:1080px;max-height:92dvh;border:0;border-radius:18px;padding:0;overflow:hidden;box-shadow:0 28px 90px rgba(0,0,0,.34)}}#system-guide-modal::backdrop{{background:rgba(13,33,48,.42);backdrop-filter:blur(2px)}}.system-guide-popup{{max-height:92dvh;overflow:auto;background:#fff}}.system-guide-header{{position:sticky;top:0;z-index:3;display:flex;justify-content:space-between;gap:18px;align-items:center;padding:18px 22px;background:linear-gradient(125deg,var(--navy-deep),var(--navy));color:#fff}}.system-guide-header .kicker{{font-size:.7rem;letter-spacing:.11em;font-weight:900;opacity:.72}}.system-guide-header h2{{font-size:1.45rem;margin:.15rem 0 0;color:#fff}}.system-guide-close{{border:1px solid rgba(255,255,255,.35);background:rgba(255,255,255,.1);color:#fff;border-radius:9px;padding:8px 13px;font-weight:800;cursor:pointer}}.system-guide-body{{padding:20px}}.system-guide-lead{{margin:0 0 16px;padding:13px 15px;border:1px solid #d7e5ee;background:#f1f7fb;border-radius:12px;color:#34566e;font-size:.9rem;line-height:1.6}}.system-guide-grid{{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(320px,.8fr);gap:16px;align-items:start}}.system-guide-panel{{border:1px solid var(--line);border-radius:14px;background:#fff;overflow:hidden}}.system-guide-panel-title{{padding:12px 14px;background:#f5f8fa;border-bottom:1px solid var(--line);font-size:.95rem;font-weight:900;color:#274c64}}.system-guide-steps{{padding:14px;display:grid;gap:10px}}.system-guide-step{{display:grid;grid-template-columns:42px 1fr;gap:11px;align-items:start;border:1px solid #dbe4ea;border-radius:12px;padding:11px 12px;background:#fbfcfd}}.system-guide-num{{display:grid;place-items:center;width:36px;height:36px;border-radius:10px;background:var(--navy);color:#fff;font-weight:900}}.system-guide-step h3{{margin:0 0 3px;font-size:.94rem;color:#214f72}}.system-guide-step p{{margin:0;color:#536a79;font-size:.81rem;line-height:1.52}}.system-guide-status-section{{padding:13px 14px 15px}}.system-guide-status-group+.system-guide-status-group{{margin-top:14px;padding-top:14px;border-top:1px solid var(--line)}}.system-guide-status-group h3{{margin:0 0 8px;font-size:.87rem;color:#36596e}}.system-guide-status-list{{display:grid;gap:7px}}.system-guide-status{{display:grid;grid-template-columns:132px 1fr;gap:10px;align-items:center}}.system-guide-badge{{display:inline-flex;align-items:center;justify-content:center;border-radius:999px;padding:6px 9px;font-size:.74rem;font-weight:900;border:1px solid transparent;text-align:center}}.system-guide-badge.ok{{background:var(--ok-bg);color:var(--ok);border-color:#bfe2d2}}.system-guide-badge.partial{{background:var(--partial-bg);color:var(--partial);border-color:#eadb9a}}.system-guide-badge.not{{background:#eef2f5;color:#61717d;border-color:#d3dde3}}.system-guide-badge.opp{{background:var(--opp-bg);color:var(--opp);border-color:#efc7c4}}.system-guide-badge.warn{{background:#fff3d8;color:#7a5a17;border-color:#ead49e}}.system-guide-badge.insuf{{background:#edf1f4;color:#586773;border-color:#d3dde3}}.system-guide-badge.related,.system-guide-badge.context{{background:#e8f2fb;color:#245f8f;border-color:#bdd7ea}}.system-guide-badge.support{{background:var(--ok-bg);color:var(--ok);border-color:#bfe2d2}}.system-guide-badge.dual{{background:linear-gradient(135deg,var(--ok-bg) 0 49%,var(--warn-bg) 51% 100%);color:#655526;border-color:#d8bf95}}.system-guide-badge.mixed{{background:var(--partial-bg);color:var(--partial);border-color:#eadb9a}}.system-guide-badge.limit{{background:var(--warn-bg);color:var(--warn);border-color:#e3b485}}.system-guide-badge.opposing{{background:var(--opp-bg);color:var(--opp);border-color:#efc7c4}}.system-guide-badge.neutral{{background:#edf1f4;color:#586773;border-color:#d3dde3}}.system-guide-status span:last-child{{font-size:.76rem;color:#556b79;line-height:1.42}}.system-guide-note{{margin-top:10px;font-size:.72rem;color:#73818b}}.system-guide-notice{{margin-top:16px;border:1px solid #cddce7;border-radius:13px;background:#f7fafc;padding:14px 15px}}.system-guide-notice h3{{margin:0 0 8px;font-size:.88rem;color:#274c64}}.system-guide-notice ul{{margin:0;padding-left:20px;color:#536a79;font-size:.8rem;line-height:1.65}}.system-guide-criteria{{margin-top:14px;display:grid;grid-template-columns:1fr 1fr;gap:10px}}.system-guide-criteria-card{{border:1px solid var(--line);border-radius:11px;padding:11px 12px;background:#fff}}.system-guide-criteria-card strong{{display:block;color:#284f67;font-size:.8rem;margin-bottom:3px}}.system-guide-criteria-card p{{margin:0;color:#5a6e7b;font-size:.76rem;line-height:1.5}}@media(max-width:820px){{#system-guide-modal{{width:calc(100vw - 14px);max-height:96dvh}}.system-guide-popup{{max-height:96dvh}}.system-guide-grid{{grid-template-columns:1fr}}.system-guide-criteria{{grid-template-columns:1fr}}.system-guide-status{{grid-template-columns:118px 1fr}}.system-guide-header{{padding:15px}}.system-guide-body{{padding:14px}}.header-guide-buttons{{justify-content:flex-start}}}}
#watch-condition-modal{{width:min(760px,calc(100vw - 28px));height:auto;max-width:760px;max-height:86dvh;border:0;border-radius:14px;padding:0;overflow:hidden;box-shadow:0 24px 80px #0008}}#watch-condition-modal .watch-condition-content{{max-height:86dvh;overflow-y:auto;background:#fff;padding:16px 18px 18px}}#watch-condition-modal .modal-header{{top:-16px;padding:16px 0 10px}}.watch-condition-close{{min-width:44px;min-height:38px;border:1px solid #8499aa;background:#fff;border-radius:8px;padding:6px 12px;cursor:pointer}}.watch-condition-lead{{margin:0 0 12px;padding:10px 12px;border-radius:10px;background:#eef5f8;color:#345366;font-size:.82rem;line-height:1.55}}.watch-condition-section{{margin-top:13px}}.watch-condition-section h3{{font-size:.91rem;margin:0 0 7px}}.watch-condition-grid{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}.watch-condition-card{{border:1px solid var(--line);border-radius:10px;background:#fbfcfd;padding:10px 11px}}.watch-condition-card strong{{display:block;font-size:.8rem;color:#274c64;margin-bottom:3px}}.watch-condition-card p,.watch-condition-section>p{{font-size:.76rem;line-height:1.55;color:#4d6474;margin:0}}.watch-condition-note{{margin-top:9px;padding-top:9px;border-top:1px solid var(--line);font-size:.72rem;color:#647784}}@media(max-width:760px){{header{{align-items:flex-start}}.header-actions{{max-width:none;width:100%;align-items:flex-start;margin-top:10px}}.header-meta{{justify-content:flex-start}}.watch-condition-grid{{grid-template-columns:1fr}}#watch-condition-modal{{width:calc(100vw - 18px);height:auto;max-height:90dvh}}#watch-condition-modal .watch-condition-content{{max-height:90dvh;padding:13px}}}}


.region-guide-content{{width:min(1180px,96vw);height:min(760px,92dvh);display:flex;flex-direction:column;background:#fff;border-radius:16px;overflow:hidden}}
#region-guide-modal{{width:auto;height:auto;max-width:none;max-height:none;padding:0;border:0;border-radius:16px;background:transparent}}
#region-guide-modal::backdrop{{background:rgba(7,25,38,.42)}}
.region-guide-header{{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:14px 17px;border-bottom:1px solid var(--line);background:#f8fafb}}
.region-guide-header h2{{margin:1px 0 0;font-size:1.15rem}}
.region-guide-close{{border:1px solid var(--line);background:#fff;color:#315268;border-radius:9px;padding:6px 10px;font-size:.74rem;font-weight:850;cursor:pointer}}
.region-guide-body{{display:grid;grid-template-columns:255px minmax(0,1fr);min-height:0;flex:1}}
.region-guide-sidebar{{padding:12px;border-right:1px solid var(--line);background:#fbfcfd;overflow:auto}}
.region-guide-role-tabs{{display:grid;grid-template-columns:1fr;gap:6px;margin-bottom:10px}}
.region-guide-role-tab{{display:flex;justify-content:space-between;align-items:center;border:1px solid var(--line);background:#fff;color:#466174;border-radius:9px;padding:7px 9px;font-size:.72rem;font-weight:850;cursor:pointer}}
.region-guide-role-tab b{{font-size:.65rem;color:#7890a0}}
.region-guide-role-tab.active{{background:#e9f3f9;border-color:#a9c7d9;color:#1e5f87}}
.region-guide-region-list{{display:grid;grid-template-columns:1fr;gap:5px}}
.region-guide-region-button{{border:1px solid #dce5eb;background:#fff;color:#38566a;border-radius:8px;padding:7px 9px;text-align:left;font-size:.73rem;font-weight:800;cursor:pointer}}
.region-guide-region-button:hover{{background:#f2f7fa}}
.region-guide-region-button.active{{background:#173f5e;color:#fff;border-color:#173f5e}}
.region-guide-map-panel{{display:flex;flex-direction:column;min-width:0;padding:12px 14px 14px}}
.region-guide-selected{{display:flex;align-items:center;gap:7px;min-height:32px;margin-bottom:8px;flex-wrap:wrap}}
.region-guide-selected strong{{font-size:1rem;color:#173f5e}}
.region-guide-selected span{{font-size:.68rem;color:#537083;background:#eef4f7;border-radius:999px;padding:4px 7px;font-weight:800}}
.region-guide-map-toolbar{{display:flex;align-items:center;justify-content:space-between;gap:10px;min-height:28px;margin:-2px 1px 6px}}
.region-guide-map-stage{{flex:1;min-height:480px;border:1px solid var(--line);border-radius:13px;background:#dfeef7;overflow:hidden;touch-action:none;cursor:grab}}
.region-guide-map-stage.dragging{{cursor:grabbing}}
.region-guide-svg{{display:block;width:100%;height:100%;min-height:480px;user-select:none}}
.region-guide-map-controls{{display:flex;align-items:center;gap:4px;flex:0 0 auto}}
.region-guide-map-controls button{{min-width:28px;height:25px;border:1px solid #cdd9e1;background:#fff;color:#31556c;border-radius:6px;padding:0 7px;font-size:.68rem;font-weight:850;line-height:1;cursor:pointer}}
.region-guide-map-controls button:hover{{background:#eef5f9;border-color:#abc2d1}}
.region-guide-water{{fill:#dfeef7}}
.region-guide-grid line{{stroke:rgba(77,110,132,.16);stroke-width:.8}}
.region-guide-land-base path{{fill:#f8fafb;stroke:#7b919f;stroke-width:.55;stroke-linejoin:round;fill-rule:evenodd}}
.region-guide-coastline path{{fill:none;stroke:#526f82;stroke-width:.85;stroke-linecap:round;stroke-linejoin:round}}
.region-guide-overlay{{display:none}}
.region-guide-overlay.active{{display:block}}
.region-guide-analysis-grid rect{{fill:rgba(240,151,36,.62);stroke:#a85f05;stroke-width:.45;stroke-linejoin:miter;shape-rendering:crispEdges;vector-effect:non-scaling-stroke}}
.region-guide-bbox-outline rect{{fill:none;stroke:#2f6d94;stroke-width:1.6;stroke-dasharray:5 3;stroke-linejoin:miter;vector-effect:non-scaling-stroke}}
.region-guide-map-legend{{display:flex;align-items:center;gap:13px;min-height:24px;margin:0;font-size:.66rem;color:#62798a;font-weight:800}}
.region-guide-map-legend[hidden]{{display:none}}
.region-guide-map-legend span{{display:inline-flex;align-items:center;gap:5px}}
.region-guide-map-legend i{{display:inline-block;width:16px;height:11px;border-radius:0}}
.region-guide-legend-outline{{border:2px dashed #356d91;background:transparent}}
.region-guide-legend-fill{{border:1px solid #a85f05;background:repeating-linear-gradient(90deg,rgba(240,151,36,.62) 0 4px,rgba(255,255,255,.75) 4px 5px)}}
.region-guide-map-label{{fill:#5c7789;font-size:9px;font-weight:800;pointer-events:none}}
.region-guide-map-label-korea{{font-size:10px;fill:#355468}}
.region-guide-purpose{{margin:8px 2px 0;min-height:20px;font-size:.72rem;color:#61798a;line-height:1.45}}
@media(max-width:760px){{.region-guide-content{{width:calc(100vw - 16px);height:92dvh}}.region-guide-body{{grid-template-columns:1fr;grid-template-rows:auto 1fr}}.region-guide-sidebar{{border-right:0;border-bottom:1px solid var(--line);padding:9px}}.region-guide-role-tabs{{grid-template-columns:repeat(3,1fr);margin-bottom:7px}}.region-guide-region-list{{display:flex;overflow-x:auto;gap:5px}}.region-guide-region-button{{white-space:nowrap}}.region-guide-map-panel{{padding:9px}}.region-guide-map-stage,.region-guide-svg{{min-height:360px}}}}


/* 2026-09 master-detail evidence workspace */
.evidence-filter-toolbar{{justify-content:space-between;align-items:center;flex-wrap:wrap;margin:0 0 8px}}.evidence-role-filters{{display:flex;gap:5px;flex-wrap:wrap}}.evidence-role-filter{{border:1px solid #d4dfe7;background:#fff;color:#35566c;border-radius:999px;padding:5px 9px;font-size:.67rem;font-weight:850;cursor:pointer}}.evidence-role-filter:hover{{background:#f4f8fa;border-color:#a9bfcd}}.evidence-role-filter.active{{background:#e8f3f9;border-color:#8fb6cc;color:#1f5f84}}.evidence-master-detail{{display:grid;grid-template-columns:minmax(320px,390px) minmax(0,1fr);gap:9px;min-height:560px}}.evidence-master-list{{border:1px solid var(--line);border-radius:10px;background:#f6f9fb;padding:6px;max-height:min(76vh,900px);overflow:auto;align-self:start}}.evidence-master-item{{display:block;width:100%;text-align:left;border:1px solid transparent;border-radius:8px;background:transparent;color:inherit;padding:9px 10px;margin:0 0 5px;cursor:pointer;transition:.12s ease}}.evidence-master-item:last-child{{margin-bottom:0}}.evidence-master-item:hover{{background:#fff;border-color:#d3dfe7}}.evidence-master-item.active{{background:#fff;border-color:#78a9c5;box-shadow:0 2px 8px rgba(25,76,107,.09)}}.evidence-master-item.attention-hidden,.evidence-master-item.role-hidden{{display:none!important}}.evidence-master-head{{display:flex;align-items:center;gap:6px;flex-wrap:wrap}}.evidence-master-head .check-id{{margin-right:auto}}.evidence-master-item .role-chips{{margin-top:5px}}.evidence-master-item p{{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;font-size:.72rem;line-height:1.42;color:#405b6c;margin:6px 0 0}}.evidence-detail-pane{{border:1px solid var(--line);border-radius:10px;background:#fff;max-height:min(76vh,900px);overflow:auto;min-width:0;scrollbar-gutter:stable}}.evidence-detail-panel{{min-width:0}}.evidence-detail-panel[hidden]{{display:none!important}}.evidence-detail-head{{position:sticky;top:0;z-index:7;display:flex;justify-content:space-between;gap:12px;align-items:flex-start;padding:11px 12px;background:rgba(255,255,255,.97);backdrop-filter:blur(5px);border-bottom:1px solid var(--line)}}.evidence-detail-head h4{{margin:5px 0 0;font-size:.86rem;line-height:1.42;color:#243f51}}.evidence-detail-meta{{display:flex;align-items:center;gap:6px;flex-wrap:wrap}}.evidence-detail-nav{{display:flex;gap:5px;white-space:nowrap}}.evidence-detail-nav button{{border:1px solid #cddae2;background:#fff;color:#31576e;border-radius:7px;padding:5px 7px;font-size:.67rem;font-weight:800;cursor:pointer}}.evidence-detail-nav button:hover{{background:#eef5f9}}.evidence-detail-panel>.evidence-body{{margin:0;border:0;border-radius:0;background:#fff;padding:12px 13px 16px}}.decision-relevance{{margin:10px 0 0;border-left:3px solid #6f94aa;background:#f4f8fa;border-radius:0 8px 8px 0;padding:8px 10px}}.decision-relevance>span{{font-size:.64rem;color:#557184;font-weight:900}}.decision-relevance p{{font-size:.76rem;margin:3px 0 0;line-height:1.48}}.decision-meanings{{margin-top:10px}}.decision-meanings>h4{{margin:0 0 6px}}.decision-meaning-card{{border:1px solid var(--line);border-left:3px solid #7899ac;border-radius:8px;padding:8px 10px;margin-top:6px;background:#fafcfd}}.decision-meaning-card.supporting{{border-left-color:var(--ok);background:#f4faf7}}.decision-meaning-card.limiting{{border-left-color:#bf8121;background:#fffaf0}}.decision-meaning-card.mixed{{border-left-color:#8973a8;background:#faf7fd}}.decision-meaning-card>div{{display:flex;gap:8px;align-items:center}}.decision-meaning-card strong{{font-size:.72rem}}.decision-meaning-card span{{font-size:.59rem;font-weight:900;border-radius:999px;background:#fff;padding:2px 5px;border:1px solid var(--line)}}.decision-meaning-card p{{font-size:.75rem;line-height:1.48;margin:4px 0 0}}.majority-aggregation{{margin:12px 0 2px;border:1px solid #cfdde5;border-radius:10px;background:#f9fbfc;padding:10px}}.majority-aggregation-head{{display:flex;justify-content:space-between;gap:10px;align-items:center}}.majority-aggregation-head h4{{margin:1px 0 0}}.majority-contract-badge{{font-size:.6rem;font-weight:900;color:#285f7e;background:#eaf4f9;border:1px solid #bed7e5;border-radius:999px;padding:3px 6px}}.majority-metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:8px 0}}.majority-metrics>div{{border:1px solid var(--line);border-radius:8px;background:#fff;padding:7px 8px}}.majority-metrics span{{display:block;font-size:.6rem;color:var(--muted);font-weight:850}}.majority-metrics strong{{font-size:1.08rem}}.majority-metrics .not-strengthening strong{{color:#526e7e}}.majority-metrics .strengthening strong{{color:#177059}}.majority-region-groups{{display:grid;grid-template-columns:1fr 1fr;gap:6px}}.majority-region-groups>div{{border:1px solid var(--line);border-radius:8px;background:#fff;padding:7px 8px}}.majority-region-groups strong{{font-size:.68rem}}.majority-region-groups p{{font-size:.69rem;line-height:1.45;margin:3px 0 0;color:#536b7a}}.majority-table-wrap{{overflow:auto;margin-top:8px;border:1px solid var(--line);border-radius:8px;background:#fff}}.majority-table{{width:100%;min-width:690px;border-collapse:collapse;font-size:.68rem}}.majority-table th,.majority-table td{{padding:6px 7px;border-bottom:1px solid #e5ecef;text-align:left}}.majority-table th{{position:sticky;top:0;background:#edf3f6;color:#40596b;z-index:1}}.majority-table tr:last-child td{{border-bottom:0}}.majority-state{{display:inline-flex;padding:2px 5px;border-radius:999px;font-size:.6rem;font-weight:900;white-space:nowrap}}.majority-state.strengthening{{background:#e7f4ee;color:#12684a}}.majority-state.not-strengthening{{background:#fff4ce;color:#755900}}.majority-note{{font-size:.64rem;color:#667b88;margin:6px 2px 0}}.quantitative-detail{{margin-top:10px}}.evidence-section-heading{{align-items:center}}
@media(max-width:1180px){{.evidence-master-detail{{grid-template-columns:310px minmax(0,1fr)}}}}
@media(max-width:880px){{.evidence-master-detail{{grid-template-columns:1fr;min-height:0}}.evidence-master-list{{display:flex;gap:6px;max-height:none;overflow-x:auto;padding:6px;scroll-snap-type:x proximity}}.evidence-master-item{{flex:0 0 285px;margin:0;scroll-snap-align:start}}.evidence-detail-pane{{max-height:none}}.evidence-detail-head{{position:static}}.majority-region-groups{{grid-template-columns:1fr}}.majority-metrics{{grid-template-columns:1fr 1fr 1fr}}}}

</style>
</head>
<body>
<main>
<header>
<div class="header-copy"><div class="brand">AIFS 강수 감시 · 예보관 분석화면</div><h1>AIFS 강수 감시 분석</h1><p class="header-sub">When. Where. What. Why. — 먼저 봐야 할 강수 신호를 찾습니다</p></div>
<div class="header-actions"><div class="header-meta"><span>초기시각 {escape(_format_init_header(init))}</span><span>표출 {len(surfaced)}</span><span>비표출 {len(non_surfaced)}</span><span>생성시각 {escape(_format_generated_at_header(product["generated_at_utc"]))}</span></div><div class="header-guide-buttons"><button id="system-guide-button" class="watch-condition-button" type="button" aria-haspopup="dialog" aria-controls="system-guide-modal">시스템 안내</button><button id="watch-condition-button" class="watch-condition-button" type="button" aria-haspopup="dialog" aria-controls="watch-condition-modal">강수 감시 조건</button><button id="region-guide-button" class="watch-condition-button" type="button" aria-haspopup="dialog" aria-controls="region-guide-modal">분석 지역</button></div></div>
</header>
<nav class="candidate-tabs-wrap" aria-label="표출 감시후보 선택"><div class="candidate-tabs-order">표출 감시후보 · 발생시각 순</div><div class="candidate-tabs" role="tablist">{candidate_tabs}</div></nav>
<div class="top-timeline-deck" aria-label="감시후보 시간축">{timeline_body}</div>
<div class="candidate-panels" data-initial-candidate="{escape(first_candidate, quote=True)}">{surface_body}</div>
<details class="non-surface-summary"><summary>비표출 감시후보 · {len(non_surfaced)}건</summary>{non_surface_rows}</details>
{internal_judgment_html}
{minority_report_html}
<dialog id="system-guide-modal" aria-labelledby="system-guide-title"><section class="system-guide-popup"><header class="system-guide-header"><div><div class="kicker">사용 안내</div><h2 id="system-guide-title">AIFS 강수 감시 분석 안내</h2></div><button class="system-guide-close" type="button">닫기</button></header><div class="system-guide-body"><p class="system-guide-lead"><strong>이 화면은 예보관이 먼저 확인할 감시후보와 판단 근거를 빠르게 파악하도록 돕는 화면입니다.</strong><br>감시후보를 선택한 뒤 운영 요약과 신호체인을 먼저 보고, 필요할 때 상세 분석과 정량 근거를 펼쳐 확인합니다.</p><div class="system-guide-grid"><section class="system-guide-panel"><div class="system-guide-panel-title">화면은 이렇게 확인합니다</div><div class="system-guide-steps"><div class="system-guide-step"><div class="system-guide-num">1</div><div><h3>감시후보·시간 확인</h3><p>상단 후보와 시간축에서 어느 지역과 어느 시간대를 우선 확인할지 봅니다.</p></div></div><div class="system-guide-step"><div class="system-guide-num">2</div><div><h3>운영 요약 확인</h3><p>핵심 신호, 제한 요인, 다음 확인을 먼저 보고 핵심시각·결합 위치가 있으면 같은 영역에서 함께 확인합니다.</p></div></div><div class="system-guide-step"><div class="system-guide-num">3</div><div><h3>강수 감시 신호체인 확인</h3><p>각 물리과정의 판독 문구와 색을 함께 봅니다. 신호체인 색은 단순 확인 여부가 아니라 강수 발달에 대한 지지·지지+제한·부분/혼합·범위별 상반·제한·참고/관련·반대·미확정의 방향을 나타냅니다.</p></div></div><div class="system-guide-step"><div class="system-guide-num">4</div><div><h3>상세 분석·근거 확인</h3><p>종합판단은 기본 화면에서 바로 확인하고, 모델 강수 예측·강수 발달 환경·예보관 확인은 ‘상세 분석 보기’를 펼쳐 확인합니다. 후속 상세분석에서는 확인항목별 사실, 기상학적 의미, 상반 근거, 정량값과 공간장을 확인합니다.</p></div></div></div></section><section class="system-guide-panel"><div class="system-guide-panel-title">상태 표시의 의미</div><div class="system-guide-status-section"><div class="system-guide-status-group"><h3>개별 판독 상태</h3><div class="system-guide-status-list"><div class="system-guide-status"><span class="system-guide-badge ok">확인</span><span>요청한 기상학적 특징이 상세자료에서 확인됨. 제한 가설을 확인한 경우에는 강수 발달을 지지한다는 뜻이 아님</span></div><div class="system-guide-status"><span class="system-guide-badge partial">부분 확인</span><span>일부 특징은 확인되지만 공간, 시간 또는 강도 측면에서 제한이 있음</span></div><div class="system-guide-status"><span class="system-guide-badge not">확인되지 않음</span><span>요청한 특징이 상세자료에서 확인되지 않음</span></div><div class="system-guide-status"><span class="system-guide-badge opp">반대 신호 확인</span><span>예상한 특징과 반대되는 신호가 확인됨</span></div><div class="system-guide-status"><span class="system-guide-badge warn">불확실</span><span>제공된 자료만으로 판정하기 어려움</span></div><div class="system-guide-status"><span class="system-guide-badge insuf">근거 부족</span><span>판독에 필요한 근거가 충분하지 않음</span></div></div><p class="system-guide-note">※ 상세 근거의 판독 상태와 신호체인의 색상은 역할이 다릅니다. 상세 근거는 요청한 가설의 확인 여부를, 신호체인 색상은 강수 발달에 대한 근거 방향을 요약합니다.</p></div><div class="system-guide-status-group"><h3>감시후보 강조 표시</h3><div class="system-guide-status-list"><div class="system-guide-status"><span class="priority-watch-badge">중점 감시</span><span>모델 강수가 확인되고 핵심시각·공간에서 강수·대기 전층 수분량(TCW)·하층 수렴·중층 상승운동의 완전한 결합이 확인된 후보입니다. 환경 지속성이 제한되어도 핵심시각의 완전한 결합이 확인되면 중점 감시가 될 수 있습니다. 3요소 결합 등으로 강수 발달 환경이 확인되더라도 완전한 핵심 결합이 확인되지 않으면 중점 감시가 아니라 우선 확인 또는 일반 감시 대상으로 남을 수 있습니다.</span></div><div class="system-guide-status"><span class="priority-check-badge">우선 확인</span><span>중점 감시 조건에는 해당하지 않지만, 확인된 지지 근거와 제한요인을 종합했을 때 운영상 확인 가치가 높은 후보입니다.</span></div><div class="system-guide-status"><strong>별도 강조 없음</strong><span>유효한 직접 강수 또는 선행환경 신호로 표출되지만 중점 감시 또는 우선 확인 조건까지는 충족하지 않은 일반 감시후보입니다. 화면에 표출되는 것 자체가 일반 감시 대상임을 의미합니다.</span></div><div class="system-guide-status"><span class="precursor-watch-badge">선행환경</span><span>직접 모델 강수가 아직 확인되지 않아도 수분 공급, 하층 수렴, 중층 상승운동 등 강수 발달 환경이 조직되는지를 감시하는 후보입니다.</span></div></div><p class="system-guide-note">※ 중점 감시와 우선 확인은 위험도 등급이 아니라 예보관 확인 우선순위를 위한 강조 표시입니다. 별도 강조가 없는 표출 후보는 일반 감시 대상입니다. 중점 감시는 모델 강수와 핵심시각·공간의 완전한 강수·수분·하층 수렴·중층 상승운동 결합을 기준으로 강조합니다.</p></div><div class="system-guide-status-group"><h3>핵심 발달환경</h3><div class="system-guide-status-list"><div class="system-guide-status"><span class="system-guide-badge ok">환경장 결합 확인</span><span>핵심시각 환경 존재와 전체 시간대의 환경 지속성을 구분합니다. 시공간 일치 확인에 사용된 다변수 판독은 해당 Check가 요구한 모델 강수와 환경요소가 같은 시각·공간에서 함께 확인되고, 그 결합 위치가 운영 예보권역으로 구조화되어야 합니다. 4요소 완전 결합은 강수·대기 전층 수분량(TCW)·하층 수렴·중층 상승운동을 모두 요구하며, 3요소 결합 판독은 실제 Check가 요구한 요소만 위치 정보와 원본 기상장에 표시합니다. 이후 약화·분리 신호는 환경 지속성의 제한으로 별도 표시합니다. ‘지도에서 보기’는 판독된 운영권역과 권역 내 상대 위치를 보여주며, 강조된 권역 전체가 중첩영역이라는 의미는 아닙니다. ‘원본 기상장’은 해당 시공간 일치 Check에서 실제 사용한 동일 시각의 기상장만 보여주고, ‘판독 근거’는 해당 확인 항목으로 이동합니다.</span></div></div></div><div class="system-guide-status-group"><h3>Step07 판정 합의</h3><div class="system-guide-status-list"><div class="system-guide-status"><span class="ensemble-consensus-badge unanimous">판정 합의 3/3</span><span>동일한 근거를 독립적으로 3회 판독한 구조화 판정이 모두 일치함</span></div><div class="system-guide-status"><span class="ensemble-consensus-badge majority">판정 합의 2/3 · 이견</span><span>2회 판독이 같은 판정에 합의했고, 채택되지 않은 의견은 페이지 하단 마이너리티 리포트에 보존됨</span></div><div class="system-guide-status"><span class="ensemble-consensus-badge unresolved">판정 합의 미성립</span><span>세 판독이 서로 달라 다수 합의가 성립하지 않아 해당 확인 항목을 종합판단 근거에서 제외함</span></div></div><p class="system-guide-note">※ 3/3과 2/3은 LLM 판독 일치도이며 기상현상의 발생확률, 예측확률 또는 위험도 등급이 아닙니다. 시공간 일치 위치는 같은 판정에 참여한 멤버의 예측시각을 먼저 합의한 뒤 운영권역(region_id)별로 독립 투표하여 2/3 이상 선택된 권역만 결합 위치로 사용합니다. 같은 권역의 세부 방향은 2/3 합의가 없으면 미지정으로 표시합니다.</p></div><div class="system-guide-status-group"><h3>신호체인 색상 의미</h3><div class="system-guide-status-list"><div class="system-guide-status"><span class="system-guide-badge support">지지</span><span>강수 발생·발달 또는 시공간적 연계를 직접 지지하는 근거가 확인됨</span></div><div class="system-guide-status"><span class="system-guide-badge dual">지지+제한</span><span>지지하는 근거와 제한하는 근거가 서로 다른 확인 항목에서 각각 명확하게 확인됨</span></div><div class="system-guide-status"><span class="system-guide-badge mixed">부분·혼합</span><span>지지가 부분적이거나 하나의 판독 내부에서 공간·시간별 결과가 엇갈려 지지와 제한으로 명확히 분리하기 어려움</span></div><div class="system-guide-status"><span class="system-guide-badge mixed">범위별 상반</span><span>지지와 제한 근거가 모두 있으나 판단축·시간·공간 범위가 달라 하나의 지지+제한으로 직접 병합하지 않음</span></div><div class="system-guide-status"><span class="system-guide-badge limit">제한</span><span>강수 발달·유지 가능성을 제한하는 가설 또는 근거가 확인됨</span></div><div class="system-guide-status"><span class="system-guide-badge context">참고·관련</span><span>복합 분석 또는 참고 목적의 관련 근거이며 독립적인 강수 발달 지지로 간주하지 않음</span></div><div class="system-guide-status"><span class="system-guide-badge opposing">반대</span><span>요청한 지지 특징과 반대되는 신호가 확인됨</span></div><div class="system-guide-status"><span class="system-guide-badge neutral">미확정·미판독</span><span>독립 판독이 없거나, 미확정·근거 부족·판독 제외 상태임</span></div></div></div><div class="system-guide-status-group"><h3>신호체인 보조 문구</h3><div class="system-guide-status-list"><div class="system-guide-status"><span class="system-guide-badge neutral">개별 판독 없음</span><span>해당 물리과정을 독립 확인 항목으로는 따로 판독하지 않음</span></div><div class="system-guide-status"><span class="system-guide-badge neutral">판정 합의 미성립</span><span>독립 판독 간 다수 합의가 성립하지 않아 종합판단 근거에서 제외됨</span></div><div class="system-guide-status"><span class="system-guide-badge neutral">정량 검증 제외</span><span>독립 판독은 수행했으나 정량 일관성 검증을 통과하지 못해 종합판단 근거에서 제외됨</span></div><div class="system-guide-status"><span class="system-guide-badge neutral">판독 제외</span><span>그 밖의 내부 검증 사유로 해당 독립 판독을 종합판단 근거에 사용하지 않음</span></div><div class="system-guide-status"><span class="system-guide-badge limit">제한 근거 확인</span><span>강수 발달을 제한하는 독립 가설이 상세자료에서 확인됨</span></div><div class="system-guide-status"><span class="system-guide-badge context">관련 근거 확인</span><span>독립 판독은 없지만 복합 분석에서 관련 근거가 확인됨</span></div></div><p class="system-guide-note">※ 신호체인은 관련 확인 항목을 물리과정별로 집계하되 판단축·시간·공간 범위의 호환성을 함께 확인합니다. 호환되는 서로 다른 항목에서 지지와 제한이 각각 확인되면 ‘지지+제한’, 하나의 판독 자체가 엇갈리면 ‘혼합’, 서로 다른 범위의 지지와 제한이 직접 비교되지 않으면 ‘범위별 상반’으로 표시합니다. 참고·관련 근거는 독립적인 강수 발달 지지 근거와 구분합니다.</p></div></div></section></div><section class="system-guide-notice"><h3>유의사항</h3><ul><li>이 화면은 기상특보의 3시간·12시간 강수량 기준을 자동 판정하는 화면이 아닙니다.</li><li>감시후보 표출은 정량값 하나나 신호체인 색상 개수가 아니라 모델 강수와 여러 강수 발달 환경 근거를 함께 종합한 결과입니다.</li><li>최종 예보 판단은 관측자료와 다른 수치예측자료를 함께 확인하여 예보관이 수행합니다.</li></ul></section><section class="system-guide-criteria"><div class="system-guide-criteria-card"><strong>모델 강수 예측 신호</strong><p>후보영역과 겹치는 운영 예보권역(육상·해상)에서 6시간 강수량 1.0 mm 이상의 식별 가능한 모델 강수영역을 모델 강수 예측 신호로 확인합니다. 모델 강수 예측 확인만으로 자동 표출하지는 않습니다.</p></div><div class="system-guide-criteria-card"><strong>강수 발달 환경</strong><p>수분 공급, 하층 수렴, 중층 상승운동 등 복수의 환경 근거와 시공간 일치를 함께 분석합니다. 모델 강수와 환경이 모두 확인되고 핵심시각·공간의 4요소 완전 결합까지 검증되면 중점 감시로 강조합니다. 직접 강수가 확인된 경계후보에서 감시 가치가 ‘있음(meaningful)’이어도 자동으로 우선 확인이 되지는 않습니다. Step07에서 실제 관측된 운영상 지지 근거와 하층·중층 강제력 또는 검증된 시공간 결합이 함께 있을 때만 우선 확인으로 강조하며, 그 조건이 없거나 감시 가치가 ‘조건부(marginal)’이면 배지 없이 일반 감시후보로 표출합니다. 직접 강수가 아직 없더라도 선행환경 후보에서 복수의 핵심 환경 범주와 동역학적 강제력이 유효하면 우선 확인 또는 일반 감시로 표출할 수 있습니다.</p></div><div class="system-guide-criteria-card"><strong>다권역 정량 집계</strong><p>‘N개 권역 중 M개’와 같은 개수·권역 목록은 Step07의 정량값과 물리 상태 전이를 Python에서 재계산한 구조화 결과를 사용합니다. 925 hPa 수렴에서 음수는 발산이며, 음수가 덜 음수가 되는 변화는 발산 약화이지 수렴 강화로 계산하지 않습니다. LLM 서술의 집계값이 구조화 결과와 다르면 해당 판독을 다시 생성합니다.</p></div></section></div></section></dialog>
<dialog id="watch-condition-modal" aria-labelledby="watch-condition-title"><div class="watch-condition-content"><div class="modal-header"><div><span class="section-kicker">운영 기준</span><h2 id="watch-condition-title">강수 감시 조건</h2></div><button class="watch-condition-close" type="button">닫기</button></div><p class="watch-condition-lead">이 화면은 기상특보의 3시간·12시간 강수량 기준을 판정하는 화면이 아닙니다. 모델 강수 예측과 강수 발달 환경을 상세 예측자료로 재검증한 뒤 감시가 필요한 후보를 표출합니다.</p><section class="watch-condition-section"><h3>표출되는 감시후보</h3><div class="watch-condition-grid"><div class="watch-condition-card"><strong>직접 강수 후보</strong><p>운영 예보권역에서 직접 모델 강수가 확인된 뒤 강수 발달 환경과 운영상 감시 가치를 함께 평가합니다. 핵심시각·공간의 강수·대기 전층 수분량(TCW)·하층 수렴·중층 상승운동 4요소가 완전 결합하면 중점 감시, 감시 가치가 충분하면서 Step07에서 관측된 운영상 지지 근거와 하층·중층 강제력 또는 검증된 시공간 결합이 함께 확인된 경계후보만 우선 확인으로 강조합니다. 그 조건이 없거나 감시 가치가 조건부이지만 물리과정이 성립하는 후보는 별도 배지 없이 일반 감시로 표출합니다.</p></div><div class="watch-condition-card"><strong>선행환경 후보</strong><p>직접 모델 강수가 아직 확인되지 않아도 수분 공급·하층 수렴·중층 상승운동 등 복수의 핵심 환경 범주가 유효하고 동역학적 강제력이 포함된 경우 선행환경 후보로 유지합니다. 환경 결합과 감시 가치가 충분하면 우선 확인, 일부 제한이 있으면 일반 감시로 표출할 수 있습니다. 직접 강수 부재 자체는 선행환경 후보의 자동 비표출 사유가 아닙니다.</p></div></div></section><section class="watch-condition-section"><h3>비표출되는 경우</h3><p>직접 강수 후보에서 모델 강수가 확인되지 않은 경우, 물리과정이 성립하기 어렵거나 감시 가치가 낮거나 미확정인 경우, 또는 선행환경 후보에서 복수의 핵심 환경 범주와 동역학적 강제력이 충분히 남지 않은 경우는 비표출 감시후보에 보존합니다.</p></section><section class="watch-condition-section"><h3>정량 강수량의 역할</h3><p>모델 강수 예측 존재 판정에는 6시간 강수량 1.0 mm를 운영상 최소기준으로 사용합니다. 모델 강수 예측 후보 탐색에서는 별도로 운영권역의 6시간 강수량 최대값이 {peak_anchor_text} mm 이상인 국지 최대 시각을 정량 참고값으로 사용하며, 이 {peak_anchor_text} mm 기준은 강한 강수나 최종 표출 임계값 또는 기상특보 기준이 아닙니다.</p><p>감시후보명은 단일 최대강수 격자만으로 정하지 않습니다. 1.0 mm 이상인 연속 강수영역의 크기·중심·운영권역 겹침을 함께 계산하여 권역에 넓게 분포하면 권역명, 일부에 집중되면 북부·남부 등 세부 위치, 매우 국지적이면 ‘국지’, 주 강수영역 중심이 권역 밖이고 일부만 걸치면 ‘인접’으로 구분합니다.</p><p class="watch-condition-note">최종 표출은 정량값 하나가 아니라 모델 강수 예측, 수분 공급, 하층 수렴, 중층 상승운동, 시공간 일치와 AI의 물리과정·운영상 가치 종합판단을 반영한 결과입니다. 물리적 성립성과 감시 가치는 내부 판단축이며 상단에서 별도 등급으로 제시하지 않고 페이지 하단에 접어 둡니다. 현재 체계는 AIFS 강수 예측과 강수 발달 환경을 이용해 강수 감시후보를 분석하며, 강수형태(비·눈 등)는 별도로 판정하지 않습니다.</p></section></div></dialog>
{region_guide_dialog}
<dialog id="image-modal" aria-label="근거 이미지 확대"><div class="modal-content"><div class="modal-header"><div><h2 id="modal-title">근거 이미지</h2><p id="modal-feature" class="question-trace"></p></div><button class="modal-close" type="button">닫기</button></div><p id="modal-note" class="modal-note"></p><p id="modal-time" class="question-trace"></p><div id="modal-images" class="modal-image-grid"></div></div></dialog>
<dialog id="map-modal" aria-labelledby="map-modal-title"><div class="modal-content map-modal-content"><div class="modal-header"><div><h2 id="map-modal-title">후보 위치도</h2><p class="question-trace">후보 분석영역의 위치를 벡터지도로 확인합니다. 지도를 다시 누르면 팝업이 닫힙니다.</p></div><button class="map-modal-close" type="button">닫기</button></div><div class="map-modal-meta"><span><b>분석영역</b><i id="map-modal-area"></i></span><span><b>예상기간</b><i id="map-modal-time"></i></span></div><div class="map-modal-controls"><div class="map-zoom-controls"><button type="button" data-map-zoom="in">확대 +</button><button type="button" data-map-zoom="out">축소 −</button><button type="button" data-map-zoom="reset">원래 크기</button></div><span class="map-modal-tip">마우스 휠로 확대/축소하고, 드래그하여 이동할 수 있습니다. 지도를 다시 누르면 닫힙니다.</span></div><div id="map-modal-stage" class="map-modal-stage" tabindex="0"></div><p class="map-modal-note">표시된 사각형은 감시후보 분석에 사용된 공간 범위를 의미합니다.</p></div></dialog>
</main>
<script>
(() => {{
  function resolveAssetPath(path) {{
    if (!path || /^(?:[a-z]+:|\\/\\/|data:)/i.test(path)) return path;
    const pathname = window.location.pathname;
    let basePath;
    if (pathname.endsWith('/')) basePath = pathname;
    else if (/\\.html?$/i.test(pathname)) basePath = pathname.slice(0, pathname.lastIndexOf('/') + 1);
    else basePath = pathname + '/';
    return new URL(path, window.location.origin + basePath).href;
  }}
  const imageRetryDelays = [500, 1500];
  function bindImageWithRetry(img, path) {{
    if (!img || !path) return;
    const baseUrl = resolveAssetPath(path);
    let retryIndex = 0;
    const frame = img.closest('.overview-image-frame');
    img.addEventListener('load', () => {{
      retryIndex = 0;
      frame?.classList.remove('load-error');
    }});
    img.addEventListener('error', () => {{
      if (retryIndex < imageRetryDelays.length) {{
        const attempt = retryIndex + 1;
        const delay = imageRetryDelays[retryIndex++];
        setTimeout(() => {{
          const sep = baseUrl.includes('?') ? '&' : '?';
          img.src = baseUrl + sep + '_retry=' + attempt + '_' + Date.now();
        }}, delay);
        return;
      }}
      frame?.classList.add('load-error');
    }});
    img.src = baseUrl;
  }}
  function hydrateDeferredImages(root) {{
    if (!root) return;
    root.querySelectorAll('img[data-src]').forEach(img => {{
      const path = img.dataset.src;
      if (!path) return;
      img.removeAttribute('data-src');
      bindImageWithRetry(img, path);
    }});
  }}
  document.querySelectorAll('img[src]').forEach(img => {{
    const original = img.getAttribute('src');
    img.removeAttribute('src');
    bindImageWithRetry(img, original);
  }});
  const panels = [...document.querySelectorAll('[data-candidate-panel]')];
  const timelines = [...document.querySelectorAll('[data-candidate-timeline]')];
  const tabs = [...document.querySelectorAll('[data-candidate-target]')];
  function activateCandidate(id, updateHash = true) {{
    panels.forEach(panel => panel.classList.toggle('active', panel.dataset.candidatePanel === id));
    timelines.forEach(timeline => timeline.classList.toggle('active', timeline.dataset.candidateTimeline === id));
    tabs.forEach(tab => {{
      const active = tab.dataset.candidateTarget === id;
      tab.classList.toggle('active', active);
      tab.setAttribute('aria-selected', active ? 'true' : 'false');
    }});
    const activePanel = panels.find(panel => panel.dataset.candidatePanel === id);
    const activeOverview = activePanel?.querySelector('.overview-figure.active');
    hydrateDeferredImages(activeOverview || activePanel);
    if (updateHash && id) history.replaceState(null, '', '#' + id);
  }}
  tabs.forEach(tab => tab.addEventListener('click', () => activateCandidate(tab.dataset.candidateTarget)));
  const hashId = location.hash.replace('#','');
  const initial = tabs.some(tab => tab.dataset.candidateTarget === hashId) ? hashId : (tabs[0]?.dataset.candidateTarget || '');
  if (initial) activateCandidate(initial, false);

  function setPeakLinkedHover(source, enabled) {{
    const index = source?.dataset?.peakIndex;
    const section = source?.closest('.timeline-section');
    if (!index || !section) return;
    section.querySelectorAll(`[data-peak-index="${{index}}"]`).forEach(node => {{
      node.classList.toggle('peak-linked-hover', enabled);
    }});
  }}
  document.querySelectorAll('.timeline-peak-marker[data-peak-index]').forEach(node => {{
    node.addEventListener('mouseenter', () => setPeakLinkedHover(node, true));
    node.addEventListener('mouseleave', () => setPeakLinkedHover(node, false));
    node.addEventListener('focus', () => setPeakLinkedHover(node, true));
    node.addEventListener('blur', () => setPeakLinkedHover(node, false));
  }});

  function evidenceMasterItems(panel) {{
    if (!panel) return [];
    return [...panel.querySelectorAll('.evidence-master-item[data-evidence-target]')];
  }}
  function visibleEvidenceMasterItems(panel) {{
    return evidenceMasterItems(panel).filter(item =>
      !item.classList.contains('attention-hidden') && !item.classList.contains('role-hidden')
    );
  }}
  function activateEvidence(panel, targetId, options = {{}}) {{
    if (!panel || !targetId) return false;
    const items = evidenceMasterItems(panel);
    const item = items.find(node => node.dataset.evidenceTarget === targetId);
    const target = document.getElementById(targetId);
    if (!item || !target) return false;
    items.forEach(node => {{
      const active = node === item;
      node.classList.toggle('active', active);
      node.setAttribute('aria-selected', active ? 'true' : 'false');
    }});
    panel.querySelectorAll('.evidence-detail-panel').forEach(detailPanel => {{
      const active = detailPanel.id === targetId;
      detailPanel.hidden = !active;
      detailPanel.classList.toggle('active', active);
    }});
    const detailPane = panel.querySelector('.evidence-detail-pane');
    if (detailPane) detailPane.scrollTop = 0;
    hydrateDeferredImages(target);
    if (options.focusMaster) item.focus({{preventScroll:true}});
    if (options.scrollWorkspace) {{
      const matrix = panel.querySelector('.matrix-section');
      matrix?.scrollIntoView({{behavior:'smooth', block:'start'}});
    }}
    return true;
  }}
  function ensureVisibleEvidenceSelection(panel) {{
    if (!panel) return;
    const visible = visibleEvidenceMasterItems(panel);
    if (!visible.length) {{
      panel.querySelectorAll('.evidence-detail-panel').forEach(detailPanel => {{ detailPanel.hidden = true; }});
      return;
    }}
    const active = evidenceMasterItems(panel).find(item => item.classList.contains('active'));
    if (!active || !visible.includes(active)) activateEvidence(panel, visible[0].dataset.evidenceTarget);
  }}
  function setAttentionFilter(panel, enabled) {{
    if (!panel) return;
    const button = panel.querySelector('[data-attention-toggle]');
    evidenceMasterItems(panel).forEach(item => {{
      item.classList.toggle('attention-hidden', enabled && item.dataset.attention !== 'true');
    }});
    if (button) {{
      button.setAttribute('aria-pressed', enabled ? 'true' : 'false');
      const count = button.querySelector('b')?.textContent || '';
      const label = enabled ? '전체 보기 ' : '주의·제한만 보기 ';
      const firstText = [...button.childNodes].find(node => node.nodeType === Node.TEXT_NODE);
      if (firstText) firstText.textContent = label;
      if (button.querySelector('b')) button.querySelector('b').textContent = count;
    }}
    ensureVisibleEvidenceSelection(panel);
  }}
  function setEvidenceRoleFilter(panel, role = 'all') {{
    if (!panel) return;
    const normalized = role || 'all';
    evidenceMasterItems(panel).forEach(item => {{
      const roles = (item.dataset.evidenceRoles || '').split(/\\s+/).filter(Boolean);
      item.classList.toggle('role-hidden', normalized !== 'all' && !roles.includes(normalized));
    }});
    panel.querySelectorAll('[data-evidence-role-filter]').forEach(button => {{
      const active = button.dataset.evidenceRoleFilter === normalized;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    }});
    panel.querySelectorAll('[data-signal-role]').forEach(button => {{
      button.classList.toggle('active-filter', normalized !== 'all' && button.dataset.signalRole === normalized);
    }});
    ensureVisibleEvidenceSelection(panel);
  }}
  document.querySelectorAll('[data-attention-toggle]').forEach(button => {{
    button.addEventListener('click', () => {{
      const panel = button.closest('[data-candidate-panel]');
      const enabled = button.getAttribute('aria-pressed') !== 'true';
      setAttentionFilter(panel, enabled);
    }});
  }});
  document.querySelectorAll('[data-evidence-role-filter]').forEach(button => {{
    button.addEventListener('click', () => {{
      const panel = button.closest('[data-candidate-panel]');
      setEvidenceRoleFilter(panel, button.dataset.evidenceRoleFilter || 'all');
    }});
  }});
  document.querySelectorAll('.evidence-master-item[data-evidence-target]').forEach(button => {{
    button.addEventListener('click', () => {{
      const panel = button.closest('[data-candidate-panel]');
      activateEvidence(panel, button.dataset.evidenceTarget);
    }});
    button.addEventListener('keydown', event => {{
      if (!['ArrowDown','ArrowUp','ArrowRight','ArrowLeft'].includes(event.key)) return;
      const panel = button.closest('[data-candidate-panel]');
      const visible = visibleEvidenceMasterItems(panel);
      const index = visible.indexOf(button);
      if (index < 0 || visible.length < 2) return;
      event.preventDefault();
      const direction = ['ArrowDown','ArrowRight'].includes(event.key) ? 1 : -1;
      const next = visible[(index + direction + visible.length) % visible.length];
      activateEvidence(panel, next.dataset.evidenceTarget, {{focusMaster:true}});
    }});
  }});
  document.querySelectorAll('[data-evidence-nav]').forEach(button => {{
    button.addEventListener('click', () => {{
      const panel = button.closest('[data-candidate-panel]');
      if (!panel) return;
      const visible = visibleEvidenceMasterItems(panel);
      if (!visible.length) return;
      const activeIndex = visible.findIndex(item => item.classList.contains('active'));
      const delta = button.dataset.evidenceNav === 'prev' ? -1 : 1;
      const nextIndex = activeIndex < 0 ? 0 : (activeIndex + delta + visible.length) % visible.length;
      activateEvidence(panel, visible[nextIndex].dataset.evidenceTarget);
    }});
  }});

  document.querySelectorAll('[data-minority-target]').forEach(button => {{
    button.addEventListener('click', () => {{
      const targetId = button.dataset.minorityTarget || '';
      const target = targetId ? document.getElementById(targetId) : null;
      if (!target) return;
      const report = target.closest('.minority-report-panel');
      if (report) report.open = true;
      target.classList.add('minority-focus');
      target.scrollIntoView({{behavior:'smooth', block:'center'}});
      window.setTimeout(() => target.classList.remove('minority-focus'), 1800);
    }});
  }});

  document.querySelectorAll('[data-signal-role]').forEach(button => {{
    button.addEventListener('click', () => {{
      const panel = button.closest('[data-candidate-panel]');
      if (!panel) return;
      const detail = panel.querySelector('.detail-zone-disclosure');
      if (detail) detail.open = true;
      setAttentionFilter(panel, false);
      const role = button.dataset.signalRole || 'all';
      setEvidenceRoleFilter(panel, role);
      const visible = visibleEvidenceMasterItems(panel);
      if (visible.length) activateEvidence(panel, visible[0].dataset.evidenceTarget, {{scrollWorkspace:true}});
    }});
  }});

  document.querySelectorAll('.overview-tab').forEach(button => {{
    button.addEventListener('click', () => {{
      const viewer = button.closest('.overview-viewer');
      viewer.querySelectorAll('.overview-tab').forEach(tab => {{tab.classList.remove('active'); tab.setAttribute('aria-selected','false');}});
      viewer.querySelectorAll('.overview-figure').forEach(fig => {{fig.hidden = true; fig.classList.remove('active');}});
      button.classList.add('active'); button.setAttribute('aria-selected','true');
      const target = document.getElementById(button.dataset.overviewTarget);
      if (target) {{
        target.hidden = false; target.classList.add('active');
        hydrateDeferredImages(target);
      }}
    }});
  }});

  document.querySelectorAll('.alignment-check-link').forEach(button => {{
    button.addEventListener('click', () => {{
      const targetId = button.dataset.evidenceTarget;
      const target = document.getElementById(targetId);
      if (!target) return;
      const panel = target.closest('[data-candidate-panel]');
      if (!panel) return;
      const detail = panel.querySelector('.detail-zone-disclosure');
      if (detail) detail.open = true;
      setAttentionFilter(panel, false);
      setEvidenceRoleFilter(panel, 'all');
      activateEvidence(panel, targetId, {{scrollWorkspace:true}});
    }});
  }});

  const systemGuideDialog = document.getElementById('system-guide-modal');
  const systemGuideButton = document.getElementById('system-guide-button');
  const systemGuideClose = systemGuideDialog?.querySelector('.system-guide-close');
  systemGuideButton?.addEventListener('click', () => systemGuideDialog?.showModal());
  systemGuideClose?.addEventListener('click', () => systemGuideDialog?.close());
  systemGuideDialog?.addEventListener('click', event => {{ if (event.target === systemGuideDialog) systemGuideDialog.close(); }});

  const watchDialog = document.getElementById('watch-condition-modal');
  const watchButton = document.getElementById('watch-condition-button');
  const watchClose = watchDialog?.querySelector('.watch-condition-close');
  watchButton?.addEventListener('click', () => watchDialog?.showModal());
  watchClose?.addEventListener('click', () => watchDialog?.close());
  watchDialog?.addEventListener('click', event => {{ if (event.target === watchDialog) watchDialog.close(); }});

  const regionGuideDialog = document.getElementById('region-guide-modal');
  const regionGuideButton = document.getElementById('region-guide-button');
  const regionGuideClose = regionGuideDialog?.querySelector('.region-guide-close');
  const regionGuideDataNode = document.getElementById('region-guide-data');
  const regionGuideSvg = document.getElementById('region-guide-svg');
  const regionGuideName = document.getElementById('region-guide-selected-name');
  const regionGuideRole = document.getElementById('region-guide-selected-role');
  const regionGuideDefinition = document.getElementById('region-guide-selected-definition');
  const regionGuidePurpose = document.getElementById('region-guide-selected-purpose');
  const regionGuideStage = regionGuideDialog?.querySelector('.region-guide-map-stage');
  const regionGuideZoomControls = regionGuideDialog?.querySelectorAll('[data-region-guide-zoom]') || [];
  let regionGuideCatalog = {{}};
  let regionGuideViewInitialized = false;
  let regionGuideDragging = false;
  let regionGuideDragStart = null;
  let regionGuideDragView = null;
  try {{ regionGuideCatalog = regionGuideDataNode ? JSON.parse(regionGuideDataNode.textContent || '{{}}') : {{}}; }} catch (_) {{ regionGuideCatalog = {{}}; }}
  function selectRegionGuide(regionId) {{
    const info = regionGuideCatalog[regionId];
    if (!info) return;
    regionGuideDialog?.querySelectorAll('.region-guide-region-button').forEach(button => {{
      button.classList.toggle('active', button.dataset.regionGuideId === regionId);
    }});
    regionGuideDialog?.querySelectorAll('[data-region-guide-shape]').forEach(shape => {{
      shape.classList.toggle('active', shape.dataset.regionGuideShape === regionId);
    }});
    if (regionGuideName) regionGuideName.textContent = info.name || '';
    if (regionGuideRole) regionGuideRole.textContent = info.role_label || '';
    if (regionGuideDefinition) regionGuideDefinition.textContent = info.definition || '';
    if (regionGuidePurpose) regionGuidePurpose.textContent = info.analysis_purpose || '';
  }}
  function regionGuideDefaultView() {{
    if (!regionGuideSvg) return [0, 0, 270, 225];
    const raw = (regionGuideSvg.dataset.defaultViewbox || regionGuideSvg.getAttribute('viewBox') || '0 0 270 225')
      .trim().split(/\\s+/).map(Number);
    return raw.length === 4 && raw.every(Number.isFinite) ? raw : [0, 0, 270, 225];
  }}
  function regionGuideCurrentView() {{
    if (!regionGuideSvg) return regionGuideDefaultView();
    const raw = (regionGuideSvg.getAttribute('viewBox') || '').trim().split(/\\s+/).map(Number);
    return raw.length === 4 && raw.every(Number.isFinite) ? raw : regionGuideDefaultView();
  }}
  function setRegionGuideView(view) {{
    if (!regionGuideSvg || !Array.isArray(view) || view.length !== 4) return;
    regionGuideSvg.setAttribute('viewBox', view.map(value => Number(value).toFixed(3)).join(' '));
  }}
  function clampRegionGuideView(view) {{
    const base = regionGuideDefaultView();
    let [x, y, width, height] = view.map(Number);
    const minWidth = base[2] * 0.16;
    const minHeight = base[3] * 0.16;
    width = Math.max(minWidth, Math.min(base[2], width));
    height = Math.max(minHeight, Math.min(base[3], height));
    x = Math.max(base[0], Math.min(base[0] + base[2] - width, x));
    y = Math.max(base[1], Math.min(base[1] + base[3] - height, y));
    return [x, y, width, height];
  }}
  function zoomRegionGuide(scale, clientX = null, clientY = null) {{
    if (!regionGuideSvg || !regionGuideStage) return;
    const current = regionGuideCurrentView();
    const rect = regionGuideStage.getBoundingClientRect();
    const px = Number.isFinite(clientX) ? (clientX - rect.left) / Math.max(1, rect.width) : 0.5;
    const py = Number.isFinite(clientY) ? (clientY - rect.top) / Math.max(1, rect.height) : 0.5;
    const anchorX = current[0] + current[2] * Math.max(0, Math.min(1, px));
    const anchorY = current[1] + current[3] * Math.max(0, Math.min(1, py));
    const width = current[2] * scale;
    const height = current[3] * scale;
    const next = clampRegionGuideView([
      anchorX - width * Math.max(0, Math.min(1, px)),
      anchorY - height * Math.max(0, Math.min(1, py)),
      width,
      height,
    ]);
    setRegionGuideView(next);
  }}
  function initializeRegionGuideView() {{
    if (regionGuideViewInitialized || !regionGuideSvg) return;
    const selected = regionGuideDialog?.querySelector('.region-guide-region-button.active');
    const info = selected?.dataset.regionGuideId ? regionGuideCatalog[selected.dataset.regionGuideId] : null;
    if (info && Array.isArray(info.viewbox) && info.viewbox.length === 4) setRegionGuideView(info.viewbox);
    regionGuideViewInitialized = true;
  }}

  regionGuideZoomControls.forEach(button => {{
    button.addEventListener('click', () => {{
      const action = button.dataset.regionGuideZoom;
      if (action === 'in') zoomRegionGuide(0.8);
      else if (action === 'out') zoomRegionGuide(1.25);
      else if (action === 'reset') setRegionGuideView(regionGuideDefaultView());
    }});
  }});
  regionGuideStage?.addEventListener('wheel', event => {{
    event.preventDefault();
    zoomRegionGuide(event.deltaY < 0 ? 0.88 : 1.14, event.clientX, event.clientY);
  }}, {{passive:false}});
  regionGuideStage?.addEventListener('pointerdown', event => {{
    if (event.button !== 0) return;
    regionGuideDragging = true;
    regionGuideDragStart = [event.clientX, event.clientY];
    regionGuideDragView = regionGuideCurrentView();
    regionGuideStage.classList.add('dragging');
    regionGuideStage.setPointerCapture?.(event.pointerId);
  }});
  regionGuideStage?.addEventListener('pointermove', event => {{
    if (!regionGuideDragging || !regionGuideDragStart || !regionGuideDragView) return;
    const rect = regionGuideStage.getBoundingClientRect();
    const dx = (event.clientX - regionGuideDragStart[0]) * regionGuideDragView[2] / Math.max(1, rect.width);
    const dy = (event.clientY - regionGuideDragStart[1]) * regionGuideDragView[3] / Math.max(1, rect.height);
    setRegionGuideView(clampRegionGuideView([
      regionGuideDragView[0] - dx,
      regionGuideDragView[1] - dy,
      regionGuideDragView[2],
      regionGuideDragView[3],
    ]));
  }});
  function endRegionGuideDrag(event) {{
    if (!regionGuideDragging) return;
    regionGuideDragging = false;
    regionGuideDragStart = null;
    regionGuideDragView = null;
    regionGuideStage?.classList.remove('dragging');
    if (event?.pointerId !== undefined) regionGuideStage?.releasePointerCapture?.(event.pointerId);
  }}
  regionGuideStage?.addEventListener('pointerup', endRegionGuideDrag);
  regionGuideStage?.addEventListener('pointercancel', endRegionGuideDrag);

  function showRegionGuideRole(role) {{
    regionGuideDialog?.querySelectorAll('.region-guide-role-tab').forEach(tab => {{
      tab.classList.toggle('active', tab.dataset.regionGuideRole === role);
    }});
    const visible = [];
    regionGuideDialog?.querySelectorAll('.region-guide-region-button').forEach(button => {{
      const show = button.dataset.regionGuideRoleName === role;
      button.hidden = !show;
      if (show) visible.push(button);
    }});
    const selected = visible.find(button => button.classList.contains('active')) || visible[0];
    if (selected?.dataset.regionGuideId) selectRegionGuide(selected.dataset.regionGuideId);
  }}
  function openRegionGuideForName(regionName) {{
    const match = Object.entries(regionGuideCatalog).find(([, info]) => info?.name === regionName);
    if (!match) return;
    const [regionId, info] = match;
    if (info?.role) showRegionGuideRole(info.role);
    selectRegionGuide(regionId);
    if (Array.isArray(info?.viewbox) && info.viewbox.length === 4) {{
      setRegionGuideView(info.viewbox);
      regionGuideViewInitialized = true;
    }}
    regionGuideDialog?.showModal();
  }}
  document.querySelectorAll('.timeline-peak-region-link[data-region-guide-name]').forEach(button => {{
    button.addEventListener('click', () => openRegionGuideForName(button.dataset.regionGuideName || ''));
  }});
  regionGuideDialog?.querySelectorAll('.region-guide-role-tab').forEach(tab => {{
    tab.addEventListener('click', () => showRegionGuideRole(tab.dataset.regionGuideRole || ''));
  }});
  regionGuideDialog?.querySelectorAll('.region-guide-region-button').forEach(button => {{
    button.addEventListener('click', () => selectRegionGuide(button.dataset.regionGuideId || ''));
  }});
  regionGuideButton?.addEventListener('click', () => {{
    const activeTab = regionGuideDialog?.querySelector('.region-guide-role-tab.active');
    if (activeTab?.dataset.regionGuideRole) showRegionGuideRole(activeTab.dataset.regionGuideRole);
    initializeRegionGuideView();
    regionGuideDialog?.showModal();
  }});
  regionGuideClose?.addEventListener('click', () => regionGuideDialog?.close());
  regionGuideDialog?.addEventListener('click', event => {{ if (event.target === regionGuideDialog) regionGuideDialog.close(); }});

  const dialog = document.getElementById('image-modal');
  const title = document.getElementById('modal-title');
  const feature = document.getElementById('modal-feature');
  const note = document.getElementById('modal-note');
  const time = document.getElementById('modal-time');
  const images = document.getElementById('modal-images');
  document.querySelector('.modal-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {{ if (event.target === dialog) dialog.close(); }});
  images.addEventListener('click', event => {{
    if (images.classList.contains('focus-environment-grid')) return;
    if (event.target.closest('img') || event.target === images) dialog.close();
  }});
  function showImage(path, alt, caption) {{
    images.classList.remove('focus-environment-grid');
    images.replaceChildren();
    const img = document.createElement('img');
    img.alt = alt;
    img.title = '이미지를 클릭하면 닫힙니다.';
    images.appendChild(img);
    bindImageWithRetry(img, path);
    time.textContent = caption || '';
    dialog.showModal();
  }}
  function showKeyEnvironmentImages(items, focusTime, focusArea) {{
    if (!Array.isArray(items)) return;
    const validItems = items.filter(item => item && item.relative_path && item.label);
    if (validItems.length < 2) return;
    images.replaceChildren();
    images.classList.add('focus-environment-grid');
    validItems.forEach(item => {{
      const figureNode = document.createElement('figure');
      figureNode.className = 'focus-environment-figure';
      const img = document.createElement('img');
      img.alt = `${{focusTime}} ${{item.label}}`;
      const caption = document.createElement('figcaption');
      caption.textContent = item.label;
      figureNode.append(img, caption);
      images.appendChild(figureNode);
      bindImageWithRetry(img, item.relative_path);
    }});
    title.textContent = '핵심 발달환경 원본 기상장';
    feature.textContent = focusArea ? `판독 위치: ${{focusArea}}` : '';
    note.textContent = `같은 시각에 위치판독에 사용된 실제 상세 예측장 ${{validItems.length}}개를 확인합니다. 지도에서 표시한 위치의 원본 판독 근거이며 별도 결합도나 임계값을 새로 계산한 결과가 아닙니다.`;
    time.textContent = focusTime || '';
    dialog.showModal();
  }}
  document.querySelectorAll('.key-environment-location-button').forEach(button => {{
    button.addEventListener('click', () => {{
      let payload;
      try {{ payload = JSON.parse(button.dataset.focusImages || '[]'); }} catch (_) {{ return; }}
      showKeyEnvironmentImages(payload, button.dataset.focusTime || '', button.dataset.focusArea || '');
    }});
  }});

  const svgNS = ['http:', '', 'www.w3.org', '2000', 'svg'].join('/');
  function svgNode(name, attrs={{}}) {{
    const node = document.createElementNS(svgNS, name);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  }}
  document.querySelectorAll('.quant-series-card').forEach(card => {{
    let payload; try {{ payload = JSON.parse(card.dataset.quantSeries); }} catch (_) {{ return; }}
    const svg = card.querySelector('.quant-series-svg');
    const tooltip = card.querySelector('.quant-series-tooltip');
    const selector = card.querySelector('.quant-series-region');
    if (!svg || !Array.isArray(payload.regions) || !payload.regions.length) return;
    const draw = () => {{
      const regionId = selector ? selector.value : payload.regions[0].region_id;
      const region = payload.regions.find(item => item.region_id === regionId) || payload.regions[0];
      const points = Array.isArray(region.points) ? region.points : [];
      svg.replaceChildren();
      if (points.length < 2) return;
      const width = 720, height = 260, left = 64, right = 20, top = 18, bottom = 48;
      const values = points.map(point => Number(point.value)).filter(Number.isFinite);
      if (!values.length) return;
      let ymin = Math.min(...values), ymax = Math.max(...values);
      if (ymin === ymax) {{ const pad = Math.abs(ymin || 1) * .15; ymin -= pad; ymax += pad; }}
      else {{ const pad = (ymax - ymin) * .12; ymin -= pad; ymax += pad; }}
      const x = index => left + index * ((width - left - right) / Math.max(1, points.length - 1));
      const y = value => top + (ymax - value) * ((height - top - bottom) / (ymax - ymin));
      for (let tick=0; tick<=4; tick++) {{
        const value = ymin + (ymax - ymin) * tick / 4;
        const yy = y(value);
        svg.appendChild(svgNode('line', {{x1:left,y1:yy,x2:width-right,y2:yy,class:'quant-series-grid'}}));
        const label = svgNode('text', {{x:left-8,y:yy+4,'text-anchor':'end',class:'quant-series-label'}});
        label.textContent = Number(value.toFixed(2)).toString(); svg.appendChild(label);
      }}
      svg.appendChild(svgNode('line', {{x1:left,y1:top,x2:left,y2:height-bottom,class:'quant-series-axis'}}));
      svg.appendChild(svgNode('line', {{x1:left,y1:height-bottom,x2:width-right,y2:height-bottom,class:'quant-series-axis'}}));
      const polyline = svgNode('polyline', {{class:'quant-series-line',points:points.map((point,index)=>`${{x(index)}},${{y(Number(point.value))}}`).join(' ')}});
      svg.appendChild(polyline);
      points.forEach((point,index) => {{
        const xx=x(index), yy=y(Number(point.value));
        const xlabel=svgNode('text', {{x:xx,y:height-bottom+20,'text-anchor':'middle',class:'quant-series-label'}});
        xlabel.textContent=`F${{String(point.hour).padStart(3,'0')}}`; svg.appendChild(xlabel);
        const circle=svgNode('circle', {{cx:xx,cy:yy,r:5,class:`quant-series-point${{point.related_image ? ' has-image' : ''}}`,tabindex:point.related_image ? 0 : -1}});
        const showTip = event => {{
          tooltip.hidden=false;
          tooltip.textContent=`${{region.region_name}} · F${{String(point.hour).padStart(3,'0')}} · ${{point.display_value}} ${{payload.unit}}`;
          const rect=card.querySelector('.quant-series-stage').getBoundingClientRect();
          tooltip.style.left=`${{Math.min(rect.width-220, Math.max(4, event.clientX-rect.left+10))}}px`;
          tooltip.style.top=`${{Math.max(4, event.clientY-rect.top-36)}}px`;
        }};
        circle.addEventListener('pointerenter', showTip);
        circle.addEventListener('pointermove', showTip);
        circle.addEventListener('pointerleave', () => tooltip.hidden=true);
        if (point.related_image) {{
          const openRelated = () => {{
            const meta=point.related_image;
            title.textContent='정량 시간변화와 연결된 대표 공간장';
            feature.textContent=payload.feature_label;
            note.textContent='1차 AI가 시간변화 비교를 위해 선택한 동일 시각의 대표 공간장입니다.';
            showImage(meta.relative_path, payload.feature_label, `${{point.time}} · ${{meta.diagnostic_label || ''}}`);
          }};
          circle.addEventListener('click', openRelated);
          circle.addEventListener('keydown', event => {{ if (event.key === 'Enter' || event.key === ' ') {{ event.preventDefault(); openRelated(); }} }});
        }}
        svg.appendChild(circle);
      }});
      const unit=svgNode('text', {{x:left,y:12,class:'quant-series-label'}}); unit.textContent=payload.unit || ''; svg.appendChild(unit);
    }};
    selector?.addEventListener('change', draw);
    draw();
  }});
  document.querySelectorAll('.overview-image-button').forEach(button => {{
    button.addEventListener('click', () => {{
      title.textContent = '감시후보 개요이미지'; feature.textContent = '';
      note.textContent = '감시후보 탐색에 사용된 개요 예측장입니다. 상단은 탐색 맥락, 하단은 상세 재검증 결과입니다.';
      showImage(button.dataset.imagePath, '감시후보 개요이미지', button.dataset.imageCaption);
    }});
  }});
  document.querySelectorAll('.quant-feature-button').forEach(button => {{
    button.addEventListener('click', () => {{
      let meta; try {{ meta = JSON.parse(button.dataset.relatedImage); }} catch (_) {{ return; }}
      title.textContent = '정량 분석값과 연결된 상세 예측장';
      feature.textContent = button.dataset.featureLabel;
      note.textContent = '동일 확인 항목에서 선택된 대표 상세 예측장을 정량값과 함께 확인합니다.';
      showImage(meta.relative_path, button.dataset.featureLabel, button.dataset.quantitativeTime + ' · ' + (meta.diagnostic_label || ''));
    }});
  }});
  document.querySelectorAll('.evidence-image-button').forEach(button => {{
    button.addEventListener('click', () => {{
      title.textContent = '대표 판단 근거 이미지'; feature.textContent = '';
      note.textContent = '최종 분석에 사용된 대표 상세 예측장입니다.';
      showImage(button.dataset.imagePath, '대표 판단 근거 이미지', button.dataset.imageCaption);
    }});
  }});

  const mapDialog = document.getElementById('map-modal');
  const mapClose = mapDialog?.querySelector('.map-modal-close');
  const mapStage = document.getElementById('map-modal-stage');
  const mapArea = document.getElementById('map-modal-area');
  const mapTime = document.getElementById('map-modal-time');
  const mapTitle = document.getElementById('map-modal-title');
  let mapBase = {{ x: 0, y: 0, w: {MINIMAP_VIEWBOX_WIDTH:.0f}, h: {MINIMAP_VIEWBOX_HEIGHT:.0f} }};
  let mapState = {{ ...mapBase }};
  let mapSvg = null;
  let dragState = null;
  let dragMoved = false;
  function clampMapState() {{
    mapState.w = Math.min(mapBase.w, Math.max(Math.max(8, mapBase.w * 0.2), mapState.w));
    mapState.h = Math.min(mapBase.h, Math.max(Math.max(6, mapBase.h * 0.2), mapState.h));
    mapState.x = Math.min(Math.max(mapState.x, mapBase.x), mapBase.x + mapBase.w - mapState.w);
    mapState.y = Math.min(Math.max(mapState.y, mapBase.y), mapBase.y + mapBase.h - mapState.h);
  }}
  function applyMapView() {{
    if (!mapSvg) return;
    clampMapState();
    mapSvg.setAttribute('viewBox', `${{mapState.x}} ${{mapState.y}} ${{mapState.w}} ${{mapState.h}}`);
  }}
  function resetMapView() {{
    mapState = {{ ...mapBase }};
    applyMapView();
  }}
  function zoomMap(factor, clientX = null, clientY = null) {{
    if (!mapSvg || !mapStage) return;
    const rect = mapStage.getBoundingClientRect();
    const anchorX = clientX == null ? rect.left + rect.width / 2 : clientX;
    const anchorY = clientY == null ? rect.top + rect.height / 2 : clientY;
    const ratioX = rect.width ? (anchorX - rect.left) / rect.width : 0.5;
    const ratioY = rect.height ? (anchorY - rect.top) / rect.height : 0.5;
    const worldX = mapState.x + ratioX * mapState.w;
    const worldY = mapState.y + ratioY * mapState.h;
    const nextW = mapState.w * factor;
    const nextH = mapState.h * factor;
    mapState = {{
      w: nextW,
      h: nextH,
      x: worldX - ratioX * nextW,
      y: worldY - ratioY * nextH,
    }};
    applyMapView();
  }}
  document.querySelectorAll('.inline-map-trigger').forEach(button => {{
    button.addEventListener('click', event => {{
      event.stopPropagation();
      const svg = button.querySelector('.map-template svg');
      if (!svg || !mapDialog || !mapStage) return;
      mapStage.replaceChildren();
      mapSvg = svg.cloneNode(true);
      mapStage.appendChild(mapSvg);
      const defaultViewBox = String(mapSvg.dataset.defaultViewbox || '').trim().split(/\\s+/).map(Number);
      if (defaultViewBox.length === 4 && defaultViewBox.every(Number.isFinite) && defaultViewBox[2] > 0 && defaultViewBox[3] > 0) {{
        mapBase = {{ x: defaultViewBox[0], y: defaultViewBox[1], w: defaultViewBox[2], h: defaultViewBox[3] }};
      }} else {{
        mapBase = {{ x: 0, y: 0, w: {MINIMAP_VIEWBOX_WIDTH:.0f}, h: {MINIMAP_VIEWBOX_HEIGHT:.0f} }};
      }}
      mapTitle.textContent = button.dataset.mapTitle || '후보 위치도';
      mapArea.textContent = button.dataset.mapArea || '';
      mapTime.textContent = button.dataset.mapTime || '';
      resetMapView();
      mapDialog.showModal();
    }});
  }});
  mapClose?.addEventListener('click', () => mapDialog?.close());
  mapDialog?.addEventListener('click', event => {{ if (event.target === mapDialog) mapDialog.close(); }});
  mapDialog?.querySelectorAll('[data-map-zoom]').forEach(button => {{
    button.addEventListener('click', () => {{
      const mode = button.getAttribute('data-map-zoom');
      if (mode === 'in') zoomMap(0.82);
      else if (mode === 'out') zoomMap(1.22);
      else resetMapView();
    }});
  }});
  mapStage?.addEventListener('wheel', event => {{
    if (!mapSvg) return;
    event.preventDefault();
    zoomMap(event.deltaY < 0 ? 0.9 : 1.1, event.clientX, event.clientY);
  }}, {{ passive: false }});
  mapStage?.addEventListener('pointerdown', event => {{
    if (!mapSvg || event.button !== 0) return;
    dragMoved = false;
    dragState = {{ x: event.clientX, y: event.clientY, mapX: mapState.x, mapY: mapState.y }};
    mapStage.classList.add('dragging');
    mapStage.setPointerCapture(event.pointerId);
  }});
  mapStage?.addEventListener('pointermove', event => {{
    if (!dragState || !mapSvg) return;
    const rect = mapStage.getBoundingClientRect();
    const dx = event.clientX - dragState.x;
    const dy = event.clientY - dragState.y;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) dragMoved = true;
    mapState.x = dragState.mapX - (dx / rect.width) * mapState.w;
    mapState.y = dragState.mapY - (dy / rect.height) * mapState.h;
    applyMapView();
  }});
  function endMapDrag(event) {{
    if (!dragState || !mapStage) return;
    dragState = null;
    mapStage.classList.remove('dragging');
    if (event && mapStage.hasPointerCapture(event.pointerId)) mapStage.releasePointerCapture(event.pointerId);
  }}
  mapStage?.addEventListener('pointerup', endMapDrag);
  mapStage?.addEventListener('pointercancel', endMapDrag);
  mapStage?.addEventListener('click', event => {{
    if (!mapDialog?.open || !mapSvg) return;
    if (dragMoved) {{ dragMoved = false; return; }}
    if (event.target && String(event.target.tagName || '').toLowerCase() !== 'button') mapDialog.close();
  }});
  mapStage?.addEventListener('dblclick', () => resetMapView());
}})();
</script>
</body></html>
'''


def _write_stage(root: Path, stage: Path, product: dict[str, Any]) -> None:
    validate_schema(root, FORECASTER_PRODUCT_SCHEMA, product, code="FORECASTER_PRODUCT_SCHEMA_INVALID")
    try:
        validate_majority_product(product)
    except MajorityNarrativeError as exc:
        raise Pass2PipelineError("QUANTITATIVE_NARRATIVE_INVALID", str(exc)) from exc
    language_errors = validate_forecaster_product_narrative(product)
    if language_errors:
        raise Pass2PipelineError(
            "NARRATIVE_PUBLICATION_BLOCKED",
            "최종제품 자연어에 내부 영문 용어 또는 허용되지 않은 외국어가 남아 있습니다. "
            + "; ".join(language_errors[:12]),
        )
    overview_by_candidate = _prepare_overview_assets(root, product["init"], product, stage)
    excluded_roles_by_candidate = _excluded_independent_roles(root, product["init"])
    related_roles_by_candidate = _related_multivariable_role_assessments(root, product["init"])
    minimap_geography = _load_minimap_geography(root)
    region_guide_regions = _load_region_guide_regions(root)
    minority_report_path = step07_minority_report_path(root, product["init"])
    step07_minority_report = load_json_object(minority_report_path, code="STEP07_MINORITY_REPORT_MISSING")
    validate_schema(root, MINORITY_REPORT_SCHEMA, step07_minority_report, code="STEP07_MINORITY_REPORT_INVALID")
    step07_minority_report = _apply_minority_report_assets(step07_minority_report, product.get("minority_report_assets"))
    peak_precip_details_by_candidate = _peak_six_hour_precip_details_by_candidate(root, product["init"], product)
    (stage / "forecaster_product.json").write_text(json.dumps(product, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (stage / "index.html").write_text(
        _render_html(
            product,
            _forecast_timeline_from_collection_config(root),
            overview_by_candidate,
            _direct_precipitation_peak_anchor_mm(root),
            minimap_geography,
            excluded_roles_by_candidate,
            related_roles_by_candidate,
            peak_precip_details_by_candidate,
            region_guide_regions,
            step07_minority_report,
        ),
        encoding="utf-8",
    )

def _assert_local_path(package_dir: Path, relative: str) -> Path:
    local_path = urlsplit(str(relative)).path
    candidate = (package_dir / local_path).resolve()
    try:
        candidate.relative_to(package_dir.resolve())
    except ValueError as exc:
        raise Pass2PipelineError("HTML_REFERENCE_INVALID", f"HTML references a path outside the product: {relative}") from exc
    return candidate


def validate_published_product(root: Path, product_dir: Path) -> dict[str, Any]:
    """Validate a complete permanent package without reading any ``daio`` file."""
    if not product_dir.is_dir():
        raise Pass2PipelineError("FORECASTER_PRODUCT_MISSING", f"Step10 product directory is missing: {product_dir}")
    required_names = {"index.html", "forecaster_product.json", "assets"}
    allowed_names = required_names | {"history"}
    names = {path.name for path in product_dir.iterdir()}
    if not required_names.issubset(names) or not names.issubset(allowed_names):
        raise Pass2PipelineError("PERMANENT_OUTPUT_INVALID", "Step10 permanent output contains unexpected or missing files.")
    history_dir = product_dir / "history"
    if history_dir.exists() and not history_dir.is_dir():
        raise Pass2PipelineError("PERMANENT_OUTPUT_INVALID", "Step10 history entry must be a directory.")
    product_path = product_dir / "forecaster_product.json"
    html_path = product_dir / "index.html"
    product = load_json_object(product_path, code="FORECASTER_PRODUCT_INVALID")
    validate_schema(root, FORECASTER_PRODUCT_SCHEMA, product, code="FORECASTER_PRODUCT_SCHEMA_INVALID")
    try:
        validate_majority_product(product)
    except MajorityNarrativeError as exc:
        raise Pass2PipelineError("QUANTITATIVE_NARRATIVE_INVALID", str(exc)) from exc
    language_errors = validate_forecaster_product_narrative(product)
    if language_errors:
        raise Pass2PipelineError(
            "NARRATIVE_PUBLICATION_BLOCKED",
            "게시된 최종제품 자연어가 한글 기상용어 기준을 충족하지 않습니다. " + "; ".join(language_errors[:12]),
        )
    expected_status = "published" if product["surfaced_candidates"] else "no_surface_candidates"
    if product["product_status"] != expected_status:
        raise Pass2PipelineError("FORECASTER_PRODUCT_INVALID", "product_status does not match surfaced candidate count.")
    summary = product["summary"]
    if summary["candidate_count"] != len(product["surfaced_candidates"]) + len(product["non_surfaced_candidates"]) or summary["surfaced_candidate_count"] != len(product["surfaced_candidates"]) or summary["non_surfaced_candidate_count"] != len(product["non_surfaced_candidates"]):
        raise Pass2PipelineError("FORECASTER_PRODUCT_INVALID", "Product summary counts do not match candidate arrays.")
    surfaced_ids = {candidate["candidate_id"] for candidate in product["surfaced_candidates"]}
    non_surfaced_ids = {candidate["candidate_id"] for candidate in product["non_surfaced_candidates"]}
    if surfaced_ids & non_surfaced_ids or len(surfaced_ids) != len(product["surfaced_candidates"]) or len(non_surfaced_ids) != len(product["non_surfaced_candidates"]):
        raise Pass2PipelineError("FORECASTER_PRODUCT_INVALID", "Candidate arrays are not a unique partition.")
    all_candidate_ids = surfaced_ids | non_surfaced_ids
    asset_map = {asset["asset_id"]: asset for asset in product["assets"]}
    if len(asset_map) != len(product["assets"]) or len({asset["sha256"] for asset in product["assets"]}) != len(product["assets"]):
        raise Pass2PipelineError("ASSET_DEDUP_INVALID", "Assets are not deduplicated by SHA-256.")
    expected_asset_names: set[str] = set()
    for asset in product["assets"]:
        relative = asset["relative_path"]
        path = _assert_local_path(product_dir, relative)
        if _HASH_ASSET_FILENAME.fullmatch(path.name):
            raise Pass2PipelineError("ASSET_FILENAME_INVALID", f"Published asset filename is not forecaster-readable: {path.name}")
        source_refs = [item for item in asset["source_refs"] if isinstance(item, dict)]
        if not any(
            str(source["request_id"]) in path.name
            and str(source["physical_diagnostic"]) in path.name
            and "_F" in path.stem
            for source in source_refs
        ):
            raise Pass2PipelineError("ASSET_FILENAME_INVALID", f"Published asset filename has no readable source identity: {path.name}")
        expected_asset_names.add(path.name)
        if not path.is_file() or sha256sum(path) != asset["sha256"]:
            raise Pass2PipelineError("ASSET_HASH_INVALID", f"Published asset is missing or has a different hash: {asset['asset_id']}")
        if not set(asset["candidate_ids"]).issubset(all_candidate_ids):
            raise Pass2PipelineError("ASSET_CANDIDATE_INVALID", "Published asset references an unknown candidate id.")
    asset_dir = product_dir / "assets"
    if not asset_dir.is_dir():
        raise Pass2PipelineError("PERMANENT_OUTPUT_INVALID", "Step10 assets directory is missing.")
    asset_entries = list(asset_dir.iterdir())
    root_asset_names = {path.name for path in asset_entries if path.is_file()}
    unexpected_asset_dirs = [path.name for path in asset_entries if path.is_dir() and path.name != "overview"]
    if root_asset_names != expected_asset_names or unexpected_asset_dirs:
        raise Pass2PipelineError(
            "PERMANENT_OUTPUT_INVALID",
            "assets root does not exactly match product metadata or contains an unexpected directory.",
        )
    overview_dir = asset_dir / "overview"
    if overview_dir.exists():
        if not overview_dir.is_dir() or any(path.is_dir() for path in overview_dir.iterdir()):
            raise Pass2PipelineError("PERMANENT_OUTPUT_INVALID", "assets/overview must be a flat directory.")
        if any(path.suffix.lower() != ".png" for path in overview_dir.iterdir() if path.is_file()):
            raise Pass2PipelineError("PERMANENT_OUTPUT_INVALID", "assets/overview may contain PNG files only.")
    for candidate in product["non_surfaced_candidates"]:
        for asset_id in candidate.get("related_image_asset_ids", []):
            if asset_id not in asset_map:
                raise Pass2PipelineError("FORECASTER_PRODUCT_INVALID", "Non-surfaced candidate references a missing related image asset.")
            asset_candidates = set(asset_map[asset_id].get("candidate_ids", []))
            if candidate["candidate_id"] not in asset_candidates:
                raise Pass2PipelineError("FORECASTER_PRODUCT_INVALID", "Non-surfaced candidate related image asset is not linked back to the candidate.")
    for entry in product.get("minority_report_assets", []):
        if not isinstance(entry, dict):
            raise Pass2PipelineError("FORECASTER_PRODUCT_INVALID", "minority_report_assets entry must be an object.")
        candidate_id = entry.get("candidate_id")
        check_id = entry.get("check_id")
        if candidate_id not in all_candidate_ids or not isinstance(check_id, str) or not check_id:
            raise Pass2PipelineError("FORECASTER_PRODUCT_INVALID", "minority_report_assets references an unknown candidate or check.")
        for member_row in entry.get("member_assets", []):
            if not isinstance(member_row, dict):
                raise Pass2PipelineError("FORECASTER_PRODUCT_INVALID", "minority_report_assets member_assets entry must be an object.")
            for asset_id in member_row.get("asset_ids", []):
                if asset_id not in asset_map:
                    raise Pass2PipelineError("FORECASTER_PRODUCT_INVALID", "minority_report_assets references a missing asset.")
                linked = asset_map[asset_id]
                if candidate_id not in set(linked.get("candidate_ids", [])) or check_id not in set(linked.get("check_ids", [])):
                    raise Pass2PipelineError("FORECASTER_PRODUCT_INVALID", "minority_report_assets asset metadata does not match candidate/check linkage.")
    for candidate in product["surfaced_candidates"]:
        block_ids = {block["check_id"] for block in candidate["evidence_blocks"]}
        if block_ids != set(candidate["publication_check_ids"]):
            raise Pass2PipelineError("FORECASTER_PRODUCT_INVALID", "Evidence blocks do not match publication checks.")
        _assert_question_evidence_published(candidate)
        localization = candidate.get("environment_localization")
        if isinstance(localization, dict):
            if localization.get("evidence_check_id") not in block_ids:
                raise Pass2PipelineError(
                    "FORECASTER_PRODUCT_INVALID",
                    "Environment localization references a non-published evidence Check.",
                )
            localization_hour = localization.get("forecast_hour")
            window = candidate.get("effective_time_window")
            if (
                not isinstance(localization_hour, int)
                or isinstance(localization_hour, bool)
                or not isinstance(window, dict)
                or localization_hour < int(window.get("forecast_hour_start", -1))
                or localization_hour > int(window.get("forecast_hour_end", -1))
            ):
                raise Pass2PipelineError(
                    "FORECASTER_PRODUCT_INVALID",
                    "Environment localization forecast hour is outside the candidate time window.",
                )
            localization_diagnostics = {
                str(source.get("physical_diagnostic") or "")
                for asset in product["assets"]
                if candidate["candidate_id"] in asset.get("candidate_ids", [])
                and localization.get("evidence_check_id") in asset.get("check_ids", [])
                for source in asset.get("source_refs", [])
                if isinstance(source, dict) and source.get("forecast_hour") == localization_hour
            }
            required_diagnostics = {
                item[0]
                for item in _environment_localization_required_diagnostics(
                    _environment_localization_components(localization)
                )
            }
            if len(required_diagnostics) < 2:
                raise Pass2PipelineError(
                    "ENVIRONMENT_LOCALIZATION_ASSET_INVALID",
                    "Environment localization has no valid localized alignment component set.",
                )
            if not required_diagnostics.issubset(localization_diagnostics):
                missing = sorted(required_diagnostics - localization_diagnostics)
                raise Pass2PipelineError(
                    "ENVIRONMENT_LOCALIZATION_ASSET_INVALID",
                    f"Environment localization is missing exact-time source fields: {missing}.",
                )
        for block in candidate["evidence_blocks"]:
            if any(asset_id not in asset_map for asset_id in block["asset_ids"]):
                raise Pass2PipelineError("ASSET_REFERENCE_INVALID", "Evidence block references an unknown asset.")
    html = html_path.read_text(encoding="utf-8") if html_path.is_file() else ""
    if not html:
        raise Pass2PipelineError("HTML_MISSING", "index.html is missing or empty.")
    for reference in _LOCAL_REFERENCE.findall(html):
        if re.match(r"^(?:https?:|//|data:|javascript:|mailto:)", reference, flags=re.IGNORECASE):
            raise Pass2PipelineError("EXTERNAL_DEPENDENCY_INVALID", f"HTML has external dependency: {reference}")
        if not _assert_local_path(product_dir, reference).is_file():
            raise Pass2PipelineError("HTML_REFERENCE_INVALID", f"HTML local reference is missing: {reference}")
    encoded_product = json.dumps(product, ensure_ascii=False)
    if "daio" in html.lower() or "daio" in encoded_product.lower():
        raise Pass2PipelineError("DAIO_RUNTIME_REFERENCE", "Permanent product contains a daio runtime reference.")
    if re.search(r"https?://|//", html, flags=re.IGNORECASE):
        raise Pass2PipelineError("EXTERNAL_DEPENDENCY_INVALID", "HTML contains an external URL dependency.")
    return product


def _remove_directory(path: Path, *, code: str) -> None:
    if not path.exists():
        return
    if not path.is_dir():
        raise Pass2PipelineError(code, f"Expected directory is a file: {path}")
    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise Pass2PipelineError(code, f"Unable to remove temporary or replaced directory: {path}") from exc




def _publish_stage_with_rollback(stage: Path, target: Path, *, overwrite: bool) -> None:
    """Atomically publish a validated stage and restore the previous product on failure."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        try:
            os.replace(stage, target)
        except OSError as exc:
            raise Pass2PipelineError("FORECASTER_PRODUCT_PUBLISH_FAILED", f"Unable to publish Step10 product: {exc}") from exc
        return

    if not overwrite:
        raise Pass2PipelineError("FORECASTER_PRODUCT_EXISTS", f"Permanent product already exists: {target}.")

    backup = target.parent / f".{target.name}.step10_previous"
    if backup.exists():
        raise Pass2PipelineError(
            "FORECASTER_PRODUCT_BACKUP_CONFLICT",
            f"Step10 temporary backup already exists and must be resolved before overwrite: {backup}",
        )

    try:
        os.replace(target, backup)
    except OSError as exc:
        raise Pass2PipelineError(
            "FORECASTER_PRODUCT_REPLACE_FAILED",
            f"Unable to preserve the previous Step10 product before overwrite: {exc}",
        ) from exc

    try:
        os.replace(stage, target)
    except OSError as publish_exc:
        try:
            if target.exists():
                raise OSError(f"new target unexpectedly exists after failed publish: {target}")
            os.replace(backup, target)
        except OSError as rollback_exc:
            raise Pass2PipelineError(
                "FORECASTER_PRODUCT_ROLLBACK_FAILED",
                f"New Step10 publish failed and automatic rollback failed. Previous product is preserved at {backup}: {rollback_exc}",
            ) from publish_exc
        raise Pass2PipelineError(
            "FORECASTER_PRODUCT_PUBLISH_FAILED",
            f"Unable to publish Step10 product; previous product was restored: {publish_exc}",
        ) from publish_exc

    try:
        _remove_directory(backup, code="FORECASTER_PRODUCT_BACKUP_CLEANUP_FAILED")
    except Pass2PipelineError:
        # The new target is already validly published. Preserve a stale hidden
        # backup rather than misreporting the valid current product as failed;
        # the next overwrite will stop on BACKUP_CONFLICT and surface it.
        pass

def generate_forecaster_product(root: Path, init: str, *, overwrite: bool = False) -> dict[str, Any]:
    """Build, validate, and atomically publish one Step10 permanent product."""
    _validate_step10_schema_contract(root)
    target = step10_output_dir(root, init)
    work = step10_work_dir(root, init)
    stage = work / "package"
    if target.exists() and not overwrite:
        raise Pass2PipelineError("FORECASTER_PRODUCT_EXISTS", f"Permanent product already exists: {target}. Use --overwrite after validation.")
    _remove_directory(work, code="STEP10_WORK_CLEANUP_FAILED")
    published = False
    try:
        stage.mkdir(parents=True, exist_ok=False)
        (stage / "assets").mkdir()
        existing_history = target / "history"
        if target.is_dir() and existing_history.is_dir():
            shutil.copytree(existing_history, stage / "history")
        product = _build_product(root, init, stage)
        _write_stage(root, stage, product)
        validate_published_product(root, stage)
        _publish_stage_with_rollback(stage, target, overwrite=overwrite)
        published = True
        return product
    except OSError as exc:
        raise Pass2PipelineError("FORECASTER_PRODUCT_PUBLISH_FAILED", f"Unable to publish Step10 product: {exc}") from exc
    finally:
        if work.exists():
            _remove_directory(work, code="STEP10_WORK_CLEANUP_FAILED")
        if not published and target.exists() and not overwrite:
            # No mutation is performed here; this branch documents preservation of an existing product.
            pass
