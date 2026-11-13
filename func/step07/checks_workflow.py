"""Step step07 workflow responsibility; original function bodies preserved."""
from __future__ import annotations
from . import checks_context as _step_context

# Original func/step07/checks.py:711
def step07_minority_report_path(root: Path, init: str) -> Path:
    return runtime_path(root, "daio", init, "step07", "llm_wiki_minority_report.json")

# Original func/step07/checks.py:1768
def step07_output_path(root: Path, init: str) -> Path:
    return runtime_path(root, "daio", init, "step07", "pass2_check_results.json")

# Original func/step07/checks.py:1772
def load_step06_package(root: Path, init: str) -> dict[str, Any]:
    path = runtime_path(root, "daio", init, "step06", "pass2_input_package.json")
    package = load_json_object(path, code="STEP06_PACKAGE_MISSING")
    try:
        validate_pass2_package(root, package)
    except Pass2PackageError as exc:
        raise Pass2PipelineError(exc.code, str(exc)) from exc
    return package

# Original func/step07/checks.py:1782
def load_step07_results(root: Path, init: str) -> dict[str, Any]:
    """Load the single validated Step07 checkpoint for Step08/09 consumers."""
    value = load_json_object(step07_output_path(root, init), code="STEP07_RESULTS_MISSING")
    validate_schema(root, CHECK_RESULTS_SCHEMA, value)
    return value

# Original func/step07/checks.py:1789
def _provider_metadata(config: dict[str, str], prompt_hash: str, schema_hash: str) -> dict[str, str]:
    return {
        "provider": config["provider"],
        "model": config["model"],
        "reasoning_effort": config["reasoning_effort"],
        "image_detail": config["image_detail"],
        "prompt_sha256": prompt_hash,
        "schema_sha256": schema_hash,
    }

