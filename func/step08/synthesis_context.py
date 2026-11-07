"""Shared imports, constants, and data types for the Step execution roles."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import re
from typing import Any
from func.shared.ai_provider import AIProvider, build_openai_transport_schema, normalize_openai_transport_json
from func.shared.majority_narrative import (
    MajorityNarrativeError, assert_majority_narrative_current, canonicalize_majority_result,
)
from func.shared.pass2_common import Pass2PipelineError, active_candidate_groups, load_json_object, load_schema, template_text_and_hash, validate_schema, write_validated_atomic
from func.shared.pipeline_common import canonical_hash, sha256sum
from func.shared.quantitative_consistency import compute_display_quantitative_relation, format_quantitative_display_value, QuantitativeConsistencyError
from func.shared.forecaster_quantitative import (
    question_answer_errors, requirements_for_questions, required_facts, reconcile_legacy_question_answers,
)
from func.shared.korean_narrative import (
    normalize_step08_response_coordinates,
    step08_response_narrative_entries,
    validate_step04_plan_narrative,
    validate_step07_result_narrative,
    validate_step08_response_narrative,
)
from func.shared.operational_regions import load_operational_regions, selectable_pass1_regions
from func.shared.runtime_paths import runtime_path
from func.shared.signal_validation import (
    CANDIDATE_SIGNAL_STATUSES,
    DECISION_EVIDENCE_EFFECTS,
    MONITORING_VALUES,
    OPERATIONAL_SURFACE_DECISIONS,
    OPERATIONAL_PRECURSOR_CATEGORIES,
    PHYSICAL_CONSISTENCY_STATUSES,
    POSITIVE_VALIDATED_PRECURSOR_ASSESSMENTS,
    PRECURSOR_CATEGORY_ORDER,
    alignment_requires_spatial_localization,
    category_check_is_compatible,
    category_check_is_positive_support_compatible,
    candidate_effective_peak_forecast_hours,
    candidate_peak_precipitation_details_from_group,
    check_is_positive_spatiotemporal_alignment,
    complete_coupled_environment_alignment_check_ids,
    environment_persistence_evidence_contract,
    derive_signal_class,
    derive_validated_precursor_categories,
    positive_support_claim_keys,
    operational_region_sector_mentions,
    validate_candidate_name_revision,
    verified_precipitation_movement_check_ids,
    verified_precipitation_movement_region_pair,
    validate_environment_persistence_contract,
    validate_operational_precursor_support_contract,
    validate_validated_precursor_evidence,
)
from func.step07.checks_api import (
    CHECK_RESULTS_SCHEMA,
    CHECK_RESULTS_VERSION,
    load_step06_package,
    load_step07_results,
    normalize_step07_result_for_synthesis,
    validate_check_evidence_references,
    validate_spatial_localization_contract,
    validate_precipitation_motion_contract,
    validate_step07_ensemble_consensus,
    _single_majority_convergence_not_strengthen_claim,
)
SYNTHESIS_SCHEMA = "pass2_synthesis_response.schema.json"
RESULT_SCHEMA = "pass2_result.schema.json"
SYNTHESIS_PROMPT = "ai_pass2_candidate_synthesis_prompt.ko.md"
RESULT_VERSION = "step08-pass2-result-v17"
SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY = "SYS_DIRECT_PRECIPITATION_PRESENCE"
SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CONTRACT_VERSION = "1.4.0"
CANDIDATE_NAME_GROUNDING_CONTRACT_VERSION = "3.0.0"
ANALYSIS_REGION_NAMING_CONTRACT_VERSION = "1.0.0"
REPAIRABLE_SYNTHESIS_CODES = frozenset({
    "INTERPRETATION_EVIDENCE_CHECK_ID_INVALID",
    "HYPOTHESIS_EVIDENCE_INVALID",
    "QUANTITATIVE_RELATION_REF_INVALID",
    "FORECASTER_QUANTITATIVE_ANSWER_INVALID",
    "PRECURSOR_EVIDENCE_INVALID",
    "DIRECT_PRECIPITATION_EVIDENCE_INVALID",
    "ALIGNMENT_EVIDENCE_INVALID",
    "FINAL_ASSESSMENT_INVALID",
    "OPERATIONAL_GUIDANCE_INVALID",
    "OPERATIONAL_BRIEF_INVALID",
    "OPERATIONAL_SURFACE_DECISION_INVALID",
    "PHYSICAL_DECISION_INVALID",
    "ENVIRONMENT_PERSISTENCE_INVALID",
    "CANDIDATE_NAME_REVISION_INVALID",
})
_CONFIRMED_ENVIRONMENT_PRECURSOR_ASSESSMENTS = frozenset({
    "observed",
    "partially_observed",
})
_SEMANTIC_REPAIR_LIMITS = {
    "INTERPRETATION_EVIDENCE_CHECK_ID_INVALID": 1,
    "HYPOTHESIS_EVIDENCE_INVALID": 2,
    "QUANTITATIVE_RELATION_REF_INVALID": 1,
    "FORECASTER_QUANTITATIVE_ANSWER_INVALID": 2,
    "PRECURSOR_EVIDENCE_INVALID": 1,
    "DIRECT_PRECIPITATION_EVIDENCE_INVALID": 1,
    "ALIGNMENT_EVIDENCE_INVALID": 1,
    "FINAL_ASSESSMENT_INVALID": 1,
    # Guidance is model-authored.  Allow one additional bounded rewrite because
    # alignment repair can change the signal state after the initial guidance was
    # written, making that guidance stale without changing the meteorological facts.
    "OPERATIONAL_GUIDANCE_INVALID": 2,
    "OPERATIONAL_BRIEF_INVALID": 2,
    "OPERATIONAL_SURFACE_DECISION_INVALID": 1,
    "PHYSICAL_DECISION_INVALID": 1,
    "ENVIRONMENT_PERSISTENCE_INVALID": 1,
    "CANDIDATE_NAME_REVISION_INVALID": 2,
}
_EXCLUDED_EVIDENCE_DATA_SHORTAGE_TERMS = (
    "자료 부족",
    "자료가 부족",
    "자료 없음",
    "자료가 제공되지",
    "필요한 자료가 없",
    "자료를 사용할 수 없",
)
_INTERNAL_VALIDATION_NARRATIVE_TERMS = (
    "검증에서 제외",
    "정량 비교 검증",
    "정량 일관성 검증",
    "정량 일관성",
    "종합판단 근거에서 제외",
    "재생성",
    "스키마",
    "입력 지문",
    "출력 지문",
    "게이트",
)
_GUIDANCE_FIELDS = (
    "current_action",
    "next_check",
    "interpretation_limit",
)
_OPERATIONAL_BRIEF_FIELDS = (
    "key_signal",
    "key_limitation",
    "next_check",
)
_OPERATIONAL_BRIEF_MAX_CHARS = 120
_GUIDANCE_PROHIBITED_TERMS = (
    "특보",
    "예보 변경",
    "재난 대응",
    "비상근무",
    "발령",
    "위험도",
    "위험 등급",
    "가장 위험",
    "최우선",
    "1순위",
    "우선순위",
)
_GUIDANCE_NEXT_CHECK_MARKERS = re.compile(
    r"F\d{3}|\d{1,3}\s*시간|강수량|수렴|수분수송|상승운동|비습|풍속|바람|강수계|조직|중심|이동|결합|분리|재강화|재조직|영역|지역"
)
_GUIDANCE_VAGUE_NEXT_CHECK = re.compile(
    r"^(?:계속|지속|추가|후속)?\s*(?:감시|확인)(?:\s*(?:필요|필요합니다|하겠습니다|합니다))?[.!\s]*$"
)
_GUIDANCE_TIME_MARKERS = re.compile(r"F\d{3}|\d{1,3}\s*시간")
_GUIDANCE_METEOROLOGICAL_MARKERS = re.compile(
    r"강수|수렴|수분수송|수증기 플럭스|수분 공급|수증기 수송|수증기|상승운동|연직운동|비습|풍속|바람|강수계|강수대"
)
_GUIDANCE_CHANGE_MARKERS = re.compile(
    r"유지|지속|약화|강화|재증가|재강화|이동|결합|분리|잔류|재배치|확대|축소|소멸"
)
_GUIDANCE_UPDATE_DECISION_MARKERS = re.compile(
    r"(?:판단|해석).{0,24}(?:유지|재검토|보류|조정)|(?:유지|재검토|보류|조정).{0,24}(?:판단|해석)"
)
_GUIDANCE_CURRENT_ACTION_IMPERATIVE = re.compile(r"(?:하십시오|하세요|확인해\s*주세요|점검해\s*주세요)")
_GUIDANCE_POSITIVE_ALIGNMENT_ASSERTION = re.compile(
    r"시공간(?:적)?\s*(?:정합|중첩)(?:이|가|은|는|을|를)?\s*"
    r"(?:확인(?:됨|되었(?:다|습니다)?|됐(?:다|습니다)?|된다|됩니다|되는\s*(?:상태|것)|된\s*(?:상태|것)|했다|하였다|하였습니다)"
    r"|검증(?:됨|되었(?:다|습니다)?|됐(?:다|습니다)?|된다|됩니다|되는\s*(?:상태|것)|된\s*(?:상태|것)|했다|하였다|하였습니다))"
)
_ALIGNMENT_COMPONENT_MENTION_PATTERNS = {
    "direct_precipitation": re.compile(r"(?:모델\s*)?강수(?:\s*(?:영역|분포|대))?"),
    "moisture_supply": re.compile(r"(?:대기\s*전층\s*수분량(?:\s*\(TCW\))?|전층\s*수분량|TCW)"),
    "low_level_forcing": re.compile(r"(?:925\s*hPa\s*(?:수평)?수렴|하층\s*수렴)"),
    "mid_upper_forcing": re.compile(r"(?:700\s*hPa\s*(?:기압연직속도|상승운동)|중층\s*상승운동)"),
}
_ALIGNMENT_POSITIVE_RELATION_PATTERN = re.compile(
    r"(?:결합|중첩|겹(?:치|쳐|친|침|칩|쳤)[가-힣]*|같은\s*(?:위치|영역)|동일\s*(?:위치|영역)|시공간(?:적)?\s*일치)"
)
_ALIGNMENT_NEGATION_AFTER_RELATION_PATTERN = re.compile(
    r"(?:않|못|불분명|불확실|제한|뚜렷하지|확인되지|성립하지|미확정)"
)
_ALIGNMENT_CLAUSE_BREAK_PATTERN = re.compile(
    r"(?:동반|확인|나타나|보이|관찰|유지|약화|강화|제한|분리|이어지)[가-힣 ]{0,10}(?:,|;)"
)
_ALIGNMENT_CLAUSE_CONNECTIVE_PATTERN = re.compile(
    r"(?:동반|확인|나타나|보이|관찰|유지|약화|강화|제한|분리|이어지)"
    r"[가-힣 ]{0,10}?(?:고|며|면서|지만|되어|돼|어서|한편|반면|가운데)(?:\s|$)"
)
_STEP08_REGENERATION_UNIT_FIELDS = {
    "interpretation_block": ("hypothesis_assessments", "forecaster_question_answers"),
    # This is a complete, self-contained forecaster-guidance object.  A wording
    # defect must not regenerate already validated physical decisions, candidate
    # conclusions, or the operational brief.
    "operational_guidance_block": ("operational_guidance",),
    # A purely narrative/observable/spatial wording defect is not permission to
    # rewrite guidance which was already validated independently.  Keep the
    # complete narrative sub-block atomic without widening to other decisions.
    "narrative_prose_block": ("uncertainties", "final_assessment", "operational_brief"),
    "signal_conclusion_block": (
        "signal_assessment", "uncertainties", "final_assessment", "revision", "operational_brief", "operational_guidance"
    ),
    "narrative_conclusion_block": ("uncertainties", "final_assessment", "operational_brief", "operational_guidance"),
}
_MIXED_QUANTITATIVE_QUESTION_TERMS = re.compile(
    r"수렴|상승|수증기|수분|바람|이동|강수계|강수대|연속|연결|"
    r"공간|분포|영역|위치|경로|발달|조직|재편|영향|원인|동반|결합|"
    r"지속|유지|중첩|겹치|기압|전선|시어|기류"
)
_CANDIDATE_NAME_SECTOR_LABELS_KO = {
    "unspecified": "",
    "north": "북부",
    "northeast": "북동부",
    "east": "동부",
    "southeast": "남동부",
    "south": "남부",
    "southwest": "남서부",
    "west": "서부",
    "northwest": "북서부",
    "central": "중심부",
}
_CANDIDATE_NAME_ADJACENT_LABELS_KO = {
    "unspecified": "인근",
    "north": "북쪽",
    "northeast": "북동쪽",
    "east": "동쪽",
    "southeast": "남동쪽",
    "south": "남쪽",
    "southwest": "남서쪽",
    "west": "서쪽",
    "northwest": "북서쪽",
    "central": "인근",
}
_PYTHON_OWNED_STEP08_PROVENANCE_FIELDS = {
    "step07_schema_version",
    "step07_result_fingerprints",
    "intent_lineage",
    "synthesis_prompt_sha256",
    "synthesis_schema_sha256",
    "direct_precipitation_anchor_contract_version",
    "analysis_region_naming_contract_version",
    "candidate_analysis_region_index_sha256",
    "candidate_name_grounding_contract_version",
    "candidate_name_grounding_contract_sha256",
    "provider",
    "model",
    "reasoning_effort",
    "repair_reasoning_effort",
    "image_detail",
}
_PYTHON_OWNED_STEP08_CANDIDATE_FIELDS = {
    "input_fingerprint",
    "provenance",
    "final_spatial_identity",
    "candidate_name_grounding_audit",
}
