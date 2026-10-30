"""Step08 text-only candidate synthesis from Step04 context and Step07 checks."""

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
from func.step07.checks import (
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
# Transitional compatibility only: Step09 can still validate Step08 products
# generated while analysis-region naming context was temporarily part of Step08.
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


def step08_output_path(root: Path, init: str) -> Path:
    return runtime_path(root, "daio", init, "step08", "pass2_result.json")


def _question_rows(candidate_id: str, plan: dict[str, Any]) -> list[dict[str, str]]:
    questions = plan.get("forecaster_questions")
    if not isinstance(questions, list):
        raise Pass2PipelineError("FORECASTER_QUESTIONS_INVALID", f"{candidate_id} forecaster_questions must be a list.")
    rows: list[dict[str, str]] = []
    for index, question in enumerate(questions, start=1):
        if not isinstance(question, str) or not question.strip():
            raise Pass2PipelineError("FORECASTER_QUESTIONS_INVALID", f"{candidate_id} has an invalid forecaster question.")
        rows.append({"forecaster_question_id": f"FQ_{candidate_id}_{index:02d}", "question": question})
    return rows


def _check_map(candidate_id: str, step07: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in step07.get("results", []):
        if not isinstance(item, dict) or item.get("candidate_id") != candidate_id:
            continue
        rows.append(normalize_step07_result_for_synthesis(item))
    return {str(item["check_id"]): item for item in rows}


def _required_check_ids(group: dict[str, Any]) -> set[str]:
    checks = group.get("check_evidence")
    if not isinstance(checks, list):
        raise Pass2PipelineError("STEP06_PACKAGE_INVALID", f"{group.get('candidate_id')} has no check_evidence.")
    return {str(item["check_id"]) for item in checks if isinstance(item, dict) and item.get("check_id")}


def _usable_check_ids(checks: dict[str, dict[str, Any]]) -> set[str]:
    """Return checks that may support Step08 synthesis.

    Legacy results without the new flag remain readable unless they are explicitly
    insufficient. Newly generated Step07 results persist usable_for_synthesis.
    """
    return {
        check_id
        for check_id, result in checks.items()
        if result.get("assessment") != "insufficient_evidence"
        and result.get("usable_for_synthesis") is not False
    }


def _step08_exclusion_narrative_errors(value: dict[str, Any], checks: dict[str, dict[str, Any]]) -> list[str]:
    """Keep internal validation state out of user-facing prose and protect shortage wording."""
    has_complete_exclusion = any(
        result.get("evidence_status") == "complete" and result.get("usable_for_synthesis") is False
        for result in checks.values()
    )
    has_actual_data_shortage = any(
        result.get("evidence_status") in {"none", "partial"}
        or result.get("assessment") == "insufficient_evidence"
        for result in checks.values()
    )
    errors: list[str] = []
    for path, text in step08_response_narrative_entries(value, include_source_text=True):
        if not isinstance(text, str):
            continue
        internal_term = next((term for term in _INTERNAL_VALIDATION_NARRATIVE_TERMS if term in text), None)
        if internal_term is not None:
            errors.append(
                f"{path}: 사용자 자연어에는 내부 검증 처리 상태를 노출하지 말고 기상학적으로 남는 판단 한계만 설명하십시오."
            )
            continue
        if not has_complete_exclusion:
            continue
        has_data_shortage_wording = any(term in text for term in _EXCLUDED_EVIDENCE_DATA_SHORTAGE_TERMS)
        if has_data_shortage_wording and not has_actual_data_shortage:
            errors.append(
                f"{path}: 실제 자료 부족 근거가 없으므로 자료 부족 표현을 사용할 수 없습니다. 판단 가능한 기상학적 범위만 설명하십시오."
            )
    return errors


def _step08_motion_narrative_errors(value: dict[str, Any], checks: dict[str, dict[str, Any]]) -> list[str]:
    """Keep candidate-level movement wording no stronger than Step07 consensus."""
    motion_rows = []
    for result in checks.values():
        if not isinstance(result, dict) or result.get("usable_for_synthesis") is not True:
            continue
        motion = result.get("precipitation_motion")
        consensus = result.get("ensemble_consensus")
        if not isinstance(motion, dict) or not isinstance(consensus, dict):
            continue
        if motion.get("classification") in {None, "not_applicable"}:
            continue
        motion_rows.append(consensus)
    if not motion_rows:
        return []
    if any(
        row.get("precipitation_motion_agreement") == "unanimous"
        and row.get("consensus_precipitation_motion_classification") == "coherent_movement"
        for row in motion_rows
    ):
        strength = "unanimous"
    elif any(
        row.get("precipitation_motion_agreement") == "majority"
        and row.get("consensus_precipitation_motion_classification") == "coherent_movement"
        for row in motion_rows
    ):
        strength = "majority"
    else:
        strength = "unverified"
    if strength == "unanimous":
        return []

    fields: list[tuple[str, str]] = []
    final = value.get("final_assessment")
    if isinstance(final, str):
        fields.append(("final_assessment", final))
    brief = value.get("operational_brief")
    if isinstance(brief, dict) and isinstance(brief.get("key_signal"), str):
        fields.append(("operational_brief.key_signal", brief["key_signal"]))

    strong_pattern = re.compile(r"(?:이동하며|이동한다|이동합니다|이동하였다|이동했다|강수\s*이동)")
    definite_motion_pattern = re.compile(r"이동(?:하며|한다|합니다|하였다|했다)")
    qualifier_pattern = re.compile(r"(?:다수\s*판독|이동\s*신호|이동하는\s*양상|재배치|확대|불확실|가능성|단정하기\s*어렵)")
    # An explicit *lack* of verified movement is not a movement assertion.
    # Do not accept a sentence that also contains a definite movement verb.
    uncertainty_pattern = re.compile(
        r"이동\s*(?:여부|인지).{0,32}"
        r"(?:불확실|불분명|미확인|확인되지|검증되지|"
        r"판단(?:하기\s*어렵|할\s*수\s*없)|단정(?:하기\s*어렵|할\s*수\s*없))"
        r"|이동(?:이|가|은|는|을|를)?\s*"
        r"(?:(?:확인|검증)되지|(?:단정|확정)(?:하기\s*어렵|할\s*수\s*없))"
    )
    errors: list[str] = []
    for path, text in fields:
        for sentence in re.split(r"(?<=[.!?])\s+|[\r\n]+", text):
            sentence = sentence.strip()
            if not sentence or not strong_pattern.search(sentence):
                continue
            if strength == "majority" and qualifier_pattern.search(sentence):
                continue
            if (
                strength == "unverified"
                and not definite_motion_pattern.search(sentence)
                and uncertainty_pattern.search(sentence)
            ):
                continue
            errors.append(
                f"{path}: Step07 precipitation motion agreement={strength}; "
                "unqualified coherent-movement wording is not allowed; "
                f"offending_sentence={sentence[:160]!r}"
            )
            break
    return errors


def validate_step08_narrative_contract(value: dict[str, Any], checks: dict[str, dict[str, Any]]) -> list[str]:
    """Validate Korean prose plus evidence-availability and movement wording against Step07."""
    return [
        *validate_step08_response_narrative(value, include_source_text=True),
        *_step08_exclusion_narrative_errors(value, checks),
        *_step08_motion_narrative_errors(value, checks),
    ]


def _observable_ids_for_check_ids(
    group: dict[str, Any],
    check_ids: set[str],
    *,
    check_scope: str | None = None,
) -> set[str]:
    """Return capability observables for the selected Step07 Check IDs."""
    specifications = _check_specifications(group)
    values: set[str] = set()
    for check_id in check_ids:
        specification = specifications.get(check_id)
        if not isinstance(specification, dict):
            continue
        if check_scope is not None and specification.get("check_scope") != check_scope:
            continue
        capability = specification.get("capability_profile")
        if not isinstance(capability, dict):
            continue
        values.update(
            str(item)
            for item in capability.get("observable_ids", [])
            if isinstance(item, str) and item
        )
    return values


def _usable_observable_ids(
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    *,
    check_scope: str | None = None,
) -> set[str]:
    """Return observables whose Step07 Checks may support Step08 conclusions."""
    return _observable_ids_for_check_ids(
        group,
        _usable_check_ids(checks),
        check_scope=check_scope,
    )


def _available_observable_ids(
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    *,
    complete_only: bool = False,
) -> set[str]:
    """Return observables for detailed products that were actually available to Step07.

    Availability is intentionally independent from ``usable_for_synthesis``.  A
    complete product can be excluded from synthesis because ensemble assessment
    did not converge; that must not be rewritten as if the product itself were
    missing.  Partial evidence still counts as available for shortage/limitation
    wording, but not as complete evidence.
    """
    check_ids: set[str] = set()
    for check_id, result in checks.items():
        evidence_status = result.get("evidence_status")
        if complete_only:
            available = evidence_status == "complete" and result.get("evidence_complete") is True
        else:
            available = evidence_status == "complete" or (
                evidence_status == "partial" and bool(result.get("available_request_ids"))
            )
        if available:
            check_ids.add(check_id)
    return _observable_ids_for_check_ids(group, check_ids)


def _observable_grounding_contract(
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Describe observable availability separately from synthesis usability.

    This contract is used only to ground Step08 regeneration.  It does not
    convert an unusable Step07 Check into evidence and it does not author any
    meteorological conclusion.  The model still has to answer the original
    question from usable evidence, while treating available-but-unusable
    observables as a conclusion limit rather than as missing data.
    """
    specifications = _check_specifications(group)
    observable_to_checks: dict[str, set[str]] = {}
    for check_id, specification in specifications.items():
        if check_id not in checks or not isinstance(specification, dict):
            continue
        capability = specification.get("capability_profile")
        if not isinstance(capability, dict):
            continue
        for observable_id in capability.get("observable_ids", []):
            if isinstance(observable_id, str) and observable_id:
                observable_to_checks.setdefault(observable_id, set()).add(check_id)

    usable_checks = _usable_check_ids(checks)
    contract: dict[str, dict[str, Any]] = {}
    for observable_id, related_ids in sorted(observable_to_checks.items()):
        usable_ids = sorted(related_ids & usable_checks)
        available_ids: list[str] = []
        complete_ids: list[str] = []
        for check_id in sorted(related_ids):
            result = checks.get(check_id, {})
            evidence_status = result.get("evidence_status")
            if evidence_status == "complete":
                available_ids.append(check_id)
                if result.get("evidence_complete") is True:
                    complete_ids.append(check_id)
            elif evidence_status == "partial" and bool(result.get("available_request_ids")):
                available_ids.append(check_id)

        if usable_ids:
            state = "usable"
            policy = (
                "usable Check의 실제 Step07 판독 범위 안에서만 상태·변화·공간구조를 서술할 수 있습니다."
            )
        elif available_ids:
            state = "available_but_unusable"
            policy = (
                "자료가 없거나 부족하다고 쓰지 마십시오. 이 observable의 상태·강도·변화·공간구조를 "
                "확인된 사실로 사용하지 말고, 질문에 필요하면 현재 사용할 수 있는 판독 근거만으로 "
                "판단하기 어렵다는 한계만 기술하십시오. 관련 Check ID를 evidence_check_ids에 넣지 마십시오."
            )
        else:
            state = "unavailable"
            policy = (
                "이 observable을 확인된 사실로 서술하지 마십시오. 질문에 필요하면 상세 판독자료가 "
                "없거나 불완전하여 판단이 제한된다고 기술할 수 있습니다."
            )
        contract[observable_id] = {
            "state": state,
            "related_check_ids": sorted(related_ids),
            "usable_check_ids": usable_ids,
            "available_check_ids": sorted(set(available_ids)),
            "complete_check_ids": sorted(set(complete_ids)),
            "natural_language_policy": policy,
        }
    return contract


def _is_observable_limit_statement(sentence: str) -> bool:
    """Return whether prose limits a conclusion instead of asserting an observation."""
    return bool(
        re.search(
            r"(?:판단|확인|평가|해석|단정|확정).{0,24}(?:어렵|불가|할 수 없|못하|제한)"
            r"|(?:불확실|근거 부족|자료 부족|자료가 부족|자료가 없|자료 없음|자료 없이|제공되지|미제공)",
            sentence,
        )
    )


def _claims_observable_data_shortage(sentence: str) -> bool:
    """Return whether prose says the detailed product itself is absent or insufficient."""
    return bool(
        re.search(
            r"자료\s*(?:가\s*)?(?:부족|없)|자료\s*없이|자료가\s*제공되지|자료\s*미제공|미제공된\s*자료",
            sentence,
        )
    )


def _claims_moisture_flux_alignment(sentence: str) -> bool:
    """Return True only for an explicit moisture-flux overlap/alignment claim.

    This intentionally avoids sentence-wide keyword co-occurrence. A sentence
    may legitimately state that precipitation/convergence overlap while moisture
    transport is confirmed separately. Only a grammatical relation that ties
    ``수분수송`` itself to overlap/alignment is treated as an identity claim.
    """
    if "수분수송" not in sentence and "수증기 플럭스" not in sentence:
        return False
    relation = r"(?:중첩|정합|겹(?:치|쳐|친|침|칩|쳤)[^\s,.;:]*)"
    flux = r"(?:850\s*hPa\s*)?(?:수분수송|수증기\s*플럭스)(?:\s*(?:크기|영역|분포|축))?"
    precipitation = r"(?:모델\s*)?강수(?:\s*(?:영역|대|분포))?"
    modifier = r"(?:서로\s*)?(?:(?:시공간|공간)적으로\s*)?(?:부분적으로\s*)?"
    patterns = (
        # '강수와 수분수송이 중첩', '강수와 수분수송의 중첩'
        rf"{precipitation}\s*(?:과|와|및|[-–—/])\s*{flux}\s*(?:이|가|은|는|의)?\s*{modifier}{relation}",
        # '수분수송과 강수가 정합', '수분수송이 강수와 겹침'
        rf"{flux}\s*(?:과|와|및|[-–—/])\s*{precipitation}\s*(?:이|가|은|는|의)?\s*{modifier}{relation}",
        rf"{flux}\s*(?:이|가|은|는)\s*{precipitation}\s*(?:과|와)?\s*{modifier}{relation}",
        # '중첩된 수분수송 영역', '강수와 중첩되는 수분수송'
        rf"{relation}\s*(?:된|되는|한|하는)\s*{flux}",
        rf"{precipitation}\s*(?:과|와)?\s*{relation}\s*(?:된|되는|한|하는)\s*{flux}",
        # '수분수송 중첩'처럼 물리량과 관계를 직접 결합한 명사구
        rf"{flux}\s*(?:의\s*)?{relation}",
    )
    return any(re.search(pattern, sentence) for pattern in patterns)


def _step08_observable_identity_errors(
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    value: dict[str, Any],
) -> list[str]:
    """Reject only explicit high-risk substitutions of directly observed fields.

    The capability registry owns what each detailed product can directly
    observe. Python does not infer the meteorological conclusion from prose. It
    only blocks an explicit claim that assigns a physical field to a product
    which cannot provide that field.
    """
    specifications = _check_specifications(group)
    has_capability_contract = any(
        isinstance(specifications.get(check_id, {}).get("capability_profile"), dict)
        and bool(specifications.get(check_id, {}).get("capability_profile", {}).get("observable_ids"))
        for check_id in checks
    )
    if not has_capability_contract:
        return []
    usable_observables = _usable_observable_ids(group, checks)
    available_observables = _available_observable_ids(group, checks)
    complete_observables = _available_observable_ids(group, checks, complete_only=True)
    alignment_observables = _usable_observable_ids(
        group, checks, check_scope="multivariable_alignment"
    )
    has_alignment_moisture_flux = "moisture_flux_magnitude_850" in alignment_observables
    has_tcw_usable = "total_column_water" in usable_observables
    has_tcw_available = "total_column_water" in available_observables
    has_tcw_complete = "total_column_water" in complete_observables
    has_shear_usable = "vertical_wind_shear_magnitude_850_500" in usable_observables
    has_shear_available = "vertical_wind_shear_magnitude_850_500" in available_observables
    has_shear_complete = "vertical_wind_shear_magnitude_850_500" in complete_observables
    errors: list[str] = []
    for path, text in step08_response_narrative_entries(value, include_source_text=False):
        if not isinstance(text, str) or not text.strip():
            continue
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            sentence = sentence.strip()
            if not sentence:
                continue
            if re.search(r"(?:수분수송|수증기\s*플럭스)\s*(?:벡터|화살표)", sentence):
                errors.append(
                    f"{path}: 850 hPa 수증기 플럭스 크기와 실제 850 hPa 바람을 결합해 수증기 플럭스 벡터나 화살표로 재명명할 수 없습니다."
                )
                break
            if _claims_moisture_flux_alignment(sentence) and not has_alignment_moisture_flux:
                errors.append(
                    f"{path}: 시공간 일치를 직접 판독한 자료에는 850 hPa 수증기 플럭스 크기가 포함되지 않으므로, 수증기 플럭스가 강수와 일치하거나 겹친 것으로 서술할 수 없습니다."
                )
                break
            has_tcw_word = any(term in sentence for term in ("대기 전층 수분량", "전층 총수분량", "TCW"))
            if has_tcw_word and not has_tcw_usable:
                if has_tcw_complete and _claims_observable_data_shortage(sentence):
                    errors.append(
                        f"{path}: 대기 전층 수분량(TCW) 상세자료는 완결되게 제공되었으므로 자료가 없거나 부족하다고 서술할 수 없습니다. "
                        "해당 판독을 종합판단 근거로 사용할 수 없다면 TCW에 대한 결론 강도만 제한하십시오."
                    )
                    break
                if not _is_observable_limit_statement(sentence):
                    detail = (
                        "전용 상세자료는 제공되었지만 현재 종합판단에 사용할 수 있는 TCW 판독이 없으므로"
                        if has_tcw_available
                        else "사용 가능한 상세 판독자료에 대기 전층 수분량(TCW)이 포함되지 않았으므로"
                    )
                    errors.append(
                        f"{path}: {detail} TCW의 상태·변화·공간구조를 확인된 사실로 서술할 수 없습니다. 판단 한계로만 표현하십시오."
                    )
                    break
            has_shear_word = bool(re.search(r"연직\s*바람\s*시어|연직바람시어", sentence))
            if has_shear_word and not has_shear_usable:
                if has_shear_complete and _claims_observable_data_shortage(sentence):
                    errors.append(
                        f"{path}: 전용 850–500 hPa 연직바람시어 상세자료는 완결되게 제공되었으므로 자료가 없거나 부족하다고 서술할 수 없습니다. "
                        "해당 판독을 종합판단 근거로 사용할 수 없다면 연직바람시어 변화의 판단 한계만 표현하십시오."
                    )
                    break
                if not _is_observable_limit_statement(sentence):
                    detail = (
                        "전용 850–500 hPa 연직바람시어 상세자료는 제공되었지만 현재 종합판단에 사용할 수 있는 시어 판독이 없으므로"
                        if has_shear_available
                        else "전용 850–500 hPa 연직바람시어 상세자료가 제공되지 않았으므로"
                    )
                    errors.append(
                        f"{path}: {detail} 연직바람시어의 강도·변화·공간구조를 확인된 사실로 서술할 수 없습니다. 판단 한계로만 표현하십시오."
                    )
                    break
    return errors



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


def _localized_alignment_narrative_contract(
    group: dict[str, Any], checks: dict[str, dict[str, Any]], signal: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the selected public localization plus its exact component set.

    This is reference bookkeeping only.  It reuses the same deterministic
    localization selector that later persists ``environment_localization`` and
    never adds a meteorological component that Step07 did not localize.
    """
    localization = _derive_environment_localization(group, checks, signal)
    if not isinstance(localization, dict):
        return None
    components = [
        str(component)
        for component in localization.get("signal_components", [])
        if str(component) in _ALIGNMENT_COMPONENT_MENTION_PATTERNS
    ]
    return {
        "evidence_check_id": str(localization.get("evidence_check_id") or ""),
        "forecast_hour": localization.get("forecast_hour"),
        "signal_components": components,
        "regions": copy.deepcopy(localization.get("regions", [])),
        "rule": (
            "확정적인 결합·중첩·같은 위치·시공간 일치 표현에는 signal_components에 포함된 요소만 함께 묶으십시오. "
            "다른 Check에서 부분적으로 확인된 요소는 동일 결합의 구성요소로 추가하지 말고, 필요하면 별도 문장에서 "
            "'일부 동반', '별도 확인', '부분적으로 겹침'처럼 관계 강도를 분리하십시오."
        ),
    }


def _alignment_relation_is_negated(sentence: str, relation: re.Match[str]) -> bool:
    tail = sentence[relation.start(): relation.end() + 36]
    return bool(_ALIGNMENT_NEGATION_AFTER_RELATION_PATTERN.search(tail))


def _structured_alignment_location_contract(
    root: Path,
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return the only operational-region sectors that may host a positive coupling claim.

    Candidate precipitation-object identity is intentionally *not* enough here:
    it localizes the direct-precipitation object, not the precipitation/environment
    overlap.  A sector-qualified multivariable coupling statement therefore needs
    a usable Step07 ``spatial_localization.status=localized`` result.
    """
    region_index = _candidate_analysis_region_index(root, group)
    name_by_id = {
        str(row.get("region_id") or ""): str(row.get("name_ko") or "").strip()
        for row in region_index.get("regions", [])
        if isinstance(row, dict) and str(row.get("region_id") or "") and str(row.get("name_ko") or "").strip()
    }
    allowed: dict[str, set[str]] = {}
    sources: dict[str, list[str]] = {}
    for check_id, result in checks.items():
        if not isinstance(result, dict) or result.get("usable_for_synthesis") is False:
            continue
        localization = result.get("spatial_localization")
        if not isinstance(localization, dict) or localization.get("status") != "localized":
            continue
        for row in localization.get("regions", []):
            if not isinstance(row, dict):
                continue
            region_id = str(row.get("region_id") or "")
            sector = str(row.get("relative_sector") or "unspecified")
            if not region_id:
                continue
            allowed.setdefault(region_id, set()).add(sector)
            sources.setdefault(region_id, []).append(str(check_id))
    return {
        "region_names_by_id": name_by_id,
        "allowed_region_sectors": {region_id: sorted(sectors) for region_id, sectors in allowed.items()},
        "evidence_check_ids_by_region": {region_id: sorted(set(ids)) for region_id, ids in sources.items()},
        "rule": (
            "강수와 환경요소의 결합·중첩·같은 위치·위치관계를 운영권역 세부 방향과 함께 서술하려면 "
            "해당 권역/방향이 usable Step07 spatial_localization(status=localized)에 있어야 합니다. "
            "직접강수 precipitation-object 위치만으로 환경 결합 위치를 추론하지 마십시오."
        ),
    }


def _step08_alignment_location_errors(
    root: Path,
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    value: dict[str, Any],
) -> list[str]:
    signal = value.get("signal_assessment") if isinstance(value.get("signal_assessment"), dict) else {}
    # This contract protects the location of a confirmed direct-precipitation +
    # environment coupling.  Precursor-only candidates retain their existing
    # environment-location prose because the current Step07 localization schema
    # does not define a direct-precipitation coupling for them.
    if signal.get("direct_precipitation_status") != "confirmed":
        return []
    contract = _structured_alignment_location_contract(root, group, checks)
    name_by_id = contract["region_names_by_id"]
    if not name_by_id:
        return []
    allowed = {
        region_id: set(sectors)
        for region_id, sectors in contract["allowed_region_sectors"].items()
    }

    narrative_entries = list(step08_response_narrative_entries(value, include_source_text=False))
    guidance = value.get("operational_guidance")
    if isinstance(guidance, dict):
        for field in ("current_action", "next_check", "interpretation_limit"):
            narrative_entries.append((f"operational_guidance.{field}", guidance.get(field)))

    errors: list[str] = []
    for path, text in narrative_entries:
        if not isinstance(text, str) or not text.strip():
            continue
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            sentence = sentence.strip()
            if not sentence:
                continue
            # Only multivariable precipitation/environment spatial-relation prose
            # is subject to this contract.  General motion/extent wording stays
            # governed by the separate precipitation-motion rules.
            relation_matches = list(_ALIGNMENT_POSITIVE_RELATION_PATTERN.finditer(sentence))
            relation_matches.extend(re.finditer(r"(?:위치|공간)\s*관계", sentence))
            if not relation_matches:
                continue
            # The region-sector restriction applies only when precipitation and
            # at least one environment component are operands of the spatial
            # relation itself.  A later phrase such as '강수 발달 과정' must not
            # turn an environment-only overlap into a precipitation coupling.
            qualifies = False
            for relation in relation_matches:
                prefix = sentence[: relation.end()]
                if not _ALIGNMENT_COMPONENT_MENTION_PATTERNS["direct_precipitation"].search(prefix):
                    continue
                if any(
                    _ALIGNMENT_COMPONENT_MENTION_PATTERNS[component].search(prefix)
                    for component in ("moisture_supply", "low_level_forcing", "mid_upper_forcing")
                ):
                    qualifies = True
                    break
            if not qualifies:
                continue
            for mention in operational_region_sector_mentions(sentence, name_by_id):
                allowed_sectors = allowed.get(mention["region_id"], set())
                if mention["sector"] in allowed_sectors:
                    continue
                supported = sorted(allowed_sectors) if allowed_sectors else []
                errors.append(
                    f"{path}: 강수-환경 공간관계에 {mention['region_name']} '{mention['sector_label']}' 위치를 사용했지만 "
                    f"usable Step07 structured localization이 이를 지지하지 않습니다. allowed_relative_sectors={supported}. "
                    "세부 위치를 제거하고 운영권역 단위 위치가 미확정임을 명시하십시오."
                )
    return errors


def _step08_alignment_narrative_errors(
    root: Path,
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    value: dict[str, Any],
) -> list[str]:
    """Reject prose that expands a localized positive alignment component set.

    Step07 may localize a reduced three-/two-component positive overlap while a
    different Check separately observes another physical field.  The latter may
    be described as a separate/partial companion, but it must not be folded into
    the localized coupling as though all fields shared the same structured
    location.  Negative statements about a missing four-field coupling and
    standalone partial-overlap Check wording remain valid.
    """
    location_errors = _step08_alignment_location_errors(root, group, checks, value)
    signal = value.get("signal_assessment")
    if not isinstance(signal, dict):
        return location_errors
    contract = _localized_alignment_narrative_contract(group, checks, signal)
    if not contract:
        return location_errors
    localized = set(contract["signal_components"])
    if not localized:
        return location_errors
    unlocalized = set(_ALIGNMENT_COMPONENT_MENTION_PATTERNS) - localized
    if not unlocalized:
        return location_errors

    errors: list[str] = list(location_errors)
    narrative_entries = list(step08_response_narrative_entries(value, include_source_text=False))
    guidance = value.get("operational_guidance")
    if isinstance(guidance, dict):
        for field in ("current_action", "next_check", "interpretation_limit"):
            narrative_entries.append((f"operational_guidance.{field}", guidance.get(field)))
    for path, text in narrative_entries:
        if not isinstance(text, str) or not text.strip():
            continue
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            sentence = sentence.strip()
            if not sentence:
                continue
            relation_matches = list(_ALIGNMENT_POSITIVE_RELATION_PATTERN.finditer(sentence))
            if not relation_matches:
                continue
            for component in sorted(unlocalized):
                pattern = _ALIGNMENT_COMPONENT_MENTION_PATTERNS[component]
                violation_found = False
                for mention in pattern.finditer(sentence):
                    for relation in relation_matches:
                        if relation.start() < mention.end():
                            continue
                        # A completed clause such as ``상승운동이 일부 동반되며,``
                        # separates that field from a later localized coupling.
                        bridge = sentence[mention.end(): relation.start()]
                        if (
                            _ALIGNMENT_CLAUSE_BREAK_PATTERN.search(bridge)
                            or _ALIGNMENT_CLAUSE_CONNECTIVE_PATTERN.search(bridge)
                        ):
                            continue
                        if _alignment_relation_is_negated(sentence, relation):
                            continue
                        relation_prefix = sentence[: relation.end()]
                        localized_mentions = {
                            localized_component
                            for localized_component in localized
                            if _ALIGNMENT_COMPONENT_MENTION_PATTERNS[localized_component].search(relation_prefix)
                        }
                        # Do not block a separate two-field partial-overlap Check
                        # such as precipitation + mid-level ascent.  The contract
                        # targets expansion of the already-localized core coupling.
                        if len(localized_mentions) < min(2, len(localized)):
                            continue
                        errors.append(
                            f"{path}: 구조화 위치 합의에 포함되지 않은 {component} 요소를 "
                            f"확정 결합 구성요소로 확대할 수 없습니다. localized_components={sorted(localized)}; "
                            "별도 부분 근거라면 '일부 동반' 또는 별도 판독으로 분리하여 서술하십시오. "
                            f"offending_sentence={sentence[:180]!r}"
                        )
                        violation_found = True
                        break
                    if violation_found:
                        break
                if violation_found:
                    break
    return errors


def _step08_alignment_narrative_repair_reason(errors: list[str]) -> str:
    # Keep field paths: atomic interpretation regeneration contains *all*
    # hypotheses/questions, so the provider must know which owner failed.
    details = "; ".join(str(error).strip() for error in errors[:6])
    return (
        "Step07의 구조화 위치 합의에 실제 포함된 signal_components만 확정적인 결합·중첩·같은 위치 표현의 구성요소로 사용하십시오. "
        "다른 Check에서 부분적으로 확인된 물리량은 동일 결합 안에 넣지 말고 '일부 동반', '별도 확인', '부분적으로 겹침' 등으로 분리하십시오. "
        "강수와 환경요소의 결합·중첩·위치관계를 운영권역의 북부·남부·동부·서부·대각·중심 같은 세부 방향과 함께 서술하려면 "
        "그 권역/방향이 usable Step07 spatial_localization(status=localized)에 구조화되어 있어야 합니다. 직접강수 객체의 위치나 Step07 자유서술만으로 환경 결합 위치를 만들지 마십시오. "
        "구조화 위치가 없으면 세부 방향을 제거하고 '운영권역 단위 결합 위치는 특정되지 않음'처럼 불확실성을 명시하십시오. "
        "특히 구조화 위치 합의에 없는 요소를 제한요인으로 언급한 뒤 같은 문장에서 확정 결합 표현을 이어 쓰지 말고, 두 문장으로 분리하십시오. "
        "Step07의 권위 있는 Check 판정·시각·정량값은 변경하지 마십시오. "
        "반면 근거와 모순된 Step08 가설 assessment는 다시 판단할 수 있습니다. "
        f"검증 내용: {details}"
    )

def _step08_language_repair_reason(errors: list[str]) -> str:
    """Return a Korean-only repair instruction without echoing forbidden tokens."""
    if any("precipitation motion agreement=unverified" in str(error) for error in errors):
        return (
            "Step07의 유효한 독립 판독에서 동일 강수계의 연속 이동은 확정되지 않았습니다. "
            "종합판단과 운영요약에서 '이동한다', '이동하며' 등 확정적인 이동 표현을 사용하지 마십시오. "
            "실제로 확인된 강수영역의 확대·재배치·분리·재조직만 각각 구분하여 서술하고, "
            "이동 상태를 언급해야 한다면 '강수 이동 여부는 확인되지 않았습니다'와 같이 불확실성을 명확히 기술하십시오. "
            "이동이 확인되지 않았다는 설명은 이동했다는 주장이 아닙니다. 기존 정량값·시각·권역·판정은 변경하지 마십시오."
        )
    if any("precipitation motion agreement=majority" in str(error) for error in errors):
        return (
            "Step07에서 강수 이동의 독립 판독 합의는 만장일치가 아닌 다수 의견입니다. "
            "'이동한다', '이동하며' 등 이견이 없는 확정형 서술은 쓰지 마십시오. "
            "이동 양상이 다수 판독에서 확인되었다는 점과 재배치 또는 소수 이견 가능성을 같은 문장에서 함께 명시하십시오. "
            "기존 정량값·시각·권역·판정은 변경하지 마십시오."
        )
    has_latin_error = any("허용되지 않은 영문 토큰" in str(error) for error in errors)
    coordinate_rule = (
        " 숫자 좌표에 영문 한 글자 방위 약어를 붙이지 말고, 위치 의미와 숫자를 유지한 채 "
        "북위·남위·동경·서경과 도 기호를 사용해 작성하십시오."
        if has_latin_error
        else ""
    )
    return (
        "예보관용 자연어에 허용되지 않은 내부 용어 또는 자료 가용성 표현이 포함되었습니다. "
        "원래 근거의 의미, 수치, 시각, 위치와 판정 강도를 바꾸지 말고 한글 기상용어로 해당 의미 단위를 처음부터 다시 작성하십시오."
        + coordinate_rule
    )


def _step08_observable_repair_reason(errors: list[str]) -> str:
    """Return an actionable observable-grounding instruction for regeneration."""
    details = "; ".join(str(error).split(":", 1)[-1].strip() for error in errors[:6])
    topics: list[str] = []
    joined = " ".join(errors)
    if "연직바람시어" in joined or "연직 바람 시어" in joined:
        topics.append(
            "연직바람시어가 available_but_unusable이면 시어의 강도·약화·강화·공간구조 변화를 사실로 쓰지 말고, "
            "'연직바람시어 변화는 현재 사용할 수 있는 판독 근거만으로 판단하기 어렵습니다' 수준의 판단 한계만 기술하십시오. "
            "시어 상세자료가 없거나 부족하다고 표현하지 마십시오."
        )
    if "전층 수분량" in joined or "TCW" in joined:
        topics.append(
            "대기 전층 수분량(TCW)이 available_but_unusable이면 TCW 상태·변화를 사실로 쓰지 말고 판단 한계만 기술하며, "
            "TCW 상세자료가 없거나 부족하다고 표현하지 마십시오."
        )
    topic_instruction = " ".join(topics)
    return (
        "observable_grounding_contract의 state와 usable_check_ids를 기준으로 해당 의미 단위를 처음부터 다시 생성하십시오. "
        "available_but_unusable 물리량은 자료 존재와 결론 사용 가능성을 구분해야 합니다. "
        "복합 질문에서는 usable 근거로 답할 수 있는 부분과 판단할 수 없는 물리량을 문장 안에서 명확히 분리하십시오. "
        + topic_instruction
        + f" 검증 내용: {details}"
    )


def _verified_quantitative_relations(checks: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index only Python-verified Step07 relations with candidate-unique refs."""
    indexed: dict[str, dict[str, Any]] = {}
    for check_id, result in checks.items():
        validate_step07_ensemble_consensus(result)
        if result.get("usable_for_synthesis") is False:
            continue
        relations = result.get("quantitative_relations")
        if not isinstance(relations, list):
            continue
        for relation in relations:
            if not isinstance(relation, dict) or relation.get("consistency_status") not in {"passed", "corrected"}:
                continue
            relation_id = str(relation.get("relation_id") or "").strip()
            if not relation_id:
                continue
            relation_ref = f"{check_id}:{relation_id}"
            if relation_ref in indexed:
                raise Pass2PipelineError("QUANTITATIVE_RELATION_ID_DUPLICATE", f"duplicate verified quantitative relation: {relation_ref}")
            try:
                display_relation = compute_display_quantitative_relation(relation)
            except QuantitativeConsistencyError as exc:
                raise Pass2PipelineError(
                    "QUANTITATIVE_RELATION_DISPLAY_INVALID",
                    f"unable to derive forecaster display relation for {relation_ref}: {exc}",
                ) from exc
            display_values = list(display_relation["display_values"])
            public_points: list[dict[str, Any]] = []
            for point, display_value in zip(relation.get("points", []), display_values, strict=True):
                if not isinstance(point, dict):
                    continue
                public_points.append({
                    "request_id": point.get("request_id"),
                    "physical_diagnostic": point.get("physical_diagnostic") or point.get("feature_id"),
                    "forecast_hour": point.get("forecast_hour"),
                    "region_id": point.get("region_id"),
                    "region_name": point.get("region_name"),
                    "statistic": point.get("statistic"),
                    "display_value": display_value,
                    "units": point.get("units"),
                })
            indexed[relation_ref] = {
                "relation_ref": relation_ref,
                "check_id": check_id,
                "relation_id": relation_id,
                "relation_type": relation.get("relation_type"),
                "points": public_points,
                "display_relation": display_relation["display_relation"],
                "display_forecast_hour": display_relation["display_forecast_hour"],
                "consistency_status": relation.get("consistency_status"),
            }
    return indexed


def _public_check_assessment(result: dict[str, Any]) -> dict[str, Any]:
    """Return Step08 input with quantitative values limited to forecaster display precision."""
    try:
        assert_majority_narrative_current(result)
        # canonicalize_majority_result intentionally returns the original object
        # when no majority contract applies. Never mutate authoritative Step07
        # checks while building the model-facing display-precision payload.
        public = copy.deepcopy(canonicalize_majority_result(result))
    except MajorityNarrativeError as exc:
        raise Pass2PipelineError("QUANTITATIVE_NARRATIVE_INVALID", str(exc)) from exc
    claims = public.get("quantitative_claims")
    if isinstance(claims, list):
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            feature_id = str(claim.get("physical_diagnostic") or claim.get("feature_id") or "")
            value = claim.get("resolved_value")
            if isinstance(value, (int, float)) and not isinstance(value, bool) and feature_id:
                claim["display_value"] = format_quantitative_display_value(feature_id, float(value))
                claim.pop("resolved_value", None)
    # Raw arithmetic relations remain stored in Step07 and are validated there.
    # Step08 receives only the display-precision relation index separately.
    public["quantitative_relations"] = []
    return public


def validate_step07_candidate_evidence(root: Path, group: dict[str, Any], checks: dict[str, dict[str, Any]]) -> None:
    """Block Step08 when stored Step07 references or resolved claims no longer match Step05."""
    check_evidence = {
        str(item["check_id"]): item
        for item in group.get("check_evidence", [])
        if isinstance(item, dict) and isinstance(item.get("check_id"), str)
    }
    entries_by_request = {
        str(item["request"]["request_id"]): item
        for item in group.get("analysis_data_requests", [])
        if isinstance(item, dict) and isinstance(item.get("request"), dict) and isinstance(item["request"].get("request_id"), str)
    }
    specifications = _check_specifications(group)
    for check_id, result in checks.items():
        check = check_evidence.get(check_id)
        if check is None:
            raise Pass2PipelineError("STEP07_RESULTS_INVALID", f"{group.get('candidate_id')} has no Step06 check evidence for {check_id}.")
        try:
            entries = [entries_by_request[str(request_id)] for request_id in check["request_ids"]]
        except KeyError as exc:
            raise Pass2PipelineError("STEP06_PACKAGE_INVALID", f"{group.get('candidate_id')} check {check_id} has unresolved request evidence.") from exc
        specification = specifications.get(check_id)
        if specification is None:
            raise Pass2PipelineError("VARIABLE_CHECKS_INVALID", f"{group.get('candidate_id')} has no Step04 definition for {check_id}.")
        validate_check_evidence_references(check, entries, result, check_specification=specification)
        if _single_majority_convergence_not_strengthen_claim(specification) is not None and result.get("majority_aggregation") is None:
            raise Pass2PipelineError(
                "QUANTITATIVE_NARRATIVE_INVALID",
                f"{check_id}: Python majority aggregation is missing; rerun Step07.",
            )
        if result.get("majority_aggregation") is not None:
            try:
                assert_majority_narrative_current(result)
            except MajorityNarrativeError as exc:
                raise Pass2PipelineError("QUANTITATIVE_NARRATIVE_INVALID", str(exc)) from exc
        validate_spatial_localization_contract(root, group, specification, result)
        validate_precipitation_motion_contract(root, group, specification, result)
        language_errors = validate_step07_result_narrative(result)
        if language_errors:
            raise Pass2PipelineError(
                "NARRATIVE_LANGUAGE_INVALID",
                "저장된 상세 판독 결과가 새 한글 기준을 충족하지 않습니다. Step07을 다시 수행해야 합니다. "
                + "; ".join(language_errors[:8]),
            )


def _check_specifications(group: dict[str, Any]) -> dict[str, dict[str, Any]]:
    plan = group["candidate_context"]["analysis_plan"]
    values = plan.get("variable_checks")
    if not isinstance(values, list):
        raise Pass2PipelineError("VARIABLE_CHECKS_INVALID", f"{group.get('candidate_id')} has no variable_checks.")
    return {
        str(item["check_id"]): item
        for item in values
        if isinstance(item, dict) and isinstance(item.get("check_id"), str) and item["check_id"]
    }


def _candidate_detection_basis(group: dict[str, Any]) -> str:
    candidate = ((group.get("candidate_context") or {}).get("candidate") or {})
    return str(candidate.get("detection_basis") or "") if isinstance(candidate, dict) else ""


def _direct_precipitation_anchor_contract_enabled(group: dict[str, Any]) -> bool:
    """Return whether Step04 installed the system-owned presence-anchor contract.

    Legacy Step04 plans remain readable. Only the explicit SYSTEM-owned Check
    controls the independent direct-precipitation status; model-authored
    precipitation Checks never become status anchors by prose, generic contract
    version, or structural resemblance.
    """
    specifications = _check_specifications(group)
    return any(
        isinstance(spec.get("intent_contract"), dict)
        and spec["intent_contract"].get("direct_precipitation_anchor") is True
        and str(spec["intent_contract"].get("source_check_key") or "")
        == SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY
        for spec in specifications.values()
    )



def _direct_precipitation_presence_anchor_ids(group: dict[str, Any]) -> list[str]:
    specifications = _check_specifications(group)
    return sorted(
        check_id
        for check_id, specification in specifications.items()
        if isinstance(specification.get("intent_contract"), dict)
        and specification["intent_contract"].get("direct_precipitation_anchor") is True
        and str(specification["intent_contract"].get("source_check_key") or "")
        == SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY
    )



def _direct_precipitation_status_contract(
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Derive direct-precipitation status from the dedicated presence anchor.

    This is a structural state mapping, not a meteorological re-analysis.  The
    Step04 AI chooses the candidate, area, and peak hours; the system adds the
    dedicated presence Check from those immutable choices and Step07 VLM assesses
    it. Python only maps that assessment to the independent
    Step08 direct-precipitation status so motion/organization/intensity Checks
    cannot silently downgrade the existence signal.
    """
    controlled = _direct_precipitation_anchor_contract_enabled(group)
    if not controlled:
        return {
            "status_controlled_by_presence_anchor": False,
            "presence_anchor_check_ids": [],
            "required_status": None,
            "anchor_assessment": None,
            "anchor_evidence_usable": False,
        }

    anchor_ids = _direct_precipitation_presence_anchor_ids(group)
    if len(anchor_ids) != 1:
        raise Pass2PipelineError(
            "STEP06_PACKAGE_INVALID",
            f"{group.get('candidate_id')} requires exactly one Step04 SYSTEM "
            f"direct-precipitation presence anchor; actual={anchor_ids}. Rerun Step04 for this init.",
        )
    check_id = anchor_ids[0]
    result = checks.get(check_id)
    if not isinstance(result, dict):
        required_status = "insufficient_evidence"
        assessment = None
        usable = False
    else:
        assessment = str(result.get("assessment") or "")
        usable = bool(
            result.get("evidence_status") == "complete"
            and result.get("evidence_complete") is True
            and result.get("usable_for_synthesis") is not False
        )
        if not usable:
            required_status = "insufficient_evidence"
        elif assessment == "observed":
            required_status = "confirmed"
        elif assessment == "partially_observed":
            required_status = "partial"
        elif assessment in {"not_observed", "opposite_observed"}:
            required_status = "not_confirmed"
        else:
            required_status = "insufficient_evidence"

    return {
        "status_controlled_by_presence_anchor": True,
        "presence_anchor_check_ids": anchor_ids,
        "required_status": required_status,
        "anchor_assessment": assessment,
        "anchor_evidence_usable": usable,
    }


def _legacy_candidate_statuses(signal: dict[str, Any]) -> tuple[str, str]:
    """Map only explicit legacy classes; never infer an unknown state as confirmed."""
    legacy_class = str(signal.get("signal_class") or "")
    defaults = {
        # Historical class retained only for reading old checkpoints.
        "confirmed_direct_hazard": ("confirmed", "not_confirmed"),
        "direct_environment_supported": ("confirmed", "confirmed"),
        "direct_environment_conditional": ("confirmed", "partial"),
        "direct_environment_unsupported": ("confirmed", "not_confirmed"),
        "direct_environment_unresolved": ("confirmed", "insufficient_evidence"),
        "precursor_watch": ("not_confirmed", "confirmed"),
        "partially_supported": ("partial", "partial"),
        "rejected": ("not_confirmed", "not_confirmed"),
        "insufficient_evidence": ("insufficient_evidence", "insufficient_evidence"),
    }
    default_direct, default_environment = defaults.get(legacy_class, ("not_confirmed", "not_confirmed"))
    direct = str(signal.get("direct_precipitation_status") or default_direct)
    environment = str(signal.get("environment_support_status") or default_environment)
    return (
        direct if direct in CANDIDATE_SIGNAL_STATUSES else default_direct,
        environment if environment in CANDIDATE_SIGNAL_STATUSES else default_environment,
    )


def migrate_step08_document(value: dict[str, Any]) -> dict[str, Any]:
    """Add deterministic dual statuses to v4 checkpoints before validation/reuse."""
    result = copy.deepcopy(value)
    candidates = result.get("candidates")
    if not isinstance(candidates, list):
        return result
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        revision = candidate.get("revision")
        if isinstance(revision, dict):
            revision.setdefault("revised_candidate_name", None)
            revision.setdefault("candidate_name_revision_reason", None)
            revision.setdefault("candidate_name_revision_evidence_check_ids", [])
        signal = candidate.get("signal_assessment")
        if not isinstance(signal, dict):
            continue
        direct, environment = _legacy_candidate_statuses(signal)
        signal["direct_precipitation_status"] = direct
        signal["environment_support_status"] = environment
        signal["signal_class"] = derive_signal_class(direct, environment)
        signal.setdefault("environment_localization", None)
        if "environment_persistence_status" not in signal:
            signal["environment_persistence_status"] = (
                "insufficient_evidence"
                if environment in {"confirmed", "partial"}
                else "not_applicable"
            )
        signal.setdefault("environment_persistence_evidence_check_ids", [])
        if "physical_consistency_status" not in signal:
            signal["physical_consistency_status"] = "insufficient_evidence"
        if "physical_mechanism_summary" not in signal:
            signal["physical_mechanism_summary"] = "기존 결과에는 물리과정 성립성 판단이 없어 재분석이 필요합니다."
        # v8 stores directional meaning per Check instead of forcing an entire
        # partially-observed Check into one supporting/limiting bucket.  Older
        # checkpoints intentionally receive empty evidence objects so Step09
        # requires a current Step08 rerun rather than fabricating semantic roles.
        signal.setdefault("mechanism_evidence", [])
        signal.setdefault("monitoring_evidence", [])
        for legacy_field in (
            "physical_supporting_check_ids",
            "physical_limiting_check_ids",
            "mechanism_supporting_check_ids",
            "mechanism_limiting_check_ids",
            "monitoring_supporting_check_ids",
            "monitoring_limiting_check_ids",
        ):
            signal.pop(legacy_field, None)
        if "monitoring_value" not in signal:
            signal["monitoring_value"] = "undetermined"
        if "operational_surface_decision" not in signal:
            signal["operational_surface_decision"] = "not_applicable"
        if "operational_surface_reason" not in signal:
            signal["operational_surface_reason"] = "기존 결과에는 물리적 성립성과 감시 가치 판단이 없어 재분석이 필요합니다."
        # Removed in v6.  Keeping the old generic evidence list would duplicate
        # the explicit physical supporting/limiting evidence contract.
        signal.pop("operational_surface_evidence_check_ids", None)
    return result


def _guidance_time_window(group: dict[str, Any]) -> tuple[int, int]:
    """Read the Step04 candidate window used by deterministic fallback guidance."""
    candidate = ((group.get("candidate_context") or {}).get("candidate") or {})
    window = candidate.get("time_window") if isinstance(candidate, dict) else None
    if not isinstance(window, dict):
        raise Pass2PipelineError("STEP06_PACKAGE_INVALID", "candidate time_window is unavailable for operational guidance.")
    try:
        return int(window["forecast_hour_start"]), int(window["forecast_hour_end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise Pass2PipelineError("STEP06_PACKAGE_INVALID", "candidate time_window is invalid for operational guidance.") from exc


def _deterministic_operational_guidance(
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    *,
    direct_status: str,
    environment_status: str,
) -> dict[str, Any]:
    """Create only the no-usable-evidence fallback; normal guidance is model-authored."""
    start, end = _guidance_time_window(group)
    if direct_status == "confirmed" and environment_status in {"partial", "not_confirmed", "insufficient_evidence"}:
        current_action = "모델 강수 예측은 확인되었으나 강수 발달 환경은 확정하지 않고 함께 재확인합니다."
    else:
        current_action = "현재 상세 판독 근거가 충분하지 않아 판단을 보류하고 추가 자료를 확인합니다."
    return {
        "current_action": current_action,
        "next_check": f"F{start:03d}–F{end:03d} 구간에서 모델 강수량과 강수 발달 환경의 변화를 재확인합니다.",
        "interpretation_limit": "확인되지 않은 모델 강수 예측, 강수 발달 환경 또는 시공간 일치를 확정적으로 해석하지 않습니다.",
        "evidence_check_ids": sorted(checks),
    }


def _deterministic_operational_brief(
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Create only the generic no-usable-evidence fallback summary."""
    start, end = _guidance_time_window(group)
    return {
        "key_signal": "판단 가능한 상세 근거가 충분하지 않습니다.",
        "key_limitation": "완결된 상세 판독 근거가 부족합니다.",
        "next_check": f"F{start:03d}–F{end:03d} 구간의 모델 강수와 강수 발달 환경 변화를 다시 확인합니다.",
        "evidence_check_ids": [],
    }


def validate_operational_brief(
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    value: dict[str, Any],
    *,
    required: bool = True,
) -> None:
    """Validate the LLM-authored glanceable operational summary.

    Python validates length, prohibited language, and Step07 references only.
    It never rewrites meteorological meaning or authors a replacement summary.
    """
    candidate_id = str(group.get("candidate_id") or "unknown")
    brief = value.get("operational_brief")
    if brief is None and not required:
        return
    expected_fields = {*_OPERATIONAL_BRIEF_FIELDS, "evidence_check_ids"}
    if not isinstance(brief, dict) or set(brief) != expected_fields:
        raise Pass2PipelineError(
            "OPERATIONAL_BRIEF_INVALID",
            f"{candidate_id} operational_brief structure is invalid.",
        )
    text_by_field: dict[str, str] = {}
    for field in _OPERATIONAL_BRIEF_FIELDS:
        raw = brief.get(field)
        if not isinstance(raw, str) or not raw.strip():
            raise Pass2PipelineError(
                "OPERATIONAL_BRIEF_INVALID",
                f"{candidate_id} operational_brief.{field} must be non-empty text.",
            )
        compact = re.sub(r"\s+", " ", raw).strip()
        if len(compact) > _OPERATIONAL_BRIEF_MAX_CHARS:
            raise Pass2PipelineError(
                "OPERATIONAL_BRIEF_INVALID",
                f"{candidate_id} operational_brief.{field} exceeds {_OPERATIONAL_BRIEF_MAX_CHARS} characters.",
            )
        text_by_field[field] = compact

    evidence_ids = brief.get("evidence_check_ids")
    usable_check_ids = _usable_check_ids(checks)
    invalid_refs = (
        not isinstance(evidence_ids, list)
        or any(not isinstance(check_id, str) or not check_id.strip() for check_id in evidence_ids)
        or len(set(evidence_ids)) != len(evidence_ids)
        or not set(evidence_ids).issubset(usable_check_ids)
        or (bool(usable_check_ids) and not evidence_ids)
        or (not usable_check_ids and bool(evidence_ids))
    )
    if invalid_refs:
        raise Pass2PipelineError(
            "OPERATIONAL_BRIEF_INVALID",
            f"{candidate_id} operational_brief has invalid Step07 evidence_check_ids.",
        )

    combined_text = " ".join(text_by_field.values())
    forbidden = next((term for term in _GUIDANCE_PROHIBITED_TERMS if term in combined_text), None)
    if forbidden or re.search(r"(?:관심|주의|경계|심각)\s*(?:단계|등급)", combined_text):
        detail = forbidden or "단계·등급 표현"
        raise Pass2PipelineError(
            "OPERATIONAL_BRIEF_INVALID",
            f"{candidate_id} operational_brief uses prohibited operational language: {detail}.",
        )
    if re.search(r"\b(?:Step0?[1-9]|CHK_[A-Z0-9_]+|CAND_[A-Z0-9_]+)\b", combined_text, flags=re.IGNORECASE):
        raise Pass2PipelineError(
            "OPERATIONAL_BRIEF_INVALID",
            f"{candidate_id} operational_brief exposes an internal identifier.",
        )
    if not _GUIDANCE_METEOROLOGICAL_MARKERS.search(text_by_field["next_check"]):
        raise Pass2PipelineError(
            "OPERATIONAL_BRIEF_INVALID",
            f"{candidate_id} operational_brief.next_check must include a meteorological target.",
        )

    signal = value.get("signal_assessment") if isinstance(value.get("signal_assessment"), dict) else {}
    if signal.get("environment_persistence_status") == "limited" and re.search(
        r"(?:제한|문제|상반|불확실).{0,8}(?:없|없음|없다|없습니다)|(?:뚜렷한|추가).{0,8}(?:제한|문제|상반|불확실).{0,8}(?:없|없음)",
        text_by_field["key_limitation"],
    ):
        raise Pass2PipelineError(
            "OPERATIONAL_BRIEF_INVALID",
            f"{candidate_id} limited environment persistence cannot be summarized as having no limitation.",
        )


def validate_operational_guidance(
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    value: dict[str, Any],
    *,
    required: bool = True,
    quality_contract: bool = False,
) -> None:
    """Validate the bounded, evidence-linked forecaster guidance contract.

    The contract intentionally validates only operational wording constraints.  It
    never derives new risk levels, rankings, special-weather notices, or actions.
    ``required=False`` is reserved for Step10 reads of legacy Step08/09 outputs.
    """
    candidate_id = str(group.get("candidate_id") or "unknown")
    guidance = value.get("operational_guidance")
    if guidance is None and not required:
        return
    if not isinstance(guidance, dict) or set(guidance) != {*_GUIDANCE_FIELDS, "evidence_check_ids"}:
        raise Pass2PipelineError("OPERATIONAL_GUIDANCE_INVALID", f"{candidate_id} operational_guidance structure is invalid.")

    text_by_field: dict[str, str] = {}
    for field in _GUIDANCE_FIELDS:
        raw = guidance.get(field)
        if not isinstance(raw, str) or not raw.strip():
            raise Pass2PipelineError("OPERATIONAL_GUIDANCE_INVALID", f"{candidate_id} operational_guidance.{field} must be non-empty text.")
        text_by_field[field] = raw.strip()

    evidence_ids = guidance.get("evidence_check_ids")
    if (
        not isinstance(evidence_ids, list)
        or not evidence_ids
        or any(not isinstance(check_id, str) or not check_id.strip() for check_id in evidence_ids)
        or len(set(evidence_ids)) != len(evidence_ids)
        or not set(evidence_ids).issubset(set(checks))
    ):
        raise Pass2PipelineError("OPERATIONAL_GUIDANCE_INVALID", f"{candidate_id} operational_guidance has invalid Step07 evidence_check_ids.")

    combined_text = " ".join(text_by_field.values())
    forbidden = next((term for term in _GUIDANCE_PROHIBITED_TERMS if term in combined_text), None)
    if forbidden or re.search(r"(?:관심|주의|경계|심각)\s*(?:단계|등급)", combined_text):
        detail = forbidden or "단계·등급 표현"
        raise Pass2PipelineError("OPERATIONAL_GUIDANCE_INVALID", f"{candidate_id} operational_guidance uses prohibited operational language: {detail}.")
    if re.search(r"\b(?:Step0?[1-9]|CHK_[A-Z0-9_]+|CAND_[A-Z0-9_]+)\b", combined_text, flags=re.IGNORECASE):
        raise Pass2PipelineError("OPERATIONAL_GUIDANCE_INVALID", f"{candidate_id} operational_guidance exposes an internal identifier.")

    next_check = text_by_field["next_check"]
    if _GUIDANCE_VAGUE_NEXT_CHECK.fullmatch(next_check) or not _GUIDANCE_NEXT_CHECK_MARKERS.search(next_check):
        raise Pass2PipelineError("OPERATIONAL_GUIDANCE_INVALID", f"{candidate_id} operational_guidance.next_check is not a concrete recheck target.")

    # The stronger writing-quality contract is applied only while Step08 is
    # generating/reusing results under the current prompt.  Step09/10 keep the
    # baseline contract so historical products remain readable after prompt
    # evolution.  Python validates structure and intent here; it never authors
    # replacement forecaster prose.
    if quality_contract:
        current_action = text_by_field["current_action"]
        if re.match(r"^\s*F\d{3}", current_action):
            raise Pass2PipelineError(
                "OPERATIONAL_GUIDANCE_INVALID",
                f"{candidate_id} operational_guidance.current_action must state the current interpretation before forecast-hour evidence.",
            )
        if _GUIDANCE_CURRENT_ACTION_IMPERATIVE.search(current_action):
            raise Pass2PipelineError(
                "OPERATIONAL_GUIDANCE_INVALID",
                f"{candidate_id} operational_guidance.current_action must be declarative rather than an instruction to the forecaster.",
            )
        if not _GUIDANCE_TIME_MARKERS.search(next_check):
            raise Pass2PipelineError(
                "OPERATIONAL_GUIDANCE_INVALID",
                f"{candidate_id} operational_guidance.next_check must include a concrete forecast time or time range.",
            )
        if not _GUIDANCE_METEOROLOGICAL_MARKERS.search(next_check):
            raise Pass2PipelineError(
                "OPERATIONAL_GUIDANCE_INVALID",
                f"{candidate_id} operational_guidance.next_check must include a meteorological recheck target.",
            )
        if not _GUIDANCE_CHANGE_MARKERS.search(next_check):
            raise Pass2PipelineError(
                "OPERATIONAL_GUIDANCE_INVALID",
                f"{candidate_id} operational_guidance.next_check must describe the change to be evaluated.",
            )
        if not _GUIDANCE_UPDATE_DECISION_MARKERS.search(next_check):
            raise Pass2PipelineError(
                "OPERATIONAL_GUIDANCE_INVALID",
                f"{candidate_id} operational_guidance.next_check must state when the current interpretation is maintained or reconsidered.",
            )

    signal = value.get("signal_assessment") if isinstance(value.get("signal_assessment"), dict) else {}
    direct_status = signal.get("direct_precipitation_status")
    environment_status = signal.get("environment_support_status")
    if direct_status == "confirmed" and environment_status in {"partial", "not_confirmed", "insufficient_evidence"}:
        current_action = text_by_field["current_action"]
        if "모델 강수" not in current_action or "강수 발달 환경" not in current_action:
            raise Pass2PipelineError(
                "OPERATIONAL_GUIDANCE_INVALID",
                f"{candidate_id} operational_guidance.current_action must distinguish confirmed direct precipitation from environment status.",
            )
    if not signal.get("validated_spatiotemporal_alignment"):
        # Do not reject legitimate negative or prospective wording such as
        # "시공간 정합이 확인되지 않았다", "확인되는지 재확인한다", or
        # "확인되면 판단을 유지한다".  The previous substring check matched
        # the common stem "...확인" inside all of those phrases and could make
        # a guidance-only retry fail repeatedly even when the model correctly
        # described alignment as unconfirmed.  Reject only clear present/past
        # positive assertions of validated alignment.
        if _GUIDANCE_POSITIVE_ALIGNMENT_ASSERTION.search(combined_text):
            raise Pass2PipelineError("OPERATIONAL_GUIDANCE_INVALID", f"{candidate_id} operational_guidance asserts unsupported alignment.")


def _no_usable_status(
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    *,
    direct: bool,
) -> str:
    specifications = _check_specifications(group)
    relevant = [
        checks[check_id]
        for check_id, specification in specifications.items()
        if check_id in checks
        and (
            specification.get("variable_role") == "hazard_magnitude"
            if direct
            else specification.get("variable_role") in {
                "moisture_supply",
                "low_level_forcing",
                "midlevel_forcing",
                "thermodynamic_support",
                "organization_persistence",
                "synoptic_support",
            }
        )
    ]
    if not relevant or all(
        result.get("assessment") == "insufficient_evidence"
        or result.get("evidence_status") == "none"
        for result in relevant
    ):
        return "insufficient_evidence"
    return "not_confirmed"


def _no_usable_exclusion_text(checks: dict[str, dict[str, Any]]) -> str:
    reasons = {
        str(reason)
        for result in checks.values()
        if isinstance(result, dict)
        for reason in result.get("excluded_from_synthesis_reasons", [])
        if isinstance(reason, str)
    }
    labels = {
        "QUANTITATIVE_RELATION_MISMATCH": "정량 비교 불일치",
        "QUANTITATIVE_RELATION_REQUIRED": "필수 정량 관계 누락",
        "QUANTITATIVE_FEATURE_USE_INVALID": "6시간 강수량 관계 누락",
    }
    selected = [labels[reason] for reason in sorted(reasons) if reason in labels]
    return ", ".join(selected) if selected else "일관성 검증 미통과"


def _deterministic_insufficient(root: Path, group: dict[str, Any], checks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    candidate_id = str(group["candidate_id"])
    plan = group["candidate_context"]["analysis_plan"]
    check_ids = sorted(checks)
    hypotheses = []
    for hypothesis in plan.get("hypotheses", []):
        hypotheses.append({
            "hypothesis_id": hypothesis["hypothesis_id"], "statement": hypothesis["statement"],
            "supporting_check_ids": hypothesis.get("supporting_check_ids", []), "opposing_check_ids": hypothesis.get("opposing_check_ids", []),
            "assessment": "insufficient_evidence", "evidence_check_ids": [],
            "quantitative_relation_ids": [],
            "rationale": "연결된 모든 확인 결과에서 판단 가능한 근거가 부족합니다.", "uncertainties": ["해당 감시후보의 상세 예측자료가 충분하지 않습니다."],
        })
    answers = [{
        **row, "answer": "연결된 상세 예측자료가 불충분하여 답변을 확정할 수 없습니다.",
        "evidence_check_ids": [], "quantitative_relation_ids": [],
        "uncertainties": ["상세 판독 결과의 완결성이 충분하지 않습니다."],
    } for row in _question_rows(candidate_id, plan)]
    direct_status = _no_usable_status(group, checks, direct=True)
    environment_status = _no_usable_status(group, checks, direct=False)
    excluded = direct_status != "insufficient_evidence" or environment_status != "insufficient_evidence"
    exclusion_text = _no_usable_exclusion_text(checks)
    if excluded:
        direct_text = "모델 강수 예측 자료는 제공되었으나 종합판단에 사용할 수 있도록 검증되지 않았습니다."
        environment_text = "강수 발달 환경 자료는 제공되었으나 종합판단에 사용할 수 있도록 검증되지 않았습니다."
        final_text = f"상세 예측자료는 제공되었으나 {exclusion_text}로 종합판단 근거에서 제외되어 운영 평가를 보류합니다."
        uncertainty_text = f"자료 부재가 아니라 {exclusion_text}로 확인 결과가 종합판단에서 제외되었습니다."
    else:
        direct_text = "모델 강수 예측을 판단할 상세자료의 완결성이 부족합니다."
        environment_text = "강수 발달 환경을 판단할 상세자료의 완결성이 부족합니다."
        final_text = "판단 자료가 충분하지 않아 운영 평가를 보류합니다."
        uncertainty_text = "완결된 상세 판독 결과가 없어 최종 판단에 제약이 있습니다."
    naming_contract = _candidate_name_grounding_contract(root, group, checks)
    naming_option = (naming_contract.get("name_options") or [{}])[0]
    fallback_name = str(naming_option.get("name") or "강수 감시후보")
    fallback_name_evidence = [str(value) for value in naming_option.get("evidence_check_ids", []) if str(value)]
    return {
        "candidate_id": candidate_id, "hypothesis_assessments": hypotheses, "forecaster_question_answers": answers,
        "signal_assessment": {
            "direct_precipitation_status": direct_status,
            "environment_support_status": environment_status,
            "environment_persistence_status": (
                "insufficient_evidence"
                if environment_status in {"confirmed", "partial"}
                else "not_applicable"
            ),
            "environment_persistence_evidence_check_ids": [],
            "direct_precipitation_assessment": direct_text,
            "precursor_environment_assessment": environment_text,
            "contradictory_evidence": [],
            "validated_precursor_evidence": [],
            "validated_spatiotemporal_alignment": False,
            "spatiotemporal_alignment_evidence_check_ids": [],
            "signal_evidence_check_ids": [],
            "physical_consistency_status": "insufficient_evidence",
            "physical_mechanism_summary": "상세 판독 근거가 충분하지 않아 강수 신호를 설명하는 물리과정의 성립 여부를 판단하기 어렵습니다.",
            "mechanism_evidence": [],
            "monitoring_evidence": [],
            "monitoring_value": "undetermined",
            "operational_surface_decision": "not_applicable",
            "operational_surface_reason": "물리적 성립성과 감시 가치를 판단할 근거가 충분하지 않아 운영 판단을 보류합니다.",
        },
        "uncertainties": [uncertainty_text],
        "final_assessment": final_text,
        "revision": {
            "revised_candidate_name": fallback_name,
            "candidate_name_revision_reason": "사용 가능한 상세 판독 근거가 없어 SYSTEM 공간 계약에서 허용된 보수적 표시명을 사용합니다.",
            "candidate_name_revision_evidence_check_ids": fallback_name_evidence,
            "revised_area": None,
            "revised_time_window": None,
        },
        "operational_brief": _deterministic_operational_brief(group, checks),
        "operational_guidance": _deterministic_operational_guidance(
            group,
            checks,
            direct_status=direct_status,
            environment_status=environment_status,
        ),
    }


def _usable_precursor_evidence_paths(
    group: dict[str, Any], checks: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Return category-specific usable Step07 anchors for Step08 grounding.

    This is deterministic reference scoping only.  ``validated_precursor_evidence``
    is reserved for positive support, so not-observed/opposite/uncertain Checks are
    excluded from these anchor lists and remain available separately as physical
    limiting evidence.  Empty lists mean that Step08 must not declare that category
    as validated precursor support.
    """
    specifications = _check_specifications(group)
    usable = _usable_check_ids(checks)
    return {
        category: sorted(
            check_id
            for check_id, specification in specifications.items()
            if check_id in usable
            and category_check_is_positive_support_compatible(category, specification)
            and checks.get(check_id, {}).get("assessment") in POSITIVE_VALIDATED_PRECURSOR_ASSESSMENTS
        )
        for category in PRECURSOR_CATEGORY_ORDER
    }


def _confirmed_environment_precursor_evidence_paths(
    group: dict[str, Any], checks: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Return positive precursor anchors eligible for ``environment=confirmed``.

    General synthesis may still use non-supportive Checks such as ``not_observed``
    as limiting/opposing evidence.  They are excluded only from the positive
    precursor anchors used to justify a confirmed environment.  This mirrors the
    Step09 publication contract without making a deterministic meteorological
    decision.
    """
    specifications = _check_specifications(group)
    paths: dict[str, list[str]] = {}
    for category in PRECURSOR_CATEGORY_ORDER:
        if category == "direct_precipitation" or category not in OPERATIONAL_PRECURSOR_CATEGORIES:
            continue
        paths[category] = sorted(
            check_id
            for check_id, specification in specifications.items()
            if category_check_is_positive_support_compatible(category, specification)
            and isinstance(checks.get(check_id), dict)
            and checks[check_id].get("assessment") in _CONFIRMED_ENVIRONMENT_PRECURSOR_ASSESSMENTS
            and checks[check_id].get("evidence_status") == "complete"
            and checks[check_id].get("evidence_complete") is True
            and checks[check_id].get("usable_for_synthesis") is not False
        )
    return paths


def _candidate_analysis_region_index(root: Path, group: dict[str, Any]) -> dict[str, Any]:
    """Return standardized operational-region names that overlap candidate_area.

    This index is **naming context only** for Step08 natural-language synthesis.
    It does not change the candidate bbox, Step05 diagnostic domain, Step07
    evidence, or the population used by any quantitative relation.  The model
    may use these names to explain what the analysis domain covers, but may not
    infer that a meteorological feature is located in a region merely because
    the region overlaps the candidate bbox.
    """
    candidate = (group.get("candidate_context") or {}).get("candidate") or {}
    candidate_bbox = candidate.get("candidate_area")
    if not isinstance(candidate_bbox, dict):
        return {
            "contract_version": ANALYSIS_REGION_NAMING_CONTRACT_VERSION,
            "scope": "candidate_area_overlap",
            "usage": "analysis_area_naming_only",
            "regions": [],
        }

    catalog_path = runtime_path(
        root,
        "daba",
        "ai_pass1",
        "discovery_operational_regions.yaml",
    )
    regions = load_operational_regions(catalog_path)
    selected = selectable_pass1_regions(
        regions=regions,
        candidate_bbox=candidate_bbox,
    )
    return {
        "contract_version": ANALYSIS_REGION_NAMING_CONTRACT_VERSION,
        "scope": "candidate_area_overlap",
        "usage": "analysis_area_naming_only",
        "regions": [
            {
                "region_id": str(item.get("region_id", "")),
                "name_ko": str(item.get("name_ko", "")),
                "region_role": str(item.get("region_role", "")),
                "eligible_for_risk_rank": bool(item.get("eligible_for_risk_rank")),
            }
            for item in selected
        ],
    }


# LEGACY_DISABLED: Step08 now regenerates complete semantic blocks in
# ``run_step08``.  The field-scoped patch helpers below have no active caller
# and are retained only to avoid breaking external imports during migration.
def _messages(
    root: Path,
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    prompt_text: str,
) -> list[dict[str, Any]]:
    context = group["candidate_context"]
    plan = context["analysis_plan"]
    check_specifications = _check_specifications(group)
    verified_relations = _verified_quantitative_relations(checks)
    unusable_check_ids = sorted(set(checks) - _usable_check_ids(checks))
    payload = {
        "candidate": context["candidate"],
        "candidate_core_time_window": (context.get("candidate") or {}).get("time_window"),
        "candidate_analysis_region_index": _candidate_analysis_region_index(root, group),
        "hypotheses": plan.get("hypotheses", []),
        "hypothesis_evidence_grounding_contract": _hypothesis_evidence_grounding_contract(group, checks),
        "forecaster_questions": _question_rows(str(group["candidate_id"]), plan),
        "check_assessments": [_public_check_assessment(checks[check_id]) for check_id in sorted(checks)],
        "verified_quantitative_relations": [verified_relations[key] for key in sorted(verified_relations)],
        "required_forecaster_quantitative_facts": requirements_for_questions(
            _question_rows(str(group["candidate_id"]), plan), verified_relations,
        ),
        "unusable_check_ids": unusable_check_ids,
        "allowed_precursor_evidence_paths": _usable_precursor_evidence_paths(group, checks),
        "allowed_alignment_evidence_check_ids": _usable_alignment_check_ids(group, checks),
        "signal_grounding_contract": _signal_grounding_contract(group, checks),
        "candidate_name_grounding_contract": _candidate_name_grounding_contract(root, group, checks),
        "evidence_reference_contract": _reference_repair_constraints(group, checks),
        "check_semantics": [
            _public_check_semantics(check_specifications[check_id])
            for check_id in sorted(check_specifications)
        ],
        "instruction": (
            "Step04 hypothesis의 statement, supporting_check_ids, opposing_check_ids와 forecaster question 원문은 입력 전용 불변정보이며 응답에서 다시 작성하지 마십시오. "
            "가설의 evidence_check_ids는 evidence_reference_contract.hypotheses의 해당 hypothesis_id에 정의된 allowed_evidence_check_ids 안에서만 선택하십시오. "
            "hypothesis_evidence_grounding_contract는 가설마다 실제로 사용 가능한 Step07 지지·반대 Check를 분리한 강제 계약입니다. "
            "Step04 가설 statement는 확인된 현상이 아닙니다. usable supporting_check_ids가 비어 있으면 supported/partially_supported로 평가할 수 없습니다. "
            "지지·반대 모두 사용 불가라면 uncertain 또는 insufficient_evidence, 빈 evidence_check_ids/quantitative_relation_ids를 사용하고 기상학적 판단의 한계를 서술하십시오. "
            "다른 가설에서 확정된 3요소 공간 결합에 사용할 수 없는 4요소 Check의 수분 요소를 추가하지 마십시오. "
            "예보관 질문의 evidence_check_ids는 evidence_reference_contract.question_allowed_evidence_check_ids 안에서만 선택하십시오. "
            "앞 단계 결론을 반복하지 말고 상세 판독 결과를 종합하십시오. 이 단계에는 이미지가 직접 제공되지 않습니다. "
            "candidate_overlap majority 925 hPa 수렴 정량 Check의 findings/contradictory_findings는 Python 원자료에서 확정된 사실입니다. 그 권역 수와 강화 예외 목록을 추측하거나 재계산하여 변경하지 마십시오. "
            "다른 시각이나 범위의 기상학적 해석은 해당 근거를 명시하여 별도로 서술하십시오. "
            "verified_quantitative_relations는 Step05 원자료에서 Python이 사후검증한 정량관계의 기준 정보입니다. "
            "verified_quantitative_relations는 내부 원자료 검증을 통과한 관계를 예보관 화면 정밀도로 변환한 공개 표현 기준이며 display_relation과 각 point의 display_value만 사용하십시오. "
            "정량 비교·증감·최대/최소를 자연어로 서술할 때 표시값이 같아지는 경우 화면에 보이지 않는 원자료의 미세한 대소관계를 다시 계산하거나 과장하지 마십시오. "
            "새 관계를 계산하거나 만들지 말고 해당 relation_ref만 quantitative_relation_ids에 참조하십시오. "
            "required_forecaster_quantitative_facts에 지정된 질문은 Python이 이미 비교한 지역별 최대 또는 평균 6시간 강수량의 두 시각과 표시값, 증감 방향을 답변에 명시하고, relation_ref와 check_id를 해당 질문의 근거 배열에 반드시 포함하십시오. "
            "후보 전체나 다른 권역의 증감은 이 국지 비교로 추론하지 말고, 모르는 범위만 별도로 불확실하다고 서술하십시오. 검증된 비교값을 확인할 수 없다고 부정하지 마십시오. "
            "unusable_check_ids는 지지·상반 근거나 신호 판정 근거로 사용하지 마십시오. "
            "positive spatiotemporal alignment의 evidence_check_ids는 allowed_alignment_evidence_check_ids에 있는 Check만 사용하십시오. "
            "allowed_alignment_evidence_check_ids는 multivariable_alignment이면서 assessment=observed, evidence_status=complete, evidence_complete=true, usable_for_synthesis인 Check만 포함합니다. "
            "validated_spatiotemporal_alignment=false이면 spatiotemporal_alignment_evidence_check_ids는 반드시 빈 배열이어야 합니다. "
            "signal_grounding_contract는 signal_assessment 전체에 적용되는 권위 있는 참조 제약입니다. direct_precipitation.status_controlled_by_presence_anchor=true이면 required_status를 그대로 사용하고 강수 이동·연속성·조직화·피크·강도 변화 Check로 모델 강수 예측 존재 상태를 변경하지 마십시오. 최초 응답부터 system direct precipitation presence anchor, positive alignment, environment confirmed 조건과 핵심시각 환경 존재·환경 지속성 분리 계약을 동시에 만족시키십시오. "
            "candidate_name_grounding_contract는 최종 사용자 표시 후보명의 폐쇄형 허용 목록입니다. revised_candidate_name은 allowed_display_names 중 하나를 정확히 선택해 반드시 작성하십시오. 후보 전체의 직접강수 분포·연속성·환경장 맥락을 종합해 가장 대표적인 이름을 고르되, 허용 목록 밖 지명·방향·공간범위를 만들지 마십시오. candidate_name_revision_reason에는 그 이름을 선택한 이유를 간단히 쓰고, candidate_name_revision_evidence_check_ids에는 선택한 name_option의 evidence_check_ids를 포함하십시오. Step04 원본 candidate_name은 provenance로만 보존하며 최종 이름을 강제하지 않습니다. "
            "candidate_analysis_region_index는 후보 분석영역과 겹치는 표준 운영권역명의 폐쇄형 어휘 목록이며 기상현상 위치 자체의 근거는 아닙니다. 사용자 문장에서 Step07 구조화 localization 또는 검증된 정량 region_name이 실제 위치를 특정하면 반드시 그 표준 운영권역명을 사용하십시오. 구조화 지역 근거가 있는데도 '후보영역', '운영 예보권역', '일부 권역', '여러 권역', '해당 영역'만으로 위치 설명을 끝내지 마십시오. 강수-환경 결합·중첩·위치관계에 북부·남부·동부·서부·대각·중심 같은 세부 방향을 붙이는 것은 usable Step07 spatial_localization(status=localized)의 해당 relative_sector가 있을 때만 허용됩니다. Step07 finding 자유서술이나 직접강수 객체 위치만으로 환경 결합의 세부 방향을 새로 만들지 마십시오. relative_sector가 있으면 '경북권 남서부'처럼 운영권역명 뒤에 붙이십시오. 반대로 운영권역 단위 위치가 근거로 확정되지 않았으면 후보 bbox와 겹친다는 이유만으로 지명을 만들지 말고, 필요하면 '운영권역 단위 위치는 특정되지 않음'이라고 명시하십시오. "
            "각 check_assessments에는 evidence_status, quantitative_consistency_status, usable_for_synthesis, excluded_from_synthesis_reasons가 포함됩니다. "
            "Step07 v8의 ensemble_consensus는 동일 근거에 대한 3회 독립 판독의 구조화 합의 결과입니다. check_assessments.assessment는 이 합의로 확정된 권위 판정이므로 Step08에서 다시 판정하거나 반대 상태로 변경하지 마십시오. assessment_agreement=unresolved인 Check는 usable_for_synthesis=false이며 근거로 사용하지 마십시오. minority 원문은 Step08 입력에 제공되지 않으며, 합의 강도는 발생확률이나 위험도 등급으로 해석하지 마십시오. "
            "강수 이동 서술은 ensemble_consensus의 precipitation_motion_agreement와 consensus_precipitation_motion_classification을 반드시 따르십시오. coherent_movement가 unanimous인 경우에만 '이동한다/이동하며'처럼 무조건적인 연속 이동을 사실로 서술할 수 있습니다. coherent_movement가 majority이면 '이동 신호가 다수 판독에서 확인되지만' 또는 '이동하는 양상이 있으나 재배치 가능성이 남는다'처럼 이견을 문장 안에 명시하십시오. reorganization/indeterminate/stationary 또는 위치 합의가 불충분하면 하나의 강수계가 연속 이동한다고 단정하지 말고 확대·재배치·분리·재조직 또는 이동 여부 불확실로 서술하십시오. 이 규칙은 final_assessment, operational_brief.key_signal, operational_guidance 및 예보관 답변에 모두 적용합니다. "
            "check_semantics.capability.observable_ids는 해당 Check가 실제 상품에서 직접 확인할 수 있는 기상량의 식별자입니다. 근거를 종합할 때 이 observable identity를 다른 Check의 유사 개념으로 치환하지 마십시오. 특히 total_column_water 중첩을 moisture_flux 중첩으로, wind_vector_difference_850_500을 실제 하층 바람으로 바꾸어 서술하지 마십시오. "
            "unusable_check_ids와 내부 검증 실패 상태는 사용자 자연어에 설명하지 마십시오. 해당 근거를 사용하지 않은 결과 특정 강도 변화·이동·지속성을 판단할 수 없다면 그 기상학적 판단 한계만 표현하십시오. 실제 상세자료가 없거나 불완전한 경우에만 자료 부족을 언급하십시오. "
            "final_assessment는 candidate_core_time_window를 주된 예보기간으로 삼아 그 시간창의 강수 전개부터 시작하십시오. 분석 여유시간의 자료를 쓰는 경우 원래 후보시간 이후 또는 이전의 후속·선행 변화임을 명확히 구분하십시오. 강수영역의 확대와 주요 강수 중심의 이동은 서로 다른 개념이므로 Step07 근거가 구분할 때 자연어에서도 반드시 구분하십시오."
        ),
    }
    return [{"role": "system", "content": prompt_text}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def _call(provider: AIProvider, root: Path, config: dict[str, str], messages: list[dict[str, Any]]) -> dict[str, Any]:
    canonical = load_schema(root, SYNTHESIS_SCHEMA)
    schema = build_openai_transport_schema(canonical) if provider.api_family == "responses" else canonical
    response = provider.complete(messages=messages, model=config["model"], max_tokens=10000, schema=schema, schema_name="AIFSPass2CandidateSynthesis", structured_output_mode="auto", image_detail=config["image_detail"], reasoning_effort=config["reasoning_effort"])
    content = normalize_openai_transport_json(response.content, canonical) if provider.api_family == "responses" else response.content
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise Pass2PipelineError("PROVIDER_RESULT_INVALID", "Step08 provider response is not JSON.") from exc
    if not isinstance(value, dict):
        raise Pass2PipelineError("PROVIDER_RESULT_INVALID", "Step08 provider response root must be an object.")
    validate_schema(root, SYNTHESIS_SCHEMA, value, code="PROVIDER_RESULT_INVALID")
    return value


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


def _step08_regeneration_schema(root: Path, unit: str) -> dict[str, Any]:
    """Build a response schema for one complete Step08 semantic regeneration unit."""
    fields = _STEP08_REGENERATION_UNIT_FIELDS.get(unit)
    if fields is None:
        raise Pass2PipelineError("SYNTHESIS_REPAIR_INVALID", f"Unknown Step08 regeneration unit: {unit}")
    canonical = load_schema(root, SYNTHESIS_SCHEMA)
    properties = canonical.get("properties", {})
    selected: dict[str, Any] = {}
    for field in fields:
        if field not in properties:
            raise Pass2PipelineError(
                "SYNTHESIS_REPAIR_INVALID",
                f"Step08 synthesis schema is missing regeneration field: {field}",
            )
        selected[field] = copy.deepcopy(properties[field])
    return {
        "$schema": canonical.get("$schema", "https://json-schema.org/draft/2020-12/schema"),
        "title": f"AIFSHeavyRain Step08 {unit} atomic regeneration",
        "type": "object",
        "additionalProperties": False,
        "required": list(fields),
        "properties": selected,
        "$defs": copy.deepcopy(canonical.get("$defs", {})),
    }


def _merge_step08_regeneration_unit(
    original: dict[str, Any], regenerated: dict[str, Any], unit: str,
) -> dict[str, Any]:
    """Replace a complete semantic block; no individual Step08 field is patched."""
    fields = _STEP08_REGENERATION_UNIT_FIELDS.get(unit)
    if fields is None or set(regenerated) != set(fields):
        raise Pass2PipelineError(
            "SYNTHESIS_REPAIR_INVALID",
            f"Step08 {unit} regeneration returned an invalid field set.",
        )
    merged = copy.deepcopy(original)
    for field in fields:
        merged[field] = copy.deepcopy(regenerated[field])
    return merged


def _regenerate_step08_semantic_unit(
    provider: AIProvider,
    root: Path,
    config: dict[str, str],
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    prompt_text: str,
    response: dict[str, Any],
    unit: str,
    *,
    reason_code: str,
    reason_message: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Regenerate one complete Step08 semantic unit from the original Step07 evidence.

    The prior unit is explicitly discarded and is not sent as a patch template.
    Cross-dependent fields within the unit are generated together in one model call.
    """
    canonical = _step08_regeneration_schema(root, unit)
    schema = build_openai_transport_schema(canonical) if provider.api_family == "responses" else canonical
    base_messages = _messages(root, group, checks, prompt_text)
    repair_context: dict[str, Any] = {
        "regeneration_mode": "atomic_semantic_unit",
        "regeneration_unit": unit,
        "previous_validation_error": {"code": reason_code, "message": reason_message},
        "instruction": (
            "이전 결과의 일부 필드를 수정하지 마십시오. 해당 의미 단위의 이전 결과는 폐기되었습니다. "
            "원래 Step04 후보·가설·예보관 질문과 Step07 검증 근거를 다시 읽고, 응답 스키마에 포함된 모든 필드를 "
            "서로 일관된 하나의 판단으로 처음부터 다시 생성하십시오. 다른 의미 단위는 반환하지 마십시오."
        ),
    }
    if reason_code in {"ALIGNMENT_NARRATIVE_INVALID", "HYPOTHESIS_EVIDENCE_INVALID"}:
        repair_context["hypothesis_evidence_grounding_contract"] = (
            _hypothesis_evidence_grounding_contract(group, checks)
        )
    if reason_code == "ALIGNMENT_NARRATIVE_INVALID":
        signal = response.get("signal_assessment") if isinstance(response.get("signal_assessment"), dict) else {}
        repair_context["alignment_narrative_contract"] = _localized_alignment_narrative_contract(
            group, checks, signal
        )
        failure_locations = []
        for match in re.finditer(
            r"(?:hypothesis_assessments|forecaster_question_answers)\[(\d+)\]\.[a-zA-Z_][a-zA-Z0-9_]*",
            reason_message,
        ):
            path = match.group(0)
            if path in {item["path"] for item in failure_locations}:
                continue
            container = (
                "hypothesis_assessments" if path.startswith("hypothesis_assessments")
                else "forecaster_question_answers"
            )
            rows = response.get(container)
            index = int(match.group(1))
            row = rows[index] if isinstance(rows, list) and index < len(rows) else None
            id_field = "hypothesis_id" if container == "hypothesis_assessments" else "forecaster_question_id"
            failure_locations.append({
                "path": path,
                "source_id": str(row.get(id_field) or "") if isinstance(row, dict) else "",
            })
        if failure_locations:
            repair_context["alignment_failure_locations"] = failure_locations
        repair_context["alignment_narrative_instruction"] = (
            "alignment_narrative_contract.signal_components에 포함된 요소만 확정적인 결합·중첩·같은 위치 표현에 함께 묶으십시오. "
            "다른 Check에서 부분적으로 확인된 물리량은 동일 결합에 추가하지 말고, 필요하면 '일부 동반', '별도 확인', "
            "'부분적으로 겹침'처럼 별도 관계로 표현하십시오. 구조화 위치 합의에 없는 요소를 제한요인으로 언급해야 하면 "
            "확정 결합 표현과 같은 문장에 두지 말고 문장을 분리하십시오. "
            "강수-환경 공간관계에 운영권역 세부 방향을 붙일 때는 alignment_spatial_grounding_contract.allowed_region_sectors만 사용하십시오. "
            "허용 방향이 없으면 북부·남부·동부·서부·대각·중심 같은 세부 위치를 제거하고 운영권역 단위 위치가 미확정임을 명시하십시오. "
            "Step07 Check 판정과 검증된 signal_assessment 상태·시각·수치·Check의 귀속 관계는 변경하지 마십시오. "
            "가설의 기존 assessment는 STEP08 생성물이며 잘못됐다면 근거에 맞춰 재판정하십시오."
        )
        repair_context["alignment_spatial_grounding_contract"] = _structured_alignment_location_contract(
            root, group, checks
        )
    if reason_code == "CANDIDATE_NAME_REVISION_INVALID":
        context = group.get("candidate_context") if isinstance(group.get("candidate_context"), dict) else {}
        candidate = context.get("candidate") if isinstance(context.get("candidate"), dict) else {}
        repair_context["candidate_name_grounding_contract"] = _candidate_name_grounding_contract(root, group, checks)
        repair_context["candidate_name_revision_instruction"] = (
            "revised_candidate_name은 candidate_name_grounding_contract.allowed_display_names 중 하나를 정확히 선택하십시오. "
            "허용 목록 밖 지명·방향·공간범위를 새로 만들지 마십시오. candidate_name_revision_reason에는 선택 이유를 작성하고, "
            "candidate_name_revision_evidence_check_ids에는 선택한 name_option의 evidence_check_ids를 포함하십시오. "
            "강수객체가 권역 일부·경계·인접 영역에만 걸친 경우 더 넓은 권역명으로 일반화하지 마십시오."
        )
    if reason_code == "OBSERVABLE_IDENTITY_INVALID":
        repair_context["observable_grounding_contract"] = _observable_grounding_contract(group, checks)
        repair_context["observable_grounding_instruction"] = (
            "observable_grounding_contract의 state를 우선 적용하십시오. "
            "state=available_but_unusable인 물리량은 자료가 존재하지만 Step08 결론 근거로 사용할 수 없습니다. "
            "따라서 해당 물리량의 강도·증감·공간구조를 사실로 단정하지 말고, 자료가 없다고도 쓰지 마십시오. "
            "원래 예보관 질문이 여러 물리과정을 함께 묻는 경우 usable 근거로 답할 수 있는 부분과 "
            "available_but_unusable 때문에 판단할 수 없는 부분을 분리하십시오. "
            "예를 들어 연직바람시어가 available_but_unusable이면 '연직바람시어 변화는 현재 사용할 수 있는 "
            "판독 근거만으로 판단하기 어렵습니다'와 같은 판단 한계만 허용됩니다. "
            "available_but_unusable Check는 evidence_check_ids 또는 quantitative_relation_ids에 넣지 마십시오."
        )
    if unit == "interpretation_block":
        if reason_code == "ALIGNMENT_NARRATIVE_INVALID":
            # A localized alignment error in hypothesis/question prose must not
            # turn the other, already-selected signal state into a fresh decision.
            # It is authoritative context, not an output field for this unit.
            repair_context["authoritative_signal_assessment"] = response.get("signal_assessment")
            repair_context["authoritative_operational_guidance"] = response.get("operational_guidance")
            repair_context["interpretation_alignment_constraint"] = (
                "가설 근거와 예보관 질문의 해석을 authoritative_signal_assessment 및 "
                "alignment_narrative_contract와 일치시키십시오. "
                "확정 결합으로 인정된 구성요소를 추가하거나 삭제하지 마십시오. "
                "결합 위치에 포함되지 않은 수분 근거는 확정 결합과 별도 문장으로 분리해 서술하고, "
                "해당 수분 근거 자체의 확인 여부나 사용 가능한 Check ID를 임의로 변경하지 마십시오. "
                "hypothesis_evidence_grounding_contract의 각 가설별 지지·반대 Check를 엄격하게 따르십시오. "
                "지지·반대 모두 비어 있는 가설은 Step04 원래 문구를 확정 사실로 취급하지 말고 "
                "uncertain 또는 insufficient_evidence로 재판정하며, 다른 가설의 Check를 빌리지 마십시오. "
                "해당 가설의 이전 Step08 assessment는 수정할 수 있으며 Step07 판정만 불변입니다."
            )
        repair_context["required_forecaster_quantitative_facts"] = requirements_for_questions(
            _question_rows(str(group["candidate_id"]), group["candidate_context"]["analysis_plan"]),
            _verified_quantitative_relations(checks),
        )
        repair_context["evidence_reference_contract"] = _reference_repair_constraints(group, checks)
        repair_context["hypothesis_evidence_grounding_contract"] = (
            _hypothesis_evidence_grounding_contract(group, checks)
        )
        repair_context["constraints"] = (
            "Step04의 hypothesis statement, supporting_check_ids, opposing_check_ids와 forecaster question 원문은 출력 스키마에 없으며 Python이 원본으로 결합합니다. "
            "각 가설평가와 질문답변 객체는 입력에 있는 hypothesis_id/forecaster_question_id를 그대로 사용하고 assessment/answer, evidence_check_ids, quantitative_relation_ids, "
            "rationale/uncertainties를 포함한 완전한 객체로 다시 생성하십시오. "
            "가설별 evidence_check_ids는 evidence_reference_contract.hypotheses의 해당 hypothesis_id에 있는 "
            "allowed_evidence_check_ids의 부분집합만 사용하십시오. forecaster question의 evidence_check_ids는 "
            "question_allowed_evidence_check_ids의 부분집합만 사용하십시오. quantitative_relation_ids도 각 owner의 allowed 목록에서만 "
            "선택하고, relation_ref를 사용하면 relation_ref_to_check_id의 Check를 같은 owner의 evidence_check_ids에 반드시 포함하십시오. "
            "허용 목록에 없는 다른 Step07 Check가 기상학적으로 관련 있어 보여도 이 의미 단위의 근거로 새로 연결하지 마십시오. "
            "required_forecaster_quantitative_facts가 있는 질문은 명시된 운영권역의 최대 또는 평균 6시간 강수량(관계에 지정된 통계), 두 시각 표시값, 감소·증가 방향을 정확하게 기술하고 관계와 Check를 근거 배열에 포함하십시오. "
            "이 값으로 후보 전체나 다른 지역의 변화는 단정하지 마십시오. "
            "hypothesis_evidence_grounding_contract의 가설별 사용 가능한 지지·반대 Check를 따르십시오. "
            "사용 가능한 지지 근거가 없으면 supported/partially_supported로 판단하지 마십시오. "
            "가설의 지지·반대 근거가 모두 없다면 uncertain 또는 insufficient_evidence로 재판정하고 "
            "근거 배열은 비우며, 확인된 다른 가설의 결합 요소를 이 가설에 덧붙이지 마십시오."
        )
    elif unit == "signal_conclusion_block":
        repair_context["signal_grounding_contract"] = _signal_grounding_contract(group, checks)
        repair_context["constraints"] = (
            "signal_assessment 전체와 그 판단에 종속되는 uncertainties, final_assessment, revision, operational_brief, operational_guidance를 한 번에 다시 생성하십시오. "
            "현재 오류 하나만 고치지 말고 signal_grounding_contract의 direct precipitation required_status/presence anchor, precursor evidence, positive alignment, environment confirmed, environment persistence, physical_synthesis 조건을 모두 동시에 만족시키십시오. direct status가 presence anchor로 통제되는 경우 이동·연속성·조직화·강도 관련 Check로 그 상태를 다시 해석하지 마십시오. "
            "물리적 성립성은 Check 개수로 정하지 말고 Step04 가설의 supporting/opposing 관계와 Step07 판독을 이용해 강수 발생·유지 메커니즘이 일관되는지 평가하십시오. "
            "이 계약은 모든 signal_conclusion_block 재생성에서 동일하게 적용되므로 한 조건을 고치면서 다른 조건을 다시 위반하지 마십시오. "
            "Step07 usable evidence와 Step09 publication contract에 맞지 않는 confirmed, positive alignment, surface 판단을 선언하지 마십시오."
        )
    elif unit == "operational_guidance_block":
        # The previous guidance object is discarded.  All other validated
        # decisions are immutable source context, not fields to be regenerated.
        repair_context["authoritative_signal_assessment"] = response.get("signal_assessment")
        repair_context["authoritative_final_assessment"] = response.get("final_assessment")
        repair_context["authoritative_operational_brief"] = response.get("operational_brief")
        repair_context["allowed_evidence_check_ids"] = sorted(_usable_check_ids(checks))
        repair_context["constraints"] = (
            "응답에는 operational_guidance 객체 하나만 포함하십시오. 이 객체의 네 필드(current_action, next_check, "
            "interpretation_limit, evidence_check_ids)를 완전히 새로 작성하되 현재 검증된 기상학적 결론, "
            "강수·환경 상태, 근거, 종합판단, 운영요약을 변경하지 마십시오. "
            "current_action은 반드시 '현재 판단은' 또는 '모델 강수는'처럼 현재의 해석을 나타내는 "
            "한국어 평서문으로 시작하십시오. F048·F090 등 예측시간, 시간 범위, 위치, 증거를 첫머리에 두지 마십시오. "
            "시각과 근거는 현재 판단을 먼저 제시한 이후에 덧붙이거나 next_check에 기술하십시오. "
            "모델 강수 confirmed와 환경 partial/not_confirmed/insufficient_evidence가 결합된 사례라면 "
            "current_action에 정확히 '모델 강수'와 '강수 발달 환경'을 모두 쓰고 서로 구분하십시오. "
            "예보관 지시형 문장, 내부 식별자, 특보·위험도·우선순위 지시는 쓰지 마십시오. "
            "next_check에는 실제 입력의 예측시각(FNNN), 위치, 확인할 기상요소, 변화 판독, "
            "현재 해석을 유지할 조건과 재검토할 조건을 모두 포함하십시오. "
            "interpretation_limit에는 확정할 수 없는 핵심 기상학적 사항만 기술하십시오. "
            "evidence_check_ids에는 allowed_evidence_check_ids에 속하면서 실제 세 문장의 "
            "판단에 사용한 Check만 중복 없이 포함하십시오. "
            "특히 이전 오류가 문장 시작 형식 문제라면 이미 검증된 다른 내용을 다시 생성하지 마십시오."
        )
    elif unit == "narrative_prose_block":
        repair_context["authoritative_signal_assessment"] = response.get("signal_assessment")
        repair_context["authoritative_hypothesis_assessments"] = response.get("hypothesis_assessments")
        repair_context["authoritative_forecaster_question_answers"] = response.get("forecaster_question_answers")
        repair_context["authoritative_operational_guidance"] = response.get("operational_guidance")
        repair_context["constraints"] = (
            "응답에는 uncertainties, final_assessment, operational_brief만 반환하십시오. "
            "이미 검증된 signal_assessment와 operational_guidance는 변경하지 마십시오. "
            "기존의 강수·환경 판독 상태, 예보시간, 기상량의 관측가능성, 구조화 위치 합의, "
            "유효 Check와 기존 운영판단을 그대로 준수하며 종합판단 및 운영요약의 문장만 독립적으로 다시 생성하십시오. "
            "final_assessment는 후보 핵심시간의 강수 전개를 중심으로 3~5문장, "
            "operational_brief의 각 문구는 120자 이내로 작성하십시오. "
            "기존 운영판단의 current_action 및 next_check와 충돌하는 새로운 기상학적 판단을 만들지 마십시오."
        )
    elif unit == "narrative_conclusion_block":
        repair_context["authoritative_signal_assessment"] = response.get("signal_assessment")
        repair_context["authoritative_hypothesis_assessments"] = response.get("hypothesis_assessments")
        repair_context["authoritative_forecaster_question_answers"] = response.get("forecaster_question_answers")
        repair_context["constraints"] = (
            "현재 signal_assessment는 이미 검증된 기준 판단입니다. 이를 변경하거나 재해석하지 말고, uncertainties, final_assessment, "
            "operational_brief, operational_guidance 전체를 같은 기준 판단에 맞춰 함께 다시 작성하십시오. operational_brief는 화면 상단의 짧은 운영요약이며 각 문구를 120자 이내로 유지하십시오. final_assessment는 후보 핵심 시간창의 강수 전개를 중심으로 3~5문장의 예보 분석 초안 수준으로 다시 작성하고, "
            "지지·제한 환경과 최종 해석을 하나의 흐름으로 연결하십시오. current_action은 화면에 표시되지 않는 내부 판단 앵커이므로 final_assessment의 상세내용을 반복하지 마십시오."
        )
        signal = response.get("signal_assessment") if isinstance(response.get("signal_assessment"), dict) else {}
        if (
            signal.get("direct_precipitation_status") == "confirmed"
            and signal.get("environment_support_status") in {"partial", "not_confirmed", "insufficient_evidence"}
        ):
            repair_context["operational_guidance_current_action_contract"] = (
                "current_action은 지시형이 아닌 평서문으로, 정확한 용어 '모델 강수'와 '강수 발달 환경'을 모두 포함해 "
                "확인된 모델 강수 예측과 제한된 환경 지지를 분리하여 서술해야 합니다."
            )
    messages = [
        *base_messages,
        {"role": "user", "content": json.dumps(repair_context, ensure_ascii=False)},
    ]
    provider_response = provider.complete(
        messages=messages,
        model=config["model"],
        max_tokens=10000,
        schema=schema,
        schema_name=f"AIFSPass2{unit.title().replace('_', '')}Regeneration",
        structured_output_mode="auto",
        image_detail=config["image_detail"],
        reasoning_effort=config.get("repair_reasoning_effort", config["reasoning_effort"]),
    )
    content = (
        normalize_openai_transport_json(provider_response.content, canonical)
        if provider.api_family == "responses"
        else provider_response.content
    )
    try:
        regenerated = json.loads(content)
    except json.JSONDecodeError as exc:
        raise Pass2PipelineError(
            "SYNTHESIS_REPAIR_INVALID", f"Step08 {unit} regeneration is not JSON."
        ) from exc
    if not isinstance(regenerated, dict):
        raise Pass2PipelineError(
            "SYNTHESIS_REPAIR_INVALID", f"Step08 {unit} regeneration root must be an object."
        )
    merged = _merge_step08_regeneration_unit(response, regenerated, unit)
    merged = _attach_immutable_step08_context(group, merged)
    return merged, provider_response.usage


def _step08_regeneration_units_for_error(code: str) -> list[str]:
    if code == "HYPOTHESIS_EVIDENCE_INVALID":
        return ["interpretation_block"]
    if code == "INTERPRETATION_EVIDENCE_CHECK_ID_INVALID":
        # Evidence ownership belongs to the complete hypothesis/question
        # interpretation objects.  Regenerate that semantic block using the
        # explicit Step04-to-Step07 allowed-reference contract; never relax
        # the validator or silently attach unrelated usable Checks.
        return ["interpretation_block"]
    if code == "FORECASTER_QUANTITATIVE_ANSWER_INVALID":
        return ["interpretation_block"]
    if code == "QUANTITATIVE_RELATION_REF_INVALID":
        # Quantitative references belong to the complete hypothesis/question
        # interpretation objects.  Refresh the dependent conclusion prose as a
        # separate complete block afterwards.
        return ["interpretation_block", "narrative_conclusion_block"]
    if code in {
        "PRECURSOR_EVIDENCE_INVALID",
        "DIRECT_PRECIPITATION_EVIDENCE_INVALID",
        "ALIGNMENT_EVIDENCE_INVALID",
        "OPERATIONAL_SURFACE_DECISION_INVALID",
        "PHYSICAL_DECISION_INVALID",
        "ENVIRONMENT_PERSISTENCE_INVALID",
        "CANDIDATE_NAME_REVISION_INVALID",
    }:
        return ["signal_conclusion_block"]
    if code == "OPERATIONAL_GUIDANCE_INVALID":
        return ["operational_guidance_block"]
    if code in {"FINAL_ASSESSMENT_INVALID", "OPERATIONAL_BRIEF_INVALID"}:
        return ["narrative_prose_block"]
    raise Pass2PipelineError(
        "SYNTHESIS_REPAIR_INVALID", f"Unsupported Step08 semantic repair code: {code}"
    )


def _step08_regeneration_units_for_language_errors(language_errors: list[str]) -> list[str]:
    """Route each faulty prose path to its smallest complete semantic owner.

    An observable or spatial repair to a final assessment must not regenerate
    already valid operational guidance.  Guidance-owned errors get a dedicated
    guidance repair; physical signal errors still regenerate dependent guidance.
    """
    needs_interpretation = False
    needs_signal = False
    needs_narrative = False
    needs_guidance = False
    for error in language_errors:
        path = str(error).split(":", 1)[0].strip()
        if path.startswith("hypothesis_assessments[") or path.startswith("forecaster_question_answers["):
            needs_interpretation = True
        elif path.startswith("signal_assessment."):
            needs_signal = True
        elif path.startswith("operational_guidance."):
            needs_guidance = True
        else:
            needs_narrative = True
    units: list[str] = []
    if needs_interpretation:
        units.append("interpretation_block")
        # A changed interpretation may affect the candidate summary, but it
        # does not invalidate an independently verified guidance object.
        needs_narrative = True
    if needs_signal:
        units.append("signal_conclusion_block")
        # The signal block owns conclusions and guidance together.
        needs_narrative = False
        needs_guidance = False
    if needs_narrative:
        units.append("narrative_prose_block")
    if needs_guidance:
        units.append("operational_guidance_block")
    return units or ["narrative_prose_block"]

def _step08_regeneration_units_for_alignment_errors(errors: list[str]) -> list[str]:
    """Regenerate only semantic owners that *actually* violate localization.

    Unlike general narrative-language repairs, an interpretation rewrite does
    not automatically invalidate a separately validated final assessment.  The
    main loop always runs *all* synthesis and narrative validators again after
    each regeneration; if a dependent conclusion becomes invalid, its own error
    path triggers a narrative_prose_block rewrite on the following iteration.
    This preserves strict spatial grounding without cascading model rewrites.
    """
    units: list[str] = []
    for error in errors:
        path = str(error).split(":", 1)[0].strip()
        if path.startswith(("hypothesis_assessments[", "forecaster_question_answers[")):
            unit = "interpretation_block"
        elif path.startswith("signal_assessment."):
            unit = "signal_conclusion_block"
        elif path.startswith("operational_guidance."):
            unit = "operational_guidance_block"
        else:
            # Other prose fields belong to the complete standalone narrative
            # block.  Do not accidentally mutate the signal/guidance state.
            unit = "narrative_prose_block"
        if unit not in units:
            units.append(unit)
    # signal_conclusion_block regenerates all dependent narrative and guidance
    # fields itself; overlapping rewrites would contradict the atomic contract.
    if "signal_conclusion_block" in units:
        return ["signal_conclusion_block", *(
            ["interpretation_block"] if "interpretation_block" in units else []
        )]
    # An interpretation rewrite must be prior to a separately failing summary.
    return [unit for unit in (
        "interpretation_block", "narrative_prose_block", "operational_guidance_block",
    ) if unit in units] or ["narrative_prose_block"]


# Conservative allowlist for deterministic reconciliation of *new* responses.
# If the question includes a non-quantitative physical/spatial interpretation,
# a canonical numeric sentence would silently discard a legitimate AI judgment.
_MIXED_QUANTITATIVE_QUESTION_TERMS = re.compile(
    r"수렴|상승|수증기|수분|바람|이동|강수계|강수대|연속|연결|"
    r"공간|분포|영역|위치|경로|발달|조직|재편|영향|원인|동반|결합|"
    r"지속|유지|중첩|겹치|기압|전선|시어|기류"
)


def _reconcile_new_quantitative_answers(
    checks: dict[str, dict[str, Any]],
    raw_value: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Reconcile only stand-alone, unambiguous measured comparisons.

    The existing cached migration intentionally supports legacy outputs. This
    narrower entry point is for newly generated output: other meteorological
    reasoning and the existing semantic-repair loop remain authoritative.
    """
    answers = raw_value.get("forecaster_question_answers")
    if not isinstance(answers, list):
        return raw_value, []
    verified = _verified_quantitative_relations(checks)
    updates: dict[int, dict[str, Any]] = {}
    recovered_ids: list[str] = []
    for index, row in enumerate(answers):
        if not isinstance(row, dict):
            continue
        question = str(row.get("question") or "")
        if _MIXED_QUANTITATIVE_QUESTION_TERMS.search(question):
            continue
        if len(set(re.findall(r"F\d{3}", question))) != 2:
            continue
        # One comparison only, never merge incomparable regions/statistics.
        if len(required_facts(question, verified)) != 1:
            continue
        if not question_answer_errors([row], verified):
            continue
        fixed_rows, fixed_ids = reconcile_legacy_question_answers([row], verified)
        if fixed_ids:
            updates[index] = fixed_rows[0]
            recovered_ids.extend(fixed_ids)
    if not updates:
        return raw_value, []
    fixed = copy.deepcopy(raw_value)
    for index, row in updates.items():
        fixed["forecaster_question_answers"][index] = row
    return fixed, recovered_ids


def _autofix_relation_evidence_refs(
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    response: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Deterministically add an implied evidence check for an already-used relation.

    A relation_ref already declares that its owning Step07 check was used.  When
    that check is also allowed for the same Step08 owner, adding the missing
    check_id is bookkeeping normalization rather than a new meteorological
    judgment.  Relations from disallowed checks are never auto-repaired here.
    """
    verified_relations = _verified_quantitative_relations(checks)
    usable_checks = _usable_check_ids(checks)
    repaired = copy.deepcopy(response)
    fixes: list[str] = []

    plan = group["candidate_context"]["analysis_plan"]
    hypotheses = {
        str(item.get("hypothesis_id")): item
        for item in plan.get("hypotheses", [])
        if isinstance(item, dict) and item.get("hypothesis_id")
    }
    for item in repaired.get("hypothesis_assessments", []):
        if not isinstance(item, dict):
            continue
        hypothesis = hypotheses.get(str(item.get("hypothesis_id")))
        if hypothesis is None:
            continue
        allowed_checks = (
            set(str(value) for value in hypothesis.get("supporting_check_ids", []))
            | set(str(value) for value in hypothesis.get("opposing_check_ids", []))
        ) & usable_checks
        evidence_ids = list(item.get("evidence_check_ids", []))
        evidence_set = set(str(value) for value in evidence_ids)
        for relation_ref in item.get("quantitative_relation_ids", []):
            relation = verified_relations.get(str(relation_ref))
            if relation is None:
                continue
            check_id = str(relation.get("check_id"))
            if check_id in allowed_checks and check_id not in evidence_set:
                evidence_ids.append(check_id)
                evidence_set.add(check_id)
                fixes.append(f"{item.get('hypothesis_id')}:{relation_ref}->{check_id}")
        item["evidence_check_ids"] = evidence_ids

    for item in repaired.get("forecaster_question_answers", []):
        if not isinstance(item, dict):
            continue
        evidence_ids = list(item.get("evidence_check_ids", []))
        evidence_set = set(str(value) for value in evidence_ids)
        for relation_ref in item.get("quantitative_relation_ids", []):
            relation = verified_relations.get(str(relation_ref))
            if relation is None:
                continue
            check_id = str(relation.get("check_id"))
            if check_id in usable_checks and check_id not in evidence_set:
                evidence_ids.append(check_id)
                evidence_set.add(check_id)
                fixes.append(f"{item.get('forecaster_question_id')}:{relation_ref}->{check_id}")
        item["evidence_check_ids"] = evidence_ids

    if not fixes:
        return None, []
    # This helper owns only deterministic reference bookkeeping.  Do not run the
    # full candidate validator here: an unrelated signal/alignment error must not
    # suppress a safe QREL evidence-id normalization.  The caller immediately
    # re-validates the whole candidate and handles any remaining error by its own
    # bounded repair path.
    return repaired, fixes


def _attach_immutable_step08_context(
    group: dict[str, Any],
    response: dict[str, Any],
) -> dict[str, Any]:
    """Attach Step04-owned immutable text/relations after the LLM response.

    The Step08 provider no longer echoes hypothesis statements, supporting/opposing
    relation sets, or forecaster-question text.  It returns only IDs and fields it
    actually owns.  Python joins the immutable Step04 source objects back by ID.
    This removes an avoidable source of HYPOTHESIS_RELATION_MISMATCH without
    introducing any deterministic meteorological judgment.
    """
    candidate_id = str(group.get("candidate_id") or "")
    if response.get("candidate_id") != candidate_id:
        raise Pass2PipelineError(
            "CANDIDATE_ID_MISMATCH",
            f"Step08 result candidate_id differs: {response.get('candidate_id')!r}",
        )
    plan = group.get("candidate_context", {}).get("analysis_plan", {})
    source_hypotheses = [
        item for item in plan.get("hypotheses", [])
        if isinstance(item, dict) and item.get("hypothesis_id")
    ]
    rows = response.get("hypothesis_assessments")
    if not isinstance(rows, list):
        raise Pass2PipelineError("HYPOTHESIS_RESULT_INVALID", f"{candidate_id} Step08 hypothesis_assessments must be an array.")
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise Pass2PipelineError("HYPOTHESIS_RESULT_INVALID", f"{candidate_id} has a non-object hypothesis assessment.")
        hypothesis_id = str(row.get("hypothesis_id") or "")
        if not hypothesis_id or hypothesis_id in by_id:
            raise Pass2PipelineError("HYPOTHESIS_RESULT_INVALID", f"{candidate_id} has invalid or duplicate hypothesis_id={hypothesis_id!r}.")
        by_id[hypothesis_id] = row
    expected_ids = [str(item["hypothesis_id"]) for item in source_hypotheses]
    if set(by_id) != set(expected_ids):
        raise Pass2PipelineError("HYPOTHESIS_RESULT_INVALID", f"{candidate_id} Step08 must cover exactly the Step04 hypotheses.")

    enriched = copy.deepcopy(response)
    enriched_hypotheses: list[dict[str, Any]] = []
    for source in source_hypotheses:
        hypothesis_id = str(source["hypothesis_id"])
        row = copy.deepcopy(by_id[hypothesis_id])
        row["statement"] = copy.deepcopy(source.get("statement"))
        row["supporting_check_ids"] = copy.deepcopy(source.get("supporting_check_ids", []))
        row["opposing_check_ids"] = copy.deepcopy(source.get("opposing_check_ids", []))
        enriched_hypotheses.append(row)
    enriched["hypothesis_assessments"] = enriched_hypotheses

    expected_questions = _question_rows(candidate_id, plan)
    answers = response.get("forecaster_question_answers")
    if not isinstance(answers, list):
        raise Pass2PipelineError("FORECASTER_QUESTION_RESULT_INVALID", f"{candidate_id} forecaster_question_answers must be an array.")
    answer_by_id: dict[str, dict[str, Any]] = {}
    for answer in answers:
        if not isinstance(answer, dict):
            raise Pass2PipelineError("FORECASTER_QUESTION_RESULT_INVALID", f"{candidate_id} has a non-object forecaster question answer.")
        question_id = str(answer.get("forecaster_question_id") or "")
        if not question_id or question_id in answer_by_id:
            raise Pass2PipelineError("FORECASTER_QUESTION_RESULT_INVALID", f"{candidate_id} has invalid or duplicate forecaster_question_id={question_id!r}.")
        answer_by_id[question_id] = answer
    expected_question_ids = [row["forecaster_question_id"] for row in expected_questions]
    if set(answer_by_id) != set(expected_question_ids):
        raise Pass2PipelineError("FORECASTER_QUESTION_RESULT_INVALID", f"{candidate_id} must answer every Step04 forecaster question exactly once.")
    enriched_answers: list[dict[str, Any]] = []
    for source in expected_questions:
        answer = copy.deepcopy(answer_by_id[source["forecaster_question_id"]])
        answer["question"] = source["question"]
        enriched_answers.append(answer)
    enriched["forecaster_question_answers"] = enriched_answers
    return enriched


def _public_check_semantics(specification: dict[str, Any]) -> dict[str, Any]:
    """Expose lossless Check-local intent and observable identity to Step08."""
    intent = specification.get("intent_contract")
    intent = intent if isinstance(intent, dict) else {}
    capability = specification.get("capability_profile")
    capability = capability if isinstance(capability, dict) else {}
    return {
        "check_id": specification.get("check_id"),
        "variable_role": specification.get("variable_role"),
        "diagnostic_id": specification.get("diagnostic_id"),
        "product_id": specification.get("product_id"),
        "check_scope": specification.get("check_scope"),
        "required_signal_components": specification.get("required_signal_components", []),
        "expected_signal": specification.get("expected_signal"),
        "decision_relevance": specification.get("decision_relevance"),
        "verification_mode": intent.get("verification_mode"),
        "domain_scope": intent.get("domain_scope"),
        "spatial_relation_scope": intent.get("spatial_relation_scope", "not_applicable"),
        "phase_ids": intent.get("phase_ids", []),
        "purpose": intent.get("purpose"),
        "direct_precipitation_anchor": intent.get("direct_precipitation_anchor", False),
        "source_intent_hash": specification.get("intent_hash"),
        "capability": {
            "execution_product_id": capability.get("execution_product_id"),
            "signal_components": capability.get("signal_components", []),
            "observable_ids": capability.get("observable_ids", []),
        },
    }


def _hypothesis_evidence_grounding_contract(
    group: dict[str, Any], checks: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Describe Step04-owned support/opposition using only usable Step07 Checks.

    This contract does NOT reinterpret meteorology or change any Step07 result.
    In particular, a Step04 four-field hypothesis whose only Check has
    unresolved Step07 ensemble consensus cannot borrow the localized
    three-field finding from a different hypothesis.
    """
    plan = group["candidate_context"]["analysis_plan"]
    usable = _usable_check_ids(checks)
    contract: dict[str, dict[str, Any]] = {}
    for source in plan.get("hypotheses", []):
        if not isinstance(source, dict) or not source.get("hypothesis_id"):
            continue
        hypothesis_id = str(source["hypothesis_id"])
        supports = {str(item) for item in source.get("supporting_check_ids", [])}
        opposes = {str(item) for item in source.get("opposing_check_ids", [])}
        all_ids = supports | opposes
        usable_support = sorted(supports & usable)
        usable_opposing = sorted(opposes & usable)
        exclusions = {
            check_id: {
                "assessment": str(checks.get(check_id, {}).get("assessment") or "unknown"),
                "reason": "not_usable_for_synthesis",
            }
            for check_id in sorted(all_ids - usable)
        }
        no_usable = not (usable_support or usable_opposing)
        contract[hypothesis_id] = {
            "usable_supporting_check_ids": usable_support,
            "usable_opposing_check_ids": usable_opposing,
            "excluded_check_ids": exclusions,
            "allowed_evidence_check_ids": sorted(all_ids & usable),
            "supported_assessment_allowed": bool(usable_support),
            "no_usable_hypothesis_evidence": no_usable,
            "allowed_assessments_if_no_usable_evidence": [
                "uncertain", "insufficient_evidence",
            ],
            "rule": (
                "Step04 가설 문구는 검증 질문이지 확인된 사실이 아닙니다. "
                "유효한 지지 Check가 없으면 supported/partially_supported를 쓰지 마십시오. "
                "지지·반대 Check 모두 사용할 수 없다면 uncertain 또는 insufficient_evidence로 판단하고 "
                "evidence_check_ids/quantitative_relation_ids는 빈 배열로 두십시오. "
                "원래 가설의 물리량을 다른 가설의 Check에 결합하여 확정하지 마십시오. "
                "분석 근거의 사용 불가 상태는 사용자 설명에 그대로 노출하지 말고 "
                "기상학적 판단 한계로 표현하십시오."
            ),
        }
    return contract


def _step08_hypothesis_evidence_errors(
    group: dict[str, Any], checks: dict[str, dict[str, Any]], value: dict[str, Any],
) -> list[str]:
    """Require an actual usable Step07 supporting reference for a positive claim."""
    contracts = _hypothesis_evidence_grounding_contract(group, checks)
    problems = []
    for index, row in enumerate(value.get("hypothesis_assessments", [])):
        if not isinstance(row, dict):
            continue
        hypothesis_id = str(row.get("hypothesis_id") or "")
        contract = contracts.get(hypothesis_id)
        if contract is None:
            continue  # regular synthesis schema / identity validator owns this case
        status = row.get("assessment")
        path = f"hypothesis_assessments[{index}].assessment"
        if contract["no_usable_hypothesis_evidence"] and status not in {
            "uncertain", "insufficient_evidence",
        }:
            problems.append(
                f"{path}: {hypothesis_id} has no usable supporting or opposing Step07 Checks; "
                f"cannot conclude {status!r}. Reassess as uncertain or insufficient_evidence "
                "without borrowing a Check from a different hypothesis."
            )
        elif status in {"supported", "partially_supported"} and not (
            set(row.get("evidence_check_ids") or []) &
            set(contract["usable_supporting_check_ids"])
        ):
            problems.append(
                f"{path}: {hypothesis_id} has no cited usable supporting Step07 Check; "
                f"cannot conclude {status!r}."
            )
    return problems


def _reference_repair_constraints(group: dict[str, Any], checks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build deterministic relation/check constraints for one Step08 repair call."""
    usable_checks = _usable_check_ids(checks)
    verified_relations = _verified_quantitative_relations(checks)
    plan = group["candidate_context"]["analysis_plan"]
    hypothesis_constraints: dict[str, dict[str, list[str]]] = {}
    for hypothesis in plan.get("hypotheses", []):
        if not isinstance(hypothesis, dict) or not hypothesis.get("hypothesis_id"):
            continue
        hypothesis_id = str(hypothesis["hypothesis_id"])
        allowed_checks = (
            set(str(item) for item in hypothesis.get("supporting_check_ids", []))
            | set(str(item) for item in hypothesis.get("opposing_check_ids", []))
        ) & usable_checks
        allowed_relations = sorted(
            relation_ref
            for relation_ref, relation in verified_relations.items()
            if str(relation.get("check_id")) in allowed_checks
        )
        hypothesis_constraints[hypothesis_id] = {
            "allowed_evidence_check_ids": sorted(allowed_checks),
            "allowed_quantitative_relation_ids": allowed_relations,
        }
    return {
        "relation_ref_to_check_id": {
            relation_ref: str(relation.get("check_id"))
            for relation_ref, relation in sorted(verified_relations.items())
        },
        "hypotheses": hypothesis_constraints,
        "question_allowed_evidence_check_ids": sorted(usable_checks),
        "question_allowed_quantitative_relation_ids": sorted(verified_relations),
    }


def _normalize_signal_evidence_check_ids(
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    response: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Add only already-declared signal anchors to the compatibility ID list.

    ``validated_precursor_evidence`` and positive alignment already declare the
    provider's meteorological judgment.  Repeating their usable Check IDs in
    ``signal_evidence_check_ids`` is deterministic reference bookkeeping, not a
    new weather inference.  Invalid or absent anchors remain for the integrity
    validator to reject; this helper never creates categories, alignment, or a
    confirmed status.
    """
    signal = response.get("signal_assessment")
    if not isinstance(signal, dict):
        return response, []
    direct_status = signal.get("direct_precipitation_status")
    environment_status = signal.get("environment_support_status")
    if direct_status not in CANDIDATE_SIGNAL_STATUSES or environment_status not in CANDIDATE_SIGNAL_STATUSES:
        return response, []
    signal_ids = signal.get("signal_evidence_check_ids")
    if not isinstance(signal_ids, list) or len(set(signal_ids)) != len(signal_ids):
        return response, []
    _categories, category_anchors, errors = validate_validated_precursor_evidence(
        signal.get("validated_precursor_evidence"),
        check_specifications=_check_specifications(group),
        check_results=checks,
    )
    if errors:
        return response, []
    additions: set[str] = set()
    direct_status_contract = _direct_precipitation_status_contract(group, checks)
    if (
        direct_status_contract["status_controlled_by_presence_anchor"]
        and direct_status_contract["anchor_evidence_usable"]
        and direct_status_contract["required_status"] != "insufficient_evidence"
    ):
        additions.update(direct_status_contract["presence_anchor_check_ids"])
    if direct_status == "confirmed":
        additions.update(category_anchors.get("direct_precipitation", set()))
    if environment_status == "confirmed":
        additions.update(
            check_id
            for category, anchors in category_anchors.items()
            if category != "direct_precipitation"
            for check_id in anchors
        )
    # Positive alignment is an already-declared provider judgment.  Its usable
    # Check IDs are deterministic compatibility evidence regardless of whether
    # the overall environment status is confirmed or partial.
    if signal.get("validated_spatiotemporal_alignment"):
        alignment_ids = signal.get("spatiotemporal_alignment_evidence_check_ids")
        if isinstance(alignment_ids, list):
            additions.update(
                str(check_id)
                for check_id in alignment_ids
                if str(check_id) in _usable_check_ids(checks)
            )
    # Persistence evidence is also provider-declared and separately validated.
    # Mirroring those usable IDs into the compatibility signal list keeps them
    # available to Step10 publication without creating any new meteorological
    # inference or changing the authored persistence state.
    persistence_ids = signal.get("environment_persistence_evidence_check_ids")
    if isinstance(persistence_ids, list):
        additions.update(
            str(check_id)
            for check_id in persistence_ids
            if str(check_id) in _usable_check_ids(checks)
        )
    normalized_ids = list(signal_ids)
    applied: list[str] = []
    for check_id in sorted(additions):
        if check_id not in normalized_ids:
            normalized_ids.append(check_id)
            applied.append(check_id)
    if not applied:
        return response, []
    normalized = copy.deepcopy(response)
    normalized["signal_assessment"]["signal_evidence_check_ids"] = normalized_ids
    return normalized, applied


def _is_publication_eligible_alignment_check(
    check_id: str,
    *,
    specifications: dict[str, dict[str, Any]],
    checks: dict[str, dict[str, Any]],
) -> bool:
    """Match the Step09 positive-alignment publication contract exactly."""
    specification = specifications.get(check_id)
    result = checks.get(check_id)
    if not (
        isinstance(specification, dict)
        and specification.get("check_scope") == "multivariable_alignment"
        and check_is_positive_spatiotemporal_alignment(specification)
        and isinstance(result, dict)
        and result.get("assessment") == "observed"
        and result.get("evidence_status") == "complete"
        and bool(result.get("evidence_complete"))
        and result.get("usable_for_synthesis") is not False
    ):
        return False

    # Any positive precip/environment alignment used as the public
    # ``시공간 일치`` state must also say WHERE that overlap occurred.  This
    # restores the historical localization contract for four-field checks and
    # applies the same rule to reduced three-/two-field alignment checks.
    if alignment_requires_spatial_localization(specification):
        localization = result.get("spatial_localization")
        if not isinstance(localization, dict):
            return False
        hour = localization.get("forecast_hour")
        regions = localization.get("regions")
        if (
            localization.get("status") != "localized"
            or not isinstance(hour, int)
            or isinstance(hour, bool)
            or not isinstance(regions, list)
            or not regions
        ):
            return False
    return True


def _usable_alignment_check_ids(group: dict[str, Any], checks: dict[str, dict[str, Any]]) -> list[str]:
    """Return only alignment Checks that can survive the Step09 publication gate."""
    specifications = _check_specifications(group)
    return sorted(
        check_id
        for check_id in specifications
        if _is_publication_eligible_alignment_check(
            check_id,
            specifications=specifications,
            checks=checks,
        )
    )


def _observed_direct_precipitation_anchor_hours(
    group: dict[str, Any], checks: dict[str, dict[str, Any]],
) -> set[int]:
    """Return image-evidenced hours from a confirmed SYSTEM presence anchor."""
    hours: set[int] = set()
    for check_id in _confirmed_direct_precipitation_anchor_ids(group, checks):
        result = checks.get(check_id)
        if not isinstance(result, dict):
            continue
        for finding in result.get("findings", []):
            if not isinstance(finding, dict):
                continue
            for ref in finding.get("evidence_refs", []):
                if not isinstance(ref, dict):
                    continue
                hour = ref.get("forecast_hour")
                if (
                    ref.get("evidence_type") == "image"
                    and ref.get("physical_diagnostic") == "QL_PRECIP_6H"
                    and isinstance(hour, int)
                    and not isinstance(hour, bool)
                ):
                    hours.add(int(hour))
    return hours


def _derive_environment_localization(
    group: dict[str, Any], checks: dict[str, dict[str, Any]], signal: dict[str, Any],
) -> dict[str, Any] | None:
    """Select one already-validated Step07 localization for public display.

    This is deterministic reference bookkeeping.  It does not infer a new
    overlap, region, or meteorological score.
    """
    if signal.get("direct_precipitation_status") != "confirmed":
        return None
    if signal.get("environment_support_status") not in {"confirmed", "partial"}:
        return None
    if signal.get("validated_spatiotemporal_alignment") is not True:
        return None
    alignment_ids = signal.get("spatiotemporal_alignment_evidence_check_ids")
    if not isinstance(alignment_ids, list) or not alignment_ids:
        return None
    anchor_hours = _observed_direct_precipitation_anchor_hours(group, checks)
    if not anchor_hours:
        return None
    candidate = group.get("candidate_context", {}).get("candidate", {})
    peak_hours = {
        int(value) for value in candidate.get("peak_forecast_hours", [])
        if isinstance(value, int) and not isinstance(value, bool)
    } if isinstance(candidate, dict) else set()
    candidates: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
    confidence_rank = {"high": 0, "medium": 1}
    specifications = _check_specifications(group)
    for order, raw_check_id in enumerate(alignment_ids):
        check_id = str(raw_check_id)
        result = checks.get(check_id)
        if not isinstance(result, dict):
            continue
        if not (
            result.get("assessment") == "observed"
            and result.get("scope_coverage") == "complete_expected_scope"
            and result.get("evidence_status") == "complete"
            and result.get("evidence_complete") is True
            and result.get("usable_for_synthesis") is not False
            and not result.get("contradictory_findings")
            and not result.get("uncertainties")
        ):
            continue
        localization = result.get("spatial_localization")
        if not isinstance(localization, dict) or localization.get("status") != "localized":
            continue
        confidence = localization.get("confidence")
        hour = localization.get("forecast_hour")
        regions = localization.get("regions")
        if confidence not in confidence_rank or not isinstance(hour, int) or isinstance(hour, bool) or not isinstance(regions, list) or not regions:
            continue
        if hour not in anchor_hours:
            continue
        specification = specifications.get(check_id, {})
        signal_components = [
            component
            for component in (
                "direct_precipitation",
                "moisture_supply",
                "low_level_forcing",
                "mid_upper_forcing",
            )
            if component in {
                str(value)
                for value in specification.get("required_signal_components", [])
                if isinstance(value, str) and value
            }
        ]
        if len(signal_components) < 2 or "direct_precipitation" not in signal_components:
            continue
        public_value = {
            "status": "localized",
            "forecast_hour": int(hour),
            "confidence": str(confidence),
            "evidence_check_id": check_id,
            "signal_components": signal_components,
            "regions": copy.deepcopy(regions),
            "source": "step07_validated_spatial_localization",
        }
        rank = (confidence_rank[str(confidence)], 0 if hour in peak_hours else 1, order, int(hour))
        candidates.append((rank, public_value))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]




def _relative_sector_from_centroid(centroid: Any, region_bbox: Any) -> str:
    if not isinstance(centroid, dict) or not isinstance(region_bbox, dict):
        return "unspecified"
    try:
        lon = float(centroid["longitude"])
        lat = float(centroid["latitude"])
        west = float(region_bbox["west"])
        east = float(region_bbox["east"])
        south = float(region_bbox["south"])
        north = float(region_bbox["north"])
    except (KeyError, TypeError, ValueError):
        return "unspecified"
    if not (west < east and south < north):
        return "unspecified"
    x = (lon - west) / (east - west)
    y = (lat - south) / (north - south)
    horizontal = "west" if x < 1.0 / 3.0 else ("east" if x > 2.0 / 3.0 else "")
    vertical = "south" if y < 1.0 / 3.0 else ("north" if y > 2.0 / 3.0 else "")
    if vertical and horizontal:
        return f"{vertical}{horizontal}"
    if vertical:
        return vertical
    if horizontal:
        return horizontal
    return "central"


def _precipitation_object_region_identity(overlap: dict[str, Any], obj: dict[str, Any]) -> dict[str, Any] | None:
    """Summarize one object/region overlap without letting one max grid define scope.

    ``coverage_class`` is only a deterministic safety tier for generating the
    *allowed* naming vocabulary. GPT-6 Sol still chooses the final display name.
    The safety tier uses the primary object's own footprint inside the full
    operational region rather than all precipitation in that region.
    """
    region_id = str(overlap.get("region_id") or "").strip()
    if not region_id:
        return None
    overlap_count = int(overlap.get("overlap_grid_count") or 0)
    full_count = int(overlap.get("full_region_grid_count") or 0)
    region_precip_count = int(overlap.get("region_precip_grid_count") or 0)
    object_region_raw = overlap.get("object_region_coverage_fraction")
    try:
        object_region_coverage = (
            float(object_region_raw)
            if object_region_raw is not None
            else (float(overlap_count / full_count) if full_count > 0 else 0.0)
        )
    except (TypeError, ValueError, ZeroDivisionError):
        object_region_coverage = 0.0
    object_region_coverage = max(0.0, min(1.0, object_region_coverage))

    # Size-aware guardrails. These do not choose the final name; they only
    # prevent clearly tiny footprints from being offered as an unqualified
    # whole-region title. Small catalog regions need a larger fractional
    # footprint before a broad name is even eligible.
    if full_count <= 0:
        broad_name_allowed = False
        local_name_supported = overlap_count <= 2
    elif full_count <= 8:
        broad_name_allowed = overlap_count >= 2 and object_region_coverage >= 0.50
        local_name_supported = overlap_count <= 1
    elif full_count <= 32:
        broad_name_allowed = overlap_count >= 4 and object_region_coverage >= 0.25
        local_name_supported = overlap_count <= 2 or object_region_coverage < 0.10
    else:
        broad_name_allowed = overlap_count >= 6 and object_region_coverage >= 0.20
        local_name_supported = overlap_count <= 2 or object_region_coverage < 0.08

    coverage_class = "broad" if broad_name_allowed else ("local" if local_name_supported else "partial")
    region_bbox = overlap.get("region_bbox")
    object_centroid = obj.get("centroid") if isinstance(obj, dict) else None
    overlap_centroid = overlap.get("overlap_centroid")
    relation = "interior"
    object_fraction = 0.0
    try:
        object_fraction = float(overlap.get("object_overlap_fraction") or 0.0)
    except (TypeError, ValueError):
        object_fraction = 0.0
    if isinstance(object_centroid, dict) and isinstance(region_bbox, dict):
        try:
            lon = float(object_centroid["longitude"])
            lat = float(object_centroid["latitude"])
            inside = (
                float(region_bbox["west"]) <= lon <= float(region_bbox["east"])
                and float(region_bbox["south"]) <= lat <= float(region_bbox["north"])
            )
            if not inside:
                relation = "adjacent"
            elif object_fraction < 0.25:
                relation = "edge"
        except (KeyError, TypeError, ValueError):
            pass
    # For an adjacent object the direction must be based on the object's center,
    # not the small sliver that happens to overlap the operational region.
    sector_source = object_centroid if relation == "adjacent" else overlap_centroid
    sector = _relative_sector_from_centroid(sector_source, region_bbox)
    share_raw = overlap.get("object_share_of_region_precip")
    try:
        object_share = float(share_raw) if share_raw is not None else None
    except (TypeError, ValueError):
        object_share = None
    return {
        "region_id": region_id,
        "relative_sector": sector,
        "coverage_class": coverage_class,
        "object_relation": relation,
        "region_role": str(overlap.get("region_role") or ""),
        "precip_coverage_fraction": object_region_coverage,
        "object_overlap_fraction": max(0.0, min(1.0, object_fraction)),
        "overlap_grid_count": max(1, overlap_count),
        "region_precip_grid_count": max(0, region_precip_count),
        "full_region_grid_count": max(0, full_count),
        "object_share_of_region_precip": None if object_share is None else max(0.0, min(1.0, object_share)),
        "broad_name_allowed": bool(broad_name_allowed),
        "local_name_supported": bool(local_name_supported),
    }


def _derive_system_precipitation_object_identity(
    group: dict[str, Any],
    signal: dict[str, Any],
) -> dict[str, Any] | None:
    """Derive a public precipitation footprint identity from SYSTEM Step05 metadata."""
    if str(signal.get("direct_precipitation_status") or "") != "confirmed":
        return None
    context = group.get("candidate_context")
    plan = context.get("analysis_plan") if isinstance(context, dict) else None
    candidate = context.get("candidate") if isinstance(context, dict) else None
    if not isinstance(plan, dict) or not isinstance(candidate, dict):
        return None
    peak_hours = {
        int(hour)
        for hour in candidate.get("peak_forecast_hours", [])
        if isinstance(hour, int) and not isinstance(hour, bool)
    }
    if not peak_hours:
        return None
    anchor_ids = [
        str(check.get("check_id") or "")
        for check in plan.get("variable_checks", [])
        if isinstance(check, dict)
        and isinstance(check.get("intent_contract"), dict)
        and check["intent_contract"].get("direct_precipitation_anchor") is True
        and str(check["intent_contract"].get("source_check_key") or "") == "SYS_DIRECT_PRECIPITATION_PRESENCE"
        and str(check.get("check_id") or "")
    ]
    if len(anchor_ids) != 1:
        return None
    anchor_id = anchor_ids[0]
    object_rows: list[tuple[tuple[float, int, float, int], dict[str, Any], int]] = []
    for entry in group.get("analysis_data_requests", []):
        if not isinstance(entry, dict):
            continue
        request = entry.get("request")
        if not isinstance(request, dict) or request.get("product_id") != "QL_PRECIP_6H":
            continue
        source_ids = request.get("source_check_ids")
        if not isinstance(source_ids, list) or anchor_id not in {str(value) for value in source_ids}:
            continue
        stored = ((entry.get("step05") or {}).get("result")) if isinstance(entry.get("step05"), dict) else None
        if not isinstance(stored, dict) or stored.get("status") != "success":
            continue
        for image in stored.get("generated_images", []):
            if not isinstance(image, dict) or image.get("physical_diagnostic") != "QL_PRECIP_6H":
                continue
            hour = image.get("forecast_hour")
            if isinstance(hour, bool) or not isinstance(hour, int) or hour not in peak_hours:
                continue
            objects = image.get("precipitation_objects")
            if not isinstance(objects, list):
                continue
            for obj in objects:
                if not isinstance(obj, dict):
                    continue
                overlap_count = int(obj.get("operational_overlap_grid_count") or 0)
                op_max = obj.get("operational_maximum_6h_mm")
                if overlap_count <= 0 or not isinstance(op_max, (int, float)) or isinstance(op_max, bool) or float(op_max) < 1.0:
                    continue
                rank = (
                    float(obj.get("operational_precipitation_sum_mm") or 0.0),
                    overlap_count,
                    float(op_max),
                    -int(hour),
                )
                object_rows.append((rank, obj, int(hour)))
    if not object_rows:
        return None
    object_rows.sort(key=lambda item: item[0], reverse=True)
    _rank, primary, hour = object_rows[0]
    overlaps = [item for item in primary.get("region_overlaps", []) if isinstance(item, dict)]
    overlaps.sort(
        key=lambda item: (
            -float(item.get("overlap_precipitation_sum_mm") or 0.0),
            -int(item.get("overlap_grid_count") or 0),
            str(item.get("region_id") or ""),
        )
    )
    regions: list[dict[str, Any]] = []
    if overlaps:
        primary_sum = float(overlaps[0].get("overlap_precipitation_sum_mm") or 0.0)
        for overlap in overlaps:
            if regions and (
                len(regions) >= 2
                or float(overlap.get("overlap_precipitation_sum_mm") or 0.0) < primary_sum * 0.50
                or int(overlap.get("overlap_grid_count") or 0) < 2
            ):
                continue
            identity = _precipitation_object_region_identity(overlap, primary)
            if identity is not None:
                regions.append(identity)
    if not regions:
        return None
    confidence = "high" if (
        int(regions[0].get("overlap_grid_count") or 0) >= 3
        and float(regions[0].get("precip_coverage_fraction") or 0.0) >= 0.10
        and float(regions[0].get("object_overlap_fraction") or 0.0) >= 0.25
        and str(regions[0].get("object_relation") or "") != "adjacent"
    ) else "medium"
    return {
        "status": "precipitation_object",
        "forecast_hour": hour,
        "confidence": confidence,
        "evidence_check_ids": [anchor_id],
        "regions": regions,
        "source": "system_precipitation_object",
        "precipitation_object_id": str(primary.get("object_id") or ""),
        "object_grid_count": int(primary.get("grid_count") or 0),
        "object_centroid": copy.deepcopy(primary.get("centroid")),
        "object_bbox": copy.deepcopy(primary.get("bbox")),
        "operational_overlap_fraction": float(primary.get("operational_overlap_fraction") or 0.0),
    }


def _derive_final_spatial_identity(
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    signal: dict[str, Any],
) -> dict[str, Any]:
    """Derive the final public geography without using a single maximum grid.

    Confirmed direct-precipitation candidates use the SYSTEM-owned connected
    precipitation footprint produced by Step05.  Environment localization is
    retained independently in ``signal.environment_localization`` and is used
    here only for non-direct/legacy cases.  This keeps a narrow environment
    overlap from silently becoming the name of the whole precipitation episode.
    """
    object_identity = _derive_system_precipitation_object_identity(group, signal)
    if isinstance(object_identity, dict):
        return object_identity
    localization = _derive_environment_localization(group, checks, signal)
    if isinstance(localization, dict) and localization.get("status") == "localized":
        check_id = str(localization.get("evidence_check_id") or "").strip()
        return {
            "status": "localized",
            "forecast_hour": int(localization["forecast_hour"]),
            "confidence": str(localization["confidence"]),
            "evidence_check_ids": [check_id] if check_id else [],
            "regions": copy.deepcopy(localization.get("regions", [])),
            "source": "step07_validated_spatial_localization",
        }
    return {
        "status": "discovery_envelope",
        "forecast_hour": None,
        "confidence": None,
        "evidence_check_ids": [],
        "regions": [],
        "source": "step04_candidate_area",
    }



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


def _candidate_name_compact_region_name(value: Any) -> str:
    name = str(value or "").strip()
    if name.endswith(" 광역"):
        name = name[:-3].rstrip()
    return name


def _candidate_name_phrases_from_object_region(
    region: dict[str, Any], region_names: dict[str, str]
) -> list[str]:
    """Return only spatially supportable region phrases, ordered most specific first."""
    region_id = str(region.get("region_id") or "").strip()
    region_name = _candidate_name_compact_region_name(region_names.get(region_id))
    if not region_id or not region_name:
        return []
    sector_key = str(region.get("relative_sector") or "unspecified")
    relation = str(region.get("object_relation") or "interior")
    region_role = str(region.get("region_role") or "")
    phrases: list[str] = []
    if relation == "adjacent":
        direction = _CANDIDATE_NAME_ADJACENT_LABELS_KO.get(sector_key, "인근")
        noun = "인접 해상" if region_role == "marine_macro" else "인접 영역"
        return [" ".join(part for part in (region_name, direction, noun) if part).strip()]

    sector = _CANDIDATE_NAME_SECTOR_LABELS_KO.get(sector_key, "")
    specific = " ".join(part for part in (region_name, sector) if part).strip()
    if bool(region.get("local_name_supported")):
        local = " ".join(part for part in (region_name, sector, "국지") if part).strip()
        if local:
            phrases.append(local)
    if specific and specific not in phrases:
        phrases.append(specific)
    if bool(region.get("broad_name_allowed")) and region_name not in phrases:
        phrases.append(region_name)
    return phrases


def _candidate_name_grounding_contract(
    root: Path,
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build the SYSTEM grounding that constrains, but does not choose, the title."""
    context = group.get("candidate_context") if isinstance(group.get("candidate_context"), dict) else {}
    candidate = context.get("candidate") if isinstance(context.get("candidate"), dict) else {}
    source_name = str(candidate.get("candidate_name") or "").strip().replace("강우", "강수")
    region_index = _candidate_analysis_region_index(root, group)
    region_names = {
        str(item.get("region_id") or ""): str(item.get("name_ko") or "")
        for item in region_index.get("regions", [])
        if isinstance(item, dict) and str(item.get("region_id") or "")
    }
    direct_contract = _direct_precipitation_status_contract(group, checks)
    required_direct_status = str(direct_contract.get("required_status") or "")
    options: list[dict[str, Any]] = []

    def add_option(name: str, evidence_ids: list[str], basis: str) -> None:
        normalized = str(name or "").strip().replace("강우", "강수")
        if not normalized:
            return
        evidence = [str(value) for value in evidence_ids if str(value)]
        if any(item.get("name") == normalized for item in options):
            return
        options.append({"name": normalized, "evidence_check_ids": list(dict.fromkeys(evidence)), "basis": basis})

    object_identity = None
    if required_direct_status == "confirmed":
        object_identity = _derive_system_precipitation_object_identity(
            group, {"direct_precipitation_status": "confirmed"}
        )
        if not isinstance(object_identity, dict):
            raise Pass2PipelineError(
                "CANDIDATE_NAME_GROUNDING_MISSING",
                f"{group.get('candidate_id')} confirmed direct precipitation requires Step05 precipitation-object metadata. "
                "Rerun Step05 and Step06 with the current cumulative code before Step08; peak-region fallback is disabled for current products.",
            )
        regions = [item for item in object_identity.get("regions", []) if isinstance(item, dict)]
        evidence_ids = [str(value) for value in object_identity.get("evidence_check_ids", []) if str(value)]
        primary_phrases = _candidate_name_phrases_from_object_region(regions[0], region_names) if regions else []
        secondary_phrases = _candidate_name_phrases_from_object_region(regions[1], region_names) if len(regions) > 1 else []
        for phrase in primary_phrases:
            add_option(f"{phrase} 강수", evidence_ids, "system_precipitation_object_primary")
        if primary_phrases and secondary_phrases:
            add_option(
                f"{primary_phrases[0]}·{secondary_phrases[0]} 강수",
                evidence_ids,
                "system_precipitation_object_primary_secondary",
            )

        movement_pair = verified_precipitation_movement_region_pair(
            _check_specifications(group), checks
        )
        movement_ids = verified_precipitation_movement_check_ids(
            _check_specifications(group), checks
        )
        if movement_pair is not None:
            origin = _candidate_name_compact_region_name(region_names.get(movement_pair[0]))
            destination = _candidate_name_compact_region_name(region_names.get(movement_pair[1]))
            if origin and destination:
                add_option(
                    f"{origin} → {destination} 이동 강수",
                    movement_ids,
                    "unanimous_coherent_movement",
                )
        if not options:
            raise Pass2PipelineError(
                "CANDIDATE_NAME_GROUNDING_MISSING",
                f"{group.get('candidate_id')} precipitation object exists but no grounded display name could be constructed.",
            )

    if not options and source_name:
        add_option(source_name, [], "step04_source_name_fallback")
    if not options:
        add_option("강수 감시후보", [], "generic_fallback")

    allowed_evidence_ids = sorted({
        check_id
        for option in options
        for check_id in option.get("evidence_check_ids", [])
    })
    return {
        "contract_version": CANDIDATE_NAME_GROUNDING_CONTRACT_VERSION,
        "decision_owner": "llm_with_system_grounding",
        "source_candidate_name": source_name,
        "required_direct_precipitation_status": required_direct_status or None,
        "precipitation_object_grounding_required": required_direct_status == "confirmed",
        "precipitation_object_grounding_available": isinstance(object_identity, dict),
        "spatial_evidence": copy.deepcopy(object_identity),
        "name_options": options,
        "allowed_display_names": [item["name"] for item in options],
        "allowed_evidence_check_ids": allowed_evidence_ids,
        "instruction": (
            "revised_candidate_name은 name_options.name 중 하나를 정확히 선택하십시오. "
            "SYSTEM이 연속 강수객체의 실제 운영권역 겹침과 공간범위를 계산하며, LLM은 후보 전체의 기상학적 맥락을 종합해 최종 사용자 표시명을 선택합니다. "
            "broad_name_allowed=false인 권역을 무수식 전체 권역명으로 확대하지 마십시오. 허용 목록 밖 지명·방향·공간범위를 새로 만들지 마십시오."
        ),
    }


def _candidate_name_grounding_audit(contract: dict[str, Any]) -> dict[str, Any]:
    """Persist the SYSTEM-owned naming inputs so daio can be audited after the run."""
    return {
        "contract_version": str(contract.get("contract_version") or ""),
        "decision_owner": str(contract.get("decision_owner") or ""),
        "source_candidate_name": str(contract.get("source_candidate_name") or ""),
        "required_direct_precipitation_status": contract.get("required_direct_precipitation_status"),
        "precipitation_object_grounding_required": bool(contract.get("precipitation_object_grounding_required")),
        "precipitation_object_grounding_available": bool(contract.get("precipitation_object_grounding_available")),
        "spatial_evidence": copy.deepcopy(contract.get("spatial_evidence")),
        "name_options": copy.deepcopy(contract.get("name_options", [])),
        "allowed_display_names": list(contract.get("allowed_display_names", [])),
    }

def _confirmed_direct_precipitation_anchor_ids(
    group: dict[str, Any], checks: dict[str, dict[str, Any]],
) -> list[str]:
    """Return only publication-quality direct-precipitation presence anchors."""
    status_contract = _direct_precipitation_status_contract(group, checks)
    if status_contract["status_controlled_by_presence_anchor"]:
        if status_contract["required_status"] != "confirmed":
            return []
        return list(status_contract["presence_anchor_check_ids"])

    # Legacy Step04 checkpoints did not identify a pure presence anchor.  Keep
    # the historical reference-eligibility behavior for those stored results.
    candidate_ids = _usable_precursor_evidence_paths(group, checks).get("direct_precipitation", [])
    return sorted(
        check_id
        for check_id in candidate_ids
        if checks.get(check_id, {}).get("assessment") == "observed"
        and checks.get(check_id, {}).get("evidence_status") == "complete"
        and checks.get(check_id, {}).get("evidence_complete") is True
        and checks.get(check_id, {}).get("usable_for_synthesis") is not False
    )


def _signal_grounding_contract(
    group: dict[str, Any], checks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return the complete deterministic reference contract for signal synthesis.

    Every initial Step08 call and every regeneration of ``signal_conclusion_block``
    receives the same contract.  This prevents a repair for one signal constraint
    from silently regressing another constraint in the same semantic block.
    """
    precursor_paths = _usable_precursor_evidence_paths(group, checks)
    confirmed_environment_paths = _confirmed_environment_precursor_evidence_paths(group, checks)
    specifications = _check_specifications(group)
    precursor_claim_paths = {
        category: {
            check_id: sorted(positive_support_claim_keys(category, specifications.get(check_id)))
            for check_id in check_ids
        }
        for category, check_ids in precursor_paths.items()
        if check_ids
    }
    direct_status_contract = _direct_precipitation_status_contract(group, checks)
    direct_confirmed = _confirmed_direct_precipitation_anchor_ids(group, checks)
    alignment_positive = _usable_alignment_check_ids(group, checks)
    localized_alignment_by_check_id: dict[str, dict[str, Any]] = {}
    for check_id in alignment_positive:
        specification = specifications.get(check_id, {})
        result = checks.get(check_id, {})
        localization = result.get("spatial_localization") if isinstance(result, dict) else None
        if not isinstance(localization, dict) or localization.get("status") != "localized":
            continue
        components = [
            component
            for component in (
                "direct_precipitation",
                "moisture_supply",
                "low_level_forcing",
                "mid_upper_forcing",
            )
            if component in {
                str(value)
                for value in specification.get("required_signal_components", [])
                if isinstance(value, str) and value
            }
        ]
        localized_alignment_by_check_id[check_id] = {
            "forecast_hour": localization.get("forecast_hour"),
            "signal_components": components,
            "regions": copy.deepcopy(localization.get("regions", [])),
        }
    candidate = group.get("candidate_context", {}).get("candidate", {})
    peak_hours = candidate.get("peak_forecast_hours", []) if isinstance(candidate, dict) else []
    coupled_environment_alignment_ids = complete_coupled_environment_alignment_check_ids(
        alignment_positive,
        check_specifications=specifications,
        check_results=checks,
        candidate_peak_hours=peak_hours,
    )
    persistence_contract = environment_persistence_evidence_contract(
        check_specifications=specifications,
        check_results=checks,
        analysis_phases=group["candidate_context"]["analysis_plan"].get("analysis_phases", []),
    )
    return {
        "usable_signal_evidence_check_ids": sorted(_usable_check_ids(checks)),
        "precursor_evidence": {
            "allowed_category_check_ids": precursor_paths,
            "positive_support_claim_keys_by_category_check": precursor_claim_paths,
            "rule": (
                "validated_precursor_evidence의 각 category는 해당 allowed_category_check_ids 안의 Check만 사용하십시오. "
                "allowed 목록은 Step04 evidence_role뿐 아니라 atomic expected_state의 방향성과 다변수 overlap claim까지 검증한 양의 근거 경로입니다. "
                "not_strengthen/decrease/weaken/not_overlap 등 강수 발달을 제한하는 상태를 확인한 Check는 observed여도 양의 precursor 근거로 사용하지 마십시오. "
                "synoptic_support multivariable_alignment Check라도 positive_support_claim_keys_by_category_check에 category별 overlap claim이 명시되면 그 category의 양의 근거로 사용할 수 있습니다. "
                "단, 동일한 check_id:claim_key 경로를 둘 이상의 operational precursor category의 양의 anchor로 중복 사용하지 마십시오. "
                "한 multivariable Check가 여러 category를 지지하려면 category마다 서로 다른 positive claim_key가 있어야 합니다. "
                "별도 독립 Check가 있는 category에서는 그 독립 경로를 우선 사용하고, 동일 claim 하나만 공유되는 category들은 필요한 최소 범주만 남기십시오. "
                "자료가 실제로 해당 신호를 지지하는지는 Step07 assessment와 findings를 읽고 판단하되, 허용되지 않은 Check를 새로 연결하지 마십시오."
            ),
        },
        "direct_precipitation": {
            **direct_status_contract,
            "confirmed_anchor_check_ids": direct_confirmed,
            "confirmed_allowed": bool(direct_confirmed),
            "rule": (
                "status_controlled_by_presence_anchor=true이면 direct_precipitation_status는 required_status와 정확히 같아야 합니다. "
                "이 상태는 전용 presence_anchor_check_ids의 Step07 판독에서만 결정되며 강수대 이동·연속성·조직화·피크·강도 변화 Check로 낮추거나 높이지 마십시오. "
                "validated_precursor_evidence에 direct_precipitation category를 포함한다면 presence_anchor_check_ids만 사용하십시오. "
                "required_status=confirmed이면 해당 presence anchor를 validated_precursor_evidence와 signal_evidence_check_ids에 반드시 포함하십시오. "
                "legacy 계약(status_controlled_by_presence_anchor=false)에서는 기존 confirmed_anchor_check_ids 규칙을 따르십시오."
            ),
        },
        "spatiotemporal_alignment": {
            "positive_alignment_check_ids": alignment_positive,
            "positive_alignment_allowed": bool(alignment_positive),
            "localized_alignment_by_check_id": localized_alignment_by_check_id,
            "rule": (
                "validated_spatiotemporal_alignment=true이면 spatiotemporal_alignment_evidence_check_ids는 "
                "positive_alignment_check_ids의 비어 있지 않은 부분집합이어야 합니다. 목록이 비어 있으면 false와 빈 배열을 사용하십시오. "
                "localized_alignment_by_check_id의 signal_components는 해당 Check에서 구조화 위치 합의가 성립한 실제 결합 요소입니다. "
                "확정적인 결합·중첩·같은 위치·시공간 일치 표현에는 같은 Check의 signal_components만 함께 묶으십시오. "
                "다른 Check에서 부분 확인된 물리량은 동일 결합에 추가하지 말고 필요하면 '일부 동반' 또는 별도 판독으로 분리하십시오."
            ),
        },
        "physical_synthesis": {
            "usable_check_ids": sorted(_usable_check_ids(checks)),
            "direct_presence_anchor_check_ids": list(direct_status_contract.get("presence_anchor_check_ids", [])),
            "rule": (
                "mechanism_evidence에는 강수 발생·유지 메커니즘의 성립 여부를 판단한 usable Check를, monitoring_evidence에는 강수 강도·범위·지속성·예보권역 영향·이동 등 운영상 감시 가치를 판단한 usable Check를 기록하십시오. "
                "각 evidence 객체는 check_id, effect(supporting/limiting/mixed/context), reason을 가져야 하며 partially_observed Check의 지지와 제한이 함께 중요하면 mixed로 보존하십시오. "
                "Step07 assessment는 expected_signal의 확인 여부이므로 observed 자체를 자동으로 지지 근거로 해석하지 마십시오. "
                "Step04 hypothesis의 supporting_check_ids/opposing_check_ids와 expected_signal, Step07 findings/contradictory_findings/uncertainties를 함께 읽어 각 Check의 판단 역할을 결정하십시오. "
                "강수 위치·이동·약화가 단지 예보권역 영향이나 우선순위를 바꾸는 근거라면 mechanism_evidence가 아니라 monitoring_evidence로 분류하십시오. "
                "Check 개수로 물리적 성립성이나 감시 가치를 결정하지 마십시오. "
                "모델 강수 예측 presence anchor는 모델 강수 존재만 증명하므로 mechanism_evidence와 monitoring_evidence 모두에서 제외하십시오. "
                "direct=confirmed/environment=partial 후보를 surface로 판단할 때는 presence anchor 이외의 물리과정 지지 또는 혼합 근거와 감시 가치 지지 또는 혼합 근거를 각각 명시해야 합니다."
            ),
        },
        "environment_support": {
            "confirmed_requires_positive_alignment": True,
            "confirmed_allowed_by_alignment": bool(alignment_positive),
            "confirmed_precursor_anchor_check_ids_by_category": confirmed_environment_paths,
            "confirmed_precursor_allowed_assessments": sorted(_CONFIRMED_ENVIRONMENT_PRECURSOR_ASSESSMENTS),
            "complete_coupled_environment_alignment_check_ids": coupled_environment_alignment_ids,
            "rule": (
                "environment_support_status는 핵심시각·공간에서 강수 발달 환경이 실제로 존재하는지를 뜻하며, 전체 기간의 지속성·강화 여부와 분리해서 판단하십시오. "
                "confirmed는 validated_spatiotemporal_alignment=true이고 유효한 positive alignment 근거가 있는 상태에서 두 경로 중 하나로 허용됩니다. "
                "경로 A는 서로 다른 operational precursor category의 독립적인 양의 근거가 기존 계약을 만족하는 경우입니다. "
                "경로 B는 direct_precipitation_status=confirmed이고 complete_coupled_environment_alignment_check_ids에 포함된 Check가 핵심시각에서 모델 강수·수분·하층 forcing·중층 forcing의 완전한 국지 결합을 직접 확인한 경우입니다. "
                "경로 B를 사용하더라도 한 atomic claim을 여러 precursor category의 독립 근거로 복제하지 말고 validated_precursor_evidence는 실제 category별 근거만 정직하게 기록하십시오. "
                "후속 시각의 약화·분리나 주변 권역의 반대 변화는 핵심시각 환경 존재를 자동으로 취소하지 말고 environment_persistence_status에서 별도로 표현하십시오. "
                "positive alignment는 강수와 환경요소의 실제 overlap을 확인하는 supporting multivariable Check만 의미하며, 후반 overlap 약화·소멸·재조직 같은 limiting/opposing Check는 포함하지 마십시오. "
                "assessment가 not_observed, opposite_observed, uncertain, insufficient_evidence이거나 evidence가 불완전하거나 usable_for_synthesis=false인 Check는 confirmed 환경의 positive precursor anchor로 사용할 수 없습니다. "
                "경로 A와 B 모두 성립하지 않으면 partial/not_confirmed/insufficient_evidence 중 Step07 근거에 맞는 상태를 선택하십시오."
            ),
        },
        "environment_persistence": {
            **persistence_contract,
            "allowed_statuses": ["confirmed", "limited", "insufficient_evidence", "not_applicable"],
            "rule": (
                "environment_persistence_status는 핵심시각에 확인된 환경이 시간적으로 유지·강화되는지를 별도로 뜻합니다. "
                "environment_support_status가 confirmed 또는 partial이면 not_applicable을 사용하지 마십시오. "
                "effect_by_check_id와 supporting/limiting/mixed/context 목록은 validator와 동일한 결정론적 참조 계약입니다. "
                "mixed는 지지와 제한이 같은 비중이라는 뜻이 아니라, 하나의 시간변화 Check 안에 양의 지속 근거와 명시적 제한 근거가 함께 있다는 뜻입니다. "
                "시간변화에 대한 usable 근거가 없거나 판단 가능한 supporting/limiting/mixed 근거가 부족하면 insufficient_evidence를 사용하십시오. "
                "limited를 선택할 때는 limiting_check_ids 또는 mixed_check_ids 중 실제 판단에 사용한 Check를 최소 하나 포함하십시오. "
                "confirmed를 선택할 때는 서로 다른 환경 범주 두 개 이상을 대표하는 supporting_check_ids를 사용해야 하며, "
                "available limiting 또는 mixed temporal Check가 하나라도 있으면 confirmed를 선택하지 마십시오. "
                "limiting 또는 mixed Check를 근거 배열에서 빼는 방식으로 confirmed를 만들지 마십시오. "
                "context_check_ids만으로 confirmed 또는 limited를 만들지 마십시오. "
                "environment_persistence_evidence_check_ids는 allowed_evidence_check_ids 안에서만 선택하십시오. "
                "근거 목록에서 불리한 Check를 단순히 누락해 상태를 높이지 말고 Step07 findings와 contradictory_findings를 함께 읽어 실제 중요도를 판단하십시오. "
                "핵심시각의 단일 공간장 결합만으로 지속성을 confirmed로 판단하지 마십시오."
            ),
        },
    }


def _intent_lineage_rows(
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Verify that Step04 check intent hashes survived Step06/07 unchanged."""
    plan = group.get("candidate_context", {}).get("analysis_plan", {})
    plan_checks = {
        str(item.get("check_id")): item
        for item in plan.get("variable_checks", [])
        if isinstance(item, dict) and item.get("check_id")
    }
    rows: list[dict[str, str]] = []
    for check_id in sorted(checks):
        specification = plan_checks.get(check_id)
        if specification is None:
            continue
        expected = specification.get("intent_hash")
        if expected is None:
            continue
        actual = checks[check_id].get("source_intent_hash")
        if actual != expected:
            raise Pass2PipelineError(
                "INTENT_LINEAGE_MISMATCH",
                f"{group.get('candidate_id')} {check_id} Step07 source_intent_hash does not match Step04 intent_hash.",
            )
        rows.append({
            "check_id": check_id,
            "step04_intent_hash": str(expected),
            "step07_source_intent_hash": str(actual),
        })
    return rows


def _provenance(
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    step07_schema_version: str,
    config: dict[str, str],
    prompt_hash: str,
    schema_hash: str,
    analysis_region_index: dict[str, Any] | None = None,
    candidate_name_grounding_contract: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    check_fingerprints = [
        {"check_id": check_id, "input_fingerprint": str(checks[check_id]["input_fingerprint"])}
        for check_id in sorted(checks)
    ]
    intent_lineage = _intent_lineage_rows(group, checks)
    fingerprint_payload = {
        "candidate_context": group["candidate_context"],
        "check_assessments": [checks[check_id] for check_id in sorted(checks)],
        "intent_lineage": intent_lineage,
        "step07_schema_version": step07_schema_version,
        "synthesis_prompt_sha256": prompt_hash,
        "synthesis_schema_sha256": schema_hash,
        "direct_precipitation_anchor_contract_version": SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CONTRACT_VERSION,
        "provider": config,
    }
    provenance = {
        "step07_schema_version": step07_schema_version,
        "step07_result_fingerprints": check_fingerprints,
        "intent_lineage": intent_lineage,
        "synthesis_prompt_sha256": prompt_hash,
        "synthesis_schema_sha256": schema_hash,
        "direct_precipitation_anchor_contract_version": SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CONTRACT_VERSION,
        "provider": config["provider"],
        "model": config["model"],
        "reasoning_effort": config["reasoning_effort"],
        "repair_reasoning_effort": config.get("repair_reasoning_effort", config["reasoning_effort"]),
        "image_detail": config["image_detail"],
    }
    if analysis_region_index is not None:
        fingerprint_payload["analysis_region_naming_contract_version"] = ANALYSIS_REGION_NAMING_CONTRACT_VERSION
        fingerprint_payload["candidate_analysis_region_index"] = analysis_region_index
        provenance["analysis_region_naming_contract_version"] = ANALYSIS_REGION_NAMING_CONTRACT_VERSION
        provenance["candidate_analysis_region_index_sha256"] = canonical_hash(analysis_region_index)
    if candidate_name_grounding_contract is not None:
        grounding_hash = canonical_hash(candidate_name_grounding_contract)
        fingerprint_payload["candidate_name_grounding_contract_version"] = CANDIDATE_NAME_GROUNDING_CONTRACT_VERSION
        fingerprint_payload["candidate_name_grounding_contract"] = candidate_name_grounding_contract
        provenance["candidate_name_grounding_contract_version"] = CANDIDATE_NAME_GROUNDING_CONTRACT_VERSION
        provenance["candidate_name_grounding_contract_sha256"] = grounding_hash
    return canonical_hash(fingerprint_payload), provenance


def _with_provenance(
    value: dict[str, Any],
    input_fingerprint: str,
    provenance: dict[str, Any],
    *,
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    candidate_name_grounding_contract: dict[str, Any],
) -> dict[str, Any]:
    """Persist provenance and the Python-owned direct-precipitation state.

    Provider output has already passed the semantic regeneration loop.  This
    final assignment is therefore not a silent repair of contradictory prose; it
    establishes the stored owner of the state field from the validated SYSTEM
    presence-anchor assessment.
    """
    result = dict(value)
    signal = value.get("signal_assessment")
    if isinstance(signal, dict):
        result_signal = dict(signal)
        direct, environment = _legacy_candidate_statuses(result_signal)
        direct_contract = _direct_precipitation_status_contract(group, checks)
        if direct_contract["status_controlled_by_presence_anchor"]:
            direct = str(direct_contract["required_status"] or "insufficient_evidence")
        result_signal["direct_precipitation_status"] = direct
        result_signal["environment_support_status"] = environment
        result_signal["signal_class"] = derive_signal_class(direct, environment)
        if not (direct == "confirmed" and environment == "partial"):
            result_signal["operational_surface_decision"] = "not_applicable"
        result_signal["validated_precursor_signal_categories"] = derive_validated_precursor_categories(
            result_signal.get("validated_precursor_evidence")
        )
        result_signal["environment_localization"] = _derive_environment_localization(
            group, checks, result_signal
        )
        result["signal_assessment"] = result_signal
        result["final_spatial_identity"] = _derive_final_spatial_identity(
            group, checks, result_signal
        )
    result["candidate_name_grounding_audit"] = _candidate_name_grounding_audit(candidate_name_grounding_contract)
    result["input_fingerprint"] = input_fingerprint
    result["provenance"] = provenance
    return result


def _step08_sentence_count(text: str) -> int:
    """Count prose sentences conservatively for the Step08 display contract."""
    chunks = [
        item.strip()
        for item in re.split(r"(?<=[.!?])\s+|[\r\n]+", text.strip())
        if item.strip()
    ]
    return len(chunks) if chunks else (1 if text.strip() else 0)


def validate_candidate_synthesis(
    group: dict[str, Any],
    checks: dict[str, dict[str, Any]],
    value: dict[str, Any],
    *,
    root: Path | None = None,
    guidance_quality_contract: bool = False,
) -> None:
    candidate_id = str(group["candidate_id"])
    if value.get("candidate_id") != candidate_id:
        raise Pass2PipelineError("CANDIDATE_ID_MISMATCH", f"Step08 result candidate_id differs: {value.get('candidate_id')!r}")
    plan = group["candidate_context"]["analysis_plan"]
    hypothesis_by_id = {str(item["hypothesis_id"]): item for item in plan.get("hypotheses", [])}
    output_hypotheses = {str(item.get("hypothesis_id")): item for item in value.get("hypothesis_assessments", []) if isinstance(item, dict)}
    if len(output_hypotheses) != len(value.get("hypothesis_assessments", [])) or set(output_hypotheses) != set(hypothesis_by_id):
        raise Pass2PipelineError("HYPOTHESIS_RESULT_INVALID", f"{candidate_id} Step08 must cover exactly the Step04 hypotheses.")

    valid_checks = set(checks)
    usable_checks = _usable_check_ids(checks)
    verified_relations = _verified_quantitative_relations(checks)

    hypothesis_evidence_errors = _step08_hypothesis_evidence_errors(group, checks, value)
    if hypothesis_evidence_errors:
        raise Pass2PipelineError(
            "HYPOTHESIS_EVIDENCE_INVALID",
            f"{candidate_id}: " + "; ".join(hypothesis_evidence_errors[:8]),
        )

    def validate_relation_refs(owner: str, relation_ids: Any, evidence_check_ids: set[str]) -> None:
        if not isinstance(relation_ids, list) or len(set(relation_ids)) != len(relation_ids):
            raise Pass2PipelineError("QUANTITATIVE_RELATION_REF_INVALID", f"{candidate_id} {owner} has invalid quantitative_relation_ids.")
        for relation_ref in relation_ids:
            relation = verified_relations.get(str(relation_ref))
            if relation is None:
                raise Pass2PipelineError("QUANTITATIVE_RELATION_REF_INVALID", f"{candidate_id} {owner} references unavailable verified relation: {relation_ref}")
            if str(relation.get("check_id")) not in evidence_check_ids:
                raise Pass2PipelineError("QUANTITATIVE_RELATION_REF_INVALID", f"{candidate_id} {owner} relation is not backed by its evidence_check_ids: {relation_ref}")

    for hypothesis_id, output in output_hypotheses.items():
        source = hypothesis_by_id[hypothesis_id]
        if (
            output.get("statement") != source.get("statement")
            or output.get("supporting_check_ids") != source.get("supporting_check_ids")
            or output.get("opposing_check_ids") != source.get("opposing_check_ids")
        ):
            raise Pass2PipelineError("HYPOTHESIS_RELATION_MISMATCH", f"{candidate_id} changed Step04 check relations for {hypothesis_id}.")
        allowed_hypothesis_checks = set(source.get("supporting_check_ids", [])) | set(source.get("opposing_check_ids", []))
        evidence_ids = set(output.get("evidence_check_ids", []))
        if not evidence_ids.issubset(allowed_hypothesis_checks & usable_checks):
            raise Pass2PipelineError("INTERPRETATION_EVIDENCE_CHECK_ID_INVALID", f"{candidate_id} {hypothesis_id} used evidence outside its usable Step04 relations.")
        validate_relation_refs(hypothesis_id, output.get("quantitative_relation_ids"), evidence_ids)

    expected_questions = _question_rows(candidate_id, plan)
    answers = value.get("forecaster_question_answers")
    if not isinstance(answers, list) or len(answers) != len(expected_questions):
        raise Pass2PipelineError("FORECASTER_QUESTION_RESULT_INVALID", f"{candidate_id} must answer every Step04 forecaster question.")
    for expected, answer in zip(expected_questions, answers, strict=True):
        if answer.get("forecaster_question_id") != expected["forecaster_question_id"] or answer.get("question") != expected["question"]:
            raise Pass2PipelineError("FORECASTER_QUESTION_RESULT_INVALID", f"{candidate_id} altered question identity or text.")
        evidence_ids = set(answer.get("evidence_check_ids", []))
        if not evidence_ids.issubset(usable_checks):
            raise Pass2PipelineError("INTERPRETATION_EVIDENCE_CHECK_ID_INVALID", f"{candidate_id} question uses unknown or unusable evidence_check_ids.")
        validate_relation_refs(str(expected["forecaster_question_id"]), answer.get("quantitative_relation_ids"), evidence_ids)

    quantitative_answer_errors = question_answer_errors(answers, verified_relations)
    if quantitative_answer_errors:
        raise Pass2PipelineError(
            "FORECASTER_QUANTITATIVE_ANSWER_INVALID",
            f"{candidate_id}: " + "; ".join(quantitative_answer_errors[:8]),
        )

    candidate = group.get("candidate_context", {}).get("candidate", {})
    grounding_contract = _candidate_name_grounding_contract(root, group, checks) if root is not None else None
    if root is not None and "input_fingerprint" in value:
        expected_audit = _candidate_name_grounding_audit(grounding_contract)
        if value.get("candidate_name_grounding_audit") != expected_audit:
            raise Pass2PipelineError(
                "CANDIDATE_NAME_GROUNDING_AUDIT_INVALID",
                f"{candidate_id} candidate_name_grounding_audit does not match current SYSTEM grounding.",
            )
    revision_errors = validate_candidate_name_revision(
        value.get("revision"),
        candidate=candidate,
        check_specifications=_check_specifications(group),
        check_results=checks,
        grounding_contract=grounding_contract,
    )
    if revision_errors:
        raise Pass2PipelineError(
            "CANDIDATE_NAME_REVISION_INVALID",
            f"{candidate_id} " + "; ".join(revision_errors),
        )

    signal = value.get("signal_assessment")
    if not isinstance(signal, dict):
        raise Pass2PipelineError("SIGNAL_ASSESSMENT_INVALID", f"{candidate_id} has no signal_assessment.")
    direct_status = signal.get("direct_precipitation_status")
    environment_status = signal.get("environment_support_status")
    if direct_status not in CANDIDATE_SIGNAL_STATUSES or environment_status not in CANDIDATE_SIGNAL_STATUSES:
        raise Pass2PipelineError("SIGNAL_STATUS_INVALID", f"{candidate_id} has invalid independent signal statuses.")
    persistence_errors = validate_environment_persistence_contract(
        signal.get("environment_persistence_status"),
        signal.get("environment_persistence_evidence_check_ids"),
        environment_support_status=environment_status,
        check_specifications=_check_specifications(group),
        check_results=checks,
        analysis_phases=group["candidate_context"]["analysis_plan"].get("analysis_phases", []),
    )
    if persistence_errors:
        raise Pass2PipelineError(
            "ENVIRONMENT_PERSISTENCE_INVALID",
            f"{candidate_id} " + "; ".join(persistence_errors),
        )
    direct_status_contract = _direct_precipitation_status_contract(group, checks)
    physical_status = signal.get("physical_consistency_status")
    monitoring_value = signal.get("monitoring_value")
    mechanism_evidence = signal.get("mechanism_evidence")
    monitoring_evidence = signal.get("monitoring_evidence")
    physical_summary = signal.get("physical_mechanism_summary")
    surface_decision = signal.get("operational_surface_decision")
    surface_reason = signal.get("operational_surface_reason")

    if physical_status not in PHYSICAL_CONSISTENCY_STATUSES:
        raise Pass2PipelineError("PHYSICAL_DECISION_INVALID", f"{candidate_id} physical_consistency_status is invalid.")
    if monitoring_value not in MONITORING_VALUES:
        raise Pass2PipelineError("PHYSICAL_DECISION_INVALID", f"{candidate_id} monitoring_value is invalid.")
    if not isinstance(physical_summary, str) or not physical_summary.strip():
        raise Pass2PipelineError("PHYSICAL_DECISION_INVALID", f"{candidate_id} physical_mechanism_summary must be non-empty text.")
    if not isinstance(surface_reason, str) or not surface_reason.strip():
        raise Pass2PipelineError("PHYSICAL_DECISION_INVALID", f"{candidate_id} operational_surface_reason must be non-empty text.")

    direct_anchor_ids = set(direct_status_contract.get("presence_anchor_check_ids", []))

    def validate_decision_evidence(field_name: str, entries: Any) -> list[dict[str, str]]:
        if not isinstance(entries, list):
            raise Pass2PipelineError("PHYSICAL_DECISION_INVALID", f"{candidate_id} {field_name} must be an array.")
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise Pass2PipelineError("PHYSICAL_DECISION_INVALID", f"{candidate_id} {field_name}[{index}] must be an object.")
            if set(entry) != {"check_id", "effect", "reason"}:
                raise Pass2PipelineError("PHYSICAL_DECISION_INVALID", f"{candidate_id} {field_name}[{index}] has invalid fields.")
            check_id = str(entry.get("check_id") or "")
            effect = str(entry.get("effect") or "")
            reason = entry.get("reason")
            if not check_id or check_id in seen or check_id not in usable_checks:
                raise Pass2PipelineError("PHYSICAL_DECISION_INVALID", f"{candidate_id} {field_name} must reference unique usable Step07 Checks only.")
            if check_id in direct_anchor_ids:
                raise Pass2PipelineError("PHYSICAL_DECISION_INVALID", f"{candidate_id} direct-precipitation presence anchors are context for direct status only and cannot be reused as {field_name}.")
            if effect not in DECISION_EVIDENCE_EFFECTS:
                raise Pass2PipelineError("PHYSICAL_DECISION_INVALID", f"{candidate_id} {field_name}[{index}] effect is invalid.")
            if not isinstance(reason, str) or not reason.strip():
                raise Pass2PipelineError("PHYSICAL_DECISION_INVALID", f"{candidate_id} {field_name}[{index}] reason must be non-empty text.")
            seen.add(check_id)
            normalized.append({"check_id": check_id, "effect": effect, "reason": reason.strip()})
        return normalized

    mechanism_entries = validate_decision_evidence("mechanism_evidence", mechanism_evidence)
    monitoring_entries = validate_decision_evidence("monitoring_evidence", monitoring_evidence)

    def has_effect(entries: list[dict[str, str]], effects: set[str]) -> bool:
        return any(entry["effect"] in effects for entry in entries)

    mechanism_support = has_effect(mechanism_entries, {"supporting", "mixed"})
    mechanism_limit = has_effect(mechanism_entries, {"limiting", "mixed"})
    monitoring_support = has_effect(monitoring_entries, {"supporting", "mixed"})
    monitoring_limit = has_effect(monitoring_entries, {"limiting", "mixed"})

    if physical_status in {"coherent", "conditionally_coherent"} and _usable_check_ids(checks) and not mechanism_support:
        raise Pass2PipelineError(
            "PHYSICAL_DECISION_INVALID",
            f"{candidate_id} a coherent physical mechanism requires explicit supporting or mixed mechanism evidence.",
        )
    if physical_status == "conditionally_coherent" and _usable_check_ids(checks) and not mechanism_limit:
        raise Pass2PipelineError(
            "PHYSICAL_DECISION_INVALID",
            f"{candidate_id} conditionally coherent status requires explicit limiting or mixed mechanism evidence.",
        )
    if physical_status == "incoherent" and _usable_check_ids(checks) and not mechanism_limit:
        raise Pass2PipelineError(
            "PHYSICAL_DECISION_INVALID",
            f"{candidate_id} an incoherent physical mechanism requires explicit limiting or mixed mechanism evidence.",
        )

    if monitoring_value in {"meaningful", "marginal"} and _usable_check_ids(checks) and not monitoring_support:
        raise Pass2PipelineError(
            "PHYSICAL_DECISION_INVALID",
            f"{candidate_id} meaningful or marginal monitoring value requires explicit supporting or mixed non-anchor monitoring evidence.",
        )
    if monitoring_value == "marginal" and _usable_check_ids(checks) and not monitoring_limit:
        raise Pass2PipelineError(
            "PHYSICAL_DECISION_INVALID",
            f"{candidate_id} marginal monitoring value requires explicit limiting or mixed monitoring evidence.",
        )
    if monitoring_value == "low" and _usable_check_ids(checks) and not monitoring_limit:
        raise Pass2PipelineError(
            "PHYSICAL_DECISION_INVALID",
            f"{candidate_id} low monitoring value requires explicit limiting or mixed monitoring evidence.",
        )

    if surface_decision not in OPERATIONAL_SURFACE_DECISIONS:
        raise Pass2PipelineError("OPERATIONAL_SURFACE_DECISION_INVALID", f"{candidate_id} operational surface decision is invalid.")

    if direct_status == "confirmed" and environment_status == "partial":
        if surface_decision not in {"surface", "do_not_surface"}:
            raise Pass2PipelineError(
                "OPERATIONAL_SURFACE_DECISION_INVALID",
                f"{candidate_id} confirmed direct precipitation with partial environment requires surface or do_not_surface; rerun Step08 under the current policy.",
            )
        if surface_decision == "surface":
            if physical_status not in {"coherent", "conditionally_coherent"}:
                raise Pass2PipelineError(
                    "PHYSICAL_DECISION_INVALID",
                    f"{candidate_id} a surfaced boundary candidate must have a meteorologically coherent or conditionally coherent mechanism.",
                )
            if monitoring_value not in {"meaningful", "marginal"}:
                raise Pass2PipelineError(
                    "PHYSICAL_DECISION_INVALID",
                    f"{candidate_id} a surfaced boundary candidate must have meaningful or marginal monitoring value.",
                )
            if not mechanism_support:
                raise Pass2PipelineError(
                    "PHYSICAL_DECISION_INVALID",
                    f"{candidate_id} direct precipitation cannot justify surfacing without explicit meteorological mechanism support.",
                )
            if not monitoring_support:
                raise Pass2PipelineError(
                    "PHYSICAL_DECISION_INVALID",
                    f"{candidate_id} direct-precipitation presence alone cannot justify monitoring value; cite operational-value support beyond the presence anchor.",
                )
    elif surface_decision != "not_applicable":
        raise Pass2PipelineError(
            "OPERATIONAL_SURFACE_DECISION_INVALID",
            f"{candidate_id} operational surface decision is only authored for confirmed direct precipitation with partial environment support.",
        )

    if environment_status == "confirmed":
        if physical_status not in {"coherent", "conditionally_coherent"}:
            raise Pass2PipelineError(
                "PHYSICAL_DECISION_INVALID",
                f"{candidate_id} confirmed environment cannot coexist with an incoherent or indeterminate physical mechanism.",
            )
        if direct_status == "confirmed" and monitoring_value != "meaningful":
            raise Pass2PipelineError(
                "PHYSICAL_DECISION_INVALID",
                f"{candidate_id} confirmed direct precipitation plus confirmed environment requires meaningful monitoring value.",
            )
        if direct_status != "confirmed" and monitoring_value not in {"meaningful", "marginal"}:
            raise Pass2PipelineError(
                "PHYSICAL_DECISION_INVALID",
                f"{candidate_id} confirmed precursor environment requires meaningful or marginal monitoring value.",
            )
    if (
        direct_status_contract["status_controlled_by_presence_anchor"]
        and direct_status != direct_status_contract["required_status"]
    ):
        raise Pass2PipelineError(
            "DIRECT_PRECIPITATION_EVIDENCE_INVALID",
            f"{candidate_id} direct_precipitation_status must be derived from the dedicated presence anchor: "
            f"required={direct_status_contract['required_status']} actual={direct_status} "
            f"anchor_check_ids={direct_status_contract['presence_anchor_check_ids']} "
            f"anchor_assessment={direct_status_contract['anchor_assessment']}",
        )
    derived_signal_class = derive_signal_class(direct_status, environment_status)
    if value.get("input_fingerprint") and signal.get("signal_class") != derived_signal_class:
        raise Pass2PipelineError("SIGNAL_CLASS_DERIVATION_INVALID", f"{candidate_id} signal_class must be derived from independent statuses.")
    derived_categories, category_anchors, precursor_errors = validate_validated_precursor_evidence(
        signal.get("validated_precursor_evidence"),
        check_specifications=_check_specifications(group),
        check_results=checks,
    )
    if precursor_errors:
        raise Pass2PipelineError("PRECURSOR_EVIDENCE_INVALID", f"{candidate_id} " + "; ".join(precursor_errors))
    # pass2_synthesis_response does not carry the derived category list; it is
    # added deterministically by _with_provenance before persistence.  When the
    # field is already present (stored Step08 result), it must still match.
    if (
        "validated_precursor_signal_categories" in signal
        and signal.get("validated_precursor_signal_categories") != derived_categories
    ):
        raise Pass2PipelineError("PRECURSOR_EVIDENCE_INVALID", f"{candidate_id} precursor categories must be derived from validated_precursor_evidence.")

    alignment_check_ids = signal.get("spatiotemporal_alignment_evidence_check_ids")
    if not isinstance(alignment_check_ids, list) or len(set(alignment_check_ids)) != len(alignment_check_ids):
        raise Pass2PipelineError("ALIGNMENT_EVIDENCE_INVALID", f"{candidate_id} has invalid alignment evidence IDs.")
    positive_alignment = bool(signal.get("validated_spatiotemporal_alignment"))
    if positive_alignment and not alignment_check_ids:
        raise Pass2PipelineError("ALIGNMENT_EVIDENCE_INVALID", f"{candidate_id} positive alignment requires evidence checks.")
    if not positive_alignment and alignment_check_ids:
        raise Pass2PipelineError(
            "ALIGNMENT_EVIDENCE_INVALID",
            f"{candidate_id} negative or unconfirmed alignment must not retain evidence checks: {alignment_check_ids}",
        )
    alignment_specifications = _check_specifications(group)
    for check_id in alignment_check_ids:
        check_id = str(check_id)
        if not _is_publication_eligible_alignment_check(
            check_id,
            specifications=alignment_specifications,
            checks=checks,
        ):
            result = checks.get(check_id)
            specification = alignment_specifications.get(check_id)
            raise Pass2PipelineError(
                "ALIGNMENT_EVIDENCE_INVALID",
                f"{candidate_id} alignment evidence does not satisfy the Step09 publication contract: "
                f"{check_id} scope={specification.get('check_scope') if isinstance(specification, dict) else None!r} "
                f"assessment={result.get('assessment') if isinstance(result, dict) else None!r} "
                f"evidence_status={result.get('evidence_status') if isinstance(result, dict) else None!r} "
                f"evidence_complete={result.get('evidence_complete') if isinstance(result, dict) else None!r} "
                f"usable_for_synthesis={result.get('usable_for_synthesis') if isinstance(result, dict) else None!r}",
            )

    signal_check_ids = signal.get("signal_evidence_check_ids")
    if (
        not isinstance(signal_check_ids, list)
        or len(set(signal_check_ids)) != len(signal_check_ids)
        or not set(signal_check_ids).issubset(usable_checks)
    ):
        raise Pass2PipelineError("EVIDENCE_CHECK_ID_INVALID", f"{candidate_id} signal assessment has unknown or unusable evidence_check_ids.")
    if derived_signal_class != "insufficient_evidence" and _usable_check_ids(checks) and not signal_check_ids:
        raise Pass2PipelineError("EVIDENCE_CHECK_ID_INVALID", f"{candidate_id} non-insufficient signal assessment requires evidence_check_ids.")
    controlled_direct_anchor_ids = set(direct_status_contract["presence_anchor_check_ids"])
    direct_category_ids = category_anchors.get("direct_precipitation", set())
    if direct_status_contract["status_controlled_by_presence_anchor"] and direct_category_ids:
        if not direct_category_ids.issubset(controlled_direct_anchor_ids):
            raise Pass2PipelineError(
                "DIRECT_PRECIPITATION_EVIDENCE_INVALID",
                f"{candidate_id} direct_precipitation validated evidence must use only the dedicated presence anchor: "
                f"allowed={sorted(controlled_direct_anchor_ids)} actual={sorted(direct_category_ids)}",
            )

    if direct_status == "confirmed":
        direct_anchor_ids = (
            controlled_direct_anchor_ids
            if direct_status_contract["status_controlled_by_presence_anchor"]
            else category_anchors.get("direct_precipitation", set())
        )
        if not direct_anchor_ids:
            raise Pass2PipelineError(
                "DIRECT_PRECIPITATION_EVIDENCE_INVALID",
                f"{candidate_id} confirmed direct precipitation has no evidence anchor.",
            )
        if (
            direct_status_contract["status_controlled_by_presence_anchor"]
            and direct_category_ids != direct_anchor_ids
        ):
            raise Pass2PipelineError(
                "DIRECT_PRECIPITATION_EVIDENCE_INVALID",
                f"{candidate_id} confirmed direct precipitation must cite the dedicated presence anchor in "
                f"validated_precursor_evidence: required={sorted(direct_anchor_ids)} actual={sorted(direct_category_ids)}",
            )
        if not direct_anchor_ids.issubset(set(signal_check_ids)):
            raise Pass2PipelineError(
                "DIRECT_PRECIPITATION_EVIDENCE_INVALID",
                f"{candidate_id} confirmed direct precipitation omits an anchor from signal_evidence_check_ids.",
            )
        invalid_direct_anchors = [
            check_id
            for check_id in sorted(direct_anchor_ids)
            if (
                checks.get(check_id, {}).get("assessment") != "observed"
                or checks.get(check_id, {}).get("usable_for_synthesis") is False
                or checks.get(check_id, {}).get("evidence_status") != "complete"
                or checks.get(check_id, {}).get("evidence_complete") is not True
            )
        ]
        if invalid_direct_anchors:
            raise Pass2PipelineError(
                "DIRECT_PRECIPITATION_EVIDENCE_INVALID",
                f"{candidate_id} confirmed direct precipitation requires observed, complete, usable anchors: "
                f"{invalid_direct_anchors}",
            )
    if environment_status == "confirmed":
        precursor_anchor_ids = set().union(*(
            anchors for category, anchors in category_anchors.items() if category != "direct_precipitation"
        ))
        candidate = group.get("candidate_context", {}).get("candidate", {})
        peak_hours = candidate.get("peak_forecast_hours", []) if isinstance(candidate, dict) else []
        coupled_alignment_ids = complete_coupled_environment_alignment_check_ids(
            alignment_check_ids,
            check_specifications=alignment_specifications,
            check_results=checks,
            candidate_peak_hours=peak_hours,
        ) if direct_status == "confirmed" else []
        if not precursor_anchor_ids and not coupled_alignment_ids:
            raise Pass2PipelineError(
                "PRECURSOR_EVIDENCE_INVALID",
                f"{candidate_id} confirmed environment has neither independent environment anchors "
                "nor a complete localized coupled-environment alignment anchor.",
            )

        confirmed_environment_paths = _confirmed_environment_precursor_evidence_paths(group, checks)
        invalid_confirmed_anchors: list[str] = []
        for category, anchors in category_anchors.items():
            if category == "direct_precipitation" or category not in OPERATIONAL_PRECURSOR_CATEGORIES:
                continue
            allowed_ids = set(confirmed_environment_paths.get(category, []))
            for check_id in sorted(anchors):
                if check_id not in allowed_ids:
                    result = checks.get(check_id, {})
                    invalid_confirmed_anchors.append(
                        f"{category}:{check_id}"
                        f"(assessment={result.get('assessment')},"
                        f" evidence_status={result.get('evidence_status')},"
                        f" evidence_complete={result.get('evidence_complete')},"
                        f" usable_for_synthesis={result.get('usable_for_synthesis')})"
                    )
        if invalid_confirmed_anchors:
            raise Pass2PipelineError(
                "PRECURSOR_EVIDENCE_INVALID",
                f"{candidate_id} confirmed environment uses precursor anchors that do not satisfy "
                "the Step09 publication contract: " + ", ".join(invalid_confirmed_anchors),
            )

        if not signal.get("validated_spatiotemporal_alignment") or not alignment_check_ids:
            raise Pass2PipelineError("ALIGNMENT_EVIDENCE_INVALID", f"{candidate_id} confirmed environment requires positive alignment evidence.")
        _core_categories, support_contract_errors = validate_operational_precursor_support_contract(
            derived_categories,
            category_anchors,
            spatial_temporal_overlap=True,
            check_specifications=alignment_specifications,
        )
        # Environment existence has two deliberately separate valid pathways:
        # (1) distinct independent operational precursor categories, or
        # (2) for a confirmed direct-precipitation candidate only, one fully
        #     observed/localized peak-time multivariable coupling Check.
        # The second path does not manufacture independent category anchors.
        if support_contract_errors and not coupled_alignment_ids:
            raise Pass2PipelineError(
                "PRECURSOR_EVIDENCE_INVALID",
                f"{candidate_id} " + "; ".join(support_contract_errors)
                + ". Regenerate validated_precursor_evidence with distinct category-specific paths, "
                  "or use a complete localized peak-time coupled-environment alignment when direct "
                  "precipitation is confirmed.",
            )
        required_signal_ids = precursor_anchor_ids | set(alignment_check_ids)
        if not required_signal_ids.issubset(set(signal_check_ids)):
            raise Pass2PipelineError(
                "PRECURSOR_EVIDENCE_INVALID",
                f"{candidate_id} confirmed environment omits anchor or alignment evidence from signal_evidence_check_ids.",
            )
    final_assessment = value.get("final_assessment")
    if not isinstance(final_assessment, str) or not final_assessment.strip():
        raise Pass2PipelineError(
            "FINAL_ASSESSMENT_INVALID",
            f"{candidate_id} final_assessment must be non-empty text.",
        )
    if _usable_check_ids(checks):
        sentence_count = _step08_sentence_count(final_assessment)
        if sentence_count < 2 or sentence_count > 6:
            raise Pass2PipelineError(
                "FINAL_ASSESSMENT_INVALID",
                f"{candidate_id} final_assessment must contain 2-6 prose sentences; actual={sentence_count}.",
            )
    if environment_status == "partial" and re.search(
        r"(?:강수\s*발달\s*)?환경(?:이|은|는)?\s*(?:충분히\s*)?(?:지지|뒷받침)(?:됩니다|된다|되었(?:습니다|다)|됨)",
        final_assessment,
    ):
        raise Pass2PipelineError(
            "FINAL_ASSESSMENT_INVALID",
            f"{candidate_id} partial environment support is overstated in final_assessment.",
        )
    validate_operational_brief(group, checks, value)
    validate_operational_guidance(
        group,
        checks,
        value,
        quality_contract=guidance_quality_contract,
    )


def dry_run_summary(root: Path, init: str, *, candidate_id: str | None = None) -> dict[str, Any]:
    package = load_step06_package(root, init)
    step07 = load_step07_results(root, init)
    rows = []
    for group in active_candidate_groups(package, candidate_id):
        checks = _check_map(str(group["candidate_id"]), step07)
        rows.append({"candidate_id": group["candidate_id"], "check_count": len(checks), "usable_check_count": len(_usable_check_ids(checks)), "verified_quantitative_relation_count": len(_verified_quantitative_relations(checks)), "api_required": bool(_usable_check_ids(checks)), "forecaster_question_ids": [item["forecaster_question_id"] for item in _question_rows(str(group["candidate_id"]), group["candidate_context"]["analysis_plan"])]})
    validate_schema(root, RESULT_SCHEMA, {"schema_version": RESULT_VERSION, "init": init, "candidates": []})
    return {"candidates": rows, "expected_api_calls": sum(1 for row in rows if row["api_required"])}


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


def _preflight_step08_result_schema_contract(root: Path) -> None:
    """Fail before provider calls when Python-owned Step08 fields drift from RESULT_SCHEMA."""
    schema = load_schema(root, RESULT_SCHEMA)
    defs = schema.get("$defs")
    if not isinstance(defs, dict):
        raise Pass2PipelineError(
            "SCHEMA_LOAD_FAILED",
            f"{RESULT_SCHEMA} is missing $defs required by Step08 runtime output.",
        )
    provenance = defs.get("provenance")
    provenance_props = provenance.get("properties") if isinstance(provenance, dict) else None
    if not isinstance(provenance_props, dict):
        raise Pass2PipelineError(
            "SCHEMA_LOAD_FAILED",
            f"{RESULT_SCHEMA} is missing $defs.provenance.properties.",
        )
    missing_provenance = sorted(_PYTHON_OWNED_STEP08_PROVENANCE_FIELDS - set(provenance_props))
    if missing_provenance:
        raise Pass2PipelineError(
            "SCHEMA_LOAD_FAILED",
            f"{RESULT_SCHEMA} does not allow Python-owned Step08 provenance fields: {missing_provenance}",
        )
    candidate = defs.get("candidate")
    candidate_props = candidate.get("properties") if isinstance(candidate, dict) else None
    if not isinstance(candidate_props, dict):
        raise Pass2PipelineError(
            "SCHEMA_LOAD_FAILED",
            f"{RESULT_SCHEMA} is missing $defs.candidate.properties.",
        )
    missing_candidate = sorted(_PYTHON_OWNED_STEP08_CANDIDATE_FIELDS - set(candidate_props))
    if missing_candidate:
        raise Pass2PipelineError(
            "SCHEMA_LOAD_FAILED",
            f"{RESULT_SCHEMA} does not allow Python-owned Step08 candidate fields: {missing_candidate}",
        )


def _preflight_direct_precipitation_presence_anchors(
    groups: list[dict[str, Any]],
    step07: dict[str, Any],
) -> None:
    """Fail before any Step08 provider call if the SYSTEM presence contract is incomplete.

    This is structural validation only.  It prevents expensive partial Step08 runs
    when a precursor (or any other) candidate was produced by an older Step04 plan
    that lacks the universal pure direct-precipitation presence Check.
    """
    for group in groups:
        candidate_id = str(group.get("candidate_id") or "")
        anchor_ids = _direct_precipitation_presence_anchor_ids(group)
        if len(anchor_ids) != 1:
            raise Pass2PipelineError(
                "STEP06_PACKAGE_INVALID",
                f"{candidate_id} requires exactly one SYSTEM direct-precipitation "
                f"presence anchor before Step08; actual={anchor_ids}. Rerun Step04-Step07 for this init.",
            )
        specifications = _check_specifications(group)
        anchor_spec = specifications.get(anchor_ids[0])
        if not category_check_is_compatible("direct_precipitation", anchor_spec):
            raise Pass2PipelineError(
                "STEP06_PACKAGE_INVALID",
                f"{candidate_id} SYSTEM direct-precipitation presence anchor violates the pure positive-presence contract: {anchor_ids[0]}",
            )
        checks = _check_map(candidate_id, step07)
        if anchor_ids[0] not in checks:
            raise Pass2PipelineError(
                "STEP07_RESULTS_MISSING",
                f"{candidate_id} Step07 result is missing SYSTEM direct-precipitation presence anchor {anchor_ids[0]}.",
            )


def run_step08(
    *,
    root: Path,
    init: str,
    config: dict[str, str],
    provider: AIProvider | None,
    candidate_id: str | None = None,
    on_language_retry: Any | None = None,
    on_observable_retry: Any | None = None,
    on_semantic_retry: Any | None = None,
    on_reference_autofix: Any | None = None,
    resume: bool = True,
    on_candidate_reuse: Any | None = None,
    on_candidate_checkpoint: Any | None = None,
) -> dict[str, Any]:
    package = load_step06_package(root, init)
    step07 = load_step07_results(root, init)
    if step07.get("schema_version") != CHECK_RESULTS_VERSION:
        raise Pass2PipelineError(
            "STEP07_SCOPE_CONTRACT_REQUIRED",
            f"Step08 P1 requires current Step07 schema {CHECK_RESULTS_VERSION}; actual={step07.get('schema_version')!r}. Rerun Step07.",
        )
    _preflight_step08_result_schema_contract(root)
    prompt_text, prompt_hash = template_text_and_hash(root, SYNTHESIS_PROMPT)
    schema_hash = sha256sum(runtime_path(root, "prompts", "schemas", SYNTHESIS_SCHEMA))
    output_path = step08_output_path(root, init)
    active_groups = active_candidate_groups(package)
    selected_groups = active_candidate_groups(package, candidate_id)
    _preflight_direct_precipitation_presence_anchors(selected_groups, step07)
    active_ids = {str(group["candidate_id"]) for group in active_groups}
    existing_by_candidate: dict[str, dict[str, Any]] = {}
    if output_path.is_file():
        existing = load_json_object(output_path, code="STEP08_RESULT_INVALID")
        if existing.get("init") == init and existing.get("schema_version") == RESULT_VERSION:
            existing = migrate_step08_document(existing)
            validate_schema(root, RESULT_SCHEMA, existing)
            existing_by_candidate = {
                str(item["candidate_id"]): item for item in existing.get("candidates", [])
                if isinstance(item, dict) and str(item.get("candidate_id")) in active_ids
            }

    # Preserve only candidate results whose complete Step08 input fingerprint
    # still matches the current Step04/06/07 data, prompt, schema and provider
    # configuration.  This makes an interrupted full run resumable without
    # accidentally mixing stale candidate results into a new run.
    reusable_by_candidate: dict[str, dict[str, Any]] = {}
    for group in active_groups:
        group_id = str(group["candidate_id"])
        existing_value = existing_by_candidate.get(group_id)
        if existing_value is None:
            continue
        checks = _check_map(group_id, step07)
        if set(checks) != _required_check_ids(group):
            continue
        naming_contract = _candidate_name_grounding_contract(root, group, checks)
        expected_fingerprint, expected_provenance = _provenance(
            group,
            checks,
            str(step07.get("schema_version") or CHECK_RESULTS_VERSION),
            config,
            prompt_hash,
            schema_hash,
            candidate_name_grounding_contract=naming_contract,
        )
        if str(existing_value.get("input_fingerprint") or "") != expected_fingerprint:
            continue
        # Cache reuse requires both the fingerprint and the explicit provenance
        # record to match.  The fingerprint may still match if a past bug omitted
        # a provider-setting field only from the persisted provenance object.
        # Regenerate such checkpoints instead of carrying an unreconstructable
        # Step08 identity into the Step09 publication gate.
        if existing_value.get("provenance") != expected_provenance:
            continue
        # An older Step08 executable could have persisted a valid-schema but
        # quantitatively false answer before the publication gate was deployed.
        # Only migrate a cached answer when its Step07 input, prompt, schema,
        # provider settings and candidate provenance are exactly unchanged.
        # No Step09/10 auto-fix is allowed: the corrected Step08 result must be
        # validated and written atomically before it can be published.
        verified = _verified_quantitative_relations(checks)
        recovered_answers, recovered_ids = reconcile_legacy_question_answers(
            existing_value.get("forecaster_question_answers", []), verified,
        )
        if recovered_ids:
            existing_value = copy.deepcopy(existing_value)
            existing_value["forecaster_question_answers"] = recovered_answers
        try:
            plan_language_errors = validate_step04_plan_narrative(
                {"candidate_analysis_plan": group["candidate_context"]["analysis_plan"]}
            )
            if plan_language_errors:
                continue
            validate_step07_candidate_evidence(root, group, checks)
            validate_candidate_synthesis(group, checks, existing_value, root=root, guidance_quality_contract=True)
            language_errors = validate_step08_narrative_contract(existing_value, checks)
            if language_errors:
                continue
            observable_errors = _step08_observable_identity_errors(group, checks, existing_value)
            if observable_errors:
                continue
            alignment_narrative_errors = _step08_alignment_narrative_errors(root, group, checks, existing_value)
            if alignment_narrative_errors:
                continue
        except (Pass2PipelineError, ValueError, TypeError, KeyError):
            continue
        reusable_by_candidate[group_id] = existing_value
        if recovered_ids:
            print(
                f"STEP08_VERIFIED_QUANTITATIVE_RECOVERY candidate={group_id} "
                f"question_ids={','.join(recovered_ids)} mode=verified_cached_migration"
            )

    selected_ids = {str(group["candidate_id"]) for group in selected_groups}
    output_by_candidate = dict(reusable_by_candidate)
    if not resume:
        for selected_id in selected_ids:
            output_by_candidate.pop(selected_id, None)

    for group in selected_groups:
        group_id = str(group["candidate_id"])
        checks = _check_map(group_id, step07)
        required_checks = _required_check_ids(group)
        if set(checks) != required_checks:
            raise Pass2PipelineError("STEP07_RESULTS_MISSING", f"Step07 checks do not match active Step06 checks for {group['candidate_id']}.")
        plan_language_errors = validate_step04_plan_narrative({"candidate_analysis_plan": group["candidate_context"]["analysis_plan"]})
        if plan_language_errors:
            raise Pass2PipelineError(
                "NARRATIVE_LANGUAGE_INVALID",
                "Step04 분석계획 자연어가 새 한글 기준을 충족하지 않습니다. Step04부터 다시 생성해야 합니다. "
                + "; ".join(plan_language_errors[:8]),
            )
        validate_step07_candidate_evidence(root, group, checks)
        naming_contract = _candidate_name_grounding_contract(root, group, checks)
        input_fingerprint, provenance = _provenance(
            group,
            checks,
            str(step07.get("schema_version") or CHECK_RESULTS_VERSION),
            config,
            prompt_hash,
            schema_hash,
            candidate_name_grounding_contract=naming_contract,
        )

        if resume and group_id in reusable_by_candidate:
            output_by_candidate[group_id] = reusable_by_candidate[group_id]
            if on_candidate_reuse is not None:
                try:
                    on_candidate_reuse(group_id)
                except Exception:
                    pass
            continue

        no_usable_checks = not _usable_check_ids(checks)
        if no_usable_checks:
            raw_value = _deterministic_insufficient(root, group, checks)
        elif provider is not None:
            raw_value = _call(
                provider,
                root,
                config,
                _messages(
                    root,
                    group,
                    checks,
                    prompt_text,
                ),
            )
            raw_value = _attach_immutable_step08_context(group, raw_value)
            raw_value, signal_reference_fixes = _normalize_signal_evidence_check_ids(group, checks, raw_value)
            if signal_reference_fixes:
                print(
                    f"STEP08_SIGNAL_EVIDENCE_AUTOFIX candidate={group['candidate_id']} "
                    f"checks={','.join(signal_reference_fixes)}"
                )

            # Cross-field references cannot be expressed fully in JSON Schema.
            # Repair is atomic at the smallest complete semantic unit: hypothesis/
            # question interpretation block, signal+conclusion block, or narrative
            # conclusion block.  Individual LLM-authored fields are never patched.
            semantic_repair_counts: dict[str, int] = {}
            language_regeneration_attempts = 0
            observable_regeneration_attempts = 0
            alignment_narrative_regeneration_attempts = 0
            validation_passes = 0
            while True:
                validation_passes += 1
                if validation_passes > 12:
                    raise Pass2PipelineError(
                        "SYNTHESIS_REPAIR_INVALID",
                        f"{group['candidate_id']} Step08 semantic-unit regeneration exceeded bounded passes.",
                    )
                # Repair uniquely verified, quantity-only question answers
                # before asking the model to regenerate broader interpretation.
                raw_value, recovered_ids = _reconcile_new_quantitative_answers(checks, raw_value)
                if recovered_ids:
                    print(
                        f"STEP08_VERIFIED_QUANTITATIVE_RECOVERY candidate={group_id} "
                        f"question_ids={','.join(recovered_ids)} mode=verified_generated"
                    )
                try:
                    validate_candidate_synthesis(group, checks, raw_value, root=root, guidance_quality_contract=True)
                except Pass2PipelineError as exc:
                    if exc.code not in REPAIRABLE_SYNTHESIS_CODES:
                        raise
                    if exc.code == "QUANTITATIVE_RELATION_REF_INVALID":
                        autofixed_value, autofix_details = _autofix_relation_evidence_refs(group, checks, raw_value)
                        if autofixed_value is not None:
                            raw_value = autofixed_value
                            print(f"STEP08_REFERENCE_AUTOFIX candidate={group['candidate_id']} fixes={len(autofix_details)}")
                            if on_reference_autofix is not None:
                                try:
                                    on_reference_autofix(str(group["candidate_id"]), autofix_details)
                                except Exception:
                                    pass
                            continue

                    repair_limit = _SEMANTIC_REPAIR_LIMITS[exc.code]
                    repair_attempt = semantic_repair_counts.get(exc.code, 0) + 1
                    if repair_attempt > repair_limit:
                        raise
                    units = _step08_regeneration_units_for_error(exc.code)
                    repair_usage = None
                    for unit in units:
                        print(
                            f"STEP08_SEMANTIC_RETRY candidate={group['candidate_id']} "
                            f"code={exc.code} attempt={repair_attempt} unit={unit} mode=full_regeneration"
                        )
                        raw_value, repair_usage = _regenerate_step08_semantic_unit(
                            provider, root, config, group, checks, prompt_text,
                            raw_value, unit,
                            reason_code=exc.code, reason_message=str(exc),
                        )
                    semantic_repair_counts[exc.code] = repair_attempt
                    raw_value, semantic_signal_fixes = _normalize_signal_evidence_check_ids(group, checks, raw_value)
                    if semantic_signal_fixes:
                        print(
                            f"STEP08_SIGNAL_EVIDENCE_AUTOFIX candidate={group['candidate_id']} "
                            f"checks={','.join(semantic_signal_fixes)}"
                        )
                    if on_semantic_retry is not None:
                        try:
                            on_semantic_retry(str(group["candidate_id"]), exc.code, str(exc), repair_usage)
                        except Exception:
                            pass
                    continue

                coordinate_fixes = normalize_step08_response_coordinates(raw_value)
                if coordinate_fixes:
                    print(
                        f"STEP08_COORDINATE_NORMALIZED candidate={group['candidate_id']} "
                        f"fixes={coordinate_fixes}"
                    )

                language_errors = validate_step08_narrative_contract(raw_value, checks)
                if language_errors:
                    language_regeneration_attempts += 1
                    if language_regeneration_attempts > 2:
                        raise Pass2PipelineError("NARRATIVE_LANGUAGE_INVALID", "; ".join(language_errors[:12]))
                    units = _step08_regeneration_units_for_language_errors(language_errors)
                    rewrite_usage = None
                    for unit in units:
                        print(
                            f"STEP08_LANGUAGE_RETRY candidate={group['candidate_id']} "
                            f"violations={len(language_errors)} attempt={language_regeneration_attempts} "
                            f"unit={unit} mode=full_regeneration"
                        )
                        raw_value, rewrite_usage = _regenerate_step08_semantic_unit(
                            provider, root, config, group, checks, prompt_text,
                            raw_value, unit,
                            reason_code="NARRATIVE_LANGUAGE_INVALID",
                            reason_message=_step08_language_repair_reason(language_errors),
                        )
                    raw_value, language_signal_fixes = _normalize_signal_evidence_check_ids(group, checks, raw_value)
                    if language_signal_fixes:
                        print(
                            f"STEP08_SIGNAL_EVIDENCE_AUTOFIX candidate={group['candidate_id']} "
                            f"checks={','.join(language_signal_fixes)}"
                        )
                    if on_language_retry is not None:
                        try:
                            on_language_retry(str(group["candidate_id"]), language_errors, rewrite_usage)
                        except Exception:
                            pass
                    continue

                observable_errors = _step08_observable_identity_errors(group, checks, raw_value)
                if observable_errors:
                    observable_regeneration_attempts += 1
                    if observable_regeneration_attempts > 2:
                        raise Pass2PipelineError("OBSERVABLE_IDENTITY_INVALID", "; ".join(observable_errors[:12]))
                    units = _step08_regeneration_units_for_language_errors(observable_errors)
                    rewrite_usage = None
                    for unit in units:
                        print(
                            f"STEP08_OBSERVABLE_RETRY candidate={group['candidate_id']} "
                            f"violations={len(observable_errors)} attempt={observable_regeneration_attempts} "
                            f"unit={unit} mode=full_regeneration"
                        )
                        raw_value, rewrite_usage = _regenerate_step08_semantic_unit(
                            provider, root, config, group, checks, prompt_text,
                            raw_value, unit,
                            reason_code="OBSERVABLE_IDENTITY_INVALID",
                            reason_message=_step08_observable_repair_reason(observable_errors),
                        )
                    raw_value, observable_signal_fixes = _normalize_signal_evidence_check_ids(group, checks, raw_value)
                    if observable_signal_fixes:
                        print(
                            f"STEP08_SIGNAL_EVIDENCE_AUTOFIX candidate={group['candidate_id']} "
                            f"checks={','.join(observable_signal_fixes)}"
                        )
                    if on_observable_retry is not None:
                        try:
                            on_observable_retry(str(group["candidate_id"]), observable_errors, rewrite_usage)
                        except Exception:
                            pass
                    continue

                alignment_narrative_errors = _step08_alignment_narrative_errors(root, group, checks, raw_value)
                if alignment_narrative_errors:
                    alignment_narrative_regeneration_attempts += 1
                    if alignment_narrative_regeneration_attempts > 2:
                        raise Pass2PipelineError(
                            "ALIGNMENT_NARRATIVE_INVALID",
                            "; ".join(alignment_narrative_errors[:12]),
                        )
                    units = _step08_regeneration_units_for_alignment_errors(alignment_narrative_errors)
                    rewrite_usage = None
                    for unit in units:
                        print(
                            f"STEP08_ALIGNMENT_NARRATIVE_RETRY candidate={group['candidate_id']} "
                            f"violations={len(alignment_narrative_errors)} attempt={alignment_narrative_regeneration_attempts} "
                            f"unit={unit} mode=full_regeneration"
                        )
                        raw_value, rewrite_usage = _regenerate_step08_semantic_unit(
                            provider, root, config, group, checks, prompt_text,
                            raw_value, unit,
                            reason_code="ALIGNMENT_NARRATIVE_INVALID",
                            reason_message=_step08_alignment_narrative_repair_reason(alignment_narrative_errors),
                        )
                    raw_value, alignment_signal_fixes = _normalize_signal_evidence_check_ids(group, checks, raw_value)
                    if alignment_signal_fixes:
                        print(
                            f"STEP08_SIGNAL_EVIDENCE_AUTOFIX candidate={group['candidate_id']} "
                            f"checks={','.join(alignment_signal_fixes)}"
                        )
                    continue

                break
        else:
            raw_value = None
        if raw_value is not None:
            coordinate_fixes = normalize_step08_response_coordinates(raw_value)
            if coordinate_fixes:
                print(
                    f"STEP08_COORDINATE_NORMALIZED candidate={group['candidate_id']} "
                    f"fixes={coordinate_fixes} stage=final"
                )
        value = (
            _with_provenance(
                raw_value, input_fingerprint, provenance, group=group, checks=checks,
                candidate_name_grounding_contract=naming_contract,
            )
            if raw_value is not None
            else None
        )
        if value is None:
            raise Pass2PipelineError("PROVIDER_REQUIRED", "Step08 requires a provider when any check has usable evidence.")
        validate_candidate_synthesis(group, checks, value, root=root, guidance_quality_contract=True)
        language_errors = validate_step08_narrative_contract(value, checks)
        if language_errors:
            raise Pass2PipelineError("NARRATIVE_LANGUAGE_INVALID", "; ".join(language_errors[:12]))
        observable_errors = _step08_observable_identity_errors(group, checks, value)
        if observable_errors:
            raise Pass2PipelineError("OBSERVABLE_IDENTITY_INVALID", "; ".join(observable_errors[:12]))
        alignment_narrative_errors = _step08_alignment_narrative_errors(root, group, checks, value)
        if alignment_narrative_errors:
            raise Pass2PipelineError(
                "ALIGNMENT_NARRATIVE_INVALID", "; ".join(alignment_narrative_errors[:12])
            )
        output_by_candidate[group_id] = value

        # Candidate-level atomic checkpoint.  The final Step08 schema permits a
        # partial candidate array; Step09 independently requires every active
        # candidate, so an interrupted Step08 cannot be published accidentally.
        checkpoint = {
            "schema_version": RESULT_VERSION,
            "init": init,
            "candidates": [
                output_by_candidate[str(active_group["candidate_id"])]
                for active_group in active_groups
                if str(active_group["candidate_id"]) in output_by_candidate
            ],
        }
        validate_schema(root, RESULT_SCHEMA, checkpoint)
        write_validated_atomic(root, output_path, checkpoint, schema_name=RESULT_SCHEMA)
        if on_candidate_checkpoint is not None:
            try:
                on_candidate_checkpoint(group_id, len(checkpoint["candidates"]), len(active_groups))
            except Exception:
                pass

    output = [output_by_candidate[str(group["candidate_id"])] for group in active_groups if str(group["candidate_id"]) in output_by_candidate]
    document = {"schema_version": RESULT_VERSION, "init": init, "candidates": output}
    validate_schema(root, RESULT_SCHEMA, document)
    write_validated_atomic(root, output_path, document, schema_name=RESULT_SCHEMA)
    # Re-read the actual persisted document. STEP08_OK must not be emitted for
    # a checkpoint with a missing verified comparison, even if serialization or
    # a later migration path altered the in-memory object.
    persisted = load_json_object(output_path, code="STEP08_RESULT_INVALID")
    validate_schema(root, RESULT_SCHEMA, persisted)
    persisted_by_id = {
        str(item.get("candidate_id")): item
        for item in persisted.get("candidates", [])
        if isinstance(item, dict)
    }
    if len(persisted_by_id) != len(persisted.get("candidates", [])):
        raise Pass2PipelineError("STEP08_RESULT_INVALID", "Persisted Step08 candidate IDs are duplicated or malformed.")
    for group in active_groups:
        group_id = str(group["candidate_id"])
        if group_id not in persisted_by_id:
            if candidate_id is None:
                raise Pass2PipelineError("STEP08_RESULT_INVALID", f"Final Step08 document omitted {group_id}.")
            continue
        checks = _check_map(group_id, step07)
        errors = question_answer_errors(
            persisted_by_id[group_id].get("forecaster_question_answers", []),
            _verified_quantitative_relations(checks),
        )
        if errors:
            raise Pass2PipelineError(
                "FORECASTER_QUANTITATIVE_ANSWER_INVALID",
                f"Persisted Step08 {group_id}: " + "; ".join(errors[:8]),
            )
    return document