# Original func/step07/checks.py:1800
def _entry_index(group: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = group.get("analysis_data_requests")
    if not isinstance(entries, list):
        raise Pass2PipelineError("STEP06_PACKAGE_INVALID", "candidate has no analysis_data_requests.")
    return {str(entry["request"]["request_id"]): entry for entry in entries if isinstance(entry, dict) and isinstance(entry.get("request"), dict)}

# Original func/step07/checks.py:1837
def _check_fingerprint(
    root: Path, group: dict[str, Any],
    check: dict[str, Any], check_specification: dict[str, Any], entries: list[dict[str, Any]],
    config: dict[str, str], prompt_hash: str, schema_hash: str,
    *, include_majority_narrative_contract: bool = True,
) -> str:
    request_state: list[dict[str, Any]] = []
    for entry in entries:
        step05 = entry["step05"]
        request_state.append({
            "request": entry["request"],
            "step05_result": step05.get("result"),
            "step05_status": step05.get("status"),
            "images": [
                {"path": image.get("path"), "sha256": image.get("sha256")}
                for image in step05.get("generated_images", [])
            ],
        })
    fingerprint_payload = {
        "spatial_scope_context": _spatial_scope_context(group, check, entries),
        "operational_region_naming_context": _candidate_operational_region_naming_context(root, group),
        "spatial_localization_context": _candidate_operational_localization_context(root, group, check_specification),
        "precipitation_motion_context": _precipitation_motion_context(root, group, check_specification),
        "check_spec": {
            **{key: check.get(key) for key in (
                "check_id", "diagnostic_id", "expected_signal", "decision_relevance",
                "request_ids", "available_request_ids", "missing_request_ids", "evidence_complete",
                "check_scope", "required_signal_components", "available_signal_components",
                "missing_signal_components", "allowed_quantitative_forecast_hours",
                "source_intent_hash", "intent_contract_version", "source_atomic_claim_hash",
                "atomic_claim_contract_version", "atomic_claim_validation_status",
            )},
            **{key: check_specification.get(key) for key in (
                "product_id", "diagnostic_id", "variable_role", "analysis_methods",
                "check_scope", "required_signal_components",
            )},
        },
        "quantitative_validation_contract_version": QUANTITATIVE_VALIDATION_CONTRACT_VERSION,
        "resolved_requests": request_state,
        "prompt_sha256": prompt_hash,
        "output_schema_sha256": schema_hash,
        "provider": config,
        "ensemble_contract_version": ENSEMBLE_CONTRACT_VERSION,
        "ensemble_result_selection_version": ENSEMBLE_RESULT_SELECTION_VERSION,
        "ensemble_member_count": ENSEMBLE_MEMBER_COUNT,
    }
    # Only re-evaluate affected specific checks; unrelated completed results
    # keep their original fingerprint and can be resumed unchanged.
    if _single_specific_quantitative_atomic_claim(check_specification) is not None:
        fingerprint_payload["specific_selector_guidance_version"] = SPECIFIC_SELECTOR_GUIDANCE_VERSION
    if _single_majority_convergence_not_strengthen_claim(check_specification) is not None:
        # Preserve the existing not_strengthen checkpoint contract.
        fingerprint_payload["majority_quantitative_binding_version"] = MAJORITY_QUANTITATIVE_BINDING_VERSION
        if include_majority_narrative_contract:
            fingerprint_payload["majority_narrative_contract_version"] = MAJORITY_NARRATIVE_CONTRACT_VERSION
    elif _majority_convergence_binding_scope(check_specification, check) is not None:
        # Re-evaluate only new strengthen bindings; do not invalidate all Checks.
        fingerprint_payload["majority_strengthen_binding_version"] = MAJORITY_STRENGTHEN_BINDING_VERSION
    anchor_context = _system_direct_precipitation_anchor_context(
        group, check, check_specification, entries
    )
    if anchor_context is not None:
        fingerprint_payload["system_direct_precipitation_anchor_context"] = anchor_context
        fingerprint_payload["system_direct_precipitation_assessment_contract_version"] = (
            SYSTEM_DIRECT_PRECIPITATION_ASSESSMENT_CONTRACT_VERSION
        )
    return canonical_hash(fingerprint_payload)

# Original func/step07/checks.py:2729
def _bind_majority_convergence_quantitative_evidence(
    check_specification: dict[str, Any],
    check: dict[str, Any],
    entries: list[dict[str, Any]],
    assessment: dict[str, Any],
) -> dict[str, int] | None:
    """Bind numeric fields for the exact Step04 convergence-majority contract.

    This runs AFTER the independent model assessment. The hypothesis selects
    the feature, two forecast hours and candidate-overlap region scope; Step05
    selects the actual regions and numbers. The model remains responsible for
    assessment and meteorological prose, which existing validators compare
    with the deterministic evidence. Never repair identifiers by guessing.

    For this exact contract only, numerical claims and pairwise relations are
    *system-generated*, not AI-authored. The result schema is unchanged.
    """
    binding = _majority_convergence_binding_scope(check_specification, check)
    if binding is None:
        return None
    _claim, scope = binding
    feature_id = str(scope["feature_id"])
    statistic = str(scope["statistic"])
    hours = list(scope["forecast_hours"])
    namespace = _allowed_quantitative_selector_rows(check, entries)
    # Build the union of eligible cells. A full two-hour series must come from
    # one concrete Step05 request; otherwise source identity is ambiguous.
    cells_by_request: dict[str, set[tuple[str, int]]] = {}
    all_regions: set[str] = set()
    for item in namespace:
        if (
            item["feature_id"] != feature_id
            or item["forecast_hour"] not in hours
            or statistic not in item["statistics"]
        ):
            continue
        request_id = str(item["request_id"])
        region_id = str(item["region_id"])
        all_regions.add(region_id)
        cells_by_request.setdefault(request_id, set()).add((region_id, int(item["forecast_hour"])))
    if not all_regions:
        raise Pass2PipelineError(
            "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
            f"{check.get('check_id')} majority contract has no allowed Step05 convergence values.",
        )
    expected_cells = {(region_id, hour) for region_id in all_regions for hour in hours}
    complete_requests = sorted(
        request_id for request_id, cells in cells_by_request.items()
        if expected_cells.issubset(cells)
    )
    if len(complete_requests) != 1:
        if _claim.get("quantitative_scope") is None:
            # A legacy shadow claim cannot establish an unambiguous source
            # contract; leave the original AI-validation path unchanged.
            return None
        raise Pass2PipelineError(
            "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
            f"{check.get('check_id')} majority contract requires exactly one Step05 request "
            f"covering all {len(all_regions)} regions at F{hours[0]:03d}/F{hours[1]:03d}; "
            f"complete_request_ids={complete_requests}.",
        )
    request_id = complete_requests[0]
    selectors = [
        {
            "request_id": request_id,
            "feature_id": feature_id,
            "forecast_hour": hour,
            "region_id": region_id,
            "statistic": statistic,
        }
        for region_id in sorted(all_regions) for hour in hours
    ]
    # Preserve the original strict selector and arithmetic resolvers. No
    # independently obtained values and no trusted AI arithmetic are used.
    points = resolve_quantitative_claims(check, entries, selectors)
    if len(points) != len(selectors):
        raise Pass2PipelineError(
            "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
            f"{check.get('check_id')} Step05 resolved an incomplete majority selector set.",
        )
    relations: list[dict[str, Any]] = []
    for index, region_id in enumerate(sorted(all_regions), 1):
        pair = selectors[(index - 1) * 2:index * 2]
        values = points[(index - 1) * 2:index * 2]
        probe = {
            "relation_id": f"SYS_CONV_{index:03d}",
            "relation_type": "pairwise_comparison",
            "selectors": pair,
            "claimed_relation": "approximately_equal",  # overwritten from Step05
            "claimed_forecast_hour": None,
        }
        try:
            computed = compute_quantitative_relation(probe, values)
        except QuantitativeConsistencyError as exc:
            raise Pass2PipelineError(
                "QUANTITATIVE_SCOPE_CONTRACT_INVALID",
                f"{check.get('check_id')} cannot compute region={region_id}: {exc}",
            ) from exc
        probe["claimed_relation"] = computed["computed_relation"]
        relations.append(probe)
    original_claim_count = len(assessment.get("quantitative_claims", [])) if isinstance(assessment.get("quantitative_claims"), list) else 0
    original_relation_count = len(assessment.get("quantitative_relations", [])) if isinstance(assessment.get("quantitative_relations"), list) else 0
    assessment["quantitative_claims"] = selectors
    assessment["quantitative_relations"] = relations
    return {
        "regions": len(all_regions),
        "source_claims": len(selectors),
        "source_relations": len(relations),
        "model_claims_replaced": original_claim_count,
        "model_relations_replaced": original_relation_count,
    }

# Original func/step07/checks.py:3450
def quantitative_failure_uncertainty(reasons: list[str]) -> str:
    """Describe the failure type without conflating missing relations with a mismatch."""
    reason_set = set(reasons)
    if reason_set == {"QUANTITATIVE_RELATION_REQUIRED"}:
        return "필요한 정량 비교 관계가 충분히 검증되지 않아 해당 정량 근거는 종합판단에서 제외됩니다."
    if reason_set == {"QUANTITATIVE_RELATION_MISMATCH"}:
        return "정량자료의 비교 관계와 판독 결과가 일치하지 않아 해당 정량 근거는 종합판단에서 제외됩니다."
    if reason_set == {"QUANTITATIVE_FEATURE_USE_INVALID"}:
        return "시간 변화 강수 판독에 필요한 정량 비교 관계가 없어 해당 정량 근거는 종합판단에서 제외됩니다."
    return "정량 근거의 검증 조건을 충족하지 않아 해당 정량 근거는 종합판단에서 제외됩니다."

# Original func/step07/checks.py:4039
def _step07_language_error_check_ids(
    response: dict[str, Any], language_errors: list[str],
) -> list[str]:
    """Return Check IDs whose complete assessment must be regenerated."""
    assessments = response.get("assessments")
    if not isinstance(assessments, list):
        raise Pass2PipelineError("NARRATIVE_REGENERATION_INVALID", "Step07 assessment structure is invalid.")
    targets: list[str] = []
    seen: set[str] = set()
    for error in language_errors:
        path = str(error).split(":", 1)[0].strip()
        match = re.match(r"assessments\[(\d+)\]", path)
        if not match:
            continue
        index = int(match.group(1))
        if index >= len(assessments) or not isinstance(assessments[index], dict):
            continue
        check_id = str(assessments[index].get("check_id") or "")
        if check_id and check_id not in seen:
            seen.add(check_id)
            targets.append(check_id)
    if not targets:
        raise Pass2PipelineError(
            "NARRATIVE_REGENERATION_INVALID",
            "Step07 language error could not be mapped to a complete Check assessment.",
        )
    return targets

# Original func/step07/checks.py:4161
def _step07_failing_check_ids(
    root: Path, init: str, response: dict[str, Any],
    rows: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str]],
    metadata: dict[str, str],
    specifications_by_candidate: dict[str, dict[str, dict[str, Any]]],
) -> list[str]:
    """Localize a bundle validation failure to the smallest complete Check units."""
    assessments = {
        str(item.get("check_id")): item
        for item in response.get("assessments", [])
        if isinstance(item, dict)
    }
    targets: list[str] = []
    for row in rows:
        check_id = str(row[1]["check_id"])
        assessment = assessments.get(check_id)
        if assessment is None:
            targets.append(check_id)
            continue
        try:
            _bundle_result_values(
                root=root,
                init=init,
                response={"assessments": [assessment]},
                rows=[row],
                metadata=metadata,
                specifications_by_candidate=specifications_by_candidate,
            )
        except Pass2PipelineError:
            targets.append(check_id)
    return targets or [str(row[1]["check_id"]) for row in rows]

# Original func/step07/checks.py:4467
def _deduplicate_quantitative_selectors(selectors: Any) -> tuple[Any, int]:
    """Remove only exact, structurally valid duplicate quantitative selectors.

    Malformed selectors are intentionally preserved so the existing strict resolver
    can reject them with the original validation error.
    """
    if not isinstance(selectors, list):
        return selectors, 0
    seen: set[tuple[str, str, int, str, str]] = set()
    normalized: list[Any] = []
    removed = 0
    for selector in selectors:
        if not isinstance(selector, dict):
            normalized.append(selector)
            continue
        request_id = selector.get("request_id")
        feature_id = selector.get("feature_id")
        forecast_hour = selector.get("forecast_hour")
        region_id = selector.get("region_id")
        statistic = selector.get("statistic")
        valid_key = (
            all(isinstance(value, str) and value for value in (request_id, feature_id, region_id))
            and isinstance(forecast_hour, int)
            and not isinstance(forecast_hour, bool)
            and forecast_hour >= 0
            and statistic in QUANTITATIVE_STATISTICS
        )
        if not valid_key:
            normalized.append(selector)
            continue
        selector_key = (request_id, feature_id, forecast_hour, region_id, statistic)
        if selector_key in seen:
            removed += 1
            continue
        seen.add(selector_key)
        normalized.append(selector)
    return normalized, removed

