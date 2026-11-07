"""Shared imports, constants, and data types for the Step execution roles."""
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
COMPACT_OBSERVATION_RECOVERY_MAX_TOKENS = 8000
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
SPATIAL_ONLY_DIAGNOSTIC_IDS = {"multivariable_composite", "vertical_cross_section", "time_height_section", "vertical_wind_shear"}
SECTION_DIAGNOSTIC_IDS = {"vertical_cross_section", "time_height_section"}
CANDIDATE_SCOPE_ONLY_DIAGNOSTIC_IDS = {"time_height_section"}
MINIMUM_FORECAST_HOURS_BY_DIAGNOSTIC = {
    "temporal_evolution": 2,
    "time_height_section": 2,
}
DIAGNOSTIC_REQUIRED_VARIABLE_ROLES: dict[str, set[str]] = {
    "accumulated_precipitation": {"hazard_magnitude"},
    "moisture_flux": {"moisture_supply"},
    "horizontal_convergence": {"low_level_forcing"},
    "vertical_wind_shear": {"organization_persistence"},
    "multivariable_composite": {"synoptic_support", "uncertainty"},
}
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
_STEP04_FORECAST_HOUR_TOKEN = re.compile(
    r"(?i)(?<![A-Za-z0-9_])F\s*(\d{1,3})(?![A-Za-z0-9_])"
)
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
SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY = "SYS_DIRECT_PRECIPITATION_PRESENCE"
SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CLAIM_KEY = "SYS_CLM_DIRECT_PRECIPITATION_PRESENCE"
SYSTEM_DIRECT_PRECIPITATION_ANCHOR_EXECUTION_CONTRACT_VERSION = "2.3.0"
AIFS_NATIVE_SPATIAL_INTERVAL_DEGREES = 0.25
SPATIAL_STRUCTURE_VERIFICATION_MODES = frozenset({
    "multivariable_alignment", "spatial_evolution",
})
CANDIDATE_DISCOVERY_INITIAL_EVIDENCE_LIMIT = 6
CANDIDATE_DISCOVERY_INITIAL_ASSET_LIMIT = 8
CANDIDATE_DISCOVERY_RECONCILIATION_ASSET_LIMIT = 16
CANDIDATE_DISCOVERY_RECONCILIATION_EVIDENCE_LIMIT = 12
_PRECURSOR_CATEGORY_ORDER = (
    "direct_precipitation",
    "moisture_supply",
    "low_level_forcing",
    "mid_upper_forcing",
    "thermodynamic_support",
    "organization_persistence",
)
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
_CANDIDATE_AGGREGATE_MEAN = re.compile(
    r"후보\s*(?:영역|지역)(?:\s*전체)?(?:\s*(?:의|내|에서))?\s*(?:평균값?|영역평균|지역평균)",
    re.IGNORECASE,
)
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
_TOTAL_COLUMN_WATER_WORDS = re.compile(
    r"전층\s*(?:총)?수분량|총\s*수분량|\bTCW\b|total[-\s]*column\s*water",
    re.IGNORECASE,
)
_VERTICAL_SECTION_HORIZONTAL_MOISTURE_WORDS = re.compile(
    r"수분\s*(?:수송|플럭스)|수증기\s*(?:수송|플럭스)|수분\s*유입축|수증기\s*유입축",
    re.IGNORECASE,
)
_MULTIVARIABLE_MOISTURE_FLUX_WORDS = re.compile(
    r"수분\s*(?:수송|플럭스)|수증기\s*(?:수송|플럭스)|수분\s*유입축|수증기\s*유입축",
    re.IGNORECASE,
)
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
_TEMPORAL_SIGNAL_WORDS = re.compile(r"강화|약화|증가|감소|변화|이동|유지|지속|재강화|최대\s*시각")
_SPATIAL_EVOLUTION_SIGNAL_WORDS = re.compile(
    r"(?:이동|동진|서진|북상|남하|재조직|병합|분리)"
    r"|(?:강수대|강수\s*(?:영역|중심|분포)|수렴\s*(?:영역|중심)|상승\s*(?:영역|중심))[^.!?]{0,24}(?:연속|이어)"
)
_TEMPORAL_CONTEXT_WORDS = re.compile(r"이후|이전|부터|까지|동안|사이|대비|전후")
_FORECAST_HOUR_TOKEN = re.compile(r"F(\d{3})", re.IGNORECASE)
_COMPACT_EVIDENCE_ROLE_RANK = {
    "context": 0,
    "opposing": 1,
    "supporting": 2,
}
_POSITIVE_ALIGNMENT_ENVIRONMENT_COMPONENTS = frozenset({
    "moisture_supply",
    "low_level_forcing",
    "mid_upper_forcing",
})
_COMPLEMENTARY_QUANTITATIVE_STATES = {
    "increase": "decrease",
    "decrease": "increase",
    "greater_than": "less_than",
    "less_than": "greater_than",
    "strengthen": "weaken",
    "weaken": "strengthen",
}
_VARIABLE_ROLE_HYPOTHESIS_TERMS: dict[str, tuple[str, ...]] = {
    "hazard_magnitude": ("강수", "호우", "강도", "최대", "피크", "peak", "누적"),
    "moisture_supply": ("수분", "수증기", "유입", "플럭스", "850", "습윤"),
    "low_level_forcing": ("수렴", "925", "하층", "저층", "경계층"),
    "midlevel_forcing": ("상승", "오메가", "omega", "700", "중층", "연직", "단면"),
    "synoptic_support": ("저기압", "기압골", "전선", "제트", "종관", "상층", "기압계"),
    "uncertainty": ("중첩", "일치", "불확실", "신뢰", "복합", "정합", "검증"),
}
