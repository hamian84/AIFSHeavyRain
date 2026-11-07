"""Step step08 workflow responsibility; original function bodies preserved."""
from __future__ import annotations
from . import synthesis_context as _step_context

# Original func/step08/synthesis.py:190
def step08_output_path(root: Path, init: str) -> Path:
    return runtime_path(root, "daio", init, "step08", "pass2_result.json")

# Original func/step08/synthesis.py:215
def _required_check_ids(group: dict[str, Any]) -> set[str]:
    checks = group.get("check_evidence")
    if not isinstance(checks, list):
        raise Pass2PipelineError("STEP06_PACKAGE_INVALID", f"{group.get('candidate_id')} has no check_evidence.")
    return {str(item["check_id"]) for item in checks if isinstance(item, dict) and item.get("check_id")}

# Original func/step08/synthesis.py:893
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

# Original func/step08/synthesis.py:909
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

# Original func/step08/synthesis.py:940
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

# Original func/step08/synthesis.py:1204
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

# Original func/step08/synthesis.py:1228
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

# Original func/step08/synthesis.py:2308
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

# Original func/step08/synthesis.py:3092
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

# Original func/step08/synthesis.py:3490
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

# Original func/step08/synthesis.py:3523
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

# Original func/step08/synthesis.py:3575
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

# Original func/step08/synthesis.py:4103
def dry_run_summary(root: Path, init: str, *, candidate_id: str | None = None) -> dict[str, Any]:
    package = load_step06_package(root, init)
    step07 = load_step07_results(root, init)
    rows = []
    for group in active_candidate_groups(package, candidate_id):
        checks = _check_map(str(group["candidate_id"]), step07)
        rows.append({"candidate_id": group["candidate_id"], "check_count": len(checks), "usable_check_count": len(_usable_check_ids(checks)), "verified_quantitative_relation_count": len(_verified_quantitative_relations(checks)), "api_required": bool(_usable_check_ids(checks)), "forecaster_question_ids": [item["forecaster_question_id"] for item in _question_rows(str(group["candidate_id"]), group["candidate_context"]["analysis_plan"])]})
    validate_schema(root, RESULT_SCHEMA, {"schema_version": RESULT_VERSION, "init": init, "candidates": []})
    return {"candidates": rows, "expected_api_calls": sum(1 for row in rows if row["api_required"])}

# Original func/step08/synthesis.py:4211
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
