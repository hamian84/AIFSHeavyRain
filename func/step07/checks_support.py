"""Step step07 support responsibility; original function bodies preserved."""
from __future__ import annotations
from . import checks_context as _step_context

# Original func/step07/checks.py:185
def _is_system_direct_precipitation_anchor_check(specification: dict[str, Any]) -> bool:
    intent = specification.get("intent_contract")
    return bool(
        isinstance(intent, dict)
        and intent.get("direct_precipitation_anchor") is True
        and str(intent.get("source_check_key") or "")
        == SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY
    )

# Original func/step07/checks.py:288
def _spatial_localization_enabled(specification: dict[str, Any] | None) -> bool:
    """Return whether a positive precip/environment overlap must name a place."""
    return alignment_requires_spatial_localization(specification)

# Original func/step07/checks.py:459
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

# Original func/step07/checks.py:490
def _empty_precipitation_motion(*, enabled: bool) -> dict[str, Any]:
    return {
        "classification": "indeterminate" if enabled else "not_applicable",
        "origin_region_id": None,
        "destination_region_id": None,
        "confidence": None,
    }

# Original func/step07/checks.py:1807
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

# Original func/step07/checks.py:1820
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

# Original func/step07/checks.py:1941
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

# Original func/step07/checks.py:1957
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

# Original func/step07/checks.py:2017
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

# Original func/step07/checks.py:2640
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

# Original func/step07/checks.py:2690
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

# Original func/step07/checks.py:2838
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

# Original func/step07/checks.py:4635
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

# Original func/step07/checks.py:4676
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