# Original func/step07/checks.py:4507
def _ensure_relation_selectors_declared_in_claims(
    claims: Any, relations: Any, *, check: dict[str, Any], entries: list[dict[str, Any]],
) -> tuple[Any, int]:
    """Add exact quantitative relation selectors missing from quantitative_claims.

    This is reference bookkeeping, not a meteorological or arithmetic decision.
    The LLM has already selected every relation point and claimed relation. Python
    only ensures that the same exact selector tuples are also declared in the
    companion quantitative_claims array, as required by the Step07 contract.
    Invalid or malformed selectors are not repaired here and remain subject to the
    existing strict resolver and namespace validation.
    """
    if not isinstance(claims, list) or not isinstance(relations, list):
        return claims, 0

    normalized = list(claims)
    existing: set[tuple[str, str, int, str, str]] = set()
    for selector in claims:
        if not isinstance(selector, dict):
            continue
        request_id = selector.get("request_id")
        feature_id = selector.get("feature_id")
        forecast_hour = selector.get("forecast_hour")
        region_id = selector.get("region_id")
        statistic = selector.get("statistic")
        if (
            all(isinstance(value, str) and value for value in (request_id, feature_id, region_id))
            and isinstance(forecast_hour, int)
            and not isinstance(forecast_hour, bool)
            and forecast_hour >= 0
            and statistic in QUANTITATIVE_STATISTICS
        ):
            existing.add((request_id, feature_id, forecast_hour, region_id, statistic))

    added = 0
    for relation in relations:
        selectors = relation.get("selectors") if isinstance(relation, dict) else None
        if not isinstance(selectors, list):
            continue
        for selector in selectors:
            if not isinstance(selector, dict):
                continue
            request_id = selector.get("request_id")
            feature_id = selector.get("feature_id")
            forecast_hour = selector.get("forecast_hour")
            region_id = selector.get("region_id")
            statistic = selector.get("statistic")
            valid = (
                all(isinstance(value, str) and value for value in (request_id, feature_id, region_id))
                and isinstance(forecast_hour, int)
                and not isinstance(forecast_hour, bool)
                and forecast_hour >= 0
                and statistic in QUANTITATIVE_STATISTICS
            )
            if not valid:
                continue
            if _quantitative_selector_namespace_issues(check, entries, [selector], source="relation_selector"):
                raise Pass2PipelineError(
                    "QUANTITATIVE_CLAIM_INVALID",
                    f"{check.get('check_id')} relation selector is not an exact Step05 quantitative tuple: {selector}",
                )
            key = (request_id, feature_id, forecast_hour, region_id, statistic)
            if key in existing:
                continue
            normalized.append({
                "request_id": request_id,
                "feature_id": feature_id,
                "forecast_hour": forecast_hour,
                "region_id": region_id,
                "statistic": statistic,
            })
            existing.add(key)
            added += 1
    return normalized, added

# Original func/step07/checks.py:4795
def _append_narrative_annotation(statement: str, label: str, values: list[str]) -> str:
    """Append a deterministic location annotation without rewriting meteorology."""
    clean = [str(value).strip() for value in values if str(value).strip()]
    if not clean:
        return statement
    if label in statement:
        return statement
    base = str(statement or "").strip()
    if not base:
        return base
    if base.endswith("."):
        base = base[:-1].rstrip()
    return f"{base} ({label}: {', '.join(clean)})."

