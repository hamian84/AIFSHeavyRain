"""Step step08 analysis responsibility; original function bodies preserved."""
from __future__ import annotations
from . import synthesis_context as _step_context

# Original func/step08/synthesis.py:1020
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

# Original func/step08/synthesis.py:1288
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

# Original func/step08/synthesis.py:1300
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

# Original func/step08/synthesis.py:1321
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

# Original func/step08/synthesis.py:1529
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

# Original func/step08/synthesis.py:1562
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

# Original func/step08/synthesis.py:1579
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

# Original func/step08/synthesis.py:1764
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

# Original func/step08/synthesis.py:1832
def _call(provider: AIProvider, root: Path, config: dict[str, str], messages: list[dict[str, Any]]) -> dict[str, Any]:
    canonical = load_schema(root, SYNTHESIS_SCHEMA)
    schema = build_openai_transport_schema(canonical) if provider.api_family == "responses" else canonical
    response = provider.complete(messages=messages, model=config["model"], max_tokens=10000, schema=schema, schema_name="AIFSPass2CandidateSynthesis", structured_output_mode="auto", image_detail=config["image_detail"], reasoning_effort=config["reasoning_effort"])
    return _decode_validate_synthesis_provider_response(root, response, canonical, provider.api_family)

# Original func/step08/synthesis.py:1864
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

# Original func/step08/synthesis.py:1890
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

# Original func/step08/synthesis.py:1906
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
            "environment_support_status가 not_confirmed 또는 insufficient_evidence이면 environment_persistence_status=not_applicable 및 "
            "environment_persistence_evidence_check_ids=[]를 동시에 적용하십시오. 환경이 confirmed/partial이면 not_applicable을 금지하고 "
            "시계열 근거가 부족할 경우 insufficient_evidence를 사용하십시오. "
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

# Original func/step08/synthesis.py:2380
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

# Original func/step08/synthesis.py:2453
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

# Original func/step08/synthesis.py:2570
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
