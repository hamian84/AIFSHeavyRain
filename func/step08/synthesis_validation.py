"""Step step08 validation responsibility; original function bodies preserved."""
from __future__ import annotations
from . import synthesis_context as _step_context

# Original func/step08/synthesis.py:236
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

# Original func/step08/synthesis.py:267
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

# Original func/step08/synthesis.py:342
def validate_step08_narrative_contract(value: dict[str, Any], checks: dict[str, dict[str, Any]]) -> list[str]:
    """Validate Korean prose plus evidence-availability and movement wording against Step07."""
    return [
        *validate_step08_response_narrative(value, include_source_text=True),
        *_step08_exclusion_narrative_errors(value, checks),
        *_step08_motion_narrative_errors(value, checks),
    ]

# Original func/step08/synthesis.py:351
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

# Original func/step08/synthesis.py:377
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

# Original func/step08/synthesis.py:391
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

# Original func/step08/synthesis.py:419
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

# Original func/step08/synthesis.py:488
def _is_observable_limit_statement(sentence: str) -> bool:
    """Return whether prose limits a conclusion instead of asserting an observation."""
    return bool(
        re.search(
            r"(?:판단|확인|평가|해석|단정|확정).{0,24}(?:어렵|불가|할 수 없|못하|제한)"
            r"|(?:불확실|근거 부족|자료 부족|자료가 부족|자료가 없|자료 없음|자료 없이|제공되지|미제공)",
            sentence,
        )
    )

# Original func/step08/synthesis.py:499
def _claims_observable_data_shortage(sentence: str) -> bool:
    """Return whether prose says the detailed product itself is absent or insufficient."""
    return bool(
        re.search(
            r"자료\s*(?:가\s*)?(?:부족|없)|자료\s*없이|자료가\s*제공되지|자료\s*미제공|미제공된\s*자료",
            sentence,
        )
    )

# Original func/step08/synthesis.py:509
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

# Original func/step08/synthesis.py:538
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

# Original func/step08/synthesis.py:650
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

# Original func/step08/synthesis.py:680
def _alignment_relation_is_negated(sentence: str, relation: re.Match[str]) -> bool:
    tail = sentence[relation.start(): relation.end() + 36]
    return bool(_ALIGNMENT_NEGATION_AFTER_RELATION_PATTERN.search(tail))

# Original func/step08/synthesis.py:685
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

# Original func/step08/synthesis.py:732
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

# Original func/step08/synthesis.py:805
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

# Original func/step08/synthesis.py:966
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

# Original func/step08/synthesis.py:1046
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

# Original func/step08/synthesis.py:1109
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

# Original func/step08/synthesis.py:1128
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

# Original func/step08/synthesis.py:1141
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

# Original func/step08/synthesis.py:1335
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

# Original func/step08/synthesis.py:1419
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

# Original func/step08/synthesis.py:1685
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

# Original func/step08/synthesis.py:2147
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

# Original func/step08/synthesis.py:2182
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

# Original func/step08/synthesis.py:2220
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

# Original func/step08/synthesis.py:2267
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

# Original func/step08/synthesis.py:2483
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

# Original func/step08/synthesis.py:2536
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

# Original func/step08/synthesis.py:2604
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

# Original func/step08/synthesis.py:2687
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

# Original func/step08/synthesis.py:3156
def _candidate_name_compact_region_name(value: Any) -> str:
    name = str(value or "").strip()
    if name.endswith(" 광역"):
        name = name[:-3].rstrip()
    return name

# Original func/step08/synthesis.py:3163
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

# Original func/step08/synthesis.py:3193
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

# Original func/step08/synthesis.py:3295
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

# Original func/step08/synthesis.py:3309
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

# Original func/step08/synthesis.py:3332
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
                "environment_support_status가 not_confirmed 또는 insufficient_evidence이면 environment_persistence_status는 반드시 not_applicable이고 "
                "environment_persistence_evidence_check_ids는 빈 배열이어야 합니다. 이 경우 temporal Check가 존재하더라도 지속성 판정을 하지 마십시오. "
                "effect_by_check_id와 supporting/limiting/mixed/context 목록은 validator와 동일한 결정론적 참조 계약입니다. "
                "mixed는 지지와 제한이 같은 비중이라는 뜻이 아니라, 하나의 시간변화 Check 안에 양의 지속 근거와 명시적 제한 근거가 함께 있다는 뜻입니다. "
                "environment_support_status가 confirmed 또는 partial인 경우, 시간변화에 대한 usable 근거가 없거나 판단 가능한 supporting/limiting/mixed 근거가 부족하면 insufficient_evidence를 사용하십시오. "
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

# Original func/step08/synthesis.py:3620
def _step08_sentence_count(text: str) -> int:
    """Count prose sentences conservatively for the Step08 display contract."""
    chunks = [
        item.strip()
        for item in re.split(r"(?<=[.!?])\s+|[\r\n]+", text.strip())
        if item.strip()
    ]
    return len(chunks) if chunks else (1 if text.strip() else 0)

# Original func/step08/synthesis.py:3630
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

# Original func/step08/synthesis.py:4140
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

# Original func/step08/synthesis.py:4177
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



# Validate the received LLM JSON without invoking any provider.
def _decode_validate_synthesis_provider_response(root: Path, response, canonical, api_family: str) -> dict[str, Any]:
    content = normalize_openai_transport_json(response.content, canonical) if api_family == "responses" else response.content
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise Pass2PipelineError("PROVIDER_RESULT_INVALID", "Step08 provider response is not JSON.") from exc
    if not isinstance(value, dict):
        raise Pass2PipelineError("PROVIDER_RESULT_INVALID", "Step08 provider response root must be an object.")
    validate_schema(root, SYNTHESIS_SCHEMA, value, code="PROVIDER_RESULT_INVALID")
    return value