# Original func/step07/checks.py:4810
def _direct_precipitation_regions_for_statement(
    context: dict[str, Any], statement: str,
) -> list[str]:
    """Resolve the exact supported direct-precipitation region names for a finding.

    Prefer an explicitly mentioned F-hour in the sentence.  If the sentence
    summarizes multiple/no peak hours, use the stable union in forecast-hour
    order.  The values all come from deterministic Step05 operational-scope
    statistics and are therefore naming evidence, not an LLM spatial guess.
    """
    by_hour = context.get("threshold_met_regions_by_forecast_hour", {})
    if not isinstance(by_hour, dict):
        return []
    mentioned = {int(token) for token in re.findall(r"F(\d{3})", str(statement or ""))}
    rows: list[dict[str, Any]] = []
    for hour_key, regions in by_hour.items():
        if not isinstance(regions, list):
            continue
        match = re.fullmatch(r"F(\d{3})", str(hour_key))
        hour = int(match.group(1)) if match else None
        if mentioned and hour not in mentioned:
            continue
        rows.extend(region for region in regions if isinstance(region, dict))
    if mentioned and not rows:
        # Do not silently attach regions from a different forecast hour.
        return []
    if not mentioned:
        for hour_key in sorted(by_hour):
            regions = by_hour.get(hour_key)
            if isinstance(regions, list):
                rows.extend(region for region in regions if isinstance(region, dict))
    names: list[str] = []
    for row in rows:
        name = str(row.get("region_name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names

# Original func/step07/checks.py:4849
def _enrich_system_anchor_region_narrative(
    group: dict[str, Any],
    check: dict[str, Any],
    specification: dict[str, Any],
    entries: list[dict[str, Any]],
    assessment: dict[str, Any],
) -> int:
    """Deterministically add supported region names to positive anchor facts.

    This function intentionally does *not* change assessment, evidence refs,
    quantitative claims, or any meteorological conclusion.  It only appends the
    exact operational regions already established by Step05 scope statistics.
    """
    if not _is_system_direct_precipitation_anchor_check(specification):
        return 0
    if str(assessment.get("assessment") or "") not in {"observed", "partially_observed"}:
        return 0
    context = _system_direct_precipitation_anchor_context(group, check, specification, entries)
    if not isinstance(context, dict) or not context.get("threshold_met"):
        return 0
    findings = assessment.get("findings")
    if not isinstance(findings, list):
        return 0
    changed = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        statement = str(finding.get("statement") or "").strip()
        if "강수" not in statement or REGION_NARRATIVE_DIRECT_ANNOTATION in statement:
            continue
        names = _direct_precipitation_regions_for_statement(context, statement)
        if not names:
            continue
        updated = _append_narrative_annotation(statement, REGION_NARRATIVE_DIRECT_ANNOTATION, names)
        if updated != statement:
            finding["statement"] = updated
            changed += 1
    return changed

# Original func/step07/checks.py:4889
def _localized_region_labels(
    root: Path, group: dict[str, Any], localization: dict[str, Any],
) -> list[str]:
    naming = _candidate_operational_region_naming_context(root, group)
    name_by_id = {
        str(row.get("region_id") or ""): str(row.get("name_ko") or "").strip()
        for row in naming.get("regions", [])
        if isinstance(row, dict)
    }
    labels: list[str] = []
    for row in localization.get("regions", []):
        if not isinstance(row, dict):
            continue
        name = name_by_id.get(str(row.get("region_id") or ""), "")
        if not name:
            continue
        sector = str(row.get("relative_sector") or "unspecified")
        sector_ko = SPATIAL_LOCALIZATION_SECTOR_LABEL_KO.get(sector, "")
        label = f"{name} {sector_ko}".strip()
        if label not in labels:
            labels.append(label)
    return labels

# Original func/step07/checks.py:4913
def _enrich_localized_alignment_region_narrative(
    root: Path,
    group: dict[str, Any],
    check: dict[str, Any],
    specification: dict[str, Any],
    assessment: dict[str, Any],
) -> int:
    """Append validated alignment locations to overlap findings.

    The annotation is added only after ``spatial_localization`` itself has been
    model-authored.  Contract validation later verifies that localization
    against the actual image evidence, so this step does not invent a place.
    """
    localization = assessment.get("spatial_localization")
    if not isinstance(localization, dict) or localization.get("status") != "localized":
        return 0
    labels = _localized_region_labels(root, group, localization)
    if not labels:
        return 0
    findings = assessment.get("findings")
    if not isinstance(findings, list):
        return 0
    relation_tokens = ("겹", "결합", "중첩", "함께 나타", "같은 위치")
    changed = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        statement = str(finding.get("statement") or "").strip()
        if not statement or REGION_NARRATIVE_ALIGNMENT_ANNOTATION in statement:
            continue
        if not any(token in statement for token in relation_tokens):
            continue
        updated = _append_narrative_annotation(statement, REGION_NARRATIVE_ALIGNMENT_ANNOTATION, labels)
        if updated != statement:
            finding["statement"] = updated
            changed += 1
    return changed

# Original func/step07/checks.py:5231
def _drop_unrepresentable_extremum_relations(relations: Any) -> tuple[Any, list[str]]:
    """Drop extremum relations that the Step07 relation schema cannot represent.

    ``extremum`` is a temporal relation: all selectors must refer to one
    operational region and differ by forecast hour.  A single-hour comparison
    across several regions is a spatial ranking/max-location claim and has no
    ``claimed_region_id`` field in the current schema.  Keeping such a relation
    guarantees a deterministic ``extremum requires one common region`` failure.

    The quantitative claims themselves are preserved.  If a temporal relation
    is contractually required, downstream quantitative-consistency validation
    will still raise ``QUANTITATIVE_RELATION_REQUIRED`` and fail closed for that
    Check; this normalization therefore does not weaken required quantitative
    validation.
    """
    if not isinstance(relations, list):
        return relations, []
    kept: list[Any] = []
    dropped: list[str] = []
    for relation in relations:
        if not isinstance(relation, dict) or relation.get("relation_type") != "extremum":
            kept.append(relation)
            continue
        selectors = relation.get("selectors")
        if not isinstance(selectors, list):
            kept.append(relation)
            continue
        region_ids = {
            str(selector.get("region_id") or "")
            for selector in selectors
            if isinstance(selector, dict) and str(selector.get("region_id") or "")
        }
        if len(region_ids) > 1:
            dropped.append(str(relation.get("relation_id") or "<unknown>"))
            continue
        kept.append(relation)
    return kept, dropped

# Original func/step07/checks.py:5377
def _bundle_result_values(
    *, root: Path, init: str, response: dict[str, Any], rows: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str]],
    metadata: dict[str, str], specifications_by_candidate: dict[str, dict[str, dict[str, Any]]],
    on_quantitative_selector_dedup: Callable[[str, str, str, int], None] | None = None,
    on_quantitative_relation_selector_autofix: Callable[[str, str, str, int], None] | None = None,
    on_quantitative_relation_autocorrect: Callable[[str, str, str, str, str, str], None] | None = None,
    quantitative_relation_pass_status: str = "passed",
    allow_quantitative_relation_mismatch: bool = False,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Build and fully validate a bundle response before it reaches the checkpoint."""
    assessments = {str(item.get("check_id")): item for item in response["assessments"] if isinstance(item, dict)}
    expected = {str(check["check_id"]) for _group, check, _entries, _fingerprint in rows}
    if set(assessments) != expected:
        raise Pass2PipelineError("PROVIDER_RESULT_INVALID", f"Bundle check IDs differ expected={sorted(expected)} actual={sorted(assessments)}")
    values: dict[tuple[str, str], dict[str, Any]] = {}
    for group_value, check, entries, fingerprint in rows:
        ai = assessments[str(check["check_id"])]
        specification = specifications_by_candidate[str(group_value["candidate_id"])][str(check["check_id"])]
        anchor_context = _system_direct_precipitation_anchor_context(
            group_value, check, specification, entries
        )
        _validate_system_direct_precipitation_assessment(
            str(check["check_id"]), anchor_context, ai
        )
        _validate_image_diagnostic_narrative_identity(entries, ai)
        _validate_moisture_transport_direction_reasoning(entries, ai)
        # Validate the model-authored spatial claim before appending any
        # deterministic region labels.  An invalid response must not be
        # mutated with a correct-looking annotation that leaves its original
        # unsupported place claim intact (and can contaminate retry context).
        _validate_spatial_localization_narrative_grounding(
            root, group_value, check, specification, ai
        )
        direct_region_enrichments = _enrich_system_anchor_region_narrative(
            group_value, check, specification, entries, ai
        )
        alignment_region_enrichments = _enrich_localized_alignment_region_narrative(
            root, group_value, check, specification, ai
        )
        if direct_region_enrichments or alignment_region_enrichments:
            print(
                f"STEP07_REGION_NARRATIVE_ENRICHED candidate={group_value['candidate_id']} "
                f"check={check['check_id']} direct={direct_region_enrichments} "
                f"alignment={alignment_region_enrichments}"
            )
        _validate_system_anchor_region_narrative(group_value, check, specification, entries, ai)
        _validate_localized_alignment_region_narrative(root, group_value, check, specification, ai)
        bound_majority = _bind_majority_convergence_quantitative_evidence(
            specification, check, entries, ai,
        )
        if bound_majority is not None:
            print(
                f"STEP07_SYSTEM_QUANTITATIVE_BINDING candidate={group_value['candidate_id']} "
                f"bundle={check['bundle_id']} check={check['check_id']} "
                f"version={MAJORITY_QUANTITATIVE_BINDING_VERSION} "
                f"regions={bound_majority['regions']} source_claims={bound_majority['source_claims']} "
                f"source_relations={bound_majority['source_relations']} "
                f"model_claims_replaced={bound_majority['model_claims_replaced']} "
                f"model_relations_replaced={bound_majority['model_relations_replaced']}"
            )
        order_fixes = _normalize_specific_quantitative_relation_order(ai, specification)
        if order_fixes:
            print(
                f"STEP07_RELATION_ORDER_NORMALIZED candidate={group_value['candidate_id']} "
                f"bundle={check['bundle_id']} check={check['check_id']} fixes={order_fixes}"
            )
        relations_for_namespace_check, dropped_extremum_relations = _drop_unrepresentable_extremum_relations(
            ai.get("quantitative_relations", [])
        )
        if dropped_extremum_relations:
            ai["quantitative_relations"] = relations_for_namespace_check
            print(
                f"STEP07_SPATIAL_EXTREMUM_RELATION_DROPPED candidate={group_value['candidate_id']} "
                f"bundle={check['bundle_id']} check={check['check_id']} "
                f"relations={','.join(dropped_extremum_relations)}"
            )
        # Fail before AUTOFIX: never copy a relation's invented selector into claims.
        namespace_issues = _quantitative_response_namespace_issues(check, entries, {
            "quantitative_claims": ai.get("quantitative_claims"),
            "quantitative_relations": relations_for_namespace_check,
        })
        if namespace_issues:
            first = namespace_issues[0]
            raise Pass2PipelineError(
                "QUANTITATIVE_CLAIM_INVALID",
                f"{check.get('check_id')} unavailable Step05 quantitative selector at "
                f"{first['source']}: {first['selector']}",
            )
        completed_selectors, relation_selector_added_count = _ensure_relation_selectors_declared_in_claims(
            ai["quantitative_claims"], relations_for_namespace_check, check=check, entries=entries
        )
        normalized_selectors, removed_count = _deduplicate_quantitative_selectors(completed_selectors)
        if relation_selector_added_count:
            candidate_value = str(group_value["candidate_id"])
            bundle_value = str(check["bundle_id"])
            check_value = str(check["check_id"])
            print(
                f"STEP07_RELATION_SELECTOR_AUTOFIX candidate={candidate_value} "
                f"bundle={bundle_value} check={check_value} added={relation_selector_added_count}"
            )
            if on_quantitative_relation_selector_autofix is not None:
                try:
                    on_quantitative_relation_selector_autofix(
                        candidate_value, bundle_value, check_value, relation_selector_added_count
                    )
                except Exception:
                    pass
        if removed_count:
            candidate_value = str(group_value["candidate_id"])
            bundle_value = str(check["bundle_id"])
            check_value = str(check["check_id"])
            print(
                f"STEP07_QUANTITATIVE_SELECTOR_DEDUP candidate={candidate_value} "
                f"bundle={bundle_value} check={check_value} removed={removed_count}"
            )
            if on_quantitative_selector_dedup is not None:
                try:
                    on_quantitative_selector_dedup(candidate_value, bundle_value, check_value, removed_count)
                except Exception:
                    # Operational logging must not change an already-valid normalization path.
                    pass
        # Validate the Step04 specific-scope selector identities before
        # comparing relations; otherwise a wrong region may trigger a less
        # actionable arithmetic mismatch first.
        _validate_specific_quantitative_selector_preflight(
            specification, check, entries, normalized_selectors,
        )
        quantitative_claims = resolve_quantitative_claims(check, entries, normalized_selectors)
        # Quantitative relation mismatches must regenerate the complete Check assessment.
        # Do not silently autocorrect a relation while preserving an assessment/narrative
        # that may have been derived from the opposite relation.
        autocorrect_relation_ids: set[str] = set()
        quantitative_relations, quantitative_consistency_status, relations_consistent, relation_corrections = resolve_quantitative_relations(
            check,
            entries,
            ai.get("quantitative_relations", []),
            normalized_selectors,
            pass_status=quantitative_relation_pass_status,
            allow_mismatch=allow_quantitative_relation_mismatch,
            autocorrect_relation_ids=autocorrect_relation_ids,
        )
        if relation_corrections:
            candidate_value = str(group_value["candidate_id"])
            bundle_value = str(check["bundle_id"])
            check_value = str(check["check_id"])
            for correction in relation_corrections:
                relation_id = str(correction.get("relation_id") or "")
                claimed = str(correction.get("claimed_relation") or "")
                computed = str(correction.get("computed_relation") or "")
                print(
                    f"STEP07_RELATION_AUTOCORRECT candidate={candidate_value} bundle={bundle_value} "
                    f"check={check_value} relation={relation_id} claimed={claimed} computed={computed}"
                )
                if on_quantitative_relation_autocorrect is not None:
                    try:
                        on_quantitative_relation_autocorrect(
                            candidate_value, bundle_value, check_value, relation_id, claimed, computed
                        )
                    except Exception:
                        pass
        quantitative_probe = {
            "assessment": ai.get("assessment"),
            "findings": ai.get("findings", []),
            "contradictory_findings": ai.get("contradictory_findings", []),
            "quantitative_claims": quantitative_claims,
            "quantitative_relations": quantitative_relations,
        }
        quantitative_failure_details = quantitative_consistency_failure_details(
            specification, quantitative_probe, check=check, entries=entries
        )
        quantitative_failure_reasons = list(dict.fromkeys(
            str(detail["code"]) for detail in quantitative_failure_details
        ))
        if quantitative_failure_reasons:
            quantitative_consistency_status = "failed"
            relations_consistent = False
            if not allow_quantitative_relation_mismatch:
                primary = quantitative_failure_details[0]
                raise Pass2PipelineError(str(primary["code"]), str(primary["detail"]))
        findings = _normalize_ai_evidence_references(check, entries, ai["findings"])
        contradictory_findings = _normalize_ai_evidence_references(check, entries, ai["contradictory_findings"])
        uncertainties = list(ai["uncertainties"])
        if check["missing_request_ids"]:
            uncertainties.append(
                "일부 상세 예측자료가 생성되지 않아 해당 확인 결과의 완결성이 제한됩니다."
            )
        if not relations_consistent:
            uncertainties.append(
                quantitative_failure_uncertainty(quantitative_failure_reasons)
            )
        usable_for_synthesis = bool(relations_consistent)
        exclusion_reasons = [] if usable_for_synthesis else list(quantitative_failure_reasons)
        majority_aggregation = _majority_convergence_aggregation_from_result(
            specification, {"quantitative_claims": quantitative_claims}
        )
        value = {
            "candidate_id": group_value["candidate_id"], "check_id": check["check_id"], "source_intent_hash": check.get("source_intent_hash"), "intent_contract_version": check.get("intent_contract_version"), "expected_signal": check["expected_signal"], "decision_relevance": check["decision_relevance"],
            "request_ids": check["request_ids"], "available_request_ids": check["available_request_ids"], "missing_request_ids": check["missing_request_ids"],
            "evidence_complete": check["evidence_complete"], "evidence_status": check["evidence_status"], "input_fingerprint": fingerprint,
            "assessment": ai["assessment"], "scope_coverage": ai["scope_coverage"],
            "spatial_localization": ai["spatial_localization"],
            "precipitation_motion": ai["precipitation_motion"],
            "findings": findings, "contradictory_findings": contradictory_findings,
            "quantitative_claims": quantitative_claims, "quantitative_relations": quantitative_relations,
            **({"majority_aggregation": majority_aggregation} if majority_aggregation is not None else {}),
            "quantitative_consistency_status": quantitative_consistency_status,
            "quantitative_consistency_failure_reasons": quantitative_failure_reasons,
            "quantitative_consistency_failure_details": quantitative_failure_details,
            "usable_for_synthesis": usable_for_synthesis,
            "excluded_from_synthesis_reasons": exclusion_reasons,
            "uncertainties": uncertainties, "provider_metadata": metadata,
        }
        if _single_majority_convergence_not_strengthen_claim(specification) is not None:
            if majority_aggregation is None:
                raise Pass2PipelineError("QUANTITATIVE_SCOPE_CONTRACT_INVALID", "수렴 다수권역 정량 집계가 누락되었습니다.")
            try:
                value = canonicalize_majority_result(value)
            except MajorityNarrativeError as exc:
                raise Pass2PipelineError("QUANTITATIVE_EXPECTED_STATE_MISMATCH", str(exc)) from exc
        validate_scope_coverage_contract(value)
        validate_check_evidence_references(
            check,
            entries,
            value,
            check_specification=specification,
        )
        validate_spatial_localization_contract(root, group_value, specification, value)
        validate_precipitation_motion_contract(root, group_value, specification, value)
        time_errors = _step07_forecast_time_narrative_errors(init, value)
        if time_errors:
            raise Pass2PipelineError("FORECAST_TIME_NARRATIVE_INVALID", "; ".join(time_errors[:12]))
        language_errors = validate_step07_result_narrative(value)
        if language_errors:
            raise Pass2PipelineError("NARRATIVE_LANGUAGE_INVALID", "; ".join(language_errors[:12]))
        values[(str(group_value["candidate_id"]), str(check["check_id"]))] = value
    return values

# Original func/step07/checks.py:5964
def dry_run_summary(package: dict[str, Any], *, candidate_id: str | None, config: dict[str, str], root: Path) -> dict[str, Any]:
    prompt_hash = template_text_and_hash(root, CHECK_PROMPT)[1]
    schema_hash = sha256sum(runtime_path(root, "prompts", "schemas", CHECK_RESPONSE_SCHEMA))
    checks: list[dict[str, Any]] = []
    bundles: dict[tuple[str, str], dict[str, Any]] = {}
    for group in active_candidate_groups(package, candidate_id):
        indexed = _entry_index(group)
        specifications = _check_specifications(group)
        for check in group.get("check_evidence", []):
            entries = [indexed[request_id] for request_id in check["request_ids"]]
            specification = specifications.get(str(check.get("check_id")))
            if specification is None:
                raise Pass2PipelineError("VARIABLE_CHECKS_INVALID", f"{group.get('candidate_id')} has no Step04 definition for {check.get('check_id')}.")
            fingerprint = _check_fingerprint(root, group, check, specification, entries, config, prompt_hash, schema_hash)
            checks.append({"candidate_id": group["candidate_id"], "check_id": check["check_id"], "request_ids": check["request_ids"], "evidence_complete": check["evidence_complete"], "evidence_status": check["evidence_status"], "check_scope": check.get("check_scope"), "missing_signal_components": check.get("missing_signal_components", []), "bundle_id": check["bundle_id"], "fingerprint": fingerprint, "cross_section_context": check.get("cross_section_context"), "time_height_context": check.get("time_height_context"), "temporal_precipitation_primary_feature": requires_temporal_precipitation_increment_claim(specification)})
            if check["available_request_ids"] and not check.get("missing_signal_components"):
                key = (str(group["candidate_id"]), str(check["bundle_id"]))
                bundle = bundles.setdefault(key, {"candidate_id": key[0], "bundle_id": key[1], "check_ids": [], "request_ids": check["request_ids"], "png_count": 0, "quantitative_request_count": 0})
                bundle["check_ids"].append(check["check_id"])
                available_entries = [entry for entry in entries if entry["request"]["request_id"] in check["available_request_ids"]]
                bundle["png_count"] = sum(len(entry["step05"].get("generated_images", [])) for entry in available_entries)
                bundle["quantitative_request_count"] = sum(1 for entry in available_entries if entry["request"].get("data_type") == "quantitative_excerpt")
    validate_schema(root, CHECK_RESULTS_SCHEMA, {"schema_version": CHECK_RESULTS_VERSION, "init": package["init"], "results": []})
    return {"checks": checks, "bundles": list(bundles.values()), "expected_api_calls": len(bundles) * ENSEMBLE_MEMBER_COUNT, "output_schema_valid": True}

# Original func/step07/checks.py:5990
def run_step07(
    *, root: Path, init: str, package: dict[str, Any], config: dict[str, str], provider: AIProvider | None,
    candidate_id: str | None = None, overwrite: bool = False,
    on_grounding_retry: Callable[[str, str, str, int], None] | None = None,
    on_quantitative_selector_dedup: Callable[[str, str, str, int], None] | None = None,
    on_quantitative_relation_selector_autofix: Callable[[str, str, str, int], None] | None = None,
    on_quantitative_relation_autocorrect: Callable[[str, str, str, str, str, str], None] | None = None,
    on_language_retry: Callable[[str, str, list[str], dict[str, Any] | None], None] | None = None,
    on_quantitative_relation_failure: Callable[[str, str, str], None] | None = None,
) -> dict[str, Any]:
    """Run/reuse checks and atomically checkpoint after every successful bundle."""
    output_path = step07_output_path(root, init)
    prompt_text, prompt_hash = template_text_and_hash(root, CHECK_PROMPT)
    schema_hash = sha256sum(runtime_path(root, "prompts", "schemas", CHECK_RESPONSE_SCHEMA))
    metadata = _provider_metadata(config, prompt_hash, schema_hash)
    active: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str]] = {}
    specifications_by_candidate: dict[str, dict[str, dict[str, Any]]] = {}
    selected_candidate_ids = {str(group["candidate_id"]) for group in active_candidate_groups(package, candidate_id)}
    for group in active_candidate_groups(package):
        indexed = _entry_index(group)
        specifications = _check_specifications(group)
        specifications_by_candidate[str(group["candidate_id"])] = specifications
        for check in group.get("check_evidence", []):
            entries = [indexed[request_id] for request_id in check["request_ids"]]
            specification = specifications.get(str(check.get("check_id")))
            if specification is None:
                raise Pass2PipelineError("VARIABLE_CHECKS_INVALID", f"{group.get('candidate_id')} has no Step04 definition for {check.get('check_id')}.")
            source_language_errors = [
                *validate_korean_text(check.get("expected_signal"), path=f"{group.get('candidate_id')}.{check.get('check_id')}.expected_signal"),
                *validate_korean_text(check.get("decision_relevance"), path=f"{group.get('candidate_id')}.{check.get('check_id')}.decision_relevance"),
            ]
            if source_language_errors:
                raise Pass2PipelineError(
                    "NARRATIVE_LANGUAGE_INVALID",
                    "Step04 확인 항목 자연어가 새 한글 기준을 충족하지 않습니다. Step04부터 다시 생성해야 합니다. "
                    + "; ".join(source_language_errors[:8]),
                )
            active[(str(group["candidate_id"]), str(check["check_id"]))] = (group, check, entries, _check_fingerprint(root, group, check, specification, entries, config, prompt_hash, schema_hash))
    existing_on_disk = load_json_object(output_path, code="STEP07_RESULT_INVALID") if output_path.is_file() else None
    if candidate_id is not None and (
        not isinstance(existing_on_disk, dict)
        or existing_on_disk.get("init") != init
        or existing_on_disk.get("schema_version") != CHECK_RESULTS_VERSION
    ):
        raise Pass2PipelineError(
            "STEP07_ENSEMBLE_BASELINE_REQUIRED",
            "Step07 ensemble v8 최초 전환 시에는 --candidate-id 없이 전체 후보를 한 번 재수행해야 합니다.",
        )
    existing = (
        existing_on_disk
        if isinstance(existing_on_disk, dict) and (not overwrite or candidate_id is not None)
        else {"schema_version": CHECK_RESULTS_VERSION, "init": init, "results": []}
    )
    if existing.get("init") != init or existing.get("schema_version") != CHECK_RESULTS_VERSION:
        existing = {"schema_version": CHECK_RESULTS_VERSION, "init": init, "results": []}
    else:
        validate_schema(root, CHECK_RESULTS_SCHEMA, existing, code="STEP07_RESULT_INVALID")
    old = {(str(item.get("candidate_id")), str(item.get("check_id"))): item for item in existing.get("results", []) if isinstance(item, dict)}
    report_path = step07_minority_report_path(root, init)
    report_existing = (
        load_json_object(report_path, code="STEP07_MINORITY_REPORT_INVALID")
        if report_path.is_file() and (not overwrite or candidate_id is not None)
        else {
            "schema_version": MINORITY_REPORT_VERSION,
            "init": init,
            "ensemble_contract_version": ENSEMBLE_CONTRACT_VERSION,
            "member_count": ENSEMBLE_MEMBER_COUNT,
            "entries": [],
        }
    )
    if (
        report_existing.get("schema_version") != MINORITY_REPORT_VERSION
        or report_existing.get("init") != init
    ):
        report_existing = {
            "schema_version": MINORITY_REPORT_VERSION,
            "init": init,
            "ensemble_contract_version": ENSEMBLE_CONTRACT_VERSION,
            "member_count": ENSEMBLE_MEMBER_COUNT,
            "entries": [],
        }
    else:
        validate_schema(root, MINORITY_REPORT_SCHEMA, report_existing, code="STEP07_MINORITY_REPORT_INVALID")
    minority_entries: dict[tuple[str, str], dict[str, Any]] = {
        (str(item.get("candidate_id")), str(item.get("check_id"))): item
        for item in report_existing.get("entries", [])
        if isinstance(item, dict)
        and (str(item.get("candidate_id")), str(item.get("check_id"))) in active
    }

    def commit_minority_report() -> dict[str, Any]:
        report = {
            "schema_version": MINORITY_REPORT_VERSION,
            "init": init,
            "ensemble_contract_version": ENSEMBLE_CONTRACT_VERSION,
            "member_count": ENSEMBLE_MEMBER_COUNT,
            "entries": [minority_entries[key] for key in sorted(minority_entries) if key in current],
        }
        step07_document = {
            "schema_version": CHECK_RESULTS_VERSION,
            "init": init,
            "results": [current[key] for key in sorted(current)],
        }
        validate_step07_minority_report_consistency(step07_document, report)
        write_validated_atomic(root, report_path, report, schema_name=MINORITY_REPORT_SCHEMA)
        return report
    # Drop only no-longer-active checks. A selected-candidate run must preserve
    # reusable results belonging to other active candidates.
    current: dict[tuple[str, str], dict[str, Any]] = {
        key: value for key, value in old.items()
        if key in active and key[0] not in selected_candidate_ids
    }
    pending_by_bundle: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str]]] = {}
    for key, values in active.items():
        group, check, entries, fingerprint = values
        if key[0] not in selected_candidate_ids:
            continue
        previous = old.get(key)
        previous_language_valid = (
            isinstance(previous, dict)
            and not validate_step07_result_narrative(previous)
        )
        previous_quantitative_contract_valid = (
            isinstance(previous, dict)
            and previous.get("quantitative_consistency_status") in {"not_applicable", "passed", "corrected", "failed"}
            and isinstance(previous.get("usable_for_synthesis"), bool)
            and isinstance(previous.get("quantitative_relations"), list)
        )
        specification = specifications_by_candidate[str(group["candidate_id"])][str(check["check_id"])]
        majority_check = _single_majority_convergence_not_strengthen_claim(specification) is not None
        # Prior v1 checkpoints can be migrated without regenerating three AI
        # members, but ONLY when their former fingerprint still matches every
        # Step05 input and the resolved quantitative cells pass validation.
        if (not overwrite and majority_check and isinstance(previous, dict)
                and previous.get("input_fingerprint") != fingerprint
                and previous_language_valid and previous_quantitative_contract_valid):
            legacy_fingerprint = _check_fingerprint(
                root, group, check, specification, entries, config, prompt_hash, schema_hash,
                include_majority_narrative_contract=False,
            )
            if previous.get("input_fingerprint") == legacy_fingerprint:
                validate_check_evidence_references(check, entries, previous, check_specification=specification)
                reference_aggregation = _majority_convergence_aggregation_from_result(specification, previous)
                if reference_aggregation != previous.get("majority_aggregation"):
                    raise Pass2PipelineError(
                        "QUANTITATIVE_EXPECTED_STATE_MISMATCH",
                        f"{key} 기존 정량 집계가 Step07 원자료와 일치하지 않습니다.",
                    )
                try:
                    migrated = canonicalize_majority_result(previous)
                except MajorityNarrativeError as exc:
                    raise Pass2PipelineError("QUANTITATIVE_EXPECTED_STATE_MISMATCH", str(exc)) from exc
                migrated["input_fingerprint"] = fingerprint
                previous = migrated
                print(f"STEP07_MAJORITY_NARRATIVE_MIGRATED candidate={key[0]} check={key[1]}")
        if (
            not overwrite
            and isinstance(previous, dict)
            and previous.get("input_fingerprint") == fingerprint
            and previous_language_valid
            and previous_quantitative_contract_valid
        ):
            if majority_check:
                try:
                    assert_majority_narrative_current(previous)
                except MajorityNarrativeError as exc:
                    raise Pass2PipelineError("QUANTITATIVE_EXPECTED_STATE_MISMATCH", str(exc)) from exc
            current[key] = previous
        elif not check["available_request_ids"] or check.get("missing_signal_components"):
            specification = specifications_by_candidate[str(group["candidate_id"])][str(check["check_id"])]
            current[key] = _insufficient_result(key[0], check, specification, fingerprint, metadata)
        else:
            pending_by_bundle.setdefault((key[0], str(check["bundle_id"])), []).append(values)
    def commit() -> dict[str, Any]:
        document = {"schema_version": CHECK_RESULTS_VERSION, "init": init, "results": [current[key] for key in sorted(current)]}
        for key, value in current.items():
            group, check, entries, _fingerprint = active[key]
            specification = specifications_by_candidate[str(group["candidate_id"])][str(check["check_id"])]
            validate_check_evidence_references(check, entries, value, check_specification=specification)
            validate_spatial_localization_contract(root, group, specification, value)
            validate_precipitation_motion_contract(root, group, specification, value)
            validate_step07_ensemble_consensus(value)
        write_validated_atomic(root, output_path, document, schema_name=CHECK_RESULTS_SCHEMA)
        return document
    if current:
        commit()
        commit_minority_report()
    for (_candidate, _bundle), rows in sorted(pending_by_bundle.items()):
        if provider is None:
            raise Pass2PipelineError("PROVIDER_REQUIRED", "Step07 requires a provider when complete evidence is pending.")
        group = rows[0][0]
        checks = [row[1] for row in rows]
        entries_by_id = _entry_index(group)
        available_request_ids = rows[0][1]["available_request_ids"]
        def execute_ensemble_member(member_index: int) -> dict[tuple[str, str], dict[str, Any]]:
            print(
                f"STEP07_ENSEMBLE_MEMBER_START candidate={_candidate} bundle={_bundle} "
                f"member={member_index}/{ENSEMBLE_MEMBER_COUNT}"
            )
            messages = _bundle_messages(root, init, group, checks, [entries_by_id[item] for item in available_request_ids], prompt_text)
            response = _call_bundle(provider, root, messages, config)
            direction_fixes = normalize_step07_response_directions(response)
            if direction_fixes:
                print(
                    f"STEP07_DIRECTION_NORMALIZED candidate={_candidate} bundle={_bundle} "
                    f"member={member_index}/{ENSEMBLE_MEMBER_COUNT} fixes={direction_fixes} stage=initial"
                )
            language_errors = validate_step07_response_narrative(response)
            if language_errors:
                target_check_ids = _step07_language_error_check_ids(response, language_errors)
                print(
                    f"STEP07_LANGUAGE_RETRY candidate={_candidate} bundle={_bundle} "
                    f"violations={len(language_errors)} attempt=1 unit=full_check checks={','.join(target_check_ids)}"
                )
                language_error = Pass2PipelineError(
                    "NARRATIVE_LANGUAGE_INVALID", "; ".join(language_errors[:12])
                )
                response = _regenerate_step07_assessment_units(
                    provider, root, init, group, rows, entries_by_id, prompt_text, config,
                    response, target_check_ids, language_error,
                )
                direction_fixes = normalize_step07_response_directions(response)
                if direction_fixes:
                    print(
                        f"STEP07_DIRECTION_NORMALIZED candidate={_candidate} bundle={_bundle} "
                        f"member={member_index}/{ENSEMBLE_MEMBER_COUNT} fixes={direction_fixes} stage=language_retry_1"
                    )
                remaining_language_errors = validate_step07_response_narrative(response)
                if remaining_language_errors:
                    second_targets = _step07_language_error_check_ids(response, remaining_language_errors)
                    print(
                        f"STEP07_LANGUAGE_RETRY candidate={_candidate} bundle={_bundle} "
                        f"violations={len(remaining_language_errors)} attempt=2 unit=full_check checks={','.join(second_targets)}"
                    )
                    response = _regenerate_step07_assessment_units(
                        provider, root, init, group, rows, entries_by_id, prompt_text, config,
                        response, second_targets,
                        Pass2PipelineError("NARRATIVE_LANGUAGE_INVALID", "; ".join(remaining_language_errors[:12])),
                    )
                    direction_fixes = normalize_step07_response_directions(response)
                    if direction_fixes:
                        print(
                            f"STEP07_DIRECTION_NORMALIZED candidate={_candidate} bundle={_bundle} "
                            f"member={member_index}/{ENSEMBLE_MEMBER_COUNT} fixes={direction_fixes} stage=language_retry_2"
                        )
                    final_language_errors = validate_step07_response_narrative(response)
                    if final_language_errors:
                        raise Pass2PipelineError(
                            "NARRATIVE_LANGUAGE_INVALID", "; ".join(final_language_errors[:12])
                        )
                if on_language_retry is not None:
                    try:
                        on_language_retry(_candidate, _bundle, language_errors, None)
                    except Exception:
                        pass
            try:
                bundle_values = _bundle_result_values(
                    root=root,
                    init=init,
                    response=response,
                    rows=rows,
                    metadata=metadata,
                    specifications_by_candidate=specifications_by_candidate,
                    on_quantitative_selector_dedup=on_quantitative_selector_dedup,
                    on_quantitative_relation_selector_autofix=on_quantitative_relation_selector_autofix,
                    on_quantitative_relation_autocorrect=on_quantitative_relation_autocorrect,
                )
            except Pass2PipelineError as exc:
                _write_step07_grounding_failure(
                    root, init, candidate=_candidate, bundle=_bundle, member=member_index,
                    attempt=0, error=exc, response=response, rows=rows,
                )
                if not _is_grounding_validation_error(exc, response=response, rows=rows):
                    raise
                grounding_error = exc
                grounding_response = response
                bundle_values = None
                for grounding_attempt in range(1, MAX_GROUNDING_RETRY_ATTEMPTS + 1):
                    target_check_ids = _step07_failing_check_ids(
                        root, init, grounding_response, rows, metadata, specifications_by_candidate
                    )
                    print(
                        f"STEP07_GROUNDING_RETRY candidate={_candidate} bundle={_bundle} "
                        f"reason={grounding_error.code} attempt={grounding_attempt} "
                        f"unit=full_check checks={','.join(target_check_ids)}"
                    )
                    if on_grounding_retry is not None:
                        try:
                            on_grounding_retry(_candidate, _bundle, grounding_error.code, grounding_attempt)
                        except Exception:
                            pass
                    retry_response = _regenerate_step07_assessment_units(
                        provider, root, init, group, rows, entries_by_id, prompt_text, config,
                        grounding_response, target_check_ids, grounding_error,
                    )
                    direction_fixes = normalize_step07_response_directions(retry_response)
                    if direction_fixes:
                        print(
                            f"STEP07_DIRECTION_NORMALIZED candidate={_candidate} bundle={_bundle} "
                            f"member={member_index}/{ENSEMBLE_MEMBER_COUNT} fixes={direction_fixes} "
                            f"stage=grounding_retry_{grounding_attempt}"
                        )

                    language_errors = validate_step07_response_narrative(retry_response)
                    if language_errors:
                        language_targets = _step07_language_error_check_ids(retry_response, language_errors)
                        print(
                            f"STEP07_LANGUAGE_RETRY candidate={_candidate} bundle={_bundle} "
                            f"violations={len(language_errors)} attempt={grounding_attempt} "
                            f"unit=full_check checks={','.join(language_targets)}"
                        )
                        retry_response = _regenerate_step07_assessment_units(
                            provider, root, init, group, rows, entries_by_id, prompt_text, config,
                            retry_response, language_targets,
                            Pass2PipelineError("NARRATIVE_LANGUAGE_INVALID", "; ".join(language_errors[:12])),
                        )
                        direction_fixes = normalize_step07_response_directions(retry_response)
                        if direction_fixes:
                            print(
                                f"STEP07_DIRECTION_NORMALIZED candidate={_candidate} bundle={_bundle} "
                                f"member={member_index}/{ENSEMBLE_MEMBER_COUNT} fixes={direction_fixes} "
                                f"stage=grounding_language_retry_{grounding_attempt}"
                            )
                        remaining_language_errors = validate_step07_response_narrative(retry_response)
                        if remaining_language_errors:
                            raise Pass2PipelineError(
                                "NARRATIVE_LANGUAGE_INVALID", "; ".join(remaining_language_errors[:12])
                            )
                    try:
                        bundle_values = _bundle_result_values(
                            root=root,
                            init=init,
                            response=retry_response,
                            rows=rows,
                            metadata=metadata,
                            specifications_by_candidate=specifications_by_candidate,
                            on_quantitative_selector_dedup=on_quantitative_selector_dedup,
                            on_quantitative_relation_selector_autofix=on_quantitative_relation_selector_autofix,
                            on_quantitative_relation_autocorrect=on_quantitative_relation_autocorrect,
                            quantitative_relation_pass_status=(
                                "corrected" if grounding_error.code == "QUANTITATIVE_RELATION_MISMATCH" else "passed"
                            ),
                        )
                        break
                    except Pass2PipelineError as retry_exc:
                        _write_step07_grounding_failure(
                            root, init, candidate=_candidate, bundle=_bundle, member=member_index,
                            attempt=grounding_attempt, error=retry_exc,
                            response=retry_response, rows=rows,
                        )
                        if (
                            grounding_attempt < MAX_GROUNDING_RETRY_ATTEMPTS
                            and _is_grounding_validation_error(retry_exc, response=retry_response, rows=rows)
                        ):
                            grounding_error = retry_exc
                            grounding_response = retry_response
                            continue
                        if retry_exc.code not in QUANTITATIVE_FAILURE_REASONS:
                            raise
                        print(
                            f"STEP07_QUANTITATIVE_RELATION_FAILED candidate={_candidate} bundle={_bundle} "
                            f"reason={retry_exc.code}"
                        )
                        if on_quantitative_relation_failure is not None:
                            try:
                                on_quantitative_relation_failure(_candidate, _bundle, str(retry_exc))
                            except Exception:
                                pass
                        bundle_values = _bundle_result_values(
                            root=root,
                            init=init,
                            response=retry_response,
                            rows=rows,
                            metadata=metadata,
                            specifications_by_candidate=specifications_by_candidate,
                            on_quantitative_selector_dedup=on_quantitative_selector_dedup,
                            on_quantitative_relation_selector_autofix=on_quantitative_relation_selector_autofix,
                            on_quantitative_relation_autocorrect=on_quantitative_relation_autocorrect,
                            quantitative_relation_pass_status="corrected",
                            allow_quantitative_relation_mismatch=True,
                        )
                        break
                if bundle_values is None:
                    raise Pass2PipelineError(
                        "GROUNDING_RETRY_EXHAUSTED",
                        f"candidate={_candidate} bundle={_bundle} grounding retry ended without a validated result.",
                    )
            print(
                f"STEP07_ENSEMBLE_MEMBER_OK candidate={_candidate} bundle={_bundle} "
                f"member={member_index}/{ENSEMBLE_MEMBER_COUNT}"
            )
            return bundle_values

        member_bundle_values = [
            execute_ensemble_member(member_index)
            for member_index in range(1, ENSEMBLE_MEMBER_COUNT + 1)
        ]
        consensus_values: dict[tuple[str, str], dict[str, Any]] = {}
        fresh_report_entries: dict[tuple[str, str], dict[str, Any]] = {}
        expected_keys = set(member_bundle_values[0])
        if any(set(values) != expected_keys for values in member_bundle_values[1:]):
            raise Pass2PipelineError(
                "STEP07_ENSEMBLE_INVALID",
                f"candidate={_candidate} bundle={_bundle} ensemble members returned different Check sets.",
            )
        for result_key in sorted(expected_keys):
            consensus_result, report_entry = build_step07_ensemble_consensus(
                [values[result_key] for values in member_bundle_values]
            )
            try:
                consensus_result = canonicalize_majority_result(consensus_result)
            except MajorityNarrativeError as exc:
                raise Pass2PipelineError("QUANTITATIVE_EXPECTED_STATE_MISMATCH", str(exc)) from exc
            validate_step07_ensemble_consensus(consensus_result)
            consensus_values[result_key] = consensus_result
            minority_entries.pop(result_key, None)
            if report_entry is not None:
                fresh_report_entries[result_key] = report_entry
        minority_entries.update(fresh_report_entries)
        current.update(consensus_values)
        commit()
        commit_minority_report()
    final_document = commit()
    commit_minority_report()
    return final_document
