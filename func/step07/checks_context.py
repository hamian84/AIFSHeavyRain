"""Shared imports, constants, and data types for the Step execution roles."""
from __future__ import annotations
import copy
import json
import math
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
SYSTEM_DIRECT_PRECIPITATION_ASSESSMENT_CONTRACT_VERSION = "scope-availability-v1"
SUPPORTED_QUANTITATIVE_ENFORCEMENT_VERSIONS = frozenset({
    "specific-v2+convergence-majority-v1",
    QUANTITATIVE_VALIDATION_CONTRACT_VERSION,
})
SPECIFIC_SELECTOR_GUIDANCE_VERSION = "specific-required-selectors-v1"
MAJORITY_QUANTITATIVE_BINDING_VERSION = "convergence-majority-source-binding-v1"
MAJORITY_STRENGTHEN_BINDING_VERSION = "convergence-majority-strengthen-source-binding-v1"
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
SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY = "SYS_DIRECT_PRECIPITATION_PRESENCE"
_STRENGTHENING_CONVERGENCE_TRANSITIONS = frozenset({
    "convergence_strengthening",
    "divergence_to_convergence",
    "neutral_to_convergence",
})
_FORECAST_KST_PREFIX_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<hour>\d{2}):(?P<minute>\d{2})\s*KST\s*\(F(?P<fh>\d{3})\)"
)
_FORECAST_KST_SUFFIX_RE = re.compile(
    r"F(?P<fh>\d{3})\)?\s*\((?P<date>\d{4}-\d{2}-\d{2})\s+(?P<hour>\d{2}):(?P<minute>\d{2})\s*KST\)"
)
_SPATIAL_ALIGNMENT_RELATION_PATTERN = re.compile(
    r"(?:결합|중첩|겹(?:치|쳐|친|침|칩|쳤)[가-힣]*|같은\s*(?:위치|영역)|동일\s*(?:위치|영역)|시공간(?:적)?\s*일치|위치\s*관계|공간\s*관계)"
)
