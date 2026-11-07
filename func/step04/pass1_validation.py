"""Step step04 validation responsibility; original function bodies preserved."""
from __future__ import annotations
from . import pass1_context as _step_context
from func.shared.korean_narrative import normalize_step04_discovery_directions

AIFS_NATIVE_SPATIAL_INTERVAL_DEGREES = getattr(_step_context, 'AIFS_NATIVE_SPATIAL_INTERVAL_DEGREES')
CANDIDATE_DISCOVERY_RECONCILIATION_ASSET_LIMIT = getattr(_step_context, 'CANDIDATE_DISCOVERY_RECONCILIATION_ASSET_LIMIT')
CANDIDATE_DISCOVERY_RECONCILIATION_EVIDENCE_LIMIT = getattr(_step_context, 'CANDIDATE_DISCOVERY_RECONCILIATION_EVIDENCE_LIMIT')

# Original func/step04/pass1_engine.py:217
def _verification_role_for_components(components: list[str]) -> str:
    for component in ("moisture_supply", "mid_upper_forcing", "organization_persistence"):
        if component in components:
            return VERIFICATION_SIGNAL_TO_ROLE[component]
    if components:
        return VERIFICATION_SIGNAL_TO_ROLE.get(components[0], "synoptic_support")
    return "synoptic_support"

# Original func/step04/pass1_engine.py:226
def _derive_diagnostic_check_from_verification_request(row: dict[str, Any]) -> dict[str, Any]:
    """Translate one AI meteorological verification request into an executable Check.

    AI owns WHEN/WHERE/WHAT/WHY. Python owns the internal execution grammar.
    This boundary prevents model output from failing on combinations such as a
    spatial-only diagnostic paired with regional/time-series analysis methods.
    """
    components = list(dict.fromkeys(
        str(value) for value in row.get("signal_components", []) if str(value)
    ))
    unknown = sorted(set(components) - set(VERIFICATION_SIGNAL_COMPONENTS))
    if not components or unknown:
        raise ValueError(
            f"verification request check_key={row.get('check_key')!r} has invalid "
            f"signal_components={components} unknown={unknown}"
        )
    mode = str(row.get("verification_mode") or "")
    if mode not in VERIFICATION_MODES:
        raise ValueError(
            f"verification request check_key={row.get('check_key')!r} has unsupported "
            f"verification_mode={mode!r}"
        )

    domain_scope = str(row.get("domain_scope") or "candidate")
    spatial_relation_scope = str(row.get("spatial_relation_scope") or "not_applicable")
    if spatial_relation_scope not in SPATIAL_RELATION_SCOPES:
        raise ValueError(
            f"verification request check_key={row.get('check_key')!r} has unsupported "
            f"spatial_relation_scope={spatial_relation_scope!r}"
        )
    if spatial_relation_scope in {"candidate_boundary_crossing", "candidate_external_context"} and domain_scope != "context":
        raise ValueError(
            "SPATIAL_RELATION_DOMAIN_SCOPE_MISMATCH "
            f"check_key={row.get('check_key')} spatial_relation_scope={spatial_relation_scope} "
            f"domain_scope={domain_scope}; repair=claims that require the candidate boundary, "
            "outside-candidate location, approach/exit, or surrounding external source context "
            "must use domain_scope=context with a context_domain that contains candidate_area"
        )
    cross_section_line = None
    cross_section_strategy = None
    reference_band_axis = None

    if mode == "multivariable_alignment":
        allowed = {
            "direct_precipitation", "moisture_supply",
            "low_level_forcing", "mid_upper_forcing",
        }
        if len(components) < 2 or not set(components).issubset(allowed):
            raise ValueError(
                f"verification request check_key={row.get('check_key')!r} "
                "multivariable_alignment requires at least two of direct_precipitation, "
                "moisture_supply, low_level_forcing, mid_upper_forcing"
            )
        diagnostic_id = "multivariable_composite"
        variable_role = "synoptic_support"
        check_scope = "multivariable_alignment"
        analysis_methods = ["detailed_map", "variable_cross_check"]
    elif mode in {"vertical_structure", "vertical_time_evolution"}:
        allowed = {"moisture_supply", "mid_upper_forcing", "organization_persistence"}
        if not set(components).issubset(allowed):
            raise ValueError(
                f"verification request check_key={row.get('check_key')!r} {mode} only supports "
                "moisture_supply, mid_upper_forcing, organization_persistence"
            )
        variable_role = _verification_role_for_components(components)
        check_scope = "vertical_structure"
        analysis_methods = ["detailed_map"]
        if mode == "vertical_structure":
            diagnostic_id = "vertical_cross_section"
            cross_section_line = _cross_section_line_signature(row.get("cross_section_line"))
            cross_section_strategy = _normalized_cross_section_strategy(
                row.get("cross_section_strategy")
            )
            reference_band_axis = _reference_band_axis_signature(
                row.get("reference_band_axis")
            )
            if cross_section_line is None or cross_section_strategy is None or reference_band_axis is None:
                raise ValueError(
                    f"verification request check_key={row.get('check_key')!r} vertical_structure "
                    "requires cross_section_line, cross_section_strategy, and reference_band_axis"
                )
        else:
            diagnostic_id = "time_height_section"
            domain_scope = "candidate"
    else:
        if len(components) != 1:
            raise ValueError(
                f"verification request check_key={row.get('check_key')!r} mode={mode!r} "
                f"requires exactly one signal component; actual={components}"
            )
        component = components[0]
        variable_role = VERIFICATION_SIGNAL_TO_ROLE[component]
        if mode == "precipitation_amount":
            if component != "direct_precipitation":
                raise ValueError(
                    f"verification request check_key={row.get('check_key')!r} "
                    "precipitation_amount requires direct_precipitation"
                )
            diagnostic_id = "accumulated_precipitation"
            check_scope = "single_variable"
            analysis_methods = ["detailed_map", "regional_statistics", "regional_max_location"]
        elif mode == "temporal_change":
            if component == "organization_persistence":
                raise ValueError(
                    f"verification request check_key={row.get('check_key')!r} temporal_change "
                    "does not support organization_persistence; use spatial_evolution"
                )
            diagnostic_id = "temporal_evolution"
            check_scope = "temporal_evolution"
            # Temporal-change checks keep the complete operational-region time
            # series and also request AI-selected representative spatial maps.
            # Python never chooses those map hours; it only executes the hours
            # explicitly selected by the Step04 AI.
            analysis_methods = ["detailed_map", "temporal_evolution"]
            domain_scope = "candidate"
        else:
            diagnostic_id = VERIFICATION_SIGNAL_TO_SPATIAL_DIAGNOSTIC[component]
            if mode == "spatial_distribution":
                check_scope = "single_variable"
                analysis_methods = ["detailed_map"]
            elif mode == "regional_intensity":
                if component == "organization_persistence":
                    raise ValueError(
                        f"verification request check_key={row.get('check_key')!r} regional_intensity "
                        "does not support organization_persistence; use spatial_distribution"
                    )
                check_scope = "single_variable"
                analysis_methods = [
                    "detailed_map", "regional_statistics",
                    "regional_ranking", "regional_max_location",
                ]
            elif mode == "spatial_evolution":
                check_scope = "temporal_evolution"
                analysis_methods = ["detailed_map"]
            else:
                raise ValueError(
                    f"verification request check_key={row.get('check_key')!r} has unsupported "
                    f"verification_mode={mode!r}"
                )

    check = {
        "check_key": row.get("check_key"),
        "verification_mode": mode,
        "variable_role": variable_role,
        "diagnostic_id": diagnostic_id,
        "domain_scope": domain_scope,
        "spatial_relation_scope": spatial_relation_scope,
        "phase_keys": list(row.get("phase_keys", [])),
        "representative_spatial_hours": [
            int(value)
            for value in row.get("representative_spatial_hours", [])
            if isinstance(value, int) and not isinstance(value, bool)
        ],
        "analysis_methods": analysis_methods,
        "expected_signal": row.get("expected_signal"),
        "decision_relevance": row.get("decision_relevance"),
        "priority": row.get("priority"),
        "evidence_role": row.get("evidence_role"),
        "coverage_roles": list(row.get("coverage_roles", [])),
        "purpose": row.get("purpose"),
        "atomic_claims": copy.deepcopy(row.get("atomic_claims", [])) if isinstance(row.get("atomic_claims"), list) else [],
        "cross_section_line": cross_section_line,
        "cross_section_strategy": cross_section_strategy,
        "reference_band_axis": reference_band_axis,
        "check_scope": check_scope,
        "required_signal_components": components,
    }
    check["coverage_roles"] = _effective_compact_check_coverage_roles(check)
    return check

# Original func/step04/pass1_engine.py:402
def _canonicalize_step04_forecast_hour_text(
    value: Any,
    *,
    allowed_hours: set[int],
) -> tuple[Any, list[tuple[str, str]]]:
    """Canonicalize recoverable Step04 F-hour tokens without changing meaning.

    Only forecast hours that are already available for the active candidate are
    rewritten.  For example, ``F96`` becomes ``F096`` when 96 is an available
    forecast hour.  Invalid or unavailable hours such as ``F97`` are left
    untouched so the existing validators can reject them.
    """
    if not isinstance(value, str) or not value:
        return value, []

    replacements: list[tuple[str, str]] = []

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        hour = int(match.group(1))
        if hour not in allowed_hours:
            return raw
        canonical = f"F{hour:03d}"
        if raw != canonical:
            replacements.append((raw, canonical))
        return canonical

    return _STEP04_FORECAST_HOUR_TOKEN.sub(replace, value), replacements

# Original func/step04/pass1_engine.py:432
def _canonicalize_step04_candidate_plan_forecast_hours(
    value: dict[str, Any],
    *,
    candidate: dict[str, Any],
    attachment_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Normalize forecast-hour spelling in Step04 human-readable plan text.

    This is formatting canonicalization only.  It never invents a forecast hour
    and never changes the meteorological claim selected by the model.
    """
    normalized = copy.deepcopy(value)
    allowed_hours = set(_available_candidate_forecast_hours(candidate, attachment_manifest))
    if not allowed_hours:
        return normalized

    changed: list[tuple[str, str, str]] = []

    def rewrite(container: dict[str, Any], field: str, path: str) -> None:
        if field not in container:
            return
        rewritten, replacements = _canonicalize_step04_forecast_hour_text(
            container.get(field), allowed_hours=allowed_hours
        )
        container[field] = rewritten
        changed.extend((path, before, after) for before, after in replacements)

    rewrite(normalized, "analysis_objective", "analysis_objective")
    rewrite(normalized, "context_domain_reason", "context_domain_reason")

    for i, hypothesis in enumerate(normalized.get("hypotheses", [])):
        if isinstance(hypothesis, dict):
            rewrite(hypothesis, "statement", f"hypotheses[{i}].statement")

    for i, phase in enumerate(normalized.get("analysis_phases", [])):
        if isinstance(phase, dict):
            rewrite(phase, "purpose", f"analysis_phases[{i}].purpose")

    for i, request in enumerate(normalized.get("verification_requests", [])):
        if not isinstance(request, dict):
            continue
        for field in ("purpose", "expected_signal", "decision_relevance"):
            rewrite(request, field, f"verification_requests[{i}].{field}")
        line = request.get("cross_section_line")
        if isinstance(line, dict):
            rewrite(
                line,
                "selection_reason",
                f"verification_requests[{i}].cross_section_line.selection_reason",
            )
        for j, claim in enumerate(request.get("atomic_claims", [])):
            if not isinstance(claim, dict):
                continue
            for field in ("statement", "scope_description"):
                rewrite(
                    claim,
                    field,
                    f"verification_requests[{i}].atomic_claims[{j}].{field}",
                )

    questions = normalized.get("forecaster_questions")
    if isinstance(questions, list):
        for i, question in enumerate(questions):
            rewritten, replacements = _canonicalize_step04_forecast_hour_text(
                question, allowed_hours=allowed_hours
            )
            questions[i] = rewritten
            changed.extend(
                (f"forecaster_questions[{i}]", before, after)
                for before, after in replacements
            )

    if changed:
        logging.info(
            "step04 forecast-hour tokens canonicalized candidate_id=%s count=%s changes=%s",
            candidate.get("candidate_id"),
            len(changed),
            ", ".join(
                f"{path}:{before}->{after}" for path, before, after in changed[:12]
            ),
        )
    return normalized

# Original func/step04/pass1_engine.py:516
def _normalize_ai_verification_chunk_for_validation(
    value: dict[str, Any],
    previous_chunks: list[dict[str, Any]],
    *,
    chunk_index: int,
    transport_schema: dict[str, Any],
    candidate: dict[str, Any],
    attachment_manifest: dict[str, Any],
) -> dict[str, Any]:
    # Apply the same representation-only normalizer before provider-schema
    # validation so harmless duplicates/order issues do not fail before reaching
    # the central semantic normalization layer. No meteorological intent is
    # changed here.
    normalized = copy.deepcopy(value)
    transport_changes = _normalize_candidate_plan_representation_fields(normalized)
    if transport_changes:
        _log_step04_advisories(
            "candidate_plan.contract_normalization.transport",
            list(dict.fromkeys(transport_changes)),
        )
    schema_errors = _validate_jsonschema(normalized, transport_schema)
    if schema_errors:
        raise ValueError(
            "candidate-plan verification transport schema invalid: "
            + " | ".join(schema_errors[:20])
        )
    normalized = _canonicalize_step04_candidate_plan_forecast_hours(
        normalized,
        candidate=candidate,
        attachment_manifest=attachment_manifest,
    )
    requests = normalized.pop("verification_requests", None)
    if not isinstance(requests, list):
        raise ValueError("candidate-plan verification_requests must be an object array")
    for row in requests:
        _validate_ai_verification_request_intent(row)
    normalized["diagnostic_checks"] = [
        _derive_diagnostic_check_from_verification_request(row)
        for row in requests
    ]
    return _normalize_compact_chunk_for_validation(
        normalized, previous_chunks, chunk_index=chunk_index
    )

# Original func/step04/pass1_engine.py:582
def _canonical_variable_role_for_diagnostic(diagnostic_id: str, variable_role: str) -> str:
    """Normalize composite-role labels without replacing component checks.

    A multivariable composite compares precipitation, moisture, low-level
    convergence, and midlevel ascent in one spatial product. The model may
    label that check with one component role, but the composite should remain
    a cross-variable support check so dedicated component checks continue to
    provide coverage and quantitative evidence.
    """
    if diagnostic_id == "multivariable_composite" and variable_role in {
        "hazard_magnitude",
        "moisture_supply",
        "low_level_forcing",
        "midlevel_forcing",
    }:
        return "synoptic_support"
    return variable_role

# Original func/step04/pass1_engine.py:699
def _product_signal_components_from_support(
    product_id: str, support_catalog: dict[str, Any],
) -> set[str]:
    """Return product capability copied from diagnostic_catalog.yaml by Step03."""
    for product in support_catalog.get("spatial_field_products", []):
        if isinstance(product, dict) and str(product.get("product_id")) == str(product_id):
            values = product.get("signal_components")
            if isinstance(values, list):
                return {str(value) for value in values if str(value) in SIGNAL_COMPONENTS}
    return set()

# Original func/step04/pass1_engine.py:723
def _check_scope_contract_errors(
    *,
    check_key: str,
    diagnostic_id: str,
    analysis_methods: list[str] | tuple[str, ...] | set[str],
    check_scope: str,
    required_signal_components: list[str] | tuple[str, ...] | set[str],
) -> list[str]:
    """Return common Step04 Check scope-contract errors.

    This is shared by compact planning, canonical expansion, and final-plan
    validation.  It enforces only necessary implications: diagnostics or methods
    that intrinsically require a scope must declare it, and every declared scope
    must be executable with its declared components.  It deliberately does *not*
    require the inverse relation.  For example, a detailed spatial-map Check can
    validly use ``temporal_evolution`` to compare maps at multiple times.
    """
    methods = _canonical_analysis_methods_for_diagnostic(
        diagnostic_id,
        analysis_methods,
    )
    errors: list[str] = []
    if check_scope not in CHECK_SCOPES:
        return [
            f"check_key={check_key} has unsupported check_scope={check_scope!r}"
        ]
    if diagnostic_id == "temporal_evolution" or "temporal_evolution" in methods:
        if check_scope != "temporal_evolution":
            errors.append(
                f"check_key={check_key} temporal diagnostic/method must declare "
                "check_scope=temporal_evolution"
            )
    if diagnostic_id == "multivariable_composite":
        if check_scope != "multivariable_alignment":
            errors.append(
                f"check_key={check_key} multivariable_composite must declare "
                "check_scope=multivariable_alignment"
            )
    if diagnostic_id in SECTION_DIAGNOSTIC_IDS:
        if check_scope != "vertical_structure":
            errors.append(
                f"check_key={check_key} section diagnostic must declare "
                "check_scope=vertical_structure"
            )
    if diagnostic_id in SPATIAL_ONLY_DIAGNOSTIC_IDS and set(methods) & REGIONAL_ANALYSIS_METHODS:
        errors.append(
            f"check_key={check_key} {diagnostic_id} is spatial-only; use component-variable checks for "
            "regional statistics, ranking, max location, and time series"
        )

    components = [
        str(value)
        for value in required_signal_components
        if str(value)
    ]
    if not components or any(value not in SIGNAL_COMPONENTS for value in components):
        errors.append(
            f"check_key={check_key} has invalid required_signal_components={components}"
        )
        return errors
    unique_components = set(components)
    if check_scope == "multivariable_alignment":
        if diagnostic_id != "multivariable_composite" or len(unique_components) < 2:
            errors.append(
                f"check_key={check_key} multivariable_alignment requires "
                "multivariable_composite and at least two signal components"
            )
    elif check_scope == "vertical_structure":
        # A section diagnostic is a single analysis object, but it can expose
        # several physical signal components at once (for example moisture,
        # ascent, and wind structure in VERTICAL_CROSS_SECTION_CORE).  Component
        # capability is validated separately against the active Step05 product
        # catalog, so do not collapse a valid section check to one component.
        if diagnostic_id not in SECTION_DIAGNOSTIC_IDS:
            errors.append(
                f"check_key={check_key} vertical_structure requires a section diagnostic"
            )
    elif len(unique_components) != 1:
        errors.append(
            f"check_key={check_key} {check_scope} requires exactly one signal component; "
            f"actual={components}"
        )
    return errors

# Original func/step04/pass1_engine.py:808
def _validate_check_scope_contract(
    *,
    check_key: str,
    diagnostic_id: str,
    analysis_methods: list[str] | tuple[str, ...] | set[str],
    check_scope: str,
    required_signal_components: list[str] | tuple[str, ...] | set[str],
) -> None:
    """Raise the first shared Step04 Check scope-contract error."""
    errors = _check_scope_contract_errors(
        check_key=check_key,
        diagnostic_id=diagnostic_id,
        analysis_methods=analysis_methods,
        check_scope=check_scope,
        required_signal_components=required_signal_components,
    )
    if errors:
        raise ValueError(errors[0])

# Original func/step04/pass1_engine.py:858
def _executable_products_for_required_components(
    *,
    diagnostic_id: str,
    required_signal_components: list[str] | tuple[str, ...] | set[str],
    support_catalog: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Return executable products whose catalog capability covers one Check.

    Step03's diagnostic_execution_support is authoritative for executable
    diagnostic/product pairs. Product signal-components are copied from the
    same diagnostic catalog. If an older fixture does not carry component
    metadata, compatibility alone is retained for backward-compatible tests.
    """
    execution_support = support_catalog.get("diagnostic_execution_support", {})
    contract = (
        execution_support.get(diagnostic_id, {})
        if isinstance(execution_support, dict)
        else {}
    )
    if not isinstance(contract, dict):
        return [], []
    available_product_ids = {
        str(item.get("product_id"))
        for item in support_catalog.get("spatial_field_products", [])
        if isinstance(item, dict) and item.get("product_id")
    }
    compatible = [
        str(value)
        for value in contract.get("compatible_product_ids", [])
        if str(value) in available_product_ids
    ]
    required = {str(value) for value in required_signal_components if str(value)}
    component_metadata_seen = False
    executable: list[str] = []
    for product_id in compatible:
        components = _product_signal_components_from_support(product_id, support_catalog)
        if components:
            component_metadata_seen = True
            if required.issubset(components):
                executable.append(product_id)
    if not component_metadata_seen:
        return list(dict.fromkeys(compatible)), list(dict.fromkeys(compatible))
    return list(dict.fromkeys(executable)), list(dict.fromkeys(compatible))

# Original func/step04/pass1_engine.py:903
def _validate_compact_check_execution_contract(
    *,
    check_key: str,
    diagnostic_id: str,
    required_signal_components: list[str] | tuple[str, ...] | set[str],
    support_catalog: dict[str, Any],
) -> None:
    """Fail inside the repairable compact-plan stage if Step05 cannot execute a Check."""
    executable, compatible = _executable_products_for_required_components(
        diagnostic_id=diagnostic_id,
        required_signal_components=required_signal_components,
        support_catalog=support_catalog,
    )
    if executable:
        return
    raise ValueError(
        "CHECK_EXECUTION_COMPONENT_UNAVAILABLE "
        f"check_key={check_key} diagnostic_id={diagnostic_id!r} "
        f"required_signal_components={sorted({str(value) for value in required_signal_components if str(value)})} "
        f"compatible_product_ids={compatible}; "
        "the active Step05 contract has no executable product that provides all required components"
    )

# Original func/step04/pass1_engine.py:990
def _canonical_analysis_methods_for_diagnostic(
    diagnostic_id: str,
    methods: Iterable[Any],
) -> list[str]:
    """Return executable analysis methods for one diagnostic.

    ``time_height_section`` and ``vertical_cross_section`` already contain their
    own spatial/temporal axes and produce dedicated PNG plus structured JSON.
    Regional statistics, ranking, maximum-location, and a separate temporal
    evolution request are therefore not executable for these section products.
    Structured-output models may still emit those generic methods; normalize such
    responses to the dedicated section renderer instead of failing the whole plan.
    """
    ordered = list(dict.fromkeys(str(item) for item in methods))
    if diagnostic_id in SECTION_DIAGNOSTIC_IDS:
        return ["detailed_map"]
    if diagnostic_id == "multivariable_composite":
        retained = [item for item in ordered if item in SPATIAL_ANALYSIS_METHODS]
        if "detailed_map" not in retained:
            retained.insert(0, "detailed_map")
        return retained
    return ordered

# Original func/step04/pass1_engine.py:1014
def _candidate_unit_type(candidate: dict[str, Any]) -> str:
    """Return the deterministic Step04 candidate unit semantics.

    Direct-precipitation and mixed candidates are peak-centred monitoring
    episodes. A qualified precursor-only candidate has no precipitation peak,
    so it is represented as a precursor episode rather than being mislabeled
    as a peak episode.
    """
    return (
        "precursor_episode"
        if str(candidate.get("detection_basis")) == "precursor_environment"
        else "peak_episode"
    )

# Original func/step04/pass1_engine.py:1323
def _validate_jsonschema(instance: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    try:
        import jsonschema  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit("jsonschema is required. Install: pip install jsonschema") from exc
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    rendered: list[str] = []
    for error in errors:
        location = ".".join(str(value) for value in error.absolute_path) or "$"
        validator_name = str(error.validator)
        if validator_name == "maxItems":
            rendered.append(f"{location}: item count exceeds maximum {error.validator_value}")
        elif validator_name == "minItems":
            rendered.append(f"{location}: item count is below minimum {error.validator_value}")
        elif validator_name == "required":
            rendered.append(f"{location}: required property is missing")
        elif validator_name == "additionalProperties":
            unexpected: list[str] = []
            if isinstance(error.instance, dict) and isinstance(error.schema, dict):
                allowed = error.schema.get("properties")
                if isinstance(allowed, dict):
                    unexpected = sorted(str(key) for key in set(error.instance) - set(allowed))
            if unexpected:
                rendered.append(
                    f"{location}: unsupported properties are present: {unexpected}"
                )
            else:
                rendered.append(f"{location}: unsupported property is present")
        elif validator_name == "enum":
            rendered.append(f"{location}: value is outside the allowed enum")
        elif validator_name == "type":
            rendered.append(f"{location}: value has an invalid type")
        elif validator_name == "pattern":
            rendered.append(f"{location}: value does not match the required pattern")
        elif validator_name == "const":
            rendered.append(f"{location}: value does not match the required constant")
        else:
            rendered.append(f"{location}: schema {validator_name} constraint failed")
    return rendered

# Original func/step04/pass1_engine.py:1365
def _normalize_message_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        texts: list[str] = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                texts.append(item["text"])
            elif isinstance(item, str):
                texts.append(item)
        return "".join(texts)
    if value is None:
        return ""
    return str(value)

# Original func/step04/pass1_engine.py:1671
def derive_stable_batches(
    attachment_manifest: dict[str, Any],
    *,
    max_images_per_request: int,
) -> list[dict[str, Any]]:
    if max_images_per_request < 1 or max_images_per_request > 4:
        raise ValueError("max_images_per_request must be between 1 and 4")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in attachment_manifest.get("attachments", []):
        tier = str(item.get("analysis_tier", "unknown"))
        group_id = str(item.get("group_id", "ungrouped"))
        groups[(tier, group_id)].append(item)

    tier_order = {"temporal_core": 0, "synoptic_context": 1}
    batches: list[dict[str, Any]] = []
    for (tier, group_id), items in sorted(
        groups.items(), key=lambda pair: (tier_order.get(pair[0][0], 99), pair[0][0], pair[0][1])
    ):
        items.sort(key=lambda item: (str(item.get("product_id")), int(item.get("forecast_hour_start", 0))))
        for chunk_index in range(0, len(items), max_images_per_request):
            chunk = items[chunk_index : chunk_index + max_images_per_request]
            suffix = "" if len(items) <= max_images_per_request else f"_P{chunk_index // max_images_per_request + 1:02d}"
            batch_id = f"PASS1_OBS_{tier}_{group_id}{suffix}".upper()
            batches.append(
                {
                    "batch_id": batch_id,
                    "stage": "image_observation",
                    "analysis_tier": tier,
                    "group_id": group_id,
                    "attachment_count": len(chunk),
                    "attachment_ids": [str(item["asset_id"]) for item in chunk],
                    "forecast_hours": sorted(
                        {int(hour) for item in chunk for hour in item.get("forecast_hours", [])}
                    ),
                    "source_panel_count": sum(len(item.get("source_panels", [])) for item in chunk),
                }
            )
    return batches

# Original func/step04/pass1_engine.py:1711
def normalize_batch_plan(
    batch_plan: dict[str, Any],
    attachment_manifest: dict[str, Any],
    *,
    max_images_per_request: int,
) -> list[dict[str, Any]]:
    planned = batch_plan.get("batches")
    use_planned = isinstance(planned, list) and planned
    batches: list[dict[str, Any]] = []
    if use_planned:
        for item in planned:
            if not isinstance(item, dict):
                use_planned = False
                break
            ids = [str(value) for value in item.get("attachment_ids", [])]
            if not ids or len(ids) > max_images_per_request or str(item.get("stage", "")) not in {
                "image_observation",
                "pass1_image_observation",
            }:
                use_planned = False
                break
            row = dict(item)
            row["attachment_ids"] = ids
            row["attachment_count"] = len(ids)
            batches.append(row)
    if not use_planned:
        batches = derive_stable_batches(
            attachment_manifest,
            max_images_per_request=max_images_per_request,
        )

    declared_ids = [str(item["asset_id"]) for item in attachment_manifest.get("attachments", [])]
    planned_ids = [asset_id for batch in batches for asset_id in batch["attachment_ids"]]
    if Counter(planned_ids) != Counter(declared_ids):
        missing = sorted(set(declared_ids) - set(planned_ids))
        extra = sorted(set(planned_ids) - set(declared_ids))
        duplicates = sorted(asset_id for asset_id, count in Counter(planned_ids).items() if count > 1)
        raise ValueError(
            f"batch attachment coverage mismatch missing={missing} extra={extra} duplicates={duplicates}"
        )
    return batches

# Original func/step04/pass1_engine.py:1840
def _normalize_direct_precipitation_signal(value: Any) -> dict[str, Any]:
    allowed = {"not_applicable", "absent", "weak", "present", "uncertain"}
    if not isinstance(value, dict):
        return {"status": "uncertain", "significant_forecast_hours": [], "rationale": "모델 강수 예측 신호 분류가 누락되었습니다."}
    status = str(value.get("status", "uncertain")).strip()
    if status not in allowed:
        status = "uncertain"
    hours: list[int] = []
    for raw in value.get("significant_forecast_hours", []):
        try:
            hours.append(int(raw))
        except (TypeError, ValueError):
            continue
    rationale = re.sub(r"\s+", " ", str(value.get("rationale", "")).strip())
    return {
        "status": status,
        "significant_forecast_hours": sorted(set(hours)),
        "rationale": rationale[:220],
    }

# Original func/step04/pass1_engine.py:1861
def _direct_precipitation_audit_from_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    precip_rows = [
        row for row in rows
        if isinstance(row, dict) and str(row.get("product_id")) == "QL_PRECIP_6H"
    ]
    if not precip_rows:
        return {
            "status": "not_applicable",
            "source_asset_ids": [],
            "significant_forecast_hours": [],
            "rationale": "이 그룹에는 모델 강수 예측 overview가 없습니다.",
        }
    priority = {"present": 4, "weak": 3, "uncertain": 2, "absent": 1, "not_applicable": 0}
    signals = [
        _normalize_direct_precipitation_signal(row.get("direct_precipitation_signal"))
        for row in precip_rows
    ]
    status = max((signal["status"] for signal in signals), key=lambda item: priority[item])
    hours = sorted({
        hour
        for signal in signals
        for hour in signal["significant_forecast_hours"]
    })
    if status in {"absent", "not_applicable"}:
        hours = []
    rationales = _unique_nonempty_strings(signal.get("rationale") for signal in signals)
    return {
        "status": status,
        "source_asset_ids": [str(row.get("asset_id")) for row in precip_rows],
        "significant_forecast_hours": hours,
        "rationale": _truncate_utf8_text(" ".join(rationales), max_bytes=220) if rationales else "모델 강수 예측 신호를 구조화하여 통합했습니다.",
    }

# Original func/step04/pass1_engine.py:1895
def _validate_direct_precipitation_signal_for_asset(
    signal: Any,
    *,
    product_id: str,
    allowed_hours: Iterable[int],
    path: str,
) -> list[str]:
    errors: list[str] = []
    normalized = _normalize_direct_precipitation_signal(signal)
    status = normalized["status"]
    hours = set(normalized["significant_forecast_hours"])
    allowed = {int(value) for value in allowed_hours}
    if hours - allowed:
        errors.append(f"{path}.significant_forecast_hours contains values outside the asset: {sorted(hours - allowed)}")
    if product_id == "QL_PRECIP_6H":
        if status == "not_applicable":
            errors.append(f"{path}.status cannot be not_applicable for QL_PRECIP_6H")
        if status in {"present", "weak"} and not hours:
            errors.append(f"{path}.significant_forecast_hours is required when status={status}")
        if status == "absent" and hours:
            errors.append(f"{path}.significant_forecast_hours must be empty when status=absent")
    else:
        if status != "not_applicable":
            errors.append(f"{path}.status must be not_applicable for non-precipitation product")
        if hours:
            errors.append(f"{path}.significant_forecast_hours must be empty for non-precipitation product")
    if not normalized["rationale"]:
        errors.append(f"{path}.rationale must not be empty")
    return errors

# Original func/step04/pass1_engine.py:1926
def validate_batch_observation(
    observation: dict[str, Any],
    *,
    schema: dict[str, Any],
    package_id: str,
    batch: dict[str, Any],
    attachment_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    errors = _validate_jsonschema(observation, schema)
    expected_ids = [str(value) for value in batch["attachment_ids"]]
    if observation.get("package_id") != package_id:
        errors.append("package_id does not match Step04 input package")
    if observation.get("batch_id") != batch["batch_id"]:
        errors.append("batch_id does not match batch plan")
    if observation.get("analysis_tier") != batch.get("analysis_tier"):
        errors.append("analysis_tier does not match batch plan")
    if observation.get("group_id") != batch.get("group_id"):
        errors.append("group_id does not match batch plan")
    if int(observation.get("observed_attachment_count", -1)) != len(expected_ids):
        errors.append("observed_attachment_count does not match batch attachment count")
    rows = observation.get("attachment_observations", [])
    if isinstance(rows, list):
        observed_ids = [str(item.get("asset_id")) for item in rows if isinstance(item, dict)]
        if Counter(observed_ids) != Counter(expected_ids):
            errors.append(
                f"attachment_observations coverage mismatch expected={expected_ids} actual={observed_ids}"
            )
        for item in rows:
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get("asset_id"))
            source = attachment_by_id.get(asset_id)
            if source is None:
                errors.append(f"unknown observed asset_id: {asset_id}")
                continue
            if item.get("product_id") != source.get("product_id"):
                errors.append(f"product_id mismatch for {asset_id}")
            actual_hours = [int(value) for value in item.get("forecast_hours", [])]
            expected_hours = [int(value) for value in source.get("forecast_hours", [])]
            if actual_hours != expected_hours:
                errors.append(f"forecast_hours mismatch for {asset_id}")
            errors.extend(
                _validate_direct_precipitation_signal_for_asset(
                    item.get("direct_precipitation_signal"),
                    product_id=str(source.get("product_id")),
                    allowed_hours=expected_hours,
                    path=f"attachment_observations[{asset_id}].direct_precipitation_signal",
                )
            )
    return errors

# Original func/step04/pass1_engine.py:1979
def normalize_batch_observation_contract(
    value: dict[str, Any],
    *,
    package_id: str,
    batch: dict[str, Any],
    attachments_in_payload_order: list[dict[str, Any]],
    include_batch_synthesis: bool = True,
) -> dict[str, Any]:
    """Bind VLM observations to immutable manifest metadata by payload order."""
    if not isinstance(value, dict):
        return value

    normalized = dict(value)
    normalized["package_id"] = package_id
    normalized["batch_id"] = batch["batch_id"]
    normalized["analysis_tier"] = batch.get("analysis_tier")
    normalized["group_id"] = batch.get("group_id")
    normalized["observed_attachment_count"] = len(attachments_in_payload_order)
    rows = normalized.get("attachment_observations")
    if isinstance(rows, list):
        normalized_rows: list[Any] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or index >= len(attachments_in_payload_order):
                normalized_rows.append(row)
                continue
            source = attachments_in_payload_order[index]
            normalized_rows.append(
                {
                    **row,
                    "asset_id": source["asset_id"],
                    "product_id": source["product_id"],
                    "forecast_hours": [int(item) for item in source["forecast_hours"]],
                    "direct_precipitation_signal": _normalize_direct_precipitation_signal(
                        row.get("direct_precipitation_signal")
                    ),
                }
            )
        normalized["attachment_observations"] = normalized_rows
    if not include_batch_synthesis:
        normalized["cross_product_synthesis"] = []
        normalized["batch_uncertainties"] = []
    return normalized

# Original func/step04/pass1_engine.py:2023
def normalize_final_response_contract(
    value: dict[str, Any],
    *,
    package_id: str,
) -> dict[str, Any]:
    """Inject the deterministic Step04 package ID before final validation."""
    if not isinstance(value, dict):
        return value

    normalized = dict(value)
    normalized["package_id"] = package_id
    return normalized

# Original func/step04/pass1_engine.py:2037
def validate_candidate_discovery_narrative_language(response: dict[str, Any]) -> list[str]:
    """Strictly validate forecaster-facing discovery text as Korean narrative."""
    errors = validate_step04_discovery_narrative(response)
    for candidate_index, candidate in enumerate(response.get("candidates", [])):
        if not isinstance(candidate, dict):
            continue
        separation_reason = candidate.get("separation_reason")
        if separation_reason is not None and len(str(separation_reason)) > 200:
            errors.append(
                f"candidates[{candidate_index}].separation_reason must be concise (<=200 characters)"
            )
    return errors

# Original func/step04/pass1_engine.py:2051
def validate_candidate_plan_narrative_language(response: dict[str, Any]) -> list[str]:
    """Strictly validate forecaster-facing candidate-plan text as Korean narrative."""
    return validate_step04_plan_narrative(response)

# Original func/step04/pass1_engine.py:2056
def validate_step04_narrative_language(response: dict[str, Any]) -> list[str]:
    """Validate all Step04 forecaster-facing narrative fields."""
    return validate_forecaster_step04_narrative(response)

# Original func/step04/pass1_engine.py:2211
def _is_system_direct_precipitation_anchor_row(row: dict[str, Any]) -> bool:
    """Return whether an internal row is the system-owned precipitation anchor."""
    return bool(row.get("_system_direct_precipitation_anchor") is True)

# Original func/step04/pass1_engine.py:2216
def _is_system_direct_precipitation_anchor_check(check: dict[str, Any]) -> bool:
    """Return whether a canonical Check is the system-owned precipitation anchor."""
    intent = check.get("intent_contract")
    return bool(
        isinstance(intent, dict)
        and intent.get("direct_precipitation_anchor") is True
        and str(intent.get("source_check_key") or "")
        == SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY
    )

# Original func/step04/pass1_engine.py:2538
def _repair_rules_for_validation_errors(validation_errors: list[str]) -> list[str]:
    """Return repair guidance using only the Step04 AI intent contract.

    Step04 AI owns meteorological verification intent. Python owns the
    executable diagnostic contract. Repair instructions therefore never ask
    the model to edit diagnostic_id, check_scope, analysis_methods,
    variable_role, or required_signal_components.
    """
    joined = "\n".join(str(error) for error in validation_errors)
    rules: list[str] = []

    if "SPATIAL_ANALYSIS_GRID_INSUFFICIENT" in joined:
        rules.extend([
            "공간 중첩·공간 구조의 변화는 단일 원본 격자만 보이는 지도에서 판단할 수 없습니다. 후보 candidate_area와 강수 앵커는 절대로 확대하거나 제거하지 마십시오.",
            "공간 구조 판독에 필요한 경우 candidate_area를 포함하면서 실제 주변 원본 격자를 보여주는 context_domain을 사용하고, 해당 verification_request의 domain_scope=context로 지정하십시오.",
            "context_domain도 너무 좁으면 실제 주변을 포함하도록 context_domain_reason과 함께 설정하십시오. 기존 가설을 삭제하거나 물리적 중첩을 추정해서 단정하지 마십시오.",
            "주변 맥락 영상 전체에서 강수와 환경장이 동시에 보이더라도 후보 핵심영역 안의 중첩으로 오인하지 마십시오. STEP05는 후보 핵심영역을 테두리로 따로 표시합니다.",
            "직접강수 존재 확인, 단일 격자의 정량값, SYSTEM 필수 앵커는 그대로 유지하십시오.",
        ])

    if "insufficient forecast-hour coverage" in joined:
        rules.extend([
            "시간 변화 및 공간 구조 변화 검증에는 서로 다른 실제 예측시각을 최소 2개 참조해야 합니다. required=2를 낮추지 마십시오.",
            "입력의 time_feasibility_contract.effective_hours와 available_analysis_phase_types에서 실제 비교 가능한 시각을 확인하십시오.",
            "해당 check_key의 phase_keys가 가리키는 phase들의 forecast_hours 합집합이 2개 이상인지 확인하십시오. 필요하면 phase와 Check를 같은 chunk에서 함께 재구성하십시오.",
            "두 시각이 실제 존재하지 않으면 시간을 만들지 말고 오류를 그대로 보존하십시오.",
        ])

    if "unresolved check reference after normalization" in joined:
        rules.extend(
            [
                "hypothesis의 supporting_check_keys/opposing_check_keys는 실제 존재하는 Check만 참조해야 합니다.",
                "이전 chunk의 Check를 참조할 때는 check_reference_failure_context.existing_check_keys에 있는 key만 그대로 사용하고, 번호를 추정하여 존재하지 않는 chunkXX:CHKYY key를 만들지 마십시오.",
                "현재 chunk에서 생성한 Check는 해당 verification_request에 선언한 check_key 로컬값을 hypothesis에서 정확히 참조하십시오. 필요한 근거 Check가 없다면 의미 있는 verification_request를 현재 chunk에 새로 만들고 그 key를 참조하십시오. 단순히 오류 참조를 삭제하여 가설의 의미를 훼손하지 마십시오.",
            ]
        )

    if "CANDIDATE_OPERATIONAL_SCOPE_INVALID" in joined:
        rules.extend(
            [
                "CANDIDATE_OPERATIONAL_SCOPE_INVALID는 direct_precipitation/mixed 후보의 candidate_area가 현행 운영 예보권역과 전혀 겹치지 않는다는 뜻입니다.",
                "candidate_discovery input의 candidate_operational_scope.eligible_forecast_regions를 권위 있는 경계로 사용하십시오. source_corridor에만 있는 강수는 독립 direct/mixed 후보로 만들지 말고 그룹요약의 주변 맥락 또는 선행환경 근거로 남기십시오.",
                "오류를 없애기 위해 candidate_area를 국내/해상 운영권역까지 임의로 확대·이동하지 마십시오. 실제 강수 영향 핵심영역이 운영권역과 겹치지 않으면 해당 direct/mixed 후보 자체를 제거하고 candidates와 candidate_evidence의 순서를 다시 맞추십시오.",
                "group_summaries.direct_precipitation_audit.status=present는 원본 영상 관찰 보존용이며, 운영권역 밖 신호 자체가 Candidate 생성 의무를 뜻하지 않습니다. 강제 coverage는 system_direct_precipitation_coverage_guidance.required_reference_signals만 따르십시오.",
            ]
        )
    if "SYSTEM_DIRECT_PRECIPITATION_COVERAGE_REQUIRED" in joined:
        rules.extend(
            [
                "SYSTEM_DIRECT_PRECIPITATION_COVERAGE_REQUIRED는 candidate reference threshold 이상인 운영권역 정량 강수 신호가 아직 어떤 direct/mixed 후보에도 연결되지 않았다는 뜻입니다.",
                "system_direct_precipitation_coverage_guidance.required_reference_signals의 forecast_hour, region_id, region_name_ko, max_location을 확인하고 실제 영상·그룹요약과 일치하는 후보에 포함시키십시오.",
                "동일 기상계라는 근거가 있으면 기존 후보를 사용할 수 있지만, 서로 떨어진 강수군을 coverage만 맞추기 위해 하나의 큰 bbox로 합치지 마십시오. 필요한 경우 운영권역과 실제로 겹치는 별도 후보를 생성하십시오.",
                "일치하는 QL_PRECIP_6H asset/hour를 candidate_evidence에 포함하고 정량 임계값을 낮추거나 SYSTEM 신호를 삭제하지 마십시오.",
            ]
        )

    if "OPERATIONAL_REGION_TARGET_EMPTY" in joined:
        rules.extend(
            [
                "OPERATIONAL_REGION_TARGET_EMPTY는 immutable candidate_area와 겹치는 실행 가능한 operational_land/marine_macro 권역이 없는데 운영권역 정량자료를 요구했다는 뜻입니다.",
                "candidate_operational_region_overlap.regional_verification_available=false이면 regional_intensity, temporal_change, precipitation_amount를 사용하지 마십시오. spatial_distribution, spatial_evolution, multivariable_alignment 또는 적절한 vertical mode처럼 후보/맥락 공간장에서 실행 가능한 검증으로 다시 구성하십시오.",
                "오류를 피하려고 candidate_area를 운영권역 쪽으로 확대·이동하거나 존재하지 않는 region_id를 만들지 마십시오. 선행환경/source-corridor 후보는 해당 실제 위치를 유지한 채 공간장 검증을 사용해야 합니다.",
                "quantitative_scope.region_ids가 필요한 명제도 selectable_regions가 비어 있으면 작성하지 마십시오. 정량 운영권역 비교 대신 실제 제공되는 공간장으로 판정 가능한 기상학적 expected_signal을 선택하십시오.",
            ]
        )

    if "CANDIDATE_QUANTITATIVE_SCOPE_UNAVAILABLE" in joined:
        rules.extend(
            [
                "CANDIDATE_QUANTITATIVE_SCOPE_UNAVAILABLE은 immutable candidate_area와 겹치는 실행 가능한 operational_land/marine_macro 권역이 없어 Step05/Step07의 운영권역 정량 판정을 구성할 수 없다는 뜻입니다.",
                "candidate_area를 확대·이동하거나 source_corridor/marine_detail을 정량 운영권역으로 사용하지 마십시오. majority + not_strengthen 명제를 정성 판독으로 바꾸거나 expected_state/scope_quantifier를 바꾸어 같은 결론인 것처럼 처리하지 마십시오.",
                "Check 하나의 수정으로 해결할 수 없는 후보 범위 제약이므로 원래 후보·가설 컨텍스트에서 현재 chunk 전체를 다시 생성하십시오. 원래 후보 가설의 기상학적 목적은 유지하고, 실제 입력에 있는 공간장·시간·분석 모드로 실행 가능한 검증요청을 구성하십시오.",
                "원래 가설 자체가 운영권역 다수결 판정을 요구한다면 그 명제를 검증 완료로 가장하지 마십시오. 다른 공간장 검증으로 확인할 수 있는 가설과 구분하여 미검증 범위를 명시하십시오.",
            ]
        )

    if "EXPECTED_SIGNAL_PROCEDURAL_TEXT_INVALID" in joined:
        rules.extend(
            [
                "EXPECTED_SIGNAL_PROCEDURAL_TEXT_INVALID는 expected_signal이 기상학적 예상 상태가 아니라 분석 절차를 설명하는 문장이라는 뜻입니다.",
                "오류 check_key의 verification_request 전체를 다시 작성하고 expected_signal은 상세자료에서 확인/부분 확인/미확인/반대 여부를 판정할 수 있는 기상학적 명제로 작성하십시오.",
                "`확인한다`, `비교한다`, `살펴본다`, `판단한다`, `검토한다`, `분석한다`, `평가한다`로 끝내지 마십시오. 예: 단일요소 요청은 `F060 전북 부근에 925 hPa 수렴 중심이 나타난다`. 여러 요소의 중첩은 해당 요소를 모두 선언하고 multivariable_alignment를 사용하십시오.",
            ]
        )
    if "EXPECTED_SIGNAL_VAGUE_COMPARISON_INVALID" in joined:
        rules.extend(
            [
                "EXPECTED_SIGNAL_VAGUE_COMPARISON_INVALID는 `서로 다르다`, `정량적으로 구분된다`, `차이가 나타난다`, 방향 없는 `변화한다`처럼 거의 항상 참이 될 수 있는 문장이라는 뜻입니다.",
                "오류 check_key의 verification_request 전체를 다시 작성하고 어느 시각/영역이 더 강하거나 약한지, 중심이 어디에 있는지, 또는 중첩이 확인될지/약화될지처럼 지지 여부를 반증할 수 있는 방향과 상태를 명시하십시오.",
                "예: `F120 최대 6시간 강수량이 F102보다 크다`, `F120에는 강수 중심과 상승운동 중심의 중첩이 F102보다 약하다`처럼 작성하십시오.",
            ]
        )
    if "EXPECTED_SIGNAL_AMBIGUOUS_ALTERNATIVE_INVALID" in joined:
        rules.extend(
            [
                "EXPECTED_SIGNAL_AMBIGUOUS_ALTERNATIVE_INVALID는 하나의 expected_signal에 서로 반대되는 결과를 `또는/혹은`으로 함께 적었다는 뜻입니다.",
                "한 Check에는 하나의 기대 상태만 작성하십시오. 강화와 약화처럼 상반된 결과 중 하나를 가설로 선택하고, 반대 결과는 Step07에서 반대 신호로 판독할 수 있게 두십시오.",
            ]
        )
    if "EXPECTED_SIGNAL_SPATIAL_EVOLUTION_MODE_MISMATCH" in joined:
        rules.extend(
            [
                "EXPECTED_SIGNAL_SPATIAL_EVOLUTION_MODE_MISMATCH는 현재 verification_mode로는 선언된 기상요소의 이동·재배치·분리·병합·확대·축소를 검증할 수 없다는 뜻입니다.",
                "후보영역 평균이나 정량 강도의 강화·약화가 목적이면 temporal_change/regional_intensity/precipitation_amount에 그 정량 상태만 남기십시오.",
                "중심 위치의 이동이나 공간구조 재조직이 실제 목적이면 별도의 verification_request를 만들고 verification_mode=spatial_evolution을 사용하십시오. 강도 변화와 공간 이동을 한 Check에 섞지 마십시오.",
            ]
        )
    if (
        "SPATIAL_RELATION_VERTICAL_MODE_SCOPE_MISMATCH" in joined
        or "SPATIAL_RELATION_VERIFICATION_MODE_MISMATCH" in joined
    ):
        rules.extend(
            [
                "SPATIAL_RELATION 관련 오류는 verification_mode와 spatial_relation_scope가 서로 다른 종류의 공간판정을 요구한다는 뜻입니다. 오류 문자열에 포함된 check_key, verification_mode, spatial_relation_scope, domain_scope, expected_signal을 함께 확인하십시오.",
                "verification_mode=vertical_structure 또는 vertical_time_evolution이면 spatial_relation_scope는 반드시 not_applicable로 작성하십시오. 단면선이 후보영역 밖의 context_domain까지 확장되더라도 그것은 렌더링 범위일 뿐 수평적인 후보-외부 관계를 뜻하지 않습니다.",
                "expected_signal의 실제 결론이 후보 밖 공급원 위치, 후보 경계 통과, 후보영역으로의 접근·이탈 등 수평 관계라면 vertical mode를 억지로 유지하지 말고 spatial_distribution, spatial_evolution 또는 multivariable_alignment의 별도 verification_request로 구성하십시오.",
                "연직구조와 후보 밖 수평 관계가 모두 필요하면 하나의 요청에 섞지 말고 두 verification_request로 분리하십시오. 스키마 통과를 위해 의미 없이 verification_mode만 바꾸거나 spatial_relation_scope만 바꾸지 마십시오.",
            ]
        )


    if "EXPECTED_SIGNAL_CANDIDATE_AGGREGATE_UNAVAILABLE" in joined:
        rules.extend(
            [
                "EXPECTED_SIGNAL_CANDIDATE_AGGREGATE_UNAVAILABLE는 현재 수평 Step05 정량자료가 후보 bbox 전체를 하나로 합친 평균값을 제공하지 않는데 expected_signal이 `후보영역 평균` 또는 `후보지역 평균`을 요구했다는 뜻입니다.",
                "temporal_change/regional_intensity/precipitation_amount에서는 후보영역과 겹치는 운영권역별 평균·최대·순위처럼 실제 제공되는 공간 통계 단위로 다시 작성하십시오.",
                "후보영역 전체의 단일 평균을 새로 가정하거나 만들어 내지 마십시오.",
            ]
        )
    if "EXPECTED_SIGNAL_SPATIAL_EXTENT_MODE_MISMATCH" in joined:
        rules.extend(
            [
                "EXPECTED_SIGNAL_SPATIAL_EXTENT_MODE_MISMATCH는 평균·최대값 등 정량 강도를 확인하는 모드에서 실제 기상장의 면적·폭·공간범위·확대·축소까지 한 Check에 요구했다는 뜻입니다.",
                "평균·강도 변화는 temporal_change/regional_intensity/precipitation_amount에 남기십시오. 여러 운영권역의 평균값을 근거로 범지역적 강도 경향을 표현해야 한다면 `여러 운영권역에서 평균값의 뚜렷한 강화가 없다`처럼 작성할 수 있습니다.",
                "기상장 자체의 면적·폭·영역 확대·축소를 판단하려면 spatial_distribution 또는 spatial_evolution의 별도 verification_request로 분리하십시오.",
            ]
        )
    if "VERIFICATION_MOISTURE_VECTOR_SEMANTICS_MISMATCH" in joined:
        rules.extend(
            [
                "VERIFICATION_MOISTURE_VECTOR_SEMANTICS_MISMATCH는 수증기 수송 수평상품의 화살표를 수증기 플럭스 벡터로 해석했거나, 정량 시계열만으로 바람 방향을 판정하려 했다는 뜻입니다.",
                "QL_MOISTURE_FLUX850_WIND850의 음영은 850 hPa 수증기 플럭스 크기이고 화살표는 850 hPa 실제 바람입니다. `수증기 플럭스 벡터/화살표/흐름`이라고 쓰지 마십시오.",
                "방향성이 필요하면 `수증기 플럭스가 큰 영역에서 850 hPa 바람이 후보 방향으로 이어진다`처럼 수증기 플럭스 크기와 850 hPa 바람 방향을 구분해 작성하십시오. temporal_change에서는 바람 방향을 판정하지 마십시오.",
            ]
        )

    if "VERIFICATION_STANDALONE_MOISTURE_SEMANTICS_MISMATCH" in joined:
        rules.extend(
            [
                "VERIFICATION_STANDALONE_MOISTURE_SEMANTICS_MISMATCH는 독립 수분공급 수평 Check가 실제 상품에 없는 대기 전층 수분량(TCW)을 요구했다는 뜻입니다.",
                "독립 moisture_supply의 수평상품은 850 hPa 수증기 플럭스 크기 음영과 실제 850 hPa 바람입니다. spatial_distribution/spatial_evolution에서는 두 의미를 구분하고, regional_intensity/temporal_change에서는 수증기 플럭스 크기 통계만 사용하십시오.",
                "대기 전층 수분량(TCW)은 multivariable_alignment 합성자료의 수분 요소에서만 사용하십시오.",
            ]
        )
    if "VERIFICATION_VERTICAL_SECTION_MOISTURE_SEMANTICS_MISMATCH" in joined:
        rules.extend(
            [
                "VERIFICATION_VERTICAL_SECTION_MOISTURE_SEMANTICS_MISMATCH는 연직단면/시간-고도 Check가 수평 수증기 플럭스 또는 대기 전층 수분량(TCW)을 요구했다는 뜻입니다.",
                "연직 수분 구조는 비습과 고수분층의 고도·두께·연직분포로 작성하십시오. 수평 850 hPa 수증기 플럭스는 별도 moisture_supply 수평 Check로 분리하십시오.",
            ]
        )
    if "VERIFICATION_VERTICAL_SECTION_SHEAR_SEMANTICS_MISMATCH" in joined:
        rules.extend(
            [
                "VERIFICATION_VERTICAL_SECTION_SHEAR_SEMANTICS_MISMATCH는 연직단면/시간-고도 자료만으로 전용 850–500 hPa 연직바람시어를 판정하려 했다는 뜻입니다.",
                "연직단면에서는 풍속과 단면평행 바람의 연직구조만 확인하십시오. 850–500 hPa 시어가 필요하면 organization_persistence의 spatial_distribution 또는 spatial_evolution을 별도 요청으로 만드십시오.",
            ]
        )

    if "VERIFICATION_VERTICAL_SECTION_PRECIPITATION_CONTEXT_MISMATCH" in joined:
        rules.extend(
            [
                "VERIFICATION_VERTICAL_SECTION_PRECIPITATION_CONTEXT_MISMATCH는 vertical_structure/vertical_time_evolution의 expected_signal이 연직자료에서 직접 제공되지 않는 강수를 중첩·일치의 실제 검증 대상으로 포함했다는 뜻입니다.",
                "강수대·강수영역·강수중심·강수 피크는 단면 위치나 비교 시각을 정하는 종속적인 기준점으로는 언급할 수 있지만, 강수 자체가 고수분층·상승운동·바람구조와 겹치거나 일치한다는 결론은 연직 Check에서 요구하지 마십시오.",
                "연직 Check의 실제 판정 명제는 선언한 moisture_supply(비습·고수분층), mid_upper_forcing(상승·하강운동), organization_persistence(풍속·단면평행 바람)의 연직구조로 작성하십시오. vertical_structure에서는 강수 기준을 cross_section_strategy, reference_band_axis, cross_section_line.selection_reason에 우선 기록하십시오.",
                "강수와 환경장의 실제 공간적 중첩이 판단 대상이면 연직 Check에 포함하지 말고 별도의 horizontal multivariable_alignment verification_request로 분리하십시오.",
            ]
        )
    if "VERIFICATION_MULTIVARIABLE_INTENT_MISMATCH" in joined:
        rules.extend(
            [
                "VERIFICATION_MULTIVARIABLE_INTENT_MISMATCH는 expected_signal이 선언하지 않은 다른 기상요소와의 실제 중첩·일치·동반 관계를 검증 대상으로 요구한다는 뜻입니다.",
                "오류 check_key의 verification_request 전체를 다시 작성하십시오. diagnostic_id, check_scope, analysis_methods 같은 내부 실행필드는 작성하지 마십시오.",
                "한 기상요소 자체의 변화가 목적이면 signal_components는 그 요소 하나만 유지하고 expected_signal도 그 요소에서 실제 확인할 분포·강도·시간변화로 제한하십시오.",
                "강수 최대 시각 이전·이후, 강수 발생 전후처럼 다른 기상요소를 시간 기준점으로 언급하는 것은 허용됩니다. 이 경우 그 기준 요소와의 공간적 중첩·일치·동반을 결론으로 요구하지 마십시오.",
                "둘 이상의 기상요소의 공간적 중첩·일치가 실제 목적이면 signal_components에 실제 비교할 요소를 모두 포함하고 verification_mode=multivariable_alignment를 사용하십시오.",
            ]
        )
    if "VERIFICATION_WIND_STRUCTURE_SEMANTICS_MISMATCH" in joined:
        rules.extend(
            [
                "수평장의 organization_persistence 요청은 내부적으로 850-500 hPa 연직바람시어 자료로 실행됩니다. 연직바람시어 벡터는 실제 850 hPa 바람이나 수분 유입 벡터가 아닙니다.",
                "연직바람시어를 확인하려는 경우 signal_components=[organization_persistence]를 유지하고 expected_signal을 시어의 크기, 방향 또는 공간구조만 확인하도록 다시 작성하십시오.",
                "하층 유입축, 850 hPa 흐름의 방향·지속성, 수증기 수송이 실제 목적이면 signal_components=[moisture_supply]로 바꾸고 spatial_distribution 또는 spatial_evolution을 사용하십시오.",
            ]
        )
    if "requires exactly one signal component" in joined:
        rules.append(
            "해당 verification_mode는 한 기상요소만 확인합니다. 오류 check_key의 signal_components를 실제 분석 대상 하나로 제한하십시오. 둘 이상의 공간적 관계가 목적이면 verification_mode=multivariable_alignment로 구성하십시오."
        )
    if "multivariable_alignment requires at least two" in joined:
        rules.append(
            "multivariable_alignment는 실제로 공간적 중첩을 비교할 signal_components를 2개 이상 선택해야 합니다. direct_precipitation, moisture_supply, low_level_forcing, mid_upper_forcing 중 필요한 요소만 사용하십시오."
        )
    if "'6시간 누적 강수량' 대신" in joined or '"6시간 누적 강수량" 대신' in joined:
        rules.extend(
            [
                "`6시간 누적 강수량`은 6시간 증분 강수와 초기시각부터의 누적강수를 혼동시키는 금지 표현입니다.",
                "6시간 구간 강수를 뜻하면 `6시간 강수량`으로 작성하십시오. 초기시각부터 해당 예측시각까지 누적된 총량을 뜻할 때만 `초기시각부터의 누적강수량`이라고 작성하십시오.",
                "precipitation_amount의 시간별 강도·최대위치 검증은 `6시간 강수량`을 기준으로 작성하고, expected_signal·decision_relevance·purpose 전체에서 모호한 표현을 제거하십시오.",
            ]
        )

    if "precipitation_amount requires direct_precipitation" in joined:
        rules.append(
            "verification_mode=precipitation_amount는 signal_components=[direct_precipitation]에만 사용하십시오."
        )
    if "temporal_change does not support organization_persistence" in joined:
        rules.append(
            "organization_persistence의 여러 시각 바람구조를 비교하려면 verification_mode=spatial_evolution을 사용하십시오. temporal_change를 사용하지 마십시오."
        )
    if "regional_intensity does not support organization_persistence" in joined:
        rules.append(
            "organization_persistence는 regional_intensity를 사용하지 말고 spatial_distribution으로 공간구조를 확인하십시오."
        )
    if "vertical_structure" in joined and "only supports" in joined:
        rules.append(
            "vertical_structure에는 moisture_supply, mid_upper_forcing, organization_persistence 중 실제 단면에서 확인할 요소만 사용하고 단면 위치정보를 모두 유효하게 지정하십시오."
        )
    if "vertical_time_evolution" in joined and "only supports" in joined:
        rules.append(
            "vertical_time_evolution에는 moisture_supply, mid_upper_forcing, organization_persistence 중 실제 시간-고도 구조에서 확인할 요소만 사용하십시오."
        )
    if "QUANTITATIVE_SCOPE_PHASE_MISMATCH" in joined:
        rules.extend(
            [
                "QUANTITATIVE_SCOPE_PHASE_MISMATCH는 정량 명제가 요구하는 F시각과 verification_request/atomic claim이 참조하는 analysis phase가 일치하지 않는다는 뜻입니다.",
                "quantitative_scope.forecast_hours를 오류 회피 목적으로 임의 삭제하지 마십시오. 두 시각 비교가 기상학적으로 필요하면 그 시각을 실제로 포함하는 analysis phase를 선언하고 verification_request.phase_keys와 atomic_claim.phase_keys가 모두 해당 phase를 참조하도록 candidate-plan chunk 전체를 다시 구성하십시오.",
                "필요한 비교시각 또는 비교 자체가 기상학적으로 불필요하다면 원래 후보 컨텍스트에서 정량 명제와 phase 계획을 함께 다시 결정하십시오. 이전 실패 객체의 일부 필드만 patch하지 마십시오.",
            ]
        )

    if "QUANTITATIVE_ATOMICITY_INVALID" in joined:
        rules.extend([
            "QUANTITATIVE_ATOMICITY_INVALID는 한 verification_request 안에 실행 가능한 정량 명제가 둘 이상 선언된 구조적 오류입니다. 하나의 Check만 재생성해서는 별도의 요청을 추가할 수 없으므로 현재 candidate-plan chunk 전체를 재구성하십시오.",
            "서로 다른 정량 검증 명제는 각각 고유한 check_key를 가진 별도 verification_request로 작성하고, 각 요청의 atomic_claims에는 그 정량 명제 하나만 두십시오. expected_signal 및 claim_statement도 그 한 명제에 맞추십시오.",
            "오류를 피하려고 원래 기상학적 판단 기준 중 하나를 조용히 삭제하거나 quantitative_scope를 무조건 null로 바꾸지 마십시오. 기존 명제들이 실제로 별도 검증이 필요하면 모두 보존하십시오. 영상 공간구조 해석만 필요한 명제라면 실행 가능한 정량 검증인 것처럼 표시하지 마십시오.",
            "분리한 새 Check를 hypothesis의 supporting_check_keys/opposing_check_keys가 참조해야 한다면 실제 새 check_key를 참조하도록 그래프 전체를 일관되게 다시 작성하십시오. 기존 Check의 참조가 끊어지지 않게 하십시오.",
            "verification_requests가 스키마 허용 최대 개수에 도달한 경우 명제를 무단 삭제하거나 결합하지 마십시오. 한 chunk 안에서 유효한 계획을 만들 수 없다면 검증 실패를 유지하십시오.",
        ])

    if "QUANTITATIVE_SCOPE_COMPARISON_INVALID" in joined:
        rules.extend(
            [
                "QUANTITATIVE_SCOPE_COMPARISON_INVALID는 expected_state가 요구하는 시간 비교 방식과 quantitative_scope.comparison_forecast_hours가 맞지 않는다는 뜻입니다.",
                "increase/decrease/strengthen/weaken은 forecast_hours의 시간 오름차순 자체가 변화 방향을 정의하므로 comparison_forecast_hours=null로 작성하십시오. 두 시각의 좌항·우항을 명시하는 greater_than/less_than에서만 comparison_forecast_hours=[좌항 시각, 우항 시각]을 사용하십시오.",
                "스키마 통과만을 위해 decrease를 greater_than으로 바꾸거나 increase를 less_than으로 바꾸지 마십시오. 원래 기상학적 명제가 시간 변화인지 두 특정 시각의 대소 비교인지에 따라 expected_state를 유지하거나 verification_request 전체를 다시 작성하십시오.",
            ]
        )
    if (
        "QUANTITATIVE_SCOPE_CONTRACT_INVALID" in joined
        and "forecast_hours must be unique chronological forecast hours" in joined
    ):
        rules.extend(
            [
                "quantitative_scope.forecast_hours는 검증에 사용할 F시각 집합입니다. 동일 시각을 중복하지 말고, 최소 2개의 서로 다른 F시각을 시간 오름차순으로 작성하십시오.",
                "예를 들어 [18,24]는 허용되지만 [24,18], [18,24,18], [18,18]은 허용되지 않습니다. 좌항·우항의 방향은 greater_than/less_than에서만 comparison_forecast_hours로 별도 표현하십시오.",
                "오류를 피하려고 필요한 비교시각을 삭제하지 마십시오. 중복/순서 오류만 고치고, 고유 시각이 1개뿐이라면 원래 가설에 필요한 두 시각을 다시 선택하여 verification_request 전체를 재생성하십시오.",
            ]
        )

    if (
        "QUANTITATIVE_SCOPE_CONTRACT_INVALID" in joined
        and "majority not_strengthen" in joined
    ):
        rules.extend(
            [
                "QUANTITATIVE_SCOPE_CONTRACT_INVALID의 majority + not_strengthen 계약은 925 hPa 수렴(convergence925_1e5_s)의 두 시각 변화만 정량 판정합니다. quantitative_scope.forecast_hours에는 시간순으로 정확히 2개 시각만 넣으십시오.",
                "3개 이상의 시각을 하나의 majority + not_strengthen claim에 넣지 마십시오. 예를 들어 [78,84,90]은 허용되지 않습니다. 피크 전후 두 구간을 모두 확인해야 하면 [78,84]와 [84,90]처럼 각각 별도 verification_request로 분리하고, 각 요청의 atomic_claims도 해당 정량명제 1개만 포함하십시오.",
                "이 계약에서는 feature_id=convergence925_1e5_s, region_scope=candidate_overlap, region_ids=[], comparison_forecast_hours=null, statistic=mean을 유지하십시오. 오류를 피하려고 임의로 다른 feature나 expected_state로 바꾸지 마십시오.",
            ]
        )

    if "QUANTITATIVE_SCOPE_REGION_IDENTITY_MISMATCH" in joined:
        rules.extend(
            [
                "QUANTITATIVE_SCOPE_REGION_IDENTITY_MISMATCH는 자연어 명제의 운영권역명과 quantitative_scope.region_ids가 서로 다른 권역을 가리킨다는 뜻입니다.",
                "오류 문자열의 selected_region_id/selected_region_name_ko와 natural_language_regions 매핑을 권위 있는 운영권역 정보로 사용하십시오. structured region_id를 유지하는 경우 expected_signal과 atomic claim에는 selected_region_name_ko만 사용하고 다른 운영권역 이름을 섞지 마십시오.",
                "다만 원래 기상학적 명제가 natural_language_regions의 다른 권역을 실제 대상으로 한다면 자연어만 치환하지 말고 verification request 전체를 다시 생성하여 quantitative_scope.region_ids와 모든 설명 문자열을 동일한 운영권역으로 일치시키십시오.",
            ]
        )
    if "QUANTITATIVE_ABSOLUTE_THRESHOLD_CONTRACT_MISMATCH" in joined:
        rules.extend([
            "QUANTITATIVE_ABSOLUTE_THRESHOLD_CONTRACT_MISMATCH는 'F096 강수량이 1mm를 넘는다'와 같은 절대 임계값 명제에 F096 대 F090 시간 비교 계약을 잘못 붙인 오류입니다.",
            "현재 specific-v2 quantitative_scope의 greater_than/less_than은 두 F시각의 대소만 판정합니다. 고정 1mm 임계값을 비교시각으로 변환하거나 원래 명제를 임의 삭제하지 마십시오.",
            "고정 임계값 명제는 원래 검증 의미를 보존하는 독립적인 강수 존재/수치 검증 요청으로 다시 구성하십시오. 강수 존재 최소 1mm는 별도 직접강수 판정 계약으로 검증하며, 임의 수치 임계값을 현행 시간비교 정량 계약으로 표현하지 마십시오. 현재 지원되는 방법으로 검증할 수 없다면 검증 실패를 유지하십시오.",
            "해당 오류를 받은 verification_request의 expected_signal과 atomic_claim.claim_statement, expected_state 및 quantitative_scope를 같은 명제로 일치시키십시오. 다른 시각 대비 증가·감소는 필요할 경우 별도 Check로 작성하십시오.",
        ])
    if "QUANTITATIVE_SCOPE_COMPARISON_IDENTITY_MISMATCH" in joined:
        rules.extend(
            [
                "QUANTITATIVE_SCOPE_COMPARISON_IDENTITY_MISMATCH는 greater_than/less_than 자연어 명제의 좌항·우항 시각 순서와 comparison_forecast_hours 순서가 다르다는 뜻입니다.",
                "atomic claim에 F090이 F084보다 크다고 썼다면 comparison_forecast_hours=[90,84]처럼 자연어 좌항·우항과 같은 순서를 사용하십시오.",
            ]
        )
    if "QUANTITATIVE_SCOPE_REGION_UNAVAILABLE" in joined:
        rules.extend(
            [
                "QUANTITATIVE_SCOPE_REGION_UNAVAILABLE는 정량 명제의 region_id가 candidate_area와 실제로 겹치는 운영권역 목록에 없다는 뜻입니다.",
                "오류 문자열의 selectable_region_ids 중 원래 기상학적 명제와 실제 후보영역에 맞는 정확히 하나의 운영권역을 선택하십시오. 존재하지 않는 권역명을 만들거나 후보영역을 확대해 맞추지 마십시오.",
                "적절한 운영권역이 없어 해당 specific 정량 명제를 유지할 수 없다면 공간장 기반의 질적 검증으로 verification_request 전체를 다시 구성하고 quantitative_scope=null로 작성하십시오.",
            ]
        )

    if "COMPACT_PLAN_CONTINUATION_NO_PROGRESS" in joined:
        rules.extend(
            [
                "COMPACT_PLAN_CONTINUATION_NO_PROGRESS는 후속 chunk가 continuation_context.remaining_roles 중 어느 하나도 실제 실행 가능한 검증 구조로 충족하지 못했다는 뜻입니다.",
                "remaining_roles가 하나 이상이면 빈 verification_requests를 반환하지 말고, remaining_role_guidance와 existing_phases의 forecast_hours를 사용하여 최소 하나의 남은 역할을 실제로 감소시키는 verification_request를 작성하십시오.",
                "temporal_evolution과 spatial_evolution이 함께 남아 있으면 둘 이상의 실제 예측시각을 비교하는 verification_mode=spatial_evolution 요청 하나로 두 역할을 함께 충족할 수 있습니다. 단일시각 요청에 coverage_roles만 추가해서는 안 됩니다.",
                "이전 chunk의 phase와 Check를 재작성하지 말고, 필요한 기존 phase는 existing_phases의 phase_key를 그대로 참조하십시오. 새 phase가 필요할 때만 available_forecast_hours 범위에서 추가하십시오.",
            ]
        )

    if "COMPACT_PLAN_UNREFERENCED_PHASE" in joined:
        rules.extend(
            [
                "COMPACT_PLAN_UNREFERENCED_PHASE는 is_complete=true인 최종 candidate-plan chunk에 어떤 verification_request도 참조하지 않는 analysis_phase가 남았다는 뜻입니다.",
                "최종 계획의 모든 analysis_phase는 이전 chunk와 현재 chunk를 합쳐 최소 하나의 verification_request.phase_keys에서 실제로 사용되어야 합니다. 해당 시각대가 기상학적으로 필요하면 그 phase를 사용하는 의미 있는 verification_request 전체를 작성하고 가설과 연결하십시오. 필요하지 않으면 그 analysis_phase 자체를 출력하지 마십시오.",
                "오류의 chunkNN: 접두사는 검증용 namespace입니다. 출력 phase_key에는 접두사를 붙이지 마십시오. 예를 들어 chunk01:P3는 현재 chunk의 로컬 phase_key P3를 뜻합니다.",
                "candidate_peak_forecast_hours를 포함하는 peak phase는 제거하지 말고, 반드시 하나 이상의 적절한 verification_request가 참조하도록 계획 전체를 일관되게 다시 구성하십시오.",
            ]
        )

    if "POSITIVE_ALIGNMENT_MIXED_POLARITY" in joined or "POSITIVE_ALIGNMENT_NON_OVERLAP_CLAIM" in joined:
        rules.extend(
            [
                "POSITIVE_ALIGNMENT 관련 오류는 핵심시각의 강수-환경 결합과 이후 약화·분리·지속성 제한을 하나의 Check에 섞었다는 뜻입니다.",
                "핵심시각의 긍정적 결합은 evidence_role=supporting, verification_mode=multivariable_alignment인 독립 verification_request 하나로 만들고 atomic_claims에는 positive overlap 명제만 남기십시오.",
                "F018 이후 약화, decrease, weaken, not_overlap, split, reorganize 등 후속 지속성 제한은 별도 verification_request로 분리하십시오. 동일 Check 안에 결합 존재와 결합 약화를 함께 넣지 마십시오.",
            ]
        )
    if "POSITIVE_ALIGNMENT_SCOPE_TOO_BROAD" in joined:
        rules.extend(
            [
                "POSITIVE_ALIGNMENT_SCOPE_TOO_BROAD는 강수와 환경요소의 긍정적 중첩을 후보 bbox 전체(all)에 요구했다는 뜻입니다.",
                "운영상 의미 있는 결합영역이 후보 안에 하나 이상 존재하는지를 묻는 일반 결합 Check는 scope_quantifier=at_least_one을 사용하십시오. 특정 하위영역을 명시적으로 검증할 때만 specific을 사용하십시오.",
                "후보영역 전체가 동시에 중첩되어야 한다는 조건을 만들지 마십시오.",
            ]
        )
    if "POSITIVE_ALIGNMENT_DUPLICATE_ATOMIC_CLAIM" in joined:
        rules.extend(
            [
                "POSITIVE_ALIGNMENT_DUPLICATE_ATOMIC_CLAIM은 같은 시각·요소·공간범위의 긍정적 다변수 중첩을 둘 이상의 Check가 중복 검증한다는 뜻입니다.",
                "동일한 핵심시각 결합 명제는 정확히 하나의 pure multivariable_alignment verification_request에만 두십시오.",
                "후속 약화·지속성·이동을 추가로 판단해야 하면 같은 overlap Check를 다시 만들지 말고 별도 temporal/spatial verification_request로 구성하십시오.",
            ]
        )
    if "QUANTITATIVE_COMPLEMENTARY_DUPLICATE_CHECK" in joined:
        rules.extend(
            [
                "QUANTITATIVE_COMPLEMENTARY_DUPLICATE_CHECK는 동일 물리량·동일 운영권역·동일 비교시각에 대해 서로 반대 방향의 정량 명제를 별도 Check로 중복 생성했다는 뜻입니다.",
                "강화/약화 또는 증가/감소 중 하나의 검증 명제만 유지하십시오. 실제 값이 반대이면 Step07이 opposite_observed로 판정하므로 반대 명제를 별도 Check로 다시 만들지 마십시오.",
                "필요한 추가 Check는 다른 시간구간, 다른 운영권역, 다른 물리량 또는 실제로 독립적인 의사결정 질문을 검증해야 합니다.",
            ]
        )

    if "HYPOTHESIS_CHECK_ROLE_CONFLICT" in joined:
        rules.extend(
            [
                "HYPOTHESIS_CHECK_ROLE_CONFLICT는 동일한 Check를 같은 가설의 supporting과 opposing 양쪽에 동시에 연결했다는 뜻입니다.",
                "하나의 Check에는 하나의 검증 명제와 하나의 evidence_role만 유지하십시오. 지지 가설과 제한 가설이 모두 필요하면 서로 다른 verification_request로 분리하십시오.",
                "특히 강화와 약화, overlap과 not_overlap처럼 방향이 반대인 명제를 한 Check로 병합하거나 양쪽 역할에 재사용하지 마십시오.",
            ]
        )
    if "COMPACT_CHECK_MERGE_CONTRACT_CONFLICT" in joined:
        rules.extend(
            [
                "COMPACT_CHECK_MERGE_CONTRACT_CONFLICT는 의미가 다른 verification_request를 하나의 Check로 합치면 안 된다는 뜻입니다.",
                "evidence_role이 다르거나 expected_state, 비교시각, quantitative_scope가 다른 명제는 각각 독립 verification_request로 유지하십시오.",
                "강화 후 약화처럼 연속된 두 변화도 하나의 Check로 합치지 말고 각 시간구간을 별도 명제로 작성하십시오.",
            ]
        )


    internal_markers = (
        "vertical_wind_shear is spatial-only",
        "temporal diagnostic/method must declare check_scope=temporal_evolution",
        "multivariable_composite must declare check_scope=multivariable_alignment",
        "section diagnostic must declare check_scope=vertical_structure",
        "multivariable_alignment requires multivariable_composite and at least two signal components",
        "vertical_structure requires a section diagnostic",
        "has invalid required_signal_components",
        "CHECK_EXECUTION_COMPONENT_UNAVAILABLE",
    )
    if any(marker in joined for marker in internal_markers):
        rules.append(
            "이 오류는 Python이 만든 내부 실행계약의 불변조건 오류입니다. AI는 내부 실행필드를 수정하지 말고 원래 verification_request의 기상학적 의도만 일관되게 다시 생성하십시오."
        )

    return rules

# Original func/step04/pass1_engine.py:2941
def _compact_check_keys_from_validation_errors(
    validation_errors: list[str],
    *,
    parsed_plan: dict[str, Any] | None = None,
) -> list[str]:
    """Extract compact diagnostic-check keys named by semantic validation errors.

    A remaining QUANTITATIVE_SCOPE_PHASE_MISMATCH means deterministic phase
    alignment could not resolve the required hour to one existing phase. That is
    a phase-graph problem, so it must regenerate the whole candidate-plan chunk
    rather than repeatedly regenerating one Check that cannot create phases.

    Step04 repair is atomic at the diagnostic-check level.  Most semantic
    validators name ``check_key`` directly, while schema/narrative validators
    can report only an array path such as ``diagnostic_checks[3]``.  When the
    current normalized plan is available, resolve that stable array index back
    to its existing ``check_key`` so a local text error cannot force a whole
    candidate chunk regeneration.  Individual fields are never patched.
    """
    if any("QUANTITATIVE_SCOPE_PHASE_MISMATCH" in str(error) for error in validation_errors):
        return []
    if any("insufficient forecast-hour coverage" in str(error) for error in validation_errors):
        if not isinstance(parsed_plan, dict):
            return []
        phase_rows = parsed_plan.get("analysis_phases")
        if not isinstance(phase_rows, list) or not all(isinstance(x, dict) for x in phase_rows):
            return []
        # When the plan has only one distinct hour, rebuilding a Check alone
        # cannot add another phase.  Regenerate the whole phase+check graph.
        local_hours = {hour for phase in phase_rows for hour in _compact_phase_hours(phase)}
        if len(local_hours) < 2:
            return []

    keys: list[str] = []
    seen: set[str] = set()
    patterns = (
        re.compile(r"check_key=([^\s,;]+)"),
        re.compile(r"check_key['\"]?\s*[:=]\s*['\"]([^'\"\s,;]+)"),
    )
    # ``parsed_plan`` is the normalized compact chunk only when transport
    # parsing + normalization succeeded.  On parse/transport/normalization
    # failure ``call_and_validate_json`` intentionally supplies an empty dict.
    # Atomic repair cannot be localized without a valid normalized Check array,
    # so return no indexed Checks here and let the existing whole-chunk repair
    # path regenerate from the immutable candidate context.  Do not call the
    # strict ``_compact_plan_rows`` helper on a partial/empty plan because that
    # would mask the original validation error with a new shape error.
    indexed_checks: list[dict[str, Any]] = []
    if isinstance(parsed_plan, dict):
        raw_checks = parsed_plan.get("diagnostic_checks")
        if isinstance(raw_checks, list) and all(isinstance(item, dict) for item in raw_checks):
            indexed_checks = raw_checks
    available_keys = [
        str(row.get("check_key") or "").strip()
        for row in indexed_checks
        if str(row.get("check_key") or "").strip()
    ]

    def resolve_current_key(raw_key: str) -> str:
        """Resolve validator/local Check keys to the normalized chunk key.

        Compact-plan normalization namespaces declarations as
        ``chunkNN:<local_key>``.  Validators are not fully uniform: some emit
        the namespaced key while others report only the provider-authored local
        key.  Atomic repair must target the key stored in the current normalized
        plan; stripping the namespace unconditionally makes the target disappear
        and incorrectly falls back to whole-chunk regeneration.

        If a parsed plan object was supplied but does not contain a valid
        normalized ``diagnostic_checks`` array, the transport/normalization
        boundary itself failed.  In that state there is no safe Check object to
        replace, even when the error text happens to mention ``check_key``.
        Return an empty target so the bounded whole-chunk regeneration path can
        recover from the immutable candidate context.
        """
        key = raw_key.strip().strip("'\"[]()")
        if not key:
            return key
        if isinstance(parsed_plan, dict) and not available_keys:
            return ""
        if not available_keys:
            return key
        if key in available_keys:
            return key
        local_key = re.sub(r"^chunk\d+:", "", key, flags=re.IGNORECASE)
        matches = [
            candidate_key
            for candidate_key in available_keys
            if re.sub(r"^chunk\d+:", "", candidate_key, flags=re.IGNORECASE) == local_key
        ]
        return matches[0] if len(matches) == 1 else key

    index_pattern = re.compile(r"diagnostic_checks\[(\d+)\]")
    for error in validation_errors:
        text = str(error)
        for pattern in patterns:
            for match in pattern.finditer(text):
                key = resolve_current_key(match.group(1))
                if key and key not in seen:
                    seen.add(key)
                    keys.append(key)
        for match in index_pattern.finditer(text):
            index = int(match.group(1))
            if index < 0 or index >= len(indexed_checks):
                continue
            key = str(indexed_checks[index].get("check_key") or "").strip()
            if key and key not in seen:
                seen.add(key)
                keys.append(key)
    return keys

# Original func/step04/pass1_engine.py:3061
def _compact_check_scope_constraints_from_validation_errors(
    validation_errors: list[str],
) -> dict[str, str]:
    """Return scope constraints already implied by named Check contract errors.

    These are not field patches.  They make the full-Check regeneration response
    schema reject only a scope value that the local validator has already proved
    impossible for that exact Check.
    """
    constraints: dict[str, str] = {}
    for error in validation_errors:
        text = str(error)
        scope: str | None = None
        if "temporal diagnostic/method must declare check_scope=temporal_evolution" in text:
            scope = "temporal_evolution"
        elif "multivariable_composite must declare check_scope=multivariable_alignment" in text:
            scope = "multivariable_alignment"
        elif "section diagnostic must declare check_scope=vertical_structure" in text:
            scope = "vertical_structure"
        if scope is not None:
            for check_key in _compact_check_keys_from_validation_errors([text]):
                constraints[check_key] = scope
    return constraints

# Original func/step04/pass1_engine.py:4429
def _feature_ids(feature_catalog: dict[str, Any]) -> set[str]:
    return {
        str(item.get("feature_id"))
        for item in feature_catalog.get("features", [])
        if isinstance(item, dict) and item.get("feature_id")
    }

# Original func/step04/pass1_engine.py:4462
def _native_axis_grid_count(lower: float, upper: float, *, step: float = AIFS_NATIVE_SPATIAL_INTERVAL_DEGREES) -> int:
    """Quarter-degree grid centres inside closed bbox bounds (origin at 0°)."""
    import math

    tolerance = 1.0e-9
    if not math.isfinite(lower) or not math.isfinite(upper) or lower > upper:
        return 0
    first = math.ceil((lower - tolerance) / step)
    last = math.floor((upper + tolerance) / step)
    return max(0, last - first + 1)

# Original func/step04/pass1_engine.py:4477
def _spatial_analysis_support_error(
    *, candidate_id: str, check_id: str, mode: str, scope: str,
    candidate_bbox: dict[str, float], context_bbox: dict[str, float] | None,
) -> str | None:
    if mode not in SPATIAL_STRUCTURE_VERIFICATION_MODES:
        return None
    if scope not in {"candidate", "context"}:
        return None
    domain = candidate_bbox if scope == "candidate" else context_bbox
    if domain is None:
        return None  # existing scope validation reports missing context
    lon_span = domain["east"] - domain["west"]
    lat_span = domain["north"] - domain["south"]
    step = AIFS_NATIVE_SPATIAL_INTERVAL_DEGREES
    lon_grid_count = _native_axis_grid_count(domain["west"], domain["east"])
    lat_grid_count = _native_axis_grid_count(domain["south"], domain["north"])
    if lon_grid_count >= 2 and lat_grid_count >= 2:
        return None
    context_sufficient = (
        context_bbox is not None
        and _native_axis_grid_count(context_bbox["west"], context_bbox["east"]) >= 2
        and _native_axis_grid_count(context_bbox["south"], context_bbox["north"]) >= 2
    )
    hint = (
        "Use domain_scope=context and the existing context_domain to render surrounding native cells; "
        "keep candidate_area unchanged and judge overlap only inside that candidate."
        if scope == "candidate" and context_sufficient
        else "Provide a genuine context_domain containing candidate_area with >=2 quarter-degree grid centres "
             "on each axis, then use domain_scope=context; do not alter candidate_area."
    )
    return (
        "SPATIAL_ANALYSIS_GRID_INSUFFICIENT "
        f"candidate_id={candidate_id} check_key={check_id} "
        f"verification_mode={mode} domain_scope={scope} "
        f"width_lon={lon_span:.3f} width_lat={lat_span:.3f} native_step={step:.2f} "
        f"grid_nlon={lon_grid_count} grid_nlat={lat_grid_count}; "
        "spatial structure cannot be established from a single-grid-axis map. "
        + hint
    )

# Original func/step04/pass1_engine.py:4518
def _expanded_plan_spatial_support_errors(
    plan: dict[str, Any], candidate: dict[str, Any],
) -> list[str]:
    candidate_bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
    if candidate_bbox is None:
        return []  # the canonical candidate validator owns invalid geometry
    target_index = {
        str(target.get("target_id")): target
        for target in plan.get("target_areas", [])
        if isinstance(target, dict)
    }
    context_bbox = next((
        _normalized_bbox_for_validation(target.get("requested_domain"))
        for target in target_index.values()
        if target.get("target_type") == "context_domain"
    ), None)
    errors: list[str] = []
    for check in plan.get("variable_checks", []):
        if not isinstance(check, dict):
            continue
        contract = check.get("intent_contract")
        if not isinstance(contract, dict):
            continue
        mode = str(contract.get("verification_mode") or "")
        scope = str(contract.get("domain_scope") or "")
        error = _spatial_analysis_support_error(
            candidate_id=str(candidate.get("candidate_id")),
            check_id=str(check.get("check_id")), mode=mode, scope=scope,
            candidate_bbox=candidate_bbox, context_bbox=context_bbox,
        )
        if error:
            errors.append(error)
    return errors

# Original func/step04/pass1_engine.py:4553
def _bbox_overlap_fraction_of_smaller(first: Any, second: Any) -> float:
    """Return intersection area divided by the smaller bbox area.

    The ratio is used only as a duplicate-candidate guard. A planar degree-area
    approximation is sufficient because both boxes are within the same compact
    regional domain and only a relative overlap threshold is applied.
    """
    left = _normalized_bbox_for_validation(first)
    right = _normalized_bbox_for_validation(second)
    if left is None or right is None:
        return 0.0
    width = max(0.0, min(left["east"], right["east"]) - max(left["west"], right["west"]))
    height = max(0.0, min(left["north"], right["north"]) - max(left["south"], right["south"]))
    intersection = width * height
    left_area = (left["east"] - left["west"]) * (left["north"] - left["south"])
    right_area = (right["east"] - right["west"]) * (right["north"] - right["south"])
    smaller = min(left_area, right_area)
    return intersection / smaller if smaller > 0.0 else 0.0

# Original func/step04/pass1_engine.py:4573
def _time_window_overlap_hours(first: Any, second: Any) -> int:
    if not isinstance(first, dict) or not isinstance(second, dict):
        return 0
    try:
        start = max(int(first["forecast_hour_start"]), int(second["forecast_hour_start"]))
        end = min(int(first["forecast_hour_end"]), int(second["forecast_hour_end"]))
    except (KeyError, TypeError, ValueError):
        return 0
    return max(0, end - start)

# Original func/step04/pass1_engine.py:4584
def _time_window_gap_hours(first: Any, second: Any) -> int | None:
    """Return the non-negative gap between two windows, or None for invalid input."""
    if not isinstance(first, dict) or not isinstance(second, dict):
        return None
    try:
        first_start = int(first["forecast_hour_start"])
        first_end = int(first["forecast_hour_end"])
        second_start = int(second["forecast_hour_start"])
        second_end = int(second["forecast_hour_end"])
    except (KeyError, TypeError, ValueError):
        return None
    if first_start > first_end or second_start > second_end:
        return None
    if first_end < second_start:
        return second_start - first_end
    if second_end < first_start:
        return first_start - second_end
    return 0

# Original func/step04/pass1_engine.py:4604
def _candidate_mechanism_similarity(first: dict[str, Any], second: dict[str, Any]) -> tuple[bool, list[str]]:
    first_categories = {
        str(value)
        for value in first.get("precursor_signal_categories", [])
        if str(value) != "direct_precipitation"
    }
    second_categories = {
        str(value)
        for value in second.get("precursor_signal_categories", [])
        if str(value) != "direct_precipitation"
    }
    common = sorted(first_categories & second_categories)
    same_basis = (
        str(first.get("detection_basis", ""))
        == str(second.get("detection_basis", ""))
        and bool(str(first.get("detection_basis", "")))
    )
    return same_basis or len(common) >= 2, common

# Original func/step04/pass1_engine.py:4624
def _candidate_peak_gap_hours(first: dict[str, Any], second: dict[str, Any]) -> int | None:
    first_peaks = sorted({int(value) for value in first.get("peak_forecast_hours", [])})
    second_peaks = sorted({int(value) for value in second.get("peak_forecast_hours", [])})
    if not first_peaks or not second_peaks:
        return None
    first_start = int(first.get("time_window", {}).get("forecast_hour_start", min(first_peaks)))
    second_start = int(second.get("time_window", {}).get("forecast_hour_start", min(second_peaks)))
    earlier, later = (first_peaks, second_peaks) if first_start <= second_start else (second_peaks, first_peaks)
    gap = min(later) - max(earlier)
    return max(0, gap)

# Original func/step04/pass1_engine.py:4637
def _region_bbox_for_peak_validation(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    aliases = {
        "west": ("west", "lon_min"),
        "east": ("east", "lon_max"),
        "south": ("south", "lat_min"),
        "north": ("north", "lat_max"),
    }
    normalized: dict[str, float] = {}
    try:
        for target, names in aliases.items():
            source = next(name for name in names if name in value)
            normalized[target] = float(value[source])
    except (StopIteration, TypeError, ValueError):
        return None
    return _normalized_bbox_for_validation(normalized)

# Original func/step04/pass1_engine.py:4656
def _expanded_bbox(value: dict[str, float], padding: float) -> dict[str, float]:
    return {
        "west": max(-180.0, float(value["west"]) - padding),
        "east": min(180.0, float(value["east"]) + padding),
        "south": max(-90.0, float(value["south"]) - padding),
        "north": min(90.0, float(value["north"]) + padding),
    }

# Original func/step04/pass1_engine.py:4665
def _point_in_validation_bbox(latitude: Any, longitude: Any, bbox: dict[str, float]) -> bool:
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return False
    return bbox["west"] <= lon <= bbox["east"] and bbox["south"] <= lat <= bbox["north"]

# Original func/step04/pass1_engine.py:4674
def _operational_precipitation_series(overview_data: dict[str, Any]) -> list[dict[str, Any]]:
    dataset = overview_data.get("operational_region_timeseries", {})
    rows = dataset.get("series", []) if isinstance(dataset, dict) else []
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and str(row.get("feature_id")) == "tp_6h_increment_mm"
        and bool(row.get("eligible_for_risk_rank"))
    ]

# Original func/step04/pass1_engine.py:4686
def _operational_region_bbox_index(overview_data: dict[str, Any]) -> dict[str, dict[str, float]]:
    catalog = overview_data.get("operational_region_catalog", {})
    rows = catalog.get("regions", []) if isinstance(catalog, dict) else []
    result: dict[str, dict[str, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        region_id = str(row.get("region_id", "")).strip()
        bbox = _region_bbox_for_peak_validation(row.get("bbox"))
        if region_id and bbox is not None:
            result[region_id] = bbox
    return result

# Original func/step04/pass1_engine.py:4700
def _precipitation_peak_score(maximum: Any, p95: Any, mean: Any) -> float:
    try:
        maximum_value = max(0.0, float(maximum))
    except (TypeError, ValueError):
        maximum_value = 0.0
    try:
        p95_value = max(0.0, float(p95))
    except (TypeError, ValueError):
        p95_value = 0.0
    try:
        mean_value = max(0.0, float(mean))
    except (TypeError, ValueError):
        mean_value = 0.0
    # p95 is weighted more heavily than a single-grid maximum so an isolated
    # numerical spike does not define the event anchor by itself.
    return 0.40 * maximum_value + 0.45 * p95_value + 0.15 * mean_value

# Original func/step04/pass1_engine.py:4718
def _local_peak_hours_from_timeline(timeline: list[dict[str, Any]]) -> list[int]:
    ordered = sorted(
        [row for row in timeline if isinstance(row, dict) and row.get("forecast_hour") is not None],
        key=lambda row: int(row["forecast_hour"]),
    )
    if not ordered:
        return []
    scores = [float(row.get("score", 0.0)) for row in ordered]
    hours = [int(row["forecast_hour"]) for row in ordered]
    peaks: list[int] = []
    for index, score in enumerate(scores):
        if score <= 0.0:
            continue
        previous = scores[index - 1] if index > 0 else None
        following = scores[index + 1] if index + 1 < len(scores) else None
        if previous is None and following is None:
            is_peak = True
        elif previous is None:
            is_peak = score >= float(following) * 1.10
        elif following is None:
            is_peak = score >= float(previous) * 1.10
        else:
            is_peak = (
                score >= float(previous) * 0.98
                and score >= float(following) * 0.98
                and score > min(float(previous), float(following)) * 1.02
            )
        if is_peak:
            peaks.append(hours[index])
    if not peaks:
        strongest_index = max(range(len(scores)), key=lambda idx: scores[idx])
        peaks.append(hours[strongest_index])
    return peaks

# Original func/step04/pass1_engine.py:4753
def _aggregate_precipitation_timeline(
    overview_data: dict[str, Any],
    *,
    candidate_bbox: dict[str, float] | None = None,
    overlap_group_id: str | None = None,
) -> list[dict[str, Any]]:
    series_rows = _operational_precipitation_series(overview_data)
    region_bbox_index = _operational_region_bbox_index(overview_data)
    expanded_candidate = _expanded_bbox(candidate_bbox, 0.50) if candidate_bbox is not None else None
    by_hour: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for series in series_rows:
        if overlap_group_id is not None and str(series.get("overlap_group_id")) != overlap_group_id:
            continue
        region_id = str(series.get("region_id", ""))
        region_bbox = region_bbox_index.get(region_id)
        if candidate_bbox is not None and region_bbox is not None:
            if _bbox_overlap_fraction_of_smaller(candidate_bbox, region_bbox) <= 0.0:
                continue
        hours = series.get("forecast_hours", [])
        maxima = series.get("max", [])
        p95_values = series.get("p95", [])
        means = series.get("mean", [])
        locations = series.get("max_location", [])
        for index, raw_hour in enumerate(hours):
            try:
                hour = int(raw_hour)
            except (TypeError, ValueError):
                continue
            maximum = maxima[index] if index < len(maxima) else 0.0
            p95 = p95_values[index] if index < len(p95_values) else 0.0
            mean = means[index] if index < len(means) else 0.0
            location = locations[index] if index < len(locations) and isinstance(locations[index], dict) else {}
            if expanded_candidate is not None:
                location_inside = _point_in_validation_bbox(
                    location.get("latitude"), location.get("longitude"), expanded_candidate
                )
                substantial_region_overlap = (
                    region_bbox is not None
                    and _bbox_overlap_fraction_of_smaller(candidate_bbox, region_bbox) >= 0.25
                )
                if not location_inside and not substantial_region_overlap:
                    continue
            score = _precipitation_peak_score(maximum, p95, mean)
            by_hour[hour].append(
                {
                    "forecast_hour": hour,
                    "score": score,
                    "max_mm": round(float(maximum or 0.0), 3),
                    "p95_mm": round(float(p95 or 0.0), 3),
                    "mean_mm": round(float(mean or 0.0), 3),
                    "region_id": region_id,
                    "region_name_ko": str(series.get("region_name_ko", "")),
                    "overlap_group_id": str(series.get("overlap_group_id", "")),
                    "max_location": {
                        "latitude": location.get("latitude"),
                        "longitude": location.get("longitude"),
                    },
                }
            )
    timeline: list[dict[str, Any]] = []
    for hour in sorted(by_hour):
        strongest = max(by_hour[hour], key=lambda row: float(row.get("score", 0.0)))
        timeline.append(dict(strongest))
    return timeline

# Original func/step04/pass1_engine.py:4829
def _system_direct_precipitation_signal_rows(
    overview_data: dict[str, Any],
    *,
    candidate_reference_threshold_mm: float,
) -> list[dict[str, Any]]:
    """Return deterministic >=1 mm operational-region precipitation signals.

    Candidate discovery remains an AI meteorological judgment.  This dataset is
    a SYSTEM-owned coverage ledger so an AI model cannot silently turn a
    quantitative direct-precipitation signal into ``absent``.  Signals below the
    candidate-reference threshold are recorded for audit but do not force a new
    candidate.
    """
    region_bbox_index = _operational_region_bbox_index(overview_data)
    signals: list[dict[str, Any]] = []
    for series in _operational_precipitation_series(overview_data):
        hours = series.get("forecast_hours", [])
        maxima = series.get("max", [])
        p95_values = series.get("p95", [])
        means = series.get("mean", [])
        locations = series.get("max_location", [])
        for index, raw_hour in enumerate(hours):
            try:
                hour = int(raw_hour)
                maximum = float(maxima[index]) if index < len(maxima) else 0.0
            except (TypeError, ValueError):
                continue
            if maximum < DIRECT_PRECIPITATION_MINIMUM_MM_6H:
                continue
            p95 = p95_values[index] if index < len(p95_values) else None
            mean = means[index] if index < len(means) else None
            location = (
                locations[index]
                if index < len(locations) and isinstance(locations[index], dict)
                else {}
            )
            region_id = str(series.get("region_id") or "")
            signals.append(
                {
                    "forecast_hour": hour,
                    "region_id": region_id,
                    "region_name_ko": str(series.get("region_name_ko") or ""),
                    "region_role": str(series.get("region_role") or ""),
                    "overlap_group_id": str(series.get("overlap_group_id") or ""),
                    "maximum_6h_mm": round(maximum, 6),
                    "p95_6h_mm": round(float(p95), 6) if isinstance(p95, (int, float)) and not isinstance(p95, bool) else None,
                    "mean_6h_mm": round(float(mean), 6) if isinstance(mean, (int, float)) and not isinstance(mean, bool) else None,
                    "max_location": {
                        "latitude": location.get("latitude"),
                        "longitude": location.get("longitude"),
                    },
                    "region_bbox": region_bbox_index.get(region_id),
                    "candidate_generation_required": maximum >= float(candidate_reference_threshold_mm),
                }
            )
    return sorted(
        signals,
        key=lambda row: (
            int(row["forecast_hour"]),
            -float(row["maximum_6h_mm"]),
            str(row["region_id"]),
        ),
    )

# Original func/step04/pass1_engine.py:4967
def _candidate_covers_system_direct_precipitation_signal(
    candidate: dict[str, Any],
    signal: dict[str, Any],
    *,
    attachment_manifest: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Return whether a discovery candidate deterministically covers one signal."""
    if str(candidate.get("detection_basis") or "") not in {"direct_precipitation", "mixed"}:
        return False, []
    if str(candidate.get("precipitation_signal_status") or "") != "present":
        return False, []

    hour = int(signal["forecast_hour"])
    window = candidate.get("time_window")
    if not isinstance(window, dict):
        return False, []
    try:
        start = int(window.get("forecast_hour_start"))
        end = int(window.get("forecast_hour_end"))
    except (TypeError, ValueError):
        return False, []
    if not (start <= hour <= end):
        return False, []

    candidate_bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
    if candidate_bbox is None:
        return False, []
    expanded = _expanded_bbox(candidate_bbox, 0.50)
    location = signal.get("max_location") if isinstance(signal.get("max_location"), dict) else {}
    location_inside = _point_in_validation_bbox(
        location.get("latitude"), location.get("longitude"), expanded
    )
    region_bbox = signal.get("region_bbox")
    substantial_overlap = bool(
        isinstance(region_bbox, dict)
        and _bbox_overlap_fraction_of_smaller(candidate_bbox, region_bbox) >= 0.25
    )
    if not location_inside and not substantial_overlap:
        return False, []

    allowed_assets = set(_precipitation_asset_ids_for_hour(attachment_manifest, hour))
    related_assets = {
        str(value) for value in candidate.get("related_overview_asset_ids", [])
    }
    matched_assets = sorted(allowed_assets & related_assets)
    return bool(matched_assets), matched_assets

# Original func/step04/pass1_engine.py:5016
def _system_direct_precipitation_candidate_diagnostic(
    candidate: dict[str, Any],
    signal: dict[str, Any],
    *,
    attachment_manifest: dict[str, Any],
    evidence_group: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact pass/fail reasons for one candidate against one SYSTEM signal."""
    hour = int(signal["forecast_hour"])
    reasons: list[str] = []
    if str(candidate.get("detection_basis") or "") not in {"direct_precipitation", "mixed"}:
        reasons.append("detection_basis")
    if str(candidate.get("precipitation_signal_status") or "") != "present":
        reasons.append("precipitation_status")

    window = candidate.get("time_window")
    try:
        start = int(window.get("forecast_hour_start")) if isinstance(window, dict) else -1
        end = int(window.get("forecast_hour_end")) if isinstance(window, dict) else -1
        if not (start <= hour <= end):
            reasons.append("time_window")
    except (TypeError, ValueError):
        reasons.append("time_window")

    bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
    location_inside = False
    region_overlap = 0.0
    if bbox is None:
        reasons.append("candidate_bbox")
    else:
        location = signal.get("max_location") if isinstance(signal.get("max_location"), dict) else {}
        location_inside = _point_in_validation_bbox(
            location.get("latitude"), location.get("longitude"), _expanded_bbox(bbox, 0.50)
        )
        region_bbox = signal.get("region_bbox")
        if isinstance(region_bbox, dict):
            region_overlap = _bbox_overlap_fraction_of_smaller(bbox, region_bbox)
        if not location_inside and region_overlap < 0.25:
            reasons.append("spatial_overlap")

    allowed_assets = set(_precipitation_asset_ids_for_hour(attachment_manifest, hour))
    related_assets = {str(value) for value in candidate.get("related_overview_asset_ids", [])}
    matched_assets = sorted(allowed_assets & related_assets)
    if not matched_assets:
        reasons.append("precipitation_asset")

    evidence_hours: set[int] = set()
    if isinstance(evidence_group, dict):
        for item in evidence_group.get("evidence_items", []):
            if isinstance(item, dict) and str(item.get("asset_id") or "") in matched_assets:
                evidence_hours.update(
                    int(value)
                    for value in item.get("forecast_hours", [])
                    if isinstance(value, int) and not isinstance(value, bool)
                )
    if hour not in evidence_hours:
        reasons.append("evidence_hour")

    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "failure_reasons": reasons,
        "matching_precipitation_asset_ids": matched_assets,
        "location_inside_expanded_bbox": location_inside,
        "region_bbox_overlap_fraction": round(region_overlap, 6),
        "coverage_ok": not reasons,
    }

# Original func/step04/pass1_engine.py:5084
def _bbox_contains_bbox(outer_value: Any, inner_value: Any) -> bool:
    outer = _normalized_bbox_for_validation(outer_value)
    inner = _normalized_bbox_for_validation(inner_value)
    if outer is None or inner is None:
        return False
    return (
        outer["west"] <= inner["west"]
        and outer["east"] >= inner["east"]
        and outer["south"] <= inner["south"]
        and outer["north"] >= inner["north"]
    )

# Original func/step04/pass1_engine.py:5097
def _time_window_contains_window(outer: Any, inner: Any) -> bool:
    if not isinstance(outer, dict) or not isinstance(inner, dict):
        return False
    try:
        return (
            int(outer["forecast_hour_start"]) <= int(inner["forecast_hour_start"])
            and int(outer["forecast_hour_end"]) >= int(inner["forecast_hour_end"])
        )
    except (KeyError, TypeError, ValueError):
        return False

# Original func/step04/pass1_engine.py:5109
def _canonicalize_system_direct_precipitation_evidence(
    response: dict[str, Any],
    *,
    overview_data: dict[str, Any],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any] | None = None,
) -> None:
    """Backfill a covered SYSTEM precipitation hour into existing evidence items.

    This never creates a candidate, changes a bbox/time window, or invents a new
    evidence asset.  It only repairs an omitted hour when a candidate already
    covers the quantitative signal and already cites the matching precipitation
    overview asset.
    """
    reference_threshold = _peak_anchor_minimum_mm(support_catalog)
    signals = _system_direct_precipitation_signal_rows(
        overview_data,
        candidate_reference_threshold_mm=reference_threshold,
    )
    candidates = [item for item in response.get("candidates", []) if isinstance(item, dict)]
    evidence_by_id = {
        str(group.get("candidate_id")): group
        for group in response.get("candidate_evidence", [])
        if isinstance(group, dict)
    }
    for signal in signals:
        hour = int(signal["forecast_hour"])
        for candidate in candidates:
            covered, matched_assets = _candidate_covers_system_direct_precipitation_signal(
                candidate, signal, attachment_manifest=attachment_manifest
            )
            if not covered:
                continue
            candidate_id = str(candidate.get("candidate_id") or "")
            group = evidence_by_id.get(candidate_id)
            if not isinstance(group, dict):
                continue
            for item in group.get("evidence_items", []):
                if not isinstance(item, dict) or str(item.get("asset_id") or "") not in matched_assets:
                    continue
                existing = {
                    int(value)
                    for value in item.get("forecast_hours", [])
                    if isinstance(value, int) and not isinstance(value, bool)
                }
                if hour in existing:
                    continue
                item["forecast_hours"] = sorted(existing | {hour})
                logging.info(
                    "step04 system precipitation coverage evidence canonicalized "
                    "candidate_id=%s region_id=%s forecast_hour=%s asset_id=%s",
                    candidate_id,
                    signal.get("region_id"),
                    hour,
                    item.get("asset_id"),
                )
                break

# Original func/step04/pass1_engine.py:5168
def build_system_direct_precipitation_coverage_audit(
    response: dict[str, Any],
    *,
    overview_data: dict[str, Any],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit candidate discovery against Step02 operational-region precipitation."""
    reference_threshold = _peak_anchor_minimum_mm(support_catalog)
    signals = _system_direct_precipitation_signal_rows(
        overview_data,
        candidate_reference_threshold_mm=reference_threshold,
    )
    candidates = [item for item in response.get("candidates", []) if isinstance(item, dict)]
    evidence_by_id = {
        str(group.get("candidate_id")): group
        for group in response.get("candidate_evidence", [])
        if isinstance(group, dict)
    }
    audited: list[dict[str, Any]] = []
    for signal in signals:
        covered_by: list[str] = []
        candidate_diagnostics: list[dict[str, Any]] = []
        for candidate in candidates:
            candidate_id = str(candidate.get("candidate_id") or "")
            diagnostic = _system_direct_precipitation_candidate_diagnostic(
                candidate,
                signal,
                attachment_manifest=attachment_manifest,
                evidence_group=evidence_by_id.get(candidate_id),
            )
            candidate_diagnostics.append(diagnostic)
            if diagnostic["coverage_ok"]:
                covered_by.append(candidate_id)
        row = {
            key: signal.get(key)
            for key in (
                "forecast_hour", "region_id", "region_name_ko", "region_role",
                "overlap_group_id", "maximum_6h_mm", "p95_6h_mm", "mean_6h_mm",
                "max_location", "candidate_generation_required",
            )
        }
        row["coverage_status"] = "covered" if covered_by else "uncovered"
        row["covered_candidate_ids"] = sorted(set(covered_by))
        row["coverage_reason"] = (
            "covered_by_candidate"
            if covered_by
            else (
                "candidate_reference_threshold_met"
                if signal["candidate_generation_required"]
                else "below_candidate_reference_threshold"
            )
        )
        if not covered_by and signal["candidate_generation_required"]:
            row["candidate_match_diagnostics"] = candidate_diagnostics
        audited.append(row)
    required_uncovered = [
        row for row in audited
        if row["coverage_status"] == "uncovered" and row["candidate_generation_required"]
    ]
    advisory_uncovered = [
        row for row in audited
        if row["coverage_status"] == "uncovered" and not row["candidate_generation_required"]
    ]
    return {
        "contract_version": SYSTEM_DIRECT_PRECIPITATION_COVERAGE_AUDIT_VERSION,
        "source": "step02.operational_region_timeseries.tp_6h_increment_mm",
        "minimum_direct_signal_6h_mm": DIRECT_PRECIPITATION_MINIMUM_MM_6H,
        "candidate_reference_threshold_6h_mm": reference_threshold,
        "signal_count": len(audited),
        "covered_signal_count": sum(row["coverage_status"] == "covered" for row in audited),
        "uncovered_signal_count": sum(row["coverage_status"] == "uncovered" for row in audited),
        "required_uncovered_count": len(required_uncovered),
        "advisory_uncovered_count": len(advisory_uncovered),
        "required_uncovered_forecast_hours": sorted({int(row["forecast_hour"]) for row in required_uncovered}),
        "advisory_uncovered_forecast_hours": sorted({int(row["forecast_hour"]) for row in advisory_uncovered}),
        "signals": audited,
    }

# Original func/step04/pass1_engine.py:5248
def _system_direct_precipitation_coverage_errors(audit: dict[str, Any]) -> list[str]:
    rows = [
        row for row in audit.get("signals", [])
        if isinstance(row, dict)
        and row.get("coverage_status") == "uncovered"
        and row.get("candidate_generation_required") is True
    ]
    by_hour: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_hour[int(row["forecast_hour"])].append(row)
    errors: list[str] = []
    threshold = float(audit.get("candidate_reference_threshold_6h_mm") or 0.0)
    for hour in sorted(by_hour):
        regions = ", ".join(
            f"{row.get('region_id')}({row.get('region_name_ko')}:{float(row.get('maximum_6h_mm') or 0.0):.2f}mm)"
            for row in sorted(by_hour[hour], key=lambda item: -float(item.get("maximum_6h_mm") or 0.0))
        )
        diagnostics = []
        for row in by_hour[hour]:
            failures = ";".join(
                f"{item.get('candidate_id') or '?'}:{','.join(item.get('failure_reasons', [])) or 'none'}"
                for item in row.get("candidate_match_diagnostics", [])[:5]
                if isinstance(item, dict)
            )
            if failures:
                diagnostics.append(f"{row.get('region_id')}=>{failures}")
        diagnostic_text = " | ".join(diagnostics)
        errors.append(
            "SYSTEM_DIRECT_PRECIPITATION_COVERAGE_REQUIRED "
            f"forecast_hour=F{hour:03d} uncovered_regions=[{regions}] "
            f"candidate_reference_threshold_mm={threshold:g}"
            + (f" candidate_failures=[{diagnostic_text}]" if diagnostic_text else "")
            + "; add a direct_precipitation/mixed candidate or extend an existing meteorologically identical candidate only when justified, "
            "and cite the matching QL_PRECIP_6H asset/hour in candidate evidence. "
            "Do not lower the threshold or delete the quantitative signal."
        )
    return errors

# Original func/step04/pass1_engine.py:5338
def _is_sequence_subset(before: Any, after: Any) -> bool:
    if not isinstance(before, list) or not isinstance(after, list):
        return before == after
    remaining = list(after)
    for item in before:
        try:
            index = remaining.index(item)
        except ValueError:
            return False
        remaining.pop(index)
    return True

# Original func/step04/pass1_engine.py:5351
def _candidate_discovery_coverage_reconciliation_preservation_errors(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    required_uncovered_count: int,
) -> list[str]:
    """Reject reconciliation responses that rewrite unrelated discovery judgments."""
    errors: list[str] = []
    before_candidates = [item for item in before.get("candidates", []) if isinstance(item, dict)]
    after_candidates = [item for item in after.get("candidates", []) if isinstance(item, dict)]
    if len(after_candidates) < len(before_candidates):
        return ["COVERAGE_RECONCILIATION_PRESERVATION_INVALID existing candidates were deleted"]
    if len(after_candidates) - len(before_candidates) > max(1, required_uncovered_count):
        errors.append(
            "COVERAGE_RECONCILIATION_PRESERVATION_INVALID too many new candidates were added"
        )

    immutable_candidate_fields = (
        "detection_basis",
        "precipitation_signal_status",
        "precursor_signal_categories",
        "precursor_spatiotemporal_overlap",
        "analysis_envelope_request",
        "diagnostic_plan",
        "confidence",
        "separation_reason",
    )
    subset_candidate_fields = (
        "peak_forecast_hours",
        "signal_types",
        "related_product_ids",
        "related_overview_asset_ids",
        "supporting_reasons",
        "opposing_reasons",
        "uncertainties",
    )
    for index, original in enumerate(before_candidates):
        revised = after_candidates[index]
        candidate_id = str(original.get("candidate_id") or f"index={index}")
        if str(revised.get("candidate_id") or "") != str(original.get("candidate_id") or ""):
            errors.append(
                f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID candidate order/id changed candidate_id={candidate_id}"
            )
            continue
        for field in immutable_candidate_fields:
            if revised.get(field) != original.get(field):
                errors.append(
                    f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID candidate_id={candidate_id} field={field} changed"
                )
        original_hypothesis = original.get("hypothesis") if isinstance(original.get("hypothesis"), dict) else {}
        revised_hypothesis = revised.get("hypothesis") if isinstance(revised.get("hypothesis"), dict) else {}
        if revised_hypothesis.get("spatiotemporal_overlap") != original_hypothesis.get("spatiotemporal_overlap"):
            errors.append(
                f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID candidate_id={candidate_id} field=hypothesis.spatiotemporal_overlap changed"
            )
        if not _bbox_contains_bbox(revised.get("candidate_area"), original.get("candidate_area")):
            errors.append(
                f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID candidate_id={candidate_id} candidate_area shrank or moved"
            )
        if not _time_window_contains_window(revised.get("time_window"), original.get("time_window")):
            errors.append(
                f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID candidate_id={candidate_id} time_window shrank or moved"
            )
        for field in subset_candidate_fields:
            if not _is_sequence_subset(original.get(field, []), revised.get(field, [])):
                errors.append(
                    f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID candidate_id={candidate_id} field={field} lost existing values"
                )

    for candidate in after_candidates[len(before_candidates):]:
        if str(candidate.get("detection_basis") or "") not in {"direct_precipitation", "mixed"}:
            errors.append(
                "COVERAGE_RECONCILIATION_PRESERVATION_INVALID new candidate must be direct_precipitation or mixed"
            )
        if str(candidate.get("precipitation_signal_status") or "") != "present":
            errors.append(
                "COVERAGE_RECONCILIATION_PRESERVATION_INVALID new candidate precipitation_signal_status must be present"
            )

    before_evidence = {
        str(item.get("candidate_id") or ""): item
        for item in before.get("candidate_evidence", [])
        if isinstance(item, dict)
    }
    after_evidence = {
        str(item.get("candidate_id") or ""): item
        for item in after.get("candidate_evidence", [])
        if isinstance(item, dict)
    }
    for candidate_id, original_group in before_evidence.items():
        revised_group = after_evidence.get(candidate_id)
        if not isinstance(revised_group, dict):
            errors.append(
                f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID candidate_id={candidate_id} evidence group missing"
            )
            continue
        revised_items = {
            str(item.get("asset_id") or ""): item
            for item in revised_group.get("evidence_items", [])
            if isinstance(item, dict)
        }
        for original_item in original_group.get("evidence_items", []):
            if not isinstance(original_item, dict):
                continue
            asset_id = str(original_item.get("asset_id") or "")
            revised_item = revised_items.get(asset_id)
            if not isinstance(revised_item, dict):
                errors.append(
                    f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID candidate_id={candidate_id} asset_id={asset_id} evidence deleted"
                )
                continue
            if not set(original_item.get("forecast_hours", [])).issubset(
                set(revised_item.get("forecast_hours", []))
            ):
                errors.append(
                    f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID candidate_id={candidate_id} asset_id={asset_id} evidence hours deleted"
                )

    if before.get("precursor_opportunities") != after.get("precursor_opportunities"):
        errors.append("COVERAGE_RECONCILIATION_PRESERVATION_INVALID precursor_opportunities changed")
    before_assessment = before.get("precursor_candidate_assessment")
    after_assessment = after.get("precursor_candidate_assessment")
    if isinstance(before_assessment, dict) and isinstance(after_assessment, dict):
        for field in (
            "assessment_status",
            "qualified_opportunity_count",
            "source_asset_ids",
            "forecast_hours",
            "rationale",
        ):
            if before_assessment.get(field) != after_assessment.get(field):
                errors.append(
                    f"COVERAGE_RECONCILIATION_PRESERVATION_INVALID precursor_candidate_assessment.{field} changed"
                )
    if not _is_sequence_subset(
        before.get("global_uncertainties", []), after.get("global_uncertainties", [])
    ):
        errors.append("COVERAGE_RECONCILIATION_PRESERVATION_INVALID global_uncertainties were deleted")
    return errors

# Original func/step04/pass1_engine.py:5499
def _candidate_discovery_initial_compact_limit_errors(value: dict[str, Any]) -> list[str]:
    """Retain 6/8 initial discovery limits even with repair headroom in the schema."""
    errors: list[str] = []
    for index, candidate in enumerate(value.get("candidates", [])):
        if isinstance(candidate, dict) and len(candidate.get("related_overview_asset_ids", [])) > CANDIDATE_DISCOVERY_INITIAL_ASSET_LIMIT:
            errors.append(
                f"INITIAL_DISCOVERY_ASSET_LIMIT candidate_index={index} "
                f"max={CANDIDATE_DISCOVERY_INITIAL_ASSET_LIMIT}"
            )
    for index, evidence in enumerate(value.get("candidate_evidence", [])):
        if isinstance(evidence, dict) and len(evidence.get("evidence_items", [])) > CANDIDATE_DISCOVERY_INITIAL_EVIDENCE_LIMIT:
            errors.append(
                f"INITIAL_DISCOVERY_EVIDENCE_LIMIT candidate_index={index} "
                f"max={CANDIDATE_DISCOVERY_INITIAL_EVIDENCE_LIMIT}"
            )
    return errors

# Original func/step04/pass1_engine.py:5550
def _canonicalize_candidate_discovery_coverage_reconciliation_preservation(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    attachment_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply coverage reconciliation as an allowlisted patch over the original response.

    The provider still returns the full candidate-discovery object because it uses the
    discovery response schema.  Reconciliation, however, is *not* allowed to regenerate
    existing meteorological judgments.  Rebuild every existing candidate from the
    pre-reconciliation object and accept only fields needed to close direct-precipitation
    coverage gaps:

    - candidate_area: expansion only
    - time_window: expansion only
    - related_product_ids / related_overview_asset_ids: additive only
    - candidate_evidence: additive assets/hours only
    - candidate_name / hypothesis.statement: optional wording update only when the
      candidate area or time window was actually expanded

    All other existing-candidate fields (including separation_reason, uncertainties,
    confidence, diagnostic_plan, precursor judgments, etc.) are preserved exactly.
    New candidates remain provider-authored and are validated separately.
    """
    if not isinstance(before, dict) or not isinstance(after, dict):
        return after

    result = copy.deepcopy(after)
    attachment_by_id = _attachment_by_id(attachment_manifest) if attachment_manifest is not None else None
    before_candidates = [item for item in before.get("candidates", []) if isinstance(item, dict)]
    provider_candidates = [item for item in result.get("candidates", []) if isinstance(item, dict)]
    before_ids = [str(item.get("candidate_id") or "") for item in before_candidates]
    provider_by_id = {
        str(item.get("candidate_id") or ""): item
        for item in provider_candidates
        if str(item.get("candidate_id") or "")
    }

    def additive_sequence(original: Any, revised: Any) -> list[Any]:
        base = list(original) if isinstance(original, list) else []
        additions = list(revised) if isinstance(revised, list) else []
        merged = copy.deepcopy(base)
        for item in additions:
            if item not in merged:
                merged.append(copy.deepcopy(item))
        return merged

    canonical_candidates: list[dict[str, Any]] = []
    restored: list[str] = []
    coverage_changes: list[str] = []

    for index, original in enumerate(before_candidates):
        candidate_id = str(original.get("candidate_id") or f"index={index}")
        revised = provider_by_id.get(str(original.get("candidate_id") or ""))
        if not isinstance(revised, dict):
            canonical_candidates.append(copy.deepcopy(original))
            restored.append(f"{candidate_id}.candidate")
            continue

        # Start from the original object so any newly introduced non-coverage field
        # is ignored by default rather than requiring one-off preservation rules.
        merged = copy.deepcopy(original)
        area_expanded = False
        time_expanded = False

        revised_area = revised.get("candidate_area")
        if _bbox_contains_bbox(revised_area, original.get("candidate_area")):
            if revised_area != original.get("candidate_area"):
                merged["candidate_area"] = copy.deepcopy(revised_area)
                area_expanded = True
                coverage_changes.append(f"{candidate_id}.candidate_area")
        elif revised_area != original.get("candidate_area"):
            restored.append(f"{candidate_id}.candidate_area")

        revised_window = revised.get("time_window")
        if _time_window_contains_window(revised_window, original.get("time_window")):
            if revised_window != original.get("time_window"):
                merged["time_window"] = copy.deepcopy(revised_window)
                time_expanded = True
                coverage_changes.append(f"{candidate_id}.time_window")
        elif revised_window != original.get("time_window"):
            restored.append(f"{candidate_id}.time_window")

        for field in ("related_product_ids", "related_overview_asset_ids"):
            merged_values = additive_sequence(original.get(field, []), revised.get(field, []))
            added_values = [item for item in merged_values if item not in original.get(field, [])]
            if field == "related_product_ids" and any(
                str(item) != "QL_PRECIP_6H" for item in added_values
            ):
                raise ValueError(
                    "COVERAGE_RECONCILIATION_NON_PRECIPITATION_PRODUCT "
                    f"candidate_id={candidate_id} additions={added_values}"
                )
            if field == "related_overview_asset_ids" and attachment_by_id is not None:
                invalid_assets = [
                    str(asset_id) for asset_id in added_values
                    if attachment_by_id.get(str(asset_id), {}).get("product_id") != "QL_PRECIP_6H"
                ]
                if invalid_assets:
                    raise ValueError(
                        "COVERAGE_RECONCILIATION_NON_PRECIPITATION_ASSET "
                        f"candidate_id={candidate_id} additions={invalid_assets}"
                    )
            if merged_values != original.get(field, []):
                coverage_changes.append(f"{candidate_id}.{field}")
            if field == "related_overview_asset_ids" and len(merged_values) > CANDIDATE_DISCOVERY_RECONCILIATION_ASSET_LIMIT:
                raise ValueError(
                    "COVERAGE_RECONCILIATION_ASSET_CAPACITY_EXHAUSTED "
                    f"candidate_id={candidate_id} count={len(merged_values)} "
                    f"limit={CANDIDATE_DISCOVERY_RECONCILIATION_ASSET_LIMIT}; "
                    "do not discard previous links or merge independent precipitation objects"
                )
            merged[field] = merged_values

        # Wording may be updated only to describe a genuinely widened coverage scope.
        if area_expanded or time_expanded:
            revised_name = str(revised.get("candidate_name") or "").strip()
            if revised_name:
                merged["candidate_name"] = revised_name
            original_hypothesis = (
                original.get("hypothesis") if isinstance(original.get("hypothesis"), dict) else {}
            )
            revised_hypothesis = (
                revised.get("hypothesis") if isinstance(revised.get("hypothesis"), dict) else {}
            )
            merged_hypothesis = copy.deepcopy(original_hypothesis)
            revised_statement = str(revised_hypothesis.get("statement") or "").strip()
            if revised_statement:
                merged_hypothesis["statement"] = revised_statement
            merged["hypothesis"] = merged_hypothesis

        # Explicitly log any provider-authored changes that were discarded.  This is
        # diagnostic only; the allowlist above already guarantees preservation.
        for field in original.keys():
            if field in {
                "candidate_area",
                "time_window",
                "related_product_ids",
                "related_overview_asset_ids",
                "candidate_name",
                "hypothesis",
            }:
                continue
            if revised.get(field) != original.get(field):
                restored.append(f"{candidate_id}.{field}")

        canonical_candidates.append(merged)

    # Keep provider-authored new candidates in their returned order.  The preservation
    # validator restricts these to direct_precipitation/mixed + precipitation present.
    for revised in provider_candidates:
        candidate_id = str(revised.get("candidate_id") or "")
        if candidate_id not in before_ids:
            canonical_candidates.append(copy.deepcopy(revised))
            coverage_changes.append(f"{candidate_id or '<new>'}.new_candidate")
    result["candidates"] = canonical_candidates
    run_summary = result.get("run_summary")
    if isinstance(run_summary, dict):
        run_summary["candidate_count"] = len(canonical_candidates)
    result["pass2_required"] = bool(canonical_candidates)

    # Candidate evidence is also patch-only.  Existing evidence items are immutable
    # except that forecast hours may be added.  New precipitation evidence assets may
    # be appended.  Provider rewrites of product_id/reason are discarded.
    before_evidence = {
        str(item.get("candidate_id") or ""): item
        for item in before.get("candidate_evidence", [])
        if isinstance(item, dict)
    }
    provider_evidence = {
        str(item.get("candidate_id") or ""): item
        for item in result.get("candidate_evidence", [])
        if isinstance(item, dict)
    }
    canonical_evidence: list[dict[str, Any]] = []
    for original_candidate in before_candidates:
        candidate_id = str(original_candidate.get("candidate_id") or "")
        original_group = before_evidence.get(candidate_id)
        revised_group = provider_evidence.get(candidate_id)
        if not isinstance(original_group, dict):
            if isinstance(revised_group, dict):
                canonical_evidence.append(copy.deepcopy(revised_group))
            continue
        merged_group = copy.deepcopy(original_group)
        original_items = [
            item for item in original_group.get("evidence_items", []) if isinstance(item, dict)
        ]
        revised_items = [
            item for item in (revised_group or {}).get("evidence_items", []) if isinstance(item, dict)
        ] if isinstance(revised_group, dict) else []
        revised_by_asset = {
            str(item.get("asset_id") or ""): item
            for item in revised_items
            if str(item.get("asset_id") or "")
        }
        merged_items: list[dict[str, Any]] = []
        original_asset_ids: set[str] = set()
        for original_item in original_items:
            asset_id = str(original_item.get("asset_id") or "")
            original_asset_ids.add(asset_id)
            merged_item = copy.deepcopy(original_item)
            revised_item = revised_by_asset.get(asset_id)
            if isinstance(revised_item, dict):
                merged_hours = additive_sequence(
                    original_item.get("forecast_hours", []),
                    revised_item.get("forecast_hours", []),
                )
                if merged_hours != original_item.get("forecast_hours", []):
                    coverage_changes.append(
                        f"{candidate_id}.candidate_evidence[{asset_id}].forecast_hours"
                    )
                merged_item["forecast_hours"] = merged_hours
                if revised_item.get("product_id") != original_item.get("product_id"):
                    restored.append(
                        f"{candidate_id}.candidate_evidence[{asset_id}].product_id"
                    )
                if revised_item.get("reason") != original_item.get("reason"):
                    restored.append(f"{candidate_id}.candidate_evidence[{asset_id}].reason")
            merged_items.append(merged_item)
        for revised_item in revised_items:
            asset_id = str(revised_item.get("asset_id") or "")
            if asset_id and asset_id not in original_asset_ids:
                if attachment_by_id is not None and (
                    attachment_by_id.get(asset_id, {}).get("product_id") != "QL_PRECIP_6H"
                ):
                    raise ValueError(
                        "COVERAGE_RECONCILIATION_NON_PRECIPITATION_EVIDENCE "
                        f"candidate_id={candidate_id} asset_id={asset_id}"
                    )
                merged_items.append(copy.deepcopy(revised_item))
                coverage_changes.append(f"{candidate_id}.candidate_evidence[{asset_id}]")
        if len(merged_items) > CANDIDATE_DISCOVERY_RECONCILIATION_EVIDENCE_LIMIT:
            raise ValueError(
                "COVERAGE_RECONCILIATION_EVIDENCE_CAPACITY_EXHAUSTED "
                f"candidate_id={candidate_id} count={len(merged_items)} "
                f"limit={CANDIDATE_DISCOVERY_RECONCILIATION_EVIDENCE_LIMIT}; "
                "do not discard previous evidence or merge independent precipitation objects"
            )
        merged_group["evidence_items"] = merged_items
        canonical_evidence.append(merged_group)

    # Evidence for genuinely new candidates is provider-authored.
    for candidate in canonical_candidates[len(before_candidates):]:
        candidate_id = str(candidate.get("candidate_id") or "")
        revised_group = provider_evidence.get(candidate_id)
        if isinstance(revised_group, dict):
            canonical_evidence.append(copy.deepcopy(revised_group))
    result["candidate_evidence"] = canonical_evidence

    # These judgments are fully outside direct-precipitation coverage reconciliation.
    for field in ("precursor_opportunities", "global_uncertainties"):
        if before.get(field) != result.get(field):
            if field in before:
                result[field] = copy.deepcopy(before[field])
            else:
                result.pop(field, None)
            restored.append(field)

    before_assessment = before.get("precursor_candidate_assessment")
    after_assessment = result.get("precursor_candidate_assessment")
    if isinstance(before_assessment, dict) and isinstance(after_assessment, dict):
        # dismissed_precursor_groups is derived from the final candidate set and may
        # legitimately change when a new direct/mixed candidate is added.
        dismissed = copy.deepcopy(after_assessment.get("dismissed_precursor_groups"))
        result["precursor_candidate_assessment"] = copy.deepcopy(before_assessment)
        if dismissed is not None:
            result["precursor_candidate_assessment"]["dismissed_precursor_groups"] = dismissed
        if before_assessment != after_assessment:
            restored.append("precursor_candidate_assessment")
    elif before_assessment != after_assessment:
        if "precursor_candidate_assessment" in before:
            result["precursor_candidate_assessment"] = copy.deepcopy(before_assessment)
        else:
            result.pop("precursor_candidate_assessment", None)
        restored.append("precursor_candidate_assessment")

    if restored:
        logging.info(
            "step04 coverage reconciliation restored/ignored non-target fields count=%s fields=%s",
            len(restored),
            ",".join(restored[:40]),
        )
    if coverage_changes:
        logging.info(
            "step04 coverage reconciliation accepted coverage patches count=%s fields=%s",
            len(coverage_changes),
            ",".join(coverage_changes[:40]),
        )
    return result

# Original func/step04/pass1_engine.py:5933
def _system_direct_precipitation_coverage_advisories(audit: dict[str, Any]) -> list[str]:
    rows = [
        row for row in audit.get("signals", [])
        if isinstance(row, dict)
        and row.get("coverage_status") == "uncovered"
        and row.get("candidate_generation_required") is not True
    ]
    by_hour: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_hour[int(row["forecast_hour"])].append(row)
    threshold = float(audit.get("candidate_reference_threshold_6h_mm") or 0.0)
    notes: list[str] = []
    for hour in sorted(by_hour):
        strongest = max(by_hour[hour], key=lambda item: float(item.get("maximum_6h_mm") or 0.0))
        notes.append(
            "SYSTEM_DIRECT_PRECIPITATION_COVERAGE_ADVISORY "
            f"forecast_hour=F{hour:03d} strongest_uncovered_region={strongest.get('region_id')} "
            f"maximum_6h_mm={float(strongest.get('maximum_6h_mm') or 0.0):.2f} "
            f"below_candidate_reference_threshold_mm={threshold:g}; candidate generation is not forced."
        )
    return notes

# Original func/step04/pass1_engine.py:6034
def _precursor_category_for_product(product_id: str) -> str | None:
    mapping = {
        "QL_TCW_WIND850": "moisture_supply",
        "QL_CONVERGENCE925_WIND925": "low_level_forcing",
        "QL_OMEGA700_Z500": "mid_upper_forcing",
    }
    return mapping.get(product_id)

# Original func/step04/pass1_engine.py:6053
def _ordered_precursor_categories(values: Iterable[Any]) -> list[str]:
    normalized = {str(value) for value in values}
    return [value for value in _PRECURSOR_CATEGORY_ORDER if value in normalized]

# Original func/step04/pass1_engine.py:6058
def _candidate_precursor_asset_ids(
    candidate: dict[str, Any],
    *,
    attachment_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    result: list[str] = []
    for raw_asset_id in candidate.get("related_overview_asset_ids", []):
        asset_id = str(raw_asset_id)
        source = attachment_by_id.get(asset_id)
        if source is None:
            continue
        category = _precursor_category_for_product(str(source.get("product_id", "")))
        if category is not None and asset_id not in result:
            result.append(asset_id)
    return result

# Original func/step04/pass1_engine.py:6075
def _normalize_candidate_precursor_metadata(
    candidates: list[dict[str, Any]],
    *,
    attachment_by_id: dict[str, dict[str, Any]] | None,
) -> None:
    """Preserve environmental evidence even when discovery began from precipitation.

    ``detection_basis`` records how a candidate was discovered. It must not erase
    moisture/forcing evidence that was explicitly attached to the same candidate.
    The overlap flag is synchronized with the model's hypothesis-level judgment,
    but only when at least one non-precipitation category is actually referenced.
    """
    for candidate in candidates:
        declared = [str(value) for value in candidate.get("precursor_signal_categories", [])]
        derived: list[str] = []
        if attachment_by_id is not None:
            for asset_id in candidate.get("related_overview_asset_ids", []):
                source = attachment_by_id.get(str(asset_id))
                if source is None:
                    continue
                category = _precursor_category_for_product(str(source.get("product_id", "")))
                if category is not None:
                    derived.append(category)

        basis = str(candidate.get("detection_basis", ""))
        precipitation_status = str(candidate.get("precipitation_signal_status", ""))
        if basis in {"direct_precipitation", "mixed"} and precipitation_status != "absent":
            declared.append("direct_precipitation")
        if basis == "precursor_environment":
            declared = [value for value in declared if value != "direct_precipitation"]

        categories = _ordered_precursor_categories([*declared, *derived])
        candidate["precursor_signal_categories"] = categories
        non_precipitation_categories = {
            value for value in categories if value != "direct_precipitation"
        }
        hypothesis = candidate.get("hypothesis")
        hypothesis_overlap = bool(
            hypothesis.get("spatiotemporal_overlap")
            if isinstance(hypothesis, dict)
            else False
        )
        overlap = bool(
            non_precipitation_categories
            and (
                candidate.get("precursor_spatiotemporal_overlap") is True
                or hypothesis_overlap
            )
        )
        candidate["precursor_spatiotemporal_overlap"] = overlap
        if isinstance(hypothesis, dict):
            hypothesis["spatiotemporal_overlap"] = overlap

# Original func/step04/pass1_engine.py:6129
def _build_dismissed_precursor_groups(
    candidates: list[dict[str, Any]],
    *,
    attachment_by_id: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Create an audit trail for precursor signals absorbed into direct/mixed candidates."""
    if attachment_by_id is None:
        return []
    groups: list[dict[str, Any]] = []
    for candidate in candidates:
        if str(candidate.get("detection_basis")) not in {"direct_precipitation", "mixed"}:
            continue
        categories = [
            str(value)
            for value in candidate.get("precursor_signal_categories", [])
            if str(value) != "direct_precipitation"
        ]
        overlap = candidate.get("precursor_spatiotemporal_overlap") is True
        if not has_independent_precursor_support(
            categories,
            spatial_temporal_overlap=overlap,
        ):
            continue
        source_asset_ids = _candidate_precursor_asset_ids(
            candidate,
            attachment_by_id=attachment_by_id,
        )
        if len(source_asset_ids) < 2:
            continue
        candidate_id = str(candidate.get("candidate_id", ""))
        forecast_hours = sorted(
            {int(value) for value in candidate.get("peak_forecast_hours", [])}
        )
        if not candidate_id or not forecast_hours:
            continue
        groups.append(
            {
                "dismissed_group_id": f"DPG_{len(groups)+1:03d}",
                "dismissal_basis": "absorbed_into_direct_or_mixed_candidate",
                "source_asset_ids": source_asset_ids[:8],
                "forecast_hours": forecast_hours[:8],
                "precursor_signal_categories": _ordered_precursor_categories(categories),
                "overlapping_candidate_ids": [candidate_id],
                "dismissal_reason": (
                    f"수분·역학 강제력 신호가 {candidate_id}의 강수 peak와 같은 시간·영역에 "
                    "중첩되어 독립 선행환경 후보로 분리하지 않았습니다."
                ),
            }
        )
    return groups[:10]

# Original func/step04/pass1_engine.py:6181
def _precursor_candidate_evidence_advisories(
    candidates: list[dict[str, Any]],
    *,
    attachment_manifest: dict[str, Any],
) -> list[str]:
    """Return non-blocking AI precursor-classification review notes."""
    errors: list[str] = []
    attachment_by_id = _attachment_by_id(attachment_manifest)
    forcing_categories = {"low_level_forcing", "mid_upper_forcing"}
    precursor_candidates = [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict)
        and str(candidate.get("detection_basis")) == "precursor_environment"
    ]
    direct_or_mixed_candidates = [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict)
        and str(candidate.get("detection_basis")) in {"direct_precipitation", "mixed"}
    ]
    for candidate in precursor_candidates:
        candidate_id = str(candidate.get("candidate_id") or candidate.get("candidate_name") or "<unknown>")
        categories = {
            str(value)
            for value in candidate.get("precursor_signal_categories", [])
            if str(value) != "direct_precipitation"
        }
        if len(categories) < 2 or not (categories & forcing_categories):
            errors.append(
                f"{candidate_id} precursor_environment requires at least two independent precursor categories "
                "including low_level_forcing or mid_upper_forcing"
            )
        if candidate.get("precursor_spatiotemporal_overlap") is not True:
            errors.append(f"{candidate_id} precursor_environment requires precursor_spatiotemporal_overlap=true")
        if str(candidate.get("precipitation_signal_status")) == "present":
            errors.append(
                f"{candidate_id} has direct precipitation present and must be mixed rather than precursor_environment"
            )
        evidence_categories = {
            category
            for asset_id in candidate.get("related_overview_asset_ids", [])
            for category in [
                _precursor_category_for_product(
                    str(attachment_by_id.get(str(asset_id), {}).get("product_id", ""))
                )
            ]
            if category is not None
        }
        if len(evidence_categories) < 2 or not (evidence_categories & forcing_categories):
            errors.append(
                f"{candidate_id} precursor_environment lacks at least two non-precipitation overview products "
                "including a dynamical forcing product"
            )

        # Similarity to a direct/mixed candidate is useful review information,
        # but the AI retains the merge/split decision.
        for other in direct_or_mixed_candidates:
            spatial_overlap = _bbox_overlap_fraction_of_smaller(
                candidate.get("candidate_area"), other.get("candidate_area")
            )
            temporal_overlap = _time_window_overlap_hours(
                candidate.get("time_window"), other.get("time_window")
            )
            other_categories = {
                str(value)
                for value in other.get("precursor_signal_categories", [])
                if str(value) != "direct_precipitation"
            }
            common_categories = categories & other_categories
            if spatial_overlap >= 0.60 and temporal_overlap > 0 and len(common_categories) >= 2:
                errors.append(
                    f"{candidate_id} duplicates {other.get('candidate_id')} in time, area, and precursor mechanism; "
                    "merge them as one mixed candidate or retain only an independent precursor event"
                )
    return errors

# Original func/step04/pass1_engine.py:6259
def _validate_precursor_opportunities(
    opportunities: Any,
    *,
    candidates: list[dict[str, Any]],
    attachment_manifest: dict[str, Any] | None,
) -> None:
    """Validate structural precursor relation invariants, never meteorology."""
    if opportunities is None:
        return  # Legacy discovery artifact: relation provenance was unavailable.
    if not isinstance(opportunities, list):
        raise ValueError("precursor_opportunities must be an array")
    candidate_by_id = {
        str(candidate.get("candidate_id")): candidate
        for candidate in candidates if isinstance(candidate, dict) and candidate.get("candidate_id")
    }
    attachment_ids = set(_attachment_by_id(attachment_manifest)) if attachment_manifest else set()
    opportunity_ids: set[str] = set()
    created_ids: set[str] = set()
    for opportunity in opportunities:
        if not isinstance(opportunity, dict):
            raise ValueError("precursor_opportunities item is not an object")
        opportunity_id = str(opportunity.get("opportunity_id", ""))
        if not opportunity_id or opportunity_id in opportunity_ids:
            raise ValueError("precursor_opportunities opportunity_id values must be unique")
        opportunity_ids.add(opportunity_id)
        qualification = str(opportunity.get("qualification", ""))
        relation = str(opportunity.get("relation_to_direct", ""))
        creation = str(opportunity.get("candidate_creation", ""))
        created_id = opportunity.get("created_candidate_id")
        related_ids = [str(value) for value in opportunity.get("related_direct_candidate_ids", [])]
        if qualification not in {"qualified", "not_qualified"}:
            raise ValueError(f"{opportunity_id} qualification is invalid")
        if relation not in {"independent", "absorbed_into_direct", "boundary_ambiguous", "not_applicable"}:
            raise ValueError(f"{opportunity_id} relation_to_direct is invalid")
        if creation not in {"created", "not_created"}:
            raise ValueError(f"{opportunity_id} candidate_creation is invalid")
        if qualification == "not_qualified" and (relation != "not_applicable" or creation != "not_created"):
            raise ValueError(f"{opportunity_id} not_qualified must be not_applicable and not_created")
        if relation in {"absorbed_into_direct", "boundary_ambiguous"} and creation != "not_created":
            raise ValueError(f"{opportunity_id} {relation} must not create a precursor candidate")
        if creation == "created":
            if qualification != "qualified" or relation != "independent":
                raise ValueError(f"{opportunity_id} created requires qualified independent relation")
            if not isinstance(created_id, str) or created_id not in candidate_by_id:
                raise ValueError(f"{opportunity_id} created_candidate_id must reference a candidate")
            if candidate_by_id[created_id].get("detection_basis") != "precursor_environment":
                raise ValueError(f"{opportunity_id} created_candidate_id must reference precursor_environment")
            created_ids.add(created_id)
        elif created_id is not None:
            raise ValueError(f"{opportunity_id} not_created must have null created_candidate_id")
        for candidate_id in related_ids:
            candidate = candidate_by_id.get(candidate_id)
            if candidate is None:
                raise ValueError(f"{opportunity_id} references unknown related_direct_candidate_id={candidate_id}")
            if candidate.get("detection_basis") not in {"direct_precipitation", "mixed"}:
                raise ValueError(f"{opportunity_id} related_direct_candidate_ids must reference direct/mixed candidates")
        if relation in {"absorbed_into_direct", "boundary_ambiguous"} and not related_ids:
            raise ValueError(f"{opportunity_id} {relation} requires related_direct_candidate_ids")
        if attachment_ids:
            unknown_assets = sorted(set(str(value) for value in opportunity.get("support_asset_ids", [])) - attachment_ids)
            if unknown_assets:
                raise ValueError(f"{opportunity_id} references unknown support_asset_ids={unknown_assets}")
    precursor_candidates = {
        candidate_id for candidate_id, candidate in candidate_by_id.items()
        if candidate.get("detection_basis") == "precursor_environment"
    }
    if opportunities is not None and precursor_candidates != created_ids:
        raise ValueError("precursor_environment candidates must exactly match created precursor opportunities")

# Original func/step04/pass1_engine.py:6329
def _derive_legacy_precursor_assessment(
    opportunities: list[dict[str, Any]],
    existing: dict[str, Any],
) -> dict[str, Any]:
    """Keep the legacy summary as a deterministic compatibility projection."""
    independent = [
        item
        for item in opportunities
        if item.get("qualification") == "qualified"
        and item.get("relation_to_direct") == "independent"
    ]
    assets = list(dict.fromkeys(str(asset) for item in independent for asset in item.get("support_asset_ids", [])))[:8]
    hours = sorted({int(hour) for item in independent for hour in item.get("representative_forecast_hours", [])})[:8]
    return {
        "assessment_status": "qualified_independent_signal" if independent else "no_qualified_independent_signal",
        "qualified_opportunity_count": len(independent),
        "source_asset_ids": assets,
        "forecast_hours": hours,
        "rationale": "선행환경 탐색 결과에서 결정론적으로 파생한 기존 형식 호환 요약입니다.",
        "dismissed_precursor_groups": existing.get("dismissed_precursor_groups", []) if isinstance(existing, dict) else [],
    }

# Original func/step04/pass1_engine.py:6351
def _validate_precursor_candidate_assessment(
    assessment: Any,
    *,
    candidates: list[dict[str, Any]],
    attachment_manifest: dict[str, Any] | None,
) -> None:
    """Validate only executable/reference integrity of precursor assessment.

    Meteorological consistency (whether an independent precursor truly exists,
    whether a direct/mixed candidate absorbed it, or whether the opportunity
    count/rationale fully agrees with the selected candidates) is an AI judgment
    and must not abort Step04.  Those conditions are reported separately as
    non-blocking advisories by ``_precursor_candidate_assessment_advisories``.
    """
    if not isinstance(assessment, dict):
        raise ValueError("candidate discovery requires precursor_candidate_assessment")
    status = str(assessment.get("assessment_status", ""))
    if status not in {"qualified_independent_signal", "no_qualified_independent_signal"}:
        raise ValueError("precursor_candidate_assessment.assessment_status is invalid")
    try:
        opportunity_count = int(assessment.get("qualified_opportunity_count", -1))
    except (TypeError, ValueError) as exc:
        raise ValueError("precursor_candidate_assessment.qualified_opportunity_count is invalid") from exc
    if opportunity_count < 0:
        raise ValueError("precursor_candidate_assessment.qualified_opportunity_count must be non-negative")

    source_asset_ids = assessment.get("source_asset_ids", [])
    if not isinstance(source_asset_ids, list):
        raise ValueError("precursor_candidate_assessment.source_asset_ids must be an array")
    source_asset_ids = [str(value) for value in source_asset_ids]
    try:
        forecast_hours = [int(value) for value in assessment.get("forecast_hours", [])]
    except (TypeError, ValueError) as exc:
        raise ValueError("precursor_candidate_assessment.forecast_hours is invalid") from exc
    if any(hour < 0 for hour in forecast_hours):
        raise ValueError("precursor_candidate_assessment.forecast_hours contains a negative value")

    dismissed_groups = assessment.get("dismissed_precursor_groups", [])
    if not isinstance(dismissed_groups, list):
        raise ValueError("precursor_candidate_assessment.dismissed_precursor_groups must be an array")

    candidate_ids = {
        str(candidate.get("candidate_id"))
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("candidate_id")
    }
    seen_group_ids: set[str] = set()
    for index, group in enumerate(dismissed_groups, start=1):
        if not isinstance(group, dict):
            raise ValueError(f"dismissed_precursor_groups item is not an object index={index}")
        group_id = str(group.get("dismissed_group_id", ""))
        if group_id in seen_group_ids:
            raise ValueError("dismissed_precursor_groups IDs must be unique")
        seen_group_ids.add(group_id)
        unknown_candidates = sorted(
            set(str(value) for value in group.get("overlapping_candidate_ids", [])) - candidate_ids
        )
        if unknown_candidates:
            raise ValueError(f"{group_id or f'dismissed_group[{index}]'} references unknown overlapping_candidate_ids={unknown_candidates}")
        try:
            group_hours = [int(value) for value in group.get("forecast_hours", [])]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{group_id or f'dismissed_group[{index}]'} forecast_hours is invalid") from exc
        if any(hour < 0 for hour in group_hours):
            raise ValueError(f"{group_id or f'dismissed_group[{index}]'} forecast_hours contains a negative value")

    if attachment_manifest is None:
        return
    attachment_by_id = _attachment_by_id(attachment_manifest)
    unknown = sorted(set(source_asset_ids) - set(attachment_by_id))
    if unknown:
        raise ValueError(f"precursor_candidate_assessment references unknown source_asset_ids={unknown}")
    for index, group in enumerate(dismissed_groups, start=1):
        group_id = str(group.get("dismissed_group_id") or f"dismissed_group[{index}]")
        group_asset_ids = [str(value) for value in group.get("source_asset_ids", [])]
        unknown_group_assets = sorted(set(group_asset_ids) - set(attachment_by_id))
        if unknown_group_assets:
            raise ValueError(f"{group_id} references unknown source_asset_ids={unknown_group_assets}")

# Original func/step04/pass1_engine.py:6431
def _precursor_candidate_assessment_advisories(
    assessment: Any,
    *,
    candidates: list[dict[str, Any]],
    attachment_manifest: dict[str, Any] | None,
) -> list[str]:
    """Return non-blocking meteorological/audit consistency notes.

    These checks are useful for Pass2 review and debugging but they do not decide
    candidate selection, precursor independence, or merge/split.  No item returned
    by this function is allowed to fail Step04.
    """
    if not isinstance(assessment, dict):
        return []
    notes: list[str] = []
    status = str(assessment.get("assessment_status", ""))
    try:
        opportunity_count = int(assessment.get("qualified_opportunity_count", 0))
    except (TypeError, ValueError):
        opportunity_count = 0
    precursor_candidates = [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict)
        and str(candidate.get("detection_basis")) == "precursor_environment"
    ]
    source_asset_ids = [str(value) for value in assessment.get("source_asset_ids", [])]
    try:
        forecast_hours = [int(value) for value in assessment.get("forecast_hours", [])]
    except (TypeError, ValueError):
        forecast_hours = []
    rationale = re.sub(r"\s+", " ", str(assessment.get("rationale", "")).strip())
    dismissed_groups = [
        group for group in assessment.get("dismissed_precursor_groups", []) if isinstance(group, dict)
    ]

    if not rationale:
        notes.append("precursor assessment rationale is empty")
    elif re.search(r"[가-힣]", rationale) is None:
        notes.append("precursor assessment rationale is not Korean narrative")

    if status == "qualified_independent_signal":
        if opportunity_count < 1:
            notes.append("qualified precursor assessment has qualified_opportunity_count < 1")
        if not precursor_candidates:
            notes.append("qualified precursor assessment emitted no precursor_environment candidate")
        if opportunity_count < len(precursor_candidates):
            notes.append("qualified_opportunity_count is smaller than emitted precursor candidate count")
        if not source_asset_ids or not forecast_hours:
            notes.append("qualified precursor assessment has incomplete source assets or representative hours")
    elif status == "no_qualified_independent_signal":
        if opportunity_count != 0:
            notes.append("no-qualified precursor assessment has nonzero qualified_opportunity_count")
        if precursor_candidates:
            notes.append("precursor_environment candidates exist while assessment says no qualified signal")
        if source_asset_ids or forecast_hours:
            notes.append("no-qualified precursor assessment retains source assets or representative hours")

    expected_absorbed_candidates = {
        str(candidate.get("candidate_id"))
        for candidate in candidates
        if isinstance(candidate, dict)
        and str(candidate.get("detection_basis")) in {"direct_precipitation", "mixed"}
        and has_independent_precursor_support(
            candidate.get("precursor_signal_categories", []),
            spatial_temporal_overlap=(candidate.get("precursor_spatiotemporal_overlap") is True),
        )
    }
    covered_candidate_ids = {
        str(value)
        for group in dismissed_groups
        for value in group.get("overlapping_candidate_ids", [])
    }
    missing_absorbed = sorted(expected_absorbed_candidates - covered_candidate_ids)
    if missing_absorbed:
        notes.append(f"dismissed precursor audit does not cover absorbed candidates={missing_absorbed}")

    candidate_by_id = {
        str(candidate.get("candidate_id")): candidate
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("candidate_id")
    }
    for index, group in enumerate(dismissed_groups, start=1):
        group_id = str(group.get("dismissed_group_id") or f"dismissed_group[{index}]")
        categories = [str(value) for value in group.get("precursor_signal_categories", [])]
        if not has_independent_precursor_support(categories, spatial_temporal_overlap=True):
            notes.append(f"{group_id} has weak precursor-category audit support")
        try:
            group_hours = [int(value) for value in group.get("forecast_hours", [])]
        except (TypeError, ValueError):
            group_hours = []
        group_candidate_ids = [str(value) for value in group.get("overlapping_candidate_ids", [])]
        if group_candidate_ids and group_hours:
            if not any(
                any(
                    int(candidate_by_id[candidate_id].get("time_window", {}).get("forecast_hour_start", -1))
                    <= hour
                    <= int(candidate_by_id[candidate_id].get("time_window", {}).get("forecast_hour_end", -1))
                    for hour in group_hours
                )
                for candidate_id in group_candidate_ids
                if candidate_id in candidate_by_id
            ):
                notes.append(f"{group_id} audit hours do not overlap referenced candidate windows")

    if attachment_manifest is not None:
        attachment_by_id = _attachment_by_id(attachment_manifest)
        for index, group in enumerate(dismissed_groups, start=1):
            group_id = str(group.get("dismissed_group_id") or f"dismissed_group[{index}]")
            group_asset_ids = [str(value) for value in group.get("source_asset_ids", [])]
            derived_categories = {
                category
                for asset_id in group_asset_ids
                for category in [
                    _precursor_category_for_product(
                        str(attachment_by_id.get(asset_id, {}).get("product_id", ""))
                    )
                ]
                if category is not None
            }
            declared_categories = {
                str(value) for value in group.get("precursor_signal_categories", [])
            }
            if not declared_categories.issubset(derived_categories):
                notes.append(f"{group_id} declared precursor categories exceed source-asset support")
        if status == "qualified_independent_signal":
            categories = {
                category
                for asset_id in source_asset_ids
                for category in [
                    _precursor_category_for_product(
                        str(attachment_by_id.get(asset_id, {}).get("product_id", ""))
                    )
                ]
                if category is not None
            }
            if len(categories) < 2 or not categories & {"low_level_forcing", "mid_upper_forcing"}:
                notes.append("qualified precursor assessment has limited independent dynamical source support")
            assessment_asset_set = set(source_asset_ids)
            assessment_hour_set = set(forecast_hours)
            for candidate in precursor_candidates:
                candidate_id = str(candidate.get("candidate_id") or candidate.get("candidate_name") or "<unknown>")
                candidate_assets = {str(value) for value in candidate.get("related_overview_asset_ids", [])}
                candidate_hours = {int(value) for value in candidate.get("peak_forecast_hours", [])}
                if assessment_asset_set and not (candidate_assets & assessment_asset_set):
                    notes.append(f"{candidate_id} is weakly traceable to precursor assessment source assets")
                if assessment_hour_set and not (candidate_hours & assessment_hour_set):
                    notes.append(f"{candidate_id} is weakly traceable to precursor assessment forecast hours")
    return list(dict.fromkeys(notes))

# Original func/step04/pass1_engine.py:6582
def _log_step04_advisories(stage: str, notes: Iterable[str]) -> None:
    for note in dict.fromkeys(str(value).strip() for value in notes if str(value).strip()):
        logging.warning("step04 advisory stage=%s note=%s", stage, note)

# Original func/step04/pass1_engine.py:6586
def _canonicalize_significant_precipitation_evidence(
    response: dict[str, Any],
    *,
    group_summaries: list[dict[str, Any]],
    attachment_manifest: dict[str, Any],
) -> None:
    """Backfill omitted in-window precipitation hours into existing evidence items.

    Candidate boundaries remain an AI meteorological judgment.  This function only
    repairs a representational omission when all of the following are already true:
    the candidate references the precipitation asset, its declared time window
    contains the audited significant hour, and its evidence group already contains
    an item for that same asset.  It never creates or extends candidates and never
    invents a new evidence asset.
    """
    attachment_by_id = _attachment_by_id(attachment_manifest)
    candidates = [
        item for item in response.get("candidates", []) if isinstance(item, dict)
    ]
    evidence_by_id = {
        str(group.get("candidate_id")): group
        for group in response.get("candidate_evidence", [])
        if isinstance(group, dict)
    }

    for summary in group_summaries:
        if not isinstance(summary, dict):
            continue
        audit = summary.get("direct_precipitation_audit")
        if not isinstance(audit, dict) or audit.get("status") != "present":
            continue
        significant_hours = sorted(
            {int(value) for value in audit.get("significant_forecast_hours", [])}
        )
        for asset_id in [str(value) for value in audit.get("source_asset_ids", [])]:
            source = attachment_by_id.get(asset_id)
            if source is None or str(source.get("product_id")) != "QL_PRECIP_6H":
                continue
            source_hours = {int(value) for value in source.get("forecast_hours", [])}
            for hour in sorted(set(significant_hours) & source_hours):
                eligible: list[tuple[int, int, str, dict[str, Any]]] = []
                for candidate in candidates:
                    candidate_id = str(candidate.get("candidate_id", ""))
                    related_assets = {
                        str(value)
                        for value in candidate.get("related_overview_asset_ids", [])
                    }
                    if asset_id not in related_assets:
                        continue
                    window = candidate.get("time_window", {})
                    try:
                        start = int(window.get("forecast_hour_start"))
                        end = int(window.get("forecast_hour_end"))
                    except (TypeError, ValueError):
                        continue
                    if not (start <= hour <= end):
                        continue
                    evidence_group = evidence_by_id.get(candidate_id)
                    if not isinstance(evidence_group, dict):
                        continue
                    evidence_item = next(
                        (
                            item
                            for item in evidence_group.get("evidence_items", [])
                            if isinstance(item, dict)
                            and str(item.get("asset_id")) == asset_id
                        ),
                        None,
                    )
                    if evidence_item is None:
                        continue
                    existing_hours = {
                        int(value) for value in evidence_item.get("forecast_hours", [])
                    }
                    if hour in existing_hours:
                        eligible = []
                        break
                    peak_hours = [
                        int(value) for value in candidate.get("peak_forecast_hours", [])
                    ]
                    peak_distance = (
                        min(abs(hour - value) for value in peak_hours)
                        if peak_hours
                        else 10**9
                    )
                    try:
                        discovery_order = int(candidate.get("discovery_order", 10**9))
                    except (TypeError, ValueError):
                        discovery_order = 10**9
                    eligible.append(
                        (peak_distance, discovery_order, candidate_id, evidence_item)
                    )
                if not eligible:
                    continue
                _, _, candidate_id, evidence_item = min(eligible, key=lambda item: item[:3])
                normalized_hours = sorted(
                    {
                        int(value)
                        for value in evidence_item.get("forecast_hours", [])
                    }
                    | {hour}
                )
                evidence_item["forecast_hours"] = normalized_hours
                logging.info(
                    "step04 candidate discovery precipitation evidence canonicalized "
                    "candidate_id=%s asset_id=%s added_forecast_hour=%s",
                    candidate_id,
                    asset_id,
                    hour,
                )

# Original func/step04/pass1_engine.py:6697
def _significant_precipitation_coverage_advisories(
    response: dict[str, Any],
    *,
    group_summaries: list[dict[str, Any]],
    attachment_manifest: dict[str, Any],
) -> list[str]:
    """Return non-blocking direct-precipitation coverage review notes."""
    errors: list[str] = []
    attachment_by_id = _attachment_by_id(attachment_manifest)
    candidates = [item for item in response.get("candidates", []) if isinstance(item, dict)]
    evidence_by_id = {
        str(group.get("candidate_id")): group
        for group in response.get("candidate_evidence", [])
        if isinstance(group, dict)
    }
    for summary in group_summaries:
        if not isinstance(summary, dict):
            continue
        audit = summary.get("direct_precipitation_audit")
        if not isinstance(audit, dict) or audit.get("status") != "present":
            continue
        asset_ids = [str(value) for value in audit.get("source_asset_ids", [])]
        significant_hours = sorted({int(value) for value in audit.get("significant_forecast_hours", [])})
        if not asset_ids or not significant_hours:
            errors.append(
                f"group_summary={summary.get('summary_id')} has status=present but lacks precipitation asset/hour audit"
            )
            continue
        for asset_id in asset_ids:
            source = attachment_by_id.get(asset_id)
            if source is None or str(source.get("product_id")) != "QL_PRECIP_6H":
                errors.append(
                    f"group_summary={summary.get('summary_id')} direct precipitation audit references invalid asset_id={asset_id}"
                )
                continue
            source_hours = {int(value) for value in source.get("forecast_hours", [])}
            audited_hours = sorted(set(significant_hours) & source_hours)
            missing_hours: list[int] = []
            for hour in audited_hours:
                covered = False
                for candidate in candidates:
                    candidate_id = str(candidate.get("candidate_id"))
                    related_assets = {str(value) for value in candidate.get("related_overview_asset_ids", [])}
                    if asset_id not in related_assets:
                        continue
                    window = candidate.get("time_window", {})
                    try:
                        if not (int(window.get("forecast_hour_start")) <= hour <= int(window.get("forecast_hour_end"))):
                            continue
                    except (TypeError, ValueError):
                        continue
                    evidence_hours = {
                        int(value)
                        for item in evidence_by_id.get(candidate_id, {}).get("evidence_items", [])
                        if isinstance(item, dict) and str(item.get("asset_id")) == asset_id
                        for value in item.get("forecast_hours", [])
                    }
                    if hour in evidence_hours:
                        covered = True
                        break
                if not covered:
                    missing_hours.append(hour)
            if missing_hours:
                errors.append(
                    "candidate discovery group-summary precipitation observation is not linked to candidate evidence "
                    f"summary_id={summary.get('summary_id')} asset_id={asset_id} forecast_hours={missing_hours}; "
                    "this is observational provenance only and does not require candidate creation. "
                    "Operational candidate coverage is governed by the SYSTEM direct-precipitation coverage audit."
                )
    return errors

# Original func/step04/pass1_engine.py:6769
def _candidate_discovery_meteorological_qc_notes(
    candidates: list[dict[str, Any]],
    *,
    overview_data: dict[str, Any] | None,
) -> dict[str, list[str]]:
    """Build non-blocking meteorological QC notes for AI discovery judgments.

    Peak selection and candidate merge/split are meteorological judgments owned by
    the AI workflow.  Python still calculates deterministic comparison signals so
    that later candidate planning and Pass2 can revisit ambiguous decisions, but
    those comparisons must not reject an otherwise contract-valid discovery.
    """
    notes: dict[str, list[str]] = defaultdict(list)
    normalized_candidates = [item for item in candidates if isinstance(item, dict)]

    for candidate in normalized_candidates:
        candidate_id = str(
            candidate.get("candidate_id")
            or candidate.get("candidate_name")
            or "<unknown>"
        )
        declared_peaks = sorted(
            {
                int(value)
                for value in candidate.get("peak_forecast_hours", [])
                if isinstance(value, (int, float, str)) and str(value).strip() != ""
            }
        )
        peak_groups = _contiguous_hour_groups(declared_peaks, maximum_gap_hours=12)
        if len(peak_groups) > 1:
            notes[candidate_id].append(
                f"AI 판단 핵심 시각의 분리 구간={peak_groups}"
            )

        basis = str(candidate.get("detection_basis", ""))
        if overview_data is None or basis not in {"direct_precipitation", "mixed"}:
            continue
        bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
        if bbox is None:
            continue
        timeline = _aggregate_precipitation_timeline(overview_data, candidate_bbox=bbox)
        if not timeline:
            notes[candidate_id].append("후보영역 정량 강수 시계열 없음")
            continue
        window = candidate.get("time_window", {})
        try:
            start = int(window.get("forecast_hour_start"))
            end = int(window.get("forecast_hour_end"))
        except (TypeError, ValueError):
            continue
        row_by_hour = {int(row["forecast_hour"]): row for row in timeline}
        local_peak_hours = [
            hour for hour in _local_peak_hours_from_timeline(timeline) if start <= hour <= end
        ]
        if not local_peak_hours:
            notes[candidate_id].append("후보 시간창 내 정량 강수의 국지 최댓값 시각이 확인되지 않음")
            continue
        declared = set(declared_peaks)
        if declared and not declared.issubset(set(local_peak_hours)):
            notes[candidate_id].append(
                f"AI 판단 핵심 시각={declared_peaks}, 정량 강수 국지 최댓값 시각={local_peak_hours}"
            )
        peak_scores = {
            hour: float(row_by_hour[hour].get("score", 0.0)) for hour in local_peak_hours
        }
        if peak_scores:
            best_score = max(peak_scores.values())
            strongest = sorted(
                hour for hour, score in peak_scores.items() if score >= best_score * 0.90
            )
            if declared and not (declared & set(strongest)):
                notes[candidate_id].append(
                    f"정량 강수 최댓값 대표 시각={strongest}"
                )

    for index, first in enumerate(normalized_candidates):
        for second in normalized_candidates[index + 1 :]:
            overlap = _bbox_overlap_fraction_of_smaller(
                first.get("candidate_area"), second.get("candidate_area")
            )
            shared_peaks = sorted(
                {int(value) for value in first.get("peak_forecast_hours", [])}
                & {int(value) for value in second.get("peak_forecast_hours", [])}
            )
            time_overlap = _time_window_overlap_hours(
                first.get("time_window"), second.get("time_window")
            )
            time_gap = _time_window_gap_hours(
                first.get("time_window"), second.get("time_window")
            )
            mechanism_similar, _common_categories = _candidate_mechanism_similarity(first, second)
            peak_gap = _candidate_peak_gap_hours(first, second)
            near_duplicate = overlap >= 0.80 and time_overlap > 0 and bool(shared_peaks)
            adjacent_continuous = (
                overlap >= 0.60
                and peak_gap is not None
                and peak_gap <= 12
                and mechanism_similar
            )
            adjacent_peak_episodes = (
                _candidate_unit_type(first) == "peak_episode"
                and _candidate_unit_type(second) == "peak_episode"
                and overlap >= 0.30
                and time_gap is not None
                and time_gap <= 6
                and peak_gap is not None
                and peak_gap <= 12
            )
            if not near_duplicate and not adjacent_continuous and not adjacent_peak_episodes:
                continue

            def start_hour(candidate: dict[str, Any]) -> int:
                window = candidate.get("time_window", {})
                try:
                    return int(window.get("forecast_hour_start", 10**9))
                except (TypeError, ValueError):
                    return 10**9

            later = max((first, second), key=start_hour)
            later_id = str(later.get("candidate_id") or later.get("candidate_name") or "<unknown>")
            other = first if later is second else second
            other_id = str(other.get("candidate_id") or other.get("candidate_name") or "<unknown>")
            notes[later_id].append(
                f"인접 감시후보와 연속성 검토(중첩={overlap:.2f}, 시간간격={time_gap}시간, 핵심시각 간격={peak_gap}시간)"
            )
    return dict(notes)

# Original func/step04/pass1_engine.py:6897
def _apply_candidate_discovery_meteorological_qc(
    response: dict[str, Any],
    *,
    overview_data: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Attach deterministic QC as one bounded uncertainty per affected candidate.

    The candidate geometry, time window, peak hours, and merge/split decisions are
    never changed here.  The note is intentionally stored in the existing
    ``uncertainties`` field so it survives Step04 merge and becomes available to
    downstream detailed analysis without introducing a new operational artifact.
    """
    if not isinstance(response, dict):
        return response, {}
    normalized = copy.deepcopy(response)
    candidates = [item for item in normalized.get("candidates", []) if isinstance(item, dict)]
    qc_notes = _candidate_discovery_meteorological_qc_notes(
        candidates,
        overview_data=overview_data,
    )
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or candidate.get("candidate_name") or "<unknown>")
        pieces = qc_notes.get(candidate_id, [])
        if not pieces:
            continue
        suffix = ". 후속 상세분석에서 AI가 핵심 시각과 후보 병합·분리 여부를 재검토합니다."
        body = "정량 점검 참고: " + "; ".join(dict.fromkeys(pieces))
        body_budget = max(0, 160 - len(suffix))
        summary = body[:body_budget].rstrip(" ;,") + suffix
        uncertainties = [
            str(item) for item in candidate.get("uncertainties", []) if str(item).strip()
        ]
        if summary not in uncertainties:
            if len(uncertainties) >= 3:
                # Candidate discovery prompt is limited to two AI uncertainty rows,
                # leaving the third slot for deterministic QC.  Preserve earlier AI
                # text rather than silently replacing it if a provider violates that
                # compactness request.
                logging.warning(
                    "step04 meteorological QC could not attach candidate=%s because uncertainties is full notes=%s",
                    candidate_id,
                    pieces,
                )
                continue
            uncertainties.append(summary)
            candidate["uncertainties"] = uncertainties
        logging.info(
            "step04 meteorological QC advisory candidate=%s notes=%s",
            candidate_id,
            pieces,
        )
    return normalized, qc_notes

# Original func/step04/pass1_engine.py:6951
def validate_final_response(
    response: dict[str, Any],
    *,
    schema: dict[str, Any],
    package_id: str,
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    feature_catalog: dict[str, Any],
    operational_region_catalog: dict[str, Any],
    require_system_direct_precipitation_anchor: bool = False,
    validate_discovery_narrative: bool = True,
) -> list[str]:
    errors = _validate_jsonschema(response, schema)
    attachment_by_id = _attachment_by_id(attachment_manifest)
    attachment_ids = set(attachment_by_id)
    supported_products = {
        str(item.get("product_id")): {int(value) for value in item.get("allowed_forecast_hours", [])}
        for item in support_catalog.get("spatial_field_products", [])
        if isinstance(item, dict) and item.get("product_id")
    }
    diagnostic_execution_support = support_catalog.get("diagnostic_execution_support", {})
    if not isinstance(diagnostic_execution_support, dict):
        diagnostic_execution_support = {}
    known_features = _feature_ids(feature_catalog)
    known_regions = {
        str(item.get("region_id"))
        for item in operational_region_catalog.get("regions", [])
        if isinstance(item, dict) and item.get("region_id")
    }
    plan_support = support_catalog.get("analysis_plan_support", {})
    supported_analysis_methods = {
        str(item) for item in plan_support.get("supported_analysis_methods", [])
    }
    required_hazard_outputs = {
        str(item) for item in plan_support.get("required_hazard_outputs", [])
    }

    if response.get("package_id") != package_id:
        errors.append("package_id does not match Step04 input package")
    coverage = response.get("analysis_coverage", {})
    if isinstance(coverage, dict):
        if int(coverage.get("overview_attachment_count", -1)) != len(attachment_ids):
            errors.append("analysis_coverage.overview_attachment_count mismatch")
        if coverage.get("coverage_status") != "complete":
            errors.append("analysis_coverage.coverage_status must be complete after all batches passed")

    candidates = response.get("candidates", [])
    candidate_ids = [str(item.get("candidate_id")) for item in candidates if isinstance(item, dict)]
    candidate_id_set = set(candidate_ids)
    if len(candidate_ids) != len(candidate_id_set):
        errors.append("candidate_id values must be unique")
    _log_step04_advisories(
        "final.precursor_evidence",
        _precursor_candidate_evidence_advisories(
            [item for item in candidates if isinstance(item, dict)],
            attachment_manifest=attachment_manifest,
        ),
    )
    if "precursor_candidate_assessment" in response:
        try:
            _validate_precursor_candidate_assessment(
                response.get("precursor_candidate_assessment"),
                candidates=[item for item in candidates if isinstance(item, dict)],
                attachment_manifest=attachment_manifest,
            )
        except ValueError as exc:
            errors.append(str(exc))
        else:
            _log_step04_advisories(
                "final.precursor_assessment",
                _precursor_candidate_assessment_advisories(
                    response.get("precursor_candidate_assessment"),
                    candidates=[item for item in candidates if isinstance(item, dict)],
                    attachment_manifest=attachment_manifest,
                ),
            )
    summary = response.get("run_summary", {})
    if isinstance(summary, dict) and int(summary.get("candidate_count", -1)) != len(candidates):
        errors.append("run_summary.candidate_count mismatch")

    discovery_orders = [
        int(item.get("discovery_order"))
        for item in candidates
        if isinstance(item, dict) and item.get("discovery_order") is not None
    ]
    if len(discovery_orders) != len(set(discovery_orders)):
        errors.append("discovery_order values must be unique")
    if discovery_orders and sorted(discovery_orders) != list(range(1, len(discovery_orders) + 1)):
        errors.append("discovery_order values must be contiguous from 1")

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id"))
        related_assets = [str(value) for value in candidate.get("related_overview_asset_ids", [])]
        unknown = sorted(set(related_assets) - attachment_ids)
        if unknown:
            errors.append(f"{candidate_id} references unknown overview assets: {unknown}")
        related_products = {str(value) for value in candidate.get("related_product_ids", [])}
        unknown_products = sorted(related_products - set(supported_products))
        if unknown_products:
            errors.append(f"{candidate_id} references unsupported product IDs: {unknown_products}")
        actual_products = {
            str(attachment_by_id[asset_id].get("product_id"))
            for asset_id in related_assets
            if asset_id in attachment_by_id
        }
        if not actual_products.issubset(related_products):
            errors.append(f"{candidate_id} related_product_ids do not cover referenced assets")
        area = candidate.get("candidate_area", {})
        if isinstance(area, dict):
            west = float(area.get("west", 0.0))
            east = float(area.get("east", 0.0))
            south = float(area.get("south", 0.0))
            north = float(area.get("north", 0.0))
            if west >= east or south >= north:
                errors.append(f"{candidate_id} candidate_area bbox ordering is invalid")
            if not (-180.0 <= west <= 180.0 and -180.0 <= east <= 180.0):
                errors.append(f"{candidate_id} candidate_area longitude is invalid")
            if not (-90.0 <= south <= 90.0 and -90.0 <= north <= 90.0):
                errors.append(f"{candidate_id} candidate_area latitude is invalid")
        window = candidate.get("time_window", {})
        if isinstance(window, dict):
            start = int(window.get("forecast_hour_start", -1))
            end = int(window.get("forecast_hour_end", -1))
            if start > end:
                errors.append(f"{candidate_id} time_window start exceeds end")
            for hour in candidate.get("peak_forecast_hours", []):
                if int(hour) < start or int(hour) > end:
                    errors.append(f"{candidate_id} peak hour {hour} is outside time_window")
        expected_unit_type = _candidate_unit_type(candidate)
        if candidate.get("candidate_unit_type") != expected_unit_type:
            errors.append(
                f"{candidate_id} candidate_unit_type must be {expected_unit_type!r} "
                f"for detection_basis={candidate.get('detection_basis')!r}"
            )
        _log_step04_advisories(
            "candidate_discovery.signal_classification",
            [f"{candidate_id} {signal_error}" for signal_error in validate_candidate_signal_contract(candidate)],
        )

    plans = response.get("candidate_analysis_plans", [])
    plan_candidate_ids = [str(item.get("candidate_id")) for item in plans if isinstance(item, dict)]
    if Counter(plan_candidate_ids) != Counter(candidate_ids):
        errors.append("candidate_analysis_plans must contain exactly one plan for each candidate")
    plan_ids: list[str] = []
    plan_by_id: dict[str, dict[str, Any]] = {}
    check_index: dict[str, tuple[str, dict[str, Any]]] = {}
    target_index: dict[str, tuple[str, dict[str, Any]]] = {}
    phase_index: dict[str, tuple[str, dict[str, Any]]] = {}
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        candidate_id = str(plan.get("candidate_id"))
        plan_id = str(plan.get("plan_id"))
        plan_ids.append(plan_id)
        if candidate_id not in candidate_id_set:
            errors.append(f"{plan_id} references unknown candidate_id={candidate_id}")
        if plan_id != candidate_id.replace("CAND_", "PLAN_CAND"):
            errors.append(f"{plan_id} must deterministically match candidate_id={candidate_id}")
        if plan_id in plan_by_id:
            errors.append(f"duplicate analysis plan_id: {plan_id}")
        plan_by_id[plan_id] = plan
        candidate = next((item for item in candidates if str(item.get("candidate_id")) == candidate_id), {})
        candidate_bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))

        target_ids: list[str] = []
        target_types: set[str] = set()
        for target in plan.get("target_areas", []):
            if not isinstance(target, dict):
                continue
            target_id = str(target.get("target_id"))
            target_ids.append(target_id)
            target_types.add(str(target.get("target_type")))
            if target_id in target_index:
                errors.append(f"duplicate target_id: {target_id}")
            target_index[target_id] = (plan_id, target)
            region_ids = {str(value) for value in target.get("region_ids", [])}
            unknown_regions = sorted(region_ids - known_regions)
            if unknown_regions:
                errors.append(f"{target_id} references unknown operational regions: {unknown_regions}")
            resolution_method = str(target.get("resolution_method"))
            target_bbox = _normalized_bbox_for_validation(target.get("requested_domain"))
            if resolution_method == "explicit_bbox" and target_bbox is None:
                errors.append(f"{target_id} explicit_bbox requires a valid requested_domain")
            if resolution_method == "explicit_region_ids" and not region_ids:
                errors.append(f"{target_id} explicit_region_ids requires region_ids")
            target_type = str(target.get("target_type"))
            if target_type == "candidate_domain":
                if resolution_method != "explicit_bbox":
                    errors.append(f"{target_id} candidate_domain requires explicit_bbox")
                if candidate_bbox is not None and target_bbox != candidate_bbox:
                    errors.append(f"{target_id} candidate_domain bbox must equal candidate_area")
            if target_type == "context_domain":
                if resolution_method != "explicit_bbox":
                    errors.append(f"{target_id} context_domain requires explicit_bbox")
                if candidate_bbox is not None and target_bbox is not None and not _bbox_contains(target_bbox, candidate_bbox):
                    errors.append(f"{target_id} context_domain must contain candidate_area")
            if target_type == "operational_regions" and resolution_method not in {
                "candidate_area_overlap", "explicit_region_ids"
            }:
                errors.append(f"{target_id} operational_regions has invalid resolution_method")
            if (
                target_type == "operational_regions"
                and resolution_method == "candidate_area_overlap"
                and candidate_bbox is not None
            ):
                try:
                    selected_regions = _selectable_candidate_operational_regions(
                        candidate_bbox=candidate_bbox,
                        operational_region_catalog=operational_region_catalog,
                    )
                except (TypeError, ValueError) as exc:
                    errors.append(f"{target_id} operational region resolution failed: {exc}")
                else:
                    if not selected_regions:
                        errors.append(
                            _operational_region_target_empty_error(
                                candidate_id=candidate_id,
                                candidate_bbox=candidate_bbox,
                            )
                        )
        if len(target_ids) != len(set(target_ids)):
            errors.append(f"{plan_id} target_id values must be unique")
        if "candidate_domain" not in target_types:
            errors.append(f"{plan_id} must include candidate_domain target")

        phase_ids: list[str] = []
        peak_phase_hours: set[int] = set()
        for phase in plan.get("analysis_phases", []):
            if not isinstance(phase, dict):
                continue
            phase_id = str(phase.get("phase_id"))
            phase_ids.append(phase_id)
            if phase_id in phase_index:
                errors.append(f"duplicate phase_id: {phase_id}")
            phase_index[phase_id] = (plan_id, phase)
            if str(phase.get("phase_type")) == "peak":
                peak_phase_hours.update(int(value) for value in phase.get("forecast_hours", []))
        if len(phase_ids) != len(set(phase_ids)):
            errors.append(f"{plan_id} phase_id values must be unique")
        candidate_peak_hours = {int(value) for value in candidate.get("peak_forecast_hours", [])}
        if not candidate_peak_hours.issubset(peak_phase_hours):
            errors.append(f"{plan_id} peak phase does not cover candidate peak_forecast_hours")
        plan_phase_hours = {
            int(value)
            for phase in plan.get("analysis_phases", [])
            if isinstance(phase, dict)
            for value in phase.get("forecast_hours", [])
        }
        errors.extend(
            f"{plan_id} {message}"
            for message in _validate_phase_timeline(
                [item for item in plan.get("analysis_phases", []) if isinstance(item, dict)],
                available_hours=plan_phase_hours,
                candidate_peak_hours=candidate_peak_hours,
            )
        )

        checks = [item for item in plan.get("variable_checks", []) if isinstance(item, dict)]
        check_ids = [str(item.get("check_id")) for item in checks]
        if len(check_ids) != len(set(check_ids)):
            errors.append(f"{plan_id} check_id values must be unique")
        roles = {str(item.get("variable_role")) for item in checks}
        modern_plan = any(
            isinstance(item, dict) and item.get("diagnostic_id")
            for item in response.get("analysis_data_requests", [])
        )
        if "hazard_magnitude" not in roles and not modern_plan:
            errors.append(f"{plan_id} requires a hazard_magnitude check")
        explanatory = roles & {"moisture_supply", "low_level_forcing", "midlevel_forcing", "synoptic_support"}
        if len(explanatory) < 2 and not modern_plan:
            errors.append(f"{plan_id} requires at least two explanatory variable roles")
        for check in checks:
            check_id = str(check.get("check_id"))
            if check_id in check_index:
                errors.append(f"duplicate check_id: {check_id}")
            check_index[check_id] = (plan_id, check)
            product_id = check.get("product_id")
            if product_id is not None and str(product_id) not in supported_products:
                errors.append(f"{check_id} references unsupported product_id={product_id}")
            diagnostic_contract = diagnostic_execution_support.get(
                str(check.get("diagnostic_id", "")), {}
            )
            compatible_products = {
                str(value)
                for value in diagnostic_contract.get("compatible_product_ids", [])
            } if isinstance(diagnostic_contract, dict) else set()
            if product_id is not None and compatible_products and str(product_id) not in compatible_products:
                errors.append(
                    f"{check_id} product_id={product_id} is incompatible with "
                    f"diagnostic_id={check.get('diagnostic_id')!r}"
                )
            check_scope = str(check.get("check_scope") or "")
            required_components = [
                str(value) for value in check.get("required_signal_components", [])
                if str(value)
            ]
            if check_scope not in CHECK_SCOPES:
                errors.append(f"{check_id} has invalid check_scope={check_scope!r}")
            if not required_components or any(value not in SIGNAL_COMPONENTS for value in required_components):
                errors.append(f"{check_id} has invalid required_signal_components={required_components}")
            diagnostic_id = str(check.get("diagnostic_id") or "")
            methods = {str(value) for value in check.get("analysis_methods", [])}
            errors.extend(
                _check_scope_contract_errors(
                    check_key=check_id,
                    diagnostic_id=diagnostic_id,
                    analysis_methods=methods,
                    check_scope=check_scope,
                    required_signal_components=required_components,
                )
            )
            try:
                _validate_same_check_narrative_contract({
                    "check_key": check_id,
                    "check_scope": check_scope,
                    "required_signal_components": required_components,
                    "expected_signal": check.get("expected_signal"),
                    "diagnostic_id": diagnostic_id,
                    "analysis_methods": list(methods),
                })
            except ValueError as exc:
                errors.append(str(exc))
            product_components = (
                _product_signal_components_from_support(str(product_id), support_catalog)
                if product_id is not None else set()
            )
            if product_components and not set(required_components).issubset(product_components):
                errors.append(
                    f"{check_id} required_signal_components={required_components} cannot be provided by product_id={product_id!r}"
                )
            feature_ids = {str(value) for value in check.get("feature_ids", [])}
            unknown_features = sorted(feature_ids - known_features)
            if unknown_features:
                errors.append(f"{check_id} references unsupported feature_ids={unknown_features}")
            unknown_targets = sorted(set(str(value) for value in check.get("target_ids", [])) - set(target_ids))
            if unknown_targets:
                errors.append(f"{check_id} references unknown target_ids={unknown_targets}")
            unknown_phases = sorted(set(str(value) for value in check.get("phase_ids", [])) - set(phase_ids))
            if unknown_phases:
                errors.append(f"{check_id} references unknown phase_ids={unknown_phases}")
            referenced_hours = {
                int(hour)
                for phase_id in check.get("phase_ids", [])
                if str(phase_id) in phase_index
                for hour in phase_index[str(phase_id)][1].get("forecast_hours", [])
            }
            minimum_hours = _minimum_forecast_hours_for_check(diagnostic_id, check_scope)
            if len(referenced_hours) < minimum_hours:
                errors.append(
                    f"{check_id} has insufficient forecast-hour coverage "
                    f"required={minimum_hours} actual={len(referenced_hours)}"
                )
            diagnostic_id = str(check.get("diagnostic_id", ""))
            normalized_methods = _canonical_analysis_methods_for_diagnostic(
                diagnostic_id,
                check.get("analysis_methods", []),
            )
            check["analysis_methods"] = normalized_methods
            methods = set(normalized_methods)
            unsupported_methods = sorted(methods - supported_analysis_methods)
            if unsupported_methods:
                errors.append(f"{check_id} uses unsupported analysis_methods={unsupported_methods}")
            if diagnostic_id not in OPERATIONAL_DIAGNOSTIC_IDS:
                errors.append(f"{check_id} has unsupported diagnostic_id={diagnostic_id!r}")
            canonical_variable_role = _canonical_variable_role_for_diagnostic(
                diagnostic_id, str(check.get("variable_role"))
            )
            check["variable_role"] = canonical_variable_role
            required_roles_for_diagnostic = DIAGNOSTIC_REQUIRED_VARIABLE_ROLES.get(diagnostic_id)
            if (
                required_roles_for_diagnostic is not None
                and canonical_variable_role not in required_roles_for_diagnostic
            ):
                errors.append(
                    f"{check_id} has incompatible diagnostic_id/variable_role "
                    f"diagnostic_id={diagnostic_id!r} variable_role={check.get('variable_role')!r}"
                )
            raw_cross_section_line = check.get("cross_section_line")
            cross_section_line = _normalized_cross_section_line(raw_cross_section_line)
            cross_section_strategy = _normalized_cross_section_strategy(check.get("cross_section_strategy"))
            reference_band_axis = _normalized_reference_band_axis(check.get("reference_band_axis"))
            if diagnostic_id == "vertical_cross_section":
                if cross_section_line is None:
                    errors.append(f"{check_id} vertical_cross_section requires cross_section_line")
                if cross_section_strategy is None:
                    errors.append(f"{check_id} vertical_cross_section requires cross_section_strategy")
                if reference_band_axis is None:
                    errors.append(f"{check_id} vertical_cross_section requires reference_band_axis")
                else:
                    selected_targets = [
                        target_index.get(str(target_id), (None, {}))[1]
                        for target_id in check.get("target_ids", [])
                    ]
                    spatial_targets = [
                        target for target in selected_targets
                        if str(target.get("target_type")) in {"candidate_domain", "context_domain"}
                    ]
                    selected_bbox = (
                        _normalized_bbox_for_validation(spatial_targets[0].get("requested_domain"))
                        if spatial_targets else None
                    )
                    candidate_bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
                    endpoints = (
                        (cross_section_line["start_longitude"], cross_section_line["start_latitude"]),
                        (cross_section_line["end_longitude"], cross_section_line["end_latitude"]),
                    )
                    if selected_bbox is None or any(
                        not _point_in_bbox(lon, lat, selected_bbox) for lon, lat in endpoints
                    ):
                        errors.append(
                            f"{check_id} vertical_cross_section endpoints must be inside selected target"
                        )
                    if candidate_bbox is None or not _segment_intersects_bbox(
                        cross_section_line["start_longitude"],
                        cross_section_line["start_latitude"],
                        cross_section_line["end_longitude"],
                        cross_section_line["end_latitude"],
                        candidate_bbox,
                    ):
                        errors.append(f"{check_id} vertical_cross_section must intersect candidate_area")
            elif raw_cross_section_line is not None:
                errors.append(
                    f"{check_id} cross_section_line is only valid for vertical_cross_section"
                )
            if (
                not modern_plan
                and str(check.get("variable_role")) == "hazard_magnitude"
                and not required_hazard_outputs.issubset(methods)
            ):
                errors.append(f"{check_id} hazard_magnitude methods do not cover required outputs")

        referenced_phase_ids = {
            str(value)
            for check in checks
            for value in check.get("phase_ids", [])
        }
        for phase in plan.get("analysis_phases", []):
            if not isinstance(phase, dict):
                continue
            phase_id = str(phase.get("phase_id"))
            if phase_id not in referenced_phase_ids:
                errors.append(
                    f"{plan_id} contains unreferenced phase_id={phase_id} "
                    f"phase_type={phase.get('phase_type')!r}"
                )

        if any(
            {str(method) for method in check.get("analysis_methods", [])} & REGIONAL_ANALYSIS_METHODS
            for check in checks
        ) and "operational_regions" not in target_types:
            errors.append(f"{plan_id} regional analysis methods require operational_regions target")

        errors.extend(_hypothesis_check_role_errors(plan.get("hypotheses", [])))
        for hypothesis in plan.get("hypotheses", []):
            if not isinstance(hypothesis, dict):
                continue
            refs = {
                str(value)
                for key in ("supporting_check_ids", "opposing_check_ids")
                for value in hypothesis.get(key, [])
            }
            unknown_checks = sorted(refs - set(check_ids))
            if unknown_checks:
                errors.append(f"{plan_id} hypothesis references unknown check_ids={unknown_checks}")
        errors.extend(f"{plan_id} {message}" for message in _unreferenced_variable_check_errors(plan))
    if len(plan_ids) != len(set(plan_ids)):
        errors.append("analysis plan_id values must be unique")

    evidence_groups = response.get("candidate_evidence", [])
    evidence_candidate_ids = [
        str(group.get("candidate_id")) for group in evidence_groups if isinstance(group, dict)
    ]
    if Counter(evidence_candidate_ids) != Counter(candidate_ids):
        errors.append("candidate_evidence must contain exactly one group for each candidate")
    for group in evidence_groups:
        if not isinstance(group, dict):
            continue
        candidate_id = str(group.get("candidate_id"))
        if candidate_id not in candidate_id_set:
            errors.append(f"candidate_evidence references unknown candidate_id: {candidate_id}")
        for item in group.get("evidence_items", []):
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get("asset_id"))
            source = attachment_by_id.get(asset_id)
            if source is None:
                errors.append(f"candidate evidence references unknown asset_id: {asset_id}")
                continue
            if item.get("product_id") != source.get("product_id"):
                errors.append(f"candidate evidence product mismatch asset_id={asset_id}")
            allowed_hours = {int(value) for value in source.get("forecast_hours", [])}
            invalid_hours = sorted({int(value) for value in item.get("forecast_hours", [])} - allowed_hours)
            if invalid_hours:
                errors.append(f"candidate evidence has invalid forecast hours asset_id={asset_id}: {invalid_hours}")

    request_ids: list[str] = []
    for request in response.get("analysis_data_requests", []):
        if not isinstance(request, dict):
            continue
        request_id = str(request.get("request_id"))
        request_ids.append(request_id)
        candidate_id = str(request.get("candidate_id"))
        if candidate_id not in candidate_id_set:
            errors.append(f"{request_id} references unknown candidate_id={candidate_id}")
        plan_id = str(request.get("plan_id", ""))
        check_id = str(request.get("check_id", ""))
        raw_source_check_ids = request.get("source_check_ids")
        source_check_ids = (
            [str(value) for value in raw_source_check_ids]
            if isinstance(raw_source_check_ids, list) and raw_source_check_ids
            else [check_id]
        )
        source_check_ids = list(dict.fromkeys(value for value in source_check_ids if value))
        if check_id not in source_check_ids:
            errors.append(f"{request_id} source_check_ids must include primary check_id={check_id}")
        target_id = str(request.get("target_id", ""))
        phase_ids = {str(value) for value in request.get("phase_ids", [])}
        methods = {str(value) for value in request.get("analysis_methods", [])}
        diagnostic_id = str(request.get("diagnostic_id", ""))
        if diagnostic_id not in OPERATIONAL_DIAGNOSTIC_IDS:
            errors.append(f"{request_id} has unsupported diagnostic_id={diagnostic_id}")
        plan = plan_by_id.get(plan_id)
        if plan is None or str(plan.get("candidate_id")) != candidate_id:
            errors.append(f"{request_id} has invalid plan_id={plan_id}")
        linked_checks: list[dict[str, Any]] = []
        for linked_check_id in source_check_ids:
            check_pair = check_index.get(linked_check_id)
            if check_pair is None or check_pair[0] != plan_id:
                errors.append(f"{request_id} has invalid source_check_id={linked_check_id}")
                continue
            linked_checks.append(check_pair[1])
        target_pair = target_index.get(target_id)
        if target_pair is None or target_pair[0] != plan_id:
            errors.append(f"{request_id} has invalid target_id={target_id}")
        unknown_phase_refs = sorted(
            phase_id for phase_id in phase_ids
            if phase_id not in phase_index or phase_index[phase_id][0] != plan_id
        )
        if unknown_phase_refs:
            errors.append(f"{request_id} has invalid phase_ids={unknown_phase_refs}")
        if linked_checks:
            primary_pair = check_index.get(check_id)
            primary_check = (
                primary_pair[1]
                if primary_pair is not None and primary_pair[0] == plan_id
                else None
            )
            if primary_check is None:
                errors.append(f"{request_id} has invalid primary check_id={check_id}")
            elif diagnostic_id != str(primary_check.get("diagnostic_id", "")):
                errors.append(
                    f"{request_id} diagnostic_id does not match primary check_id={check_id}"
                )
            if any(
                target_id not in {str(value) for value in check.get("target_ids", [])}
                for check in linked_checks
            ):
                errors.append(
                    f"{request_id} target_id is not allowed by all source_check_ids={source_check_ids}"
                )
            allowed_phase_ids = {
                str(value) for check in linked_checks for value in check.get("phase_ids", [])
            }
            if not phase_ids.issubset(allowed_phase_ids):
                errors.append(
                    f"{request_id} phase_ids are not covered by source_check_ids={source_check_ids}"
                )
            allowed_methods = {
                str(value) for check in linked_checks for value in check.get("analysis_methods", [])
            }
            if not methods.issubset(allowed_methods):
                errors.append(
                    f"{request_id} analysis_methods are not covered by source_check_ids={source_check_ids}"
                )
        hours = {int(value) for value in request.get("forecast_hours", [])}
        allowed_phase_hours = {
            int(value)
            for phase_id in phase_ids
            for value in phase_index.get(phase_id, ("", {}))[1].get("forecast_hours", [])
        }
        if not hours.issubset(allowed_phase_hours):
            errors.append(f"{request_id} forecast_hours are not covered by phase_ids")
        if request.get("data_type") == "spatial_field":
            product_id = str(request.get("product_id"))
            request_bbox = _normalized_bbox_for_validation(request.get("requested_domain"))
            target = target_pair[1] if target_pair is not None and target_pair[0] == plan_id else {}
            target_bbox = _normalized_bbox_for_validation(target.get("requested_domain"))
            candidate = next((item for item in candidates if str(item.get("candidate_id")) == candidate_id), {})
            candidate_bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
            if request_bbox is None:
                errors.append(f"{request_id} spatial_field requires a valid requested_domain")
            if str(target.get("target_type")) in {"candidate_domain", "context_domain"}:
                expected_bbox = target_bbox or candidate_bbox
                if expected_bbox is not None and request_bbox != expected_bbox:
                    errors.append(f"{request_id} requested_domain must equal selected spatial target")
            if linked_checks and any(
                check.get("product_id") is not None
                and product_id != str(check.get("product_id"))
                for check in linked_checks
            ):
                errors.append(
                    f"{request_id} product_id does not match source_check_ids={source_check_ids}"
                )
            request_line = _cross_section_line_signature(request.get("cross_section_line"))
            if diagnostic_id == "vertical_cross_section":
                if request_line is None:
                    errors.append(f"{request_id} vertical_cross_section requires cross_section_line")
                if linked_checks and any(
                    request_line != _cross_section_line_signature(check.get("cross_section_line"))
                    for check in linked_checks
                ):
                    errors.append(
                        f"{request_id} cross_section_line does not match source_check_ids={source_check_ids}"
                    )
            elif request.get("cross_section_line") is not None:
                errors.append(
                    f"{request_id} cross_section_line is only valid for vertical_cross_section"
                )
            if product_id not in supported_products:
                errors.append(f"{request_id} uses unsupported product_id={product_id}")
            else:
                invalid = sorted(hours - supported_products[product_id])
                if invalid:
                    errors.append(f"{request_id} uses unsupported forecast_hours={invalid}")
        elif request.get("data_type") == "quantitative_excerpt":
            requested_features = {str(value) for value in request.get("feature_ids", [])}
            allowed_features = {
                str(value) for check in linked_checks for value in check.get("feature_ids", [])
            }
            if linked_checks and not requested_features.issubset(allowed_features):
                errors.append(
                    f"{request_id} feature_ids are not covered by source_check_ids={source_check_ids}"
                )
            invalid_features = sorted(requested_features - known_features)
            if invalid_features:
                errors.append(f"{request_id} uses unsupported feature_ids={invalid_features}")
    physical_request_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for item in response.get("analysis_data_requests", []):
        if isinstance(item, dict):
            physical_request_groups[_analysis_request_merge_key(item)].append(item)
    for duplicate_rows in physical_request_groups.values():
        if len(duplicate_rows) <= 1:
            continue
        exclusive_anchor_rows = []
        for item in duplicate_rows:
            source_ids = item.get("source_check_ids")
            source_ids = [str(value) for value in source_ids] if isinstance(source_ids, list) else []
            if len(source_ids) != 1:
                continue
            linked = check_index.get(source_ids[0])
            if linked is not None and _is_system_direct_precipitation_anchor_check(linked[1]):
                exclusive_anchor_rows.append(item)
        # The SYSTEM presence anchor intentionally duplicates one physical field
        # while retaining an independent request/evidence identity.  No other
        # duplicate physical-output pattern is permitted.
        if len(duplicate_rows) == 2 and len(exclusive_anchor_rows) == 1:
            continue
        errors.append(
            "analysis_data_requests contains duplicate physical outputs; merge forecast hours, "
            "methods, and source_check_ids"
        )
    if require_system_direct_precipitation_anchor:
        errors.extend(system_direct_precipitation_anchor_final_errors(response))
    requests_by_check: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for request in response.get("analysis_data_requests", []):
        if not isinstance(request, dict):
            continue
        raw_source_check_ids = request.get("source_check_ids")
        source_check_ids = (
            [str(value) for value in raw_source_check_ids]
            if isinstance(raw_source_check_ids, list) and raw_source_check_ids
            else [str(request.get("check_id", ""))]
        )
        for linked_check_id in dict.fromkeys(source_check_ids):
            requests_by_check[linked_check_id].append(request)
    for check_id, (_plan_id, check) in check_index.items():
        matching = requests_by_check.get(check_id, [])
        if not matching:
            errors.append(f"{check_id} is not implemented by analysis_data_requests")
        if str(check.get("priority")) == "required" and not any(
            str(item.get("priority")) == "required" for item in matching
        ):
            errors.append(f"{check_id} required check has no required request")
        if str(check.get("variable_role")) == "hazard_magnitude" and not any(
            isinstance(item, dict) and item.get("diagnostic_id") for item in matching
        ):
            types = {str(item.get("data_type")) for item in matching}
            if not {"spatial_field", "quantitative_excerpt"}.issubset(types):
                errors.append(f"{check_id} hazard_magnitude requires spatial and quantitative requests")
            implemented_methods = {
                str(method)
                for item in matching
                for method in item.get("analysis_methods", [])
            }
            if not required_hazard_outputs.issubset(implemented_methods):
                errors.append(f"{check_id} hazard requests do not implement all required outputs")
            quantitative = [item for item in matching if str(item.get("data_type")) == "quantitative_excerpt"]
            if quantitative:
                required_statistics = {"mean", "max", "p95", "max_location"}
                if not any(
                    required_statistics.issubset(
                        {str(value) for value in (item.get("output_spec") or {}).get("statistics", [])}
                    )
                    and bool((item.get("output_spec") or {}).get("include_time_series"))
                    and bool((item.get("output_spec") or {}).get("include_max_location"))
                    for item in quantitative
                ):
                    errors.append(f"{check_id} hazard quantitative request lacks required regional value outputs")

    if len(request_ids) != len(set(request_ids)):
        errors.append("request_id values must be unique")
    if candidates and not response.get("analysis_data_requests"):
        errors.append("analysis_data_requests must not be empty when candidates exist")
    for candidate in candidates:
        if not isinstance(candidate, dict) or str(candidate.get("detection_basis")) not in {"direct_precipitation", "mixed"}:
            continue
        candidate_id = str(candidate.get("candidate_id"))
        direct_requests = [
            item
            for item in response.get("analysis_data_requests", [])
            if isinstance(item, dict)
            and str(item.get("candidate_id")) == candidate_id
            and str(item.get("diagnostic_id")) in {"detailed_spatial_map", "accumulated_precipitation"}
            and "direct_precipitation" in _product_signal_components_from_support(
                str(item.get("product_id", "")), support_catalog
            )
        ]
        if not direct_requests:
            errors.append(f"{candidate_id} requires a direct precipitation confirmation diagnostic")
    if candidates and response.get("pass2_required") is not True:
        errors.append("pass2_required must be true when candidates exist")
    if validate_discovery_narrative:
        # Full final product: revalidate both discovery and plan prose.
        errors.extend(validate_step04_narrative_language(response))
    else:
        # Isolated plan: the already-validated discovery candidate is immutable.
        errors.extend(validate_step04_plan_narrative(response))
    return errors

# Original func/step04/pass1_engine.py:7951
def normalize_group_summary_contract(
    value: dict[str, Any],
    *,
    package_id: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Repair provider output against deterministic group identity and coverage.

    Provider output may flatten a single observation row or omit deterministic
    coverage arrays. Those fields are never model judgments, so they are rebuilt
    from the validated source context. Provider text is retained only for the
    descriptive fields of known assets and group-level synthesis arrays.
    """

    canonical = build_deterministic_group_summary(
        package_id=package_id,
        context=context,
    )
    if not isinstance(value, dict):
        return canonical

    provider_rows: list[dict[str, Any]] = []
    rows = value.get("integrated_observations")
    if isinstance(rows, list):
        provider_rows.extend(item for item in rows if isinstance(item, dict))
    if value.get("asset_id") is not None:
        provider_rows.append(value)
    provider_by_asset = {
        str(item.get("asset_id")): item
        for item in provider_rows
        if item.get("asset_id") is not None
    }

    merged_rows: list[dict[str, Any]] = []
    for source_row in canonical["integrated_observations"]:
        merged = dict(source_row)
        provider_row = provider_by_asset.get(str(source_row["asset_id"]))
        if provider_row is not None:
            if isinstance(provider_row.get("observed_signals"), list):
                merged["observed_signals"] = [
                    str(item) for item in provider_row["observed_signals"]
                ]
            for field in ("spatial_pattern", "temporal_evolution"):
                if isinstance(provider_row.get(field), str):
                    merged[field] = provider_row[field]
            if provider_row.get("confidence") in {"low", "medium", "high"}:
                merged["confidence"] = provider_row["confidence"]
            if isinstance(provider_row.get("limitations"), list):
                merged["limitations"] = [str(item) for item in provider_row["limitations"]]
        merged["direct_precipitation_signal"] = _normalize_direct_precipitation_signal(
            source_row.get("direct_precipitation_signal")
        )
        merged_rows.append(merged)
    canonical["integrated_observations"] = merged_rows
    canonical["direct_precipitation_audit"] = _direct_precipitation_audit_from_rows(merged_rows)

    for field in (
        "cross_product_relationships",
        "uncertainty",
        "omitted_or_unresolved_signals",
    ):
        candidate = value.get(field)
        if isinstance(candidate, list):
            canonical[field] = _unique_nonempty_strings(candidate)
    return canonical

# Original func/step04/pass1_engine.py:8018
def validate_group_summary(
    summary: dict[str, Any],
    *,
    schema: dict[str, Any],
    package_id: str,
    context: dict[str, Any],
    enforce_byte_budget: bool = True,
) -> list[str]:
    errors = _validate_jsonschema(summary, schema)
    expected_fixed = {
        "schema_version": GROUP_SUMMARY_SCHEMA_VERSION,
        "package_id": package_id,
        "summary_id": context["summary_id"],
        "analysis_tier": context["analysis_tier"],
        "group_id": context["group_id"],
    }
    for field, expected in expected_fixed.items():
        if summary.get(field) != expected:
            errors.append(f"{field} does not match group contract")
    expected_batch_ids = list(context["source_batch_ids"])
    expected_asset_ids = list(context["source_asset_ids"])
    expected_products = list(context["source_product_ids"])
    expected_hours = list(context["forecast_hours"])
    if Counter(str(value) for value in summary.get("source_batch_ids", [])) != Counter(expected_batch_ids):
        errors.append("source_batch_ids coverage mismatch")
    source_asset_ids = [str(value) for value in summary.get("source_asset_ids", [])]
    if Counter(source_asset_ids) != Counter(expected_asset_ids):
        errors.append("source_asset_ids coverage mismatch")
    if len(source_asset_ids) != len(set(source_asset_ids)):
        errors.append("source_asset_ids contains duplicates")
    if sorted(str(value) for value in summary.get("source_product_ids", [])) != expected_products:
        errors.append("source_product_ids mismatch")
    if sorted(int(value) for value in summary.get("forecast_hours", [])) != expected_hours:
        errors.append("forecast_hours mismatch")
    if summary.get("valid_time_range") != context["valid_time_range"]:
        errors.append("valid_time_range mismatch")
    rows = summary.get("integrated_observations", [])
    if isinstance(rows, list):
        integrated_asset_ids = [str(item.get("asset_id")) for item in rows if isinstance(item, dict)]
        if Counter(integrated_asset_ids) != Counter(expected_asset_ids):
            errors.append("integrated_observations asset coverage mismatch")
        for item in rows:
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get("asset_id"))
            source = context["attachment_by_id"].get(asset_id)
            if source is None:
                errors.append(f"integrated_observations has unexpected asset_id: {asset_id}")
                continue
            if item.get("source_batch_id") != context["asset_to_batch_id"].get(asset_id):
                errors.append(f"source_batch_id mismatch for {asset_id}")
            if item.get("product_id") != source.get("product_id"):
                errors.append(f"product_id mismatch for {asset_id}")
            if [int(value) for value in item.get("forecast_hours", [])] != [
                int(value) for value in source.get("forecast_hours", [])
            ]:
                errors.append(f"forecast_hours mismatch for {asset_id}")
            errors.extend(
                _validate_direct_precipitation_signal_for_asset(
                    item.get("direct_precipitation_signal"),
                    product_id=str(source.get("product_id")),
                    allowed_hours=source.get("forecast_hours", []),
                    path=f"integrated_observations[{asset_id}].direct_precipitation_signal",
                )
            )
    expected_audit = _direct_precipitation_audit_from_rows(
        [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []
    )
    if summary.get("direct_precipitation_audit") != expected_audit:
        errors.append("direct_precipitation_audit does not match integrated precipitation observation")
    if enforce_byte_budget and json_utf8_bytes(summary) > GROUP_SUMMARY_MAX_BYTES:
        errors.append(
            f"group summary exceeds {GROUP_SUMMARY_MAX_BYTES} UTF-8 bytes: {json_utf8_bytes(summary)}"
        )
    return errors

# Original func/step04/pass1_engine.py:8165
def build_group_checkpoint_contract(
    *,
    package_id: str,
    context: dict[str, Any],
    batches: list[dict[str, Any]],
    prompt_sha256: str,
    schema_sha256: str,
    model: str,
    provider_configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    observation_plan = [
        {
            "batch_id": batch["batch_id"],
            "analysis_tier": batch.get("analysis_tier"),
            "group_id": batch.get("group_id"),
            "attachment_ids": batch["attachment_ids"],
        }
        for batch in batches
    ]
    contract = {
        "package_id": package_id,
        "summary_id": context["summary_id"],
        "analysis_tier": context["analysis_tier"],
        "group_id": context["group_id"],
        "observation_plan_fingerprint": canonical_hash(observation_plan),
        "source_observation_fingerprint": canonical_hash(context["source_observations"]),
        "source_asset_ids": sorted(context["source_asset_ids"]),
        "group_prompt_sha256": prompt_sha256,
        "group_schema_sha256": schema_sha256,
        "model": model,
        "provider_configuration": provider_configuration or {},
        "synthesis_contract_version": GROUP_SUMMARY_CONTRACT_VERSION,
    }
    return {**contract, "fingerprint": canonical_hash(contract)}

# Original func/step04/pass1_engine.py:8409
def _candidate_discovery_scope_invalid_direct_ids(
    raw_candidates: list[Any],
    *,
    overview_data: dict[str, Any] | None,
) -> list[str]:
    """Return provider-position candidate IDs that violate the fixed forecast scope."""
    if not isinstance(overview_data, dict):
        return []
    catalog = overview_data.get("operational_region_catalog")
    if not isinstance(catalog, dict):
        return []
    invalid: list[str] = []
    for index, candidate in enumerate(raw_candidates, start=1):
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("detection_basis") or "") not in {"direct_precipitation", "mixed"}:
            continue
        bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
        if bbox is None:
            continue
        if not _selectable_candidate_operational_regions(
            candidate_bbox=bbox,
            operational_region_catalog=catalog,
        ):
            invalid.append(f"CAND_{index:03d}")
    return invalid

# Original func/step04/pass1_engine.py:8437
def _canonicalize_unreferenced_nonoperational_direct_candidates(
    value: dict[str, Any],
    *,
    overview_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Drop only safely removable source-corridor-only direct/mixed candidates.

    This is a policy/representation canonicalization, not a meteorological rewrite.
    A direct/mixed candidate outside every eligible forecast region cannot enter
    downstream operational verification.  If no precursor opportunity references
    that positional candidate ID, it can be removed together with its paired evidence
    while retaining and renumbering all other model judgments.  When a precursor
    relation references the invalid candidate, leave it untouched so whole-response
    AI repair can reconsider that meteorological relationship instead of Python
    silently reclassifying it.
    """
    if not isinstance(value, dict):
        return value
    candidates = value.get("candidates")
    evidence = value.get("candidate_evidence")
    if not isinstance(candidates, list) or not isinstance(evidence, list):
        return value
    if len(candidates) != len(evidence):
        return value

    invalid_ids = set(
        _candidate_discovery_scope_invalid_direct_ids(
            candidates, overview_data=overview_data
        )
    )
    if not invalid_ids:
        return value

    opportunities = value.get("precursor_opportunities")
    referenced_direct_ids: set[str] = set()
    if isinstance(opportunities, list):
        for opportunity in opportunities:
            if not isinstance(opportunity, dict):
                continue
            referenced_direct_ids.update(
                str(candidate_id)
                for candidate_id in opportunity.get("related_direct_candidate_ids", [])
                if str(candidate_id)
            )

    safely_removable = invalid_ids - referenced_direct_ids
    if not safely_removable:
        return value

    normalized = copy.deepcopy(value)
    kept_candidates: list[Any] = []
    kept_evidence: list[Any] = []
    id_map: dict[str, str] = {}
    for old_index, (candidate, group) in enumerate(zip(candidates, evidence), start=1):
        old_id = f"CAND_{old_index:03d}"
        if old_id in safely_removable:
            logging.warning(
                "STEP04_CANDIDATE_SCOPE_PRUNE candidate_id=%s reason=source_corridor_only_direct_or_mixed",
                old_id,
            )
            continue
        new_id = f"CAND_{len(kept_candidates) + 1:03d}"
        id_map[old_id] = new_id
        kept_candidates.append(copy.deepcopy(candidate))
        kept_evidence.append(copy.deepcopy(group))

    normalized["candidates"] = kept_candidates
    normalized["candidate_evidence"] = kept_evidence

    if isinstance(normalized.get("precursor_opportunities"), list):
        for opportunity in normalized["precursor_opportunities"]:
            if not isinstance(opportunity, dict):
                continue
            related = opportunity.get("related_direct_candidate_ids")
            if isinstance(related, list):
                opportunity["related_direct_candidate_ids"] = [
                    id_map.get(str(candidate_id), str(candidate_id))
                    for candidate_id in related
                ]
            created_id = opportunity.get("created_candidate_id")
            if isinstance(created_id, str) and created_id in id_map:
                opportunity["created_candidate_id"] = id_map[created_id]

    logging.warning(
        "STEP04_CANDIDATE_SCOPE_CANONICALIZED removed=%s retained=%s",
        sorted(safely_removable),
        len(kept_candidates),
    )
    return normalized

# Original func/step04/pass1_engine.py:8528
def _selectable_candidate_operational_regions(
    *,
    candidate_bbox: dict[str, Any],
    operational_region_catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    """Resolve Step04 regional targets with the same cheap bbox rule as Step05.

    Step05 remains authoritative for the final grid/mask calculation.  This
    preflight only prevents Step04 from emitting an operational-region target
    that is guaranteed to resolve to an empty set downstream.
    """
    regions = [
        item
        for item in operational_region_catalog.get("regions", [])
        if isinstance(item, dict)
    ]
    return selectable_pass1_regions(
        regions=regions,
        candidate_bbox=candidate_bbox,
    )

# Original func/step04/pass1_engine.py:8666
def _operational_region_target_empty_error(
    *,
    candidate_id: str,
    candidate_bbox: dict[str, Any],
    check_keys: Iterable[str] = (),
) -> str:
    keys = [str(value) for value in check_keys if str(value)]
    suffix = f" regional_check_keys={keys}" if keys else ""
    return (
        "OPERATIONAL_REGION_TARGET_EMPTY "
        f"candidate_id={candidate_id} candidate_area={candidate_bbox}{suffix}; "
        "candidate_area overlaps no enabled Pass1 operational_land/marine_macro region. "
        "Do not change candidate_area merely to satisfy execution. Use spatial-only "
        "verification for this candidate, or regenerate the regional verification request "
        "only when an actual operational-region overlap exists."
    )

# Original func/step04/pass1_engine.py:8844
def _normalize_candidate_discovery_provider_shape(
    value: dict[str, Any],
    *,
    attachment_manifest: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Wrap a complete single candidate when the provider omitted the stage envelope."""
    candidate_markers = {
        "candidate_name",
        "candidate_area",
        "time_window",
        "related_overview_asset_ids",
    }
    if "candidates" in value or not candidate_markers.issubset(value):
        return value, False
    if attachment_manifest is None:
        raise ValueError(
            "candidate discovery single-candidate response requires attachment manifest"
        )

    candidate = copy.deepcopy(value)
    global_uncertainties = candidate.pop("global_uncertainties", [])
    for key in (
        "package_id",
        "analysis_coverage",
        "run_summary",
        "pass2_required",
        "candidate_evidence",
        "candidate_id",
        "candidate_unit_type",
        "discovery_order",
        "priority_rank",
    ):
        candidate.pop(key, None)

    attachment_by_id = _attachment_by_id(attachment_manifest)
    related_asset_ids = list(
        dict.fromkeys(
            str(asset_id)
            for asset_id in candidate.get("related_overview_asset_ids", [])
        )
    )
    if not related_asset_ids:
        raise ValueError(
            "candidate discovery single-candidate response has no related overview assets"
        )
    unknown_asset_ids = [
        asset_id for asset_id in related_asset_ids if asset_id not in attachment_by_id
    ]
    if unknown_asset_ids:
        raise ValueError(
            "candidate discovery single-candidate response references unknown overview "
            f"assets: {unknown_asset_ids}"
        )
    candidate["related_overview_asset_ids"] = related_asset_ids

    evidence_items = []
    for asset_id in related_asset_ids:
        source = attachment_by_id[asset_id]
        evidence_items.append(
            {
                "asset_id": asset_id,
                "product_id": str(source.get("product_id")),
                "forecast_hours": [
                    int(hour) for hour in source.get("forecast_hours", [])
                ],
                "reason": "후보 응답에서 직접 참조한 개요 자산입니다.",
            }
        )

    return {
        "candidates": [candidate],
        "candidate_evidence": [{"evidence_items": evidence_items}],
        "global_uncertainties": global_uncertainties
        if isinstance(global_uncertainties, list)
        else [],
    }, True

# Original func/step04/pass1_engine.py:8922
def normalize_candidate_discovery_contract(
    value: dict[str, Any],
    *,
    package_id: str,
    attachment_count: int,
    attachment_manifest: dict[str, Any] | None = None,
    stage_schema: dict[str, Any] | None = None,
    group_summaries: list[dict[str, Any]] | None = None,
    overview_data: dict[str, Any] | None = None,
    support_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assign all discovery system fields after validating model judgments only."""
    if not isinstance(value, dict):
        return value
    value, single_candidate_wrapped = _normalize_candidate_discovery_provider_shape(
        value,
        attachment_manifest=attachment_manifest,
    )
    if single_candidate_wrapped:
        logging.warning(
            "candidate discovery single-candidate root normalized candidates=1"
        )
    raw_candidates_for_compatibility = value.get("candidates")
    if isinstance(raw_candidates_for_compatibility, list):
        value = dict(value)
        value["candidates"] = [
            {
                key: field_value
                for key, field_value in infer_legacy_candidate_signal_contract(item).items()
                if key not in {"candidate_id", "candidate_unit_type", "discovery_order", "priority_rank"}
            }
            if isinstance(item, dict)
            else item
            for item in raw_candidates_for_compatibility
        ]
    raw_evidence_for_compatibility = value.get("candidate_evidence")
    if isinstance(raw_evidence_for_compatibility, list):
        value = dict(value)
        value["candidate_evidence"] = [
            {
                key: field_value
                for key, field_value in item.items()
                if key != "candidate_id"
            }
            if isinstance(item, dict)
            else item
            for item in raw_evidence_for_compatibility
        ]
    if stage_schema is not None:
        errors = _validate_jsonschema(value, stage_schema)
        if errors:
            raise ValueError("candidate discovery stage schema invalid: " + " | ".join(errors[:20]))

    value = _canonicalize_unreferenced_nonoperational_direct_candidates(
        value,
        overview_data=overview_data,
    )

    precursor_candidate_assessment = copy.deepcopy(
        value.get("precursor_candidate_assessment")
    )
    precursor_opportunities = copy.deepcopy(value.get("precursor_opportunities"))
    raw_candidates = value.get("candidates", [])
    raw_evidence = value.get("candidate_evidence", [])
    if not isinstance(raw_candidates, list) or not isinstance(raw_evidence, list):
        raise ValueError("candidate discovery candidates and candidate_evidence must be arrays")
    attachment_by_id = (
        _attachment_by_id(attachment_manifest)
        if attachment_manifest is not None
        else None
    )

    candidates: list[dict[str, Any]] = []
    for index, raw_candidate in enumerate(raw_candidates, start=1):
        if not isinstance(raw_candidate, dict):
            raise ValueError(f"candidate discovery candidate is not an object at index={index}")
        candidate = {
            key: json.loads(json.dumps(item, ensure_ascii=False))
            for key, item in raw_candidate.items()
            if key not in {"candidate_id", "candidate_unit_type", "discovery_order", "priority_rank"}
        }
        candidate["candidate_id"] = f"CAND_{index:03d}"
        candidate["candidate_unit_type"] = _candidate_unit_type(candidate)
        candidate["discovery_order"] = index
        if attachment_by_id is not None:
            related_asset_ids = list(
                dict.fromkeys(
                    str(asset_id)
                    for asset_id in candidate.get("related_overview_asset_ids", [])
                )
            )
            unknown_asset_ids = [
                asset_id for asset_id in related_asset_ids if asset_id not in attachment_by_id
            ]
            if unknown_asset_ids:
                raise ValueError(
                    f"candidate discovery references unknown overview assets: {unknown_asset_ids}"
                )
            candidate["related_overview_asset_ids"] = related_asset_ids
            candidate["related_product_ids"] = list(
                dict.fromkeys(
                    str(attachment_by_id[asset_id].get("product_id"))
                    for asset_id in related_asset_ids
                )
            )
        candidates.append(candidate)

    _normalize_candidate_precursor_metadata(
        candidates,
        attachment_by_id=attachment_by_id,
    )
    if precursor_opportunities is not None:
        # Reference/contract validation is owned by the discovery validator below.
        # It runs on the final normalized response before checkpoint publication.
        precursor_candidate_assessment = _derive_legacy_precursor_assessment(
            precursor_opportunities,
            precursor_candidate_assessment if isinstance(precursor_candidate_assessment, dict) else {},
        )
    if not isinstance(precursor_candidate_assessment, dict):
        raise ValueError("candidate discovery requires precursor_candidate_assessment")
    precursor_candidate_assessment["dismissed_precursor_groups"] = (
        _build_dismissed_precursor_groups(
            candidates,
            attachment_by_id=attachment_by_id,
        )
    )

    # The discovery semantic gate checks precursor assessment references
    # and emits the same advisory once, after all projection changes.
    logging.info(
        "step04 precursor candidate assessment status=%s qualified_opportunities=%s selected_candidates=%s",
        precursor_candidate_assessment.get("assessment_status")
        if isinstance(precursor_candidate_assessment, dict) else None,
        precursor_candidate_assessment.get("qualified_opportunity_count")
        if isinstance(precursor_candidate_assessment, dict) else None,
        sum(1 for item in candidates if str(item.get("detection_basis")) == "precursor_environment"),
    )

    evidence_groups: list[dict[str, Any]] = []
    for index, raw_group in enumerate(raw_evidence, start=1):
        if not isinstance(raw_group, dict):
            raise ValueError(f"candidate discovery evidence is not an object at index={index}")
        group = {
            key: json.loads(json.dumps(item, ensure_ascii=False))
            for key, item in raw_group.items()
            if key != "candidate_id"
        }
        if attachment_by_id is not None:
            evidence_items = group.get("evidence_items", [])
            if not isinstance(evidence_items, list):
                raise ValueError(
                    f"candidate discovery evidence_items is not an array at index={index}"
                )
            for evidence_index, evidence_item in enumerate(evidence_items, start=1):
                if not isinstance(evidence_item, dict):
                    raise ValueError(
                        "candidate discovery evidence item is not an object "
                        f"at group={index} index={evidence_index}"
                    )
                asset_id = str(evidence_item.get("asset_id", ""))
                source = attachment_by_id.get(asset_id)
                if source is None:
                    raise ValueError(
                        f"candidate discovery evidence references unknown asset_id: {asset_id}"
                    )
                evidence_item["product_id"] = str(source.get("product_id"))
        if index <= len(candidates):
            group["candidate_id"] = candidates[index - 1]["candidate_id"]
        evidence_groups.append(group)

    normalized_response = {
        "candidates": candidates,
        "candidate_evidence": evidence_groups,
    }
    # Group-summary direct_precipitation_audit preserves what was visible in the
    # overview imagery, including remote/source-corridor precipitation.  It is
    # intentionally NOT canonicalized into candidate evidence.  Only the SYSTEM
    # operational-region precipitation audit below may deterministically backfill
    # a precipitation evidence hour for an existing candidate.
    if overview_data is not None and attachment_manifest is not None:
        _canonicalize_system_direct_precipitation_evidence(
            normalized_response,
            overview_data=overview_data,
            attachment_manifest=attachment_manifest,
            support_catalog=support_catalog,
        )

    normalized_result = {
        "package_id": package_id,
        "analysis_coverage": {
            "coverage_status": "complete",
            "overview_attachment_count": int(attachment_count),
        },
        "run_summary": {
            "candidate_count": len(candidates),
            "summary_text": "검증된 그룹 요약을 기반으로 후보 탐색을 완료했습니다.",
        },
        "candidates": candidates,
        "candidate_evidence": evidence_groups,
        "global_uncertainties": [
            str(item) for item in value.get("global_uncertainties", [])
        ]
        if isinstance(value.get("global_uncertainties"), list)
        else [],
        **({"precursor_opportunities": precursor_opportunities} if precursor_opportunities is not None else {}),
        "precursor_candidate_assessment": precursor_candidate_assessment,
        "pass2_required": bool(candidates),
    }
    direction_corrections = normalize_step04_discovery_directions(normalized_result)
    if direction_corrections:
        logging.info(
            "STEP04_DISCOVERY_DIRECTION_NORMALIZED replacements=%s",
            direction_corrections,
        )
    return normalized_result

# Original func/step04/pass1_engine.py:9146
def filter_request_analysis_methods(
    methods: list[str],
    *,
    allowed_methods: set[str],
) -> tuple[list[str], list[str]]:
    """Keep only methods that the request data type can execute."""
    retained = [method for method in methods if method in allowed_methods]
    removed = [method for method in methods if method not in allowed_methods]
    return retained, removed

# Original func/step04/pass1_engine.py:9157
def _prune_unreferenced_context_phases(
    plan: dict[str, Any],
    *,
    canonicalization_events: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Remove context-only phases that no diagnostic check actually executes."""
    phases = [
        item for item in plan.get("analysis_phases", []) if isinstance(item, dict)
    ]
    referenced_phase_ids = {
        str(value)
        for check in plan.get("variable_checks", [])
        if isinstance(check, dict)
        for value in check.get("phase_ids", [])
    }
    removed = [
        str(phase.get("phase_id"))
        for phase in phases
        if str(phase.get("phase_type")) == "context"
        and str(phase.get("phase_id")) not in referenced_phase_ids
    ]
    if not removed:
        return []
    removed_set = set(removed)
    plan["analysis_phases"] = [
        phase for phase in phases if str(phase.get("phase_id")) not in removed_set
    ]
    if canonicalization_events is not None:
        canonicalization_events.append(
            {
                "field": "candidate_analysis_plan.analysis_phases",
                "reason": "unreferenced_context_phases_removed",
                "removed_phase_ids": removed,
            }
        )
    return removed

# Original func/step04/pass1_engine.py:9195
def normalize_candidate_plan_contract(
    value: dict[str, Any],
    *,
    package_id: str,
    candidate_id: str,
    stage_schema: dict[str, Any] | None = None,
    support_catalog: dict[str, Any] | None = None,
    canonicalization_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Project local plan references into deterministic Pass1 identifiers."""
    if not isinstance(value, dict):
        return value
    if (
        "candidate_analysis_plan" not in value
        and isinstance(value.get("candidate_analysis_analysis_plan"), dict)
    ):
        value["candidate_analysis_plan"] = value.pop(
            "candidate_analysis_analysis_plan"
        )
    raw_plan_for_normalization = value.get("candidate_analysis_plan")
    if (
        isinstance(raw_plan_for_normalization, dict)
        and "target_areas" not in raw_plan_for_normalization
        and isinstance(raw_plan_for_normalization.get("target_target_areas"), list)
    ):
        raw_plan_for_normalization["target_areas"] = raw_plan_for_normalization.pop(
            "target_target_areas"
        )
    raw_requests_for_normalization = value.get("analysis_data_requests")
    if isinstance(raw_requests_for_normalization, list):
        for raw_request in raw_requests_for_normalization:
            if not isinstance(raw_request, dict):
                continue
            if not raw_request.get("diagnostic_id"):
                methods = [str(item) for item in raw_request.get("analysis_methods", [])]
                mapping = {
                    "detailed_map": "detailed_spatial_map",
                    "temporal_evolution": "temporal_evolution",
                    "variable_cross_check": "multivariable_composite",
                    "regional_statistics": "temporal_evolution",
                    "regional_ranking": "detailed_spatial_map",
                    "regional_max_location": "detailed_spatial_map",
                }
                raw_request["diagnostic_id"] = next(
                    (mapping[item] for item in methods if item in mapping),
                    "detailed_spatial_map",
                )
            output_spec = raw_request.get("output_spec")
            if isinstance(output_spec, dict):
                raw_request["output_spec"] = {
                    key: item for key, item in output_spec.items() if item is not None
                }
    if stage_schema is not None:
        errors = _validate_jsonschema(value, stage_schema)
        if errors:
            raise ValueError("candidate plan stage schema invalid: " + " | ".join(errors[:20]))

    raw_plan = value.get("candidate_analysis_plan")
    raw_requests = value.get("analysis_data_requests")
    if not isinstance(raw_plan, dict) or not isinstance(raw_requests, list):
        raise ValueError("candidate plan stage response is missing candidate_analysis_plan or requests")
    match = re.fullmatch(r"CAND_(\d{3})", candidate_id)
    if match is None:
        raise ValueError(f"candidate_id is not canonical: {candidate_id}")
    number = match.group(1)
    plan_id = f"PLAN_CAND{number}"

    def canonicalize_rows(
        rows: Any,
        *,
        local_field: str,
        id_prefix: str,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        if not isinstance(rows, list):
            raise ValueError(f"candidate plan {local_field} rows must be an array")
        canonical_rows: list[dict[str, Any]] = []
        mapping: dict[str, str] = {}
        for index, raw_row in enumerate(rows, start=1):
            if not isinstance(raw_row, dict):
                raise ValueError(f"candidate plan {local_field} row is not an object index={index}")
            local_key = str(raw_row.get(local_field, "")).strip()
            if not local_key or local_key in mapping:
                raise ValueError(f"candidate plan {local_field} is missing or duplicated: {local_key!r}")
            canonical_id = f"{id_prefix}{number}_{index:02d}"
            mapping[local_key] = canonical_id
            row = {
                key: json.loads(json.dumps(item, ensure_ascii=False))
                for key, item in raw_row.items()
                if key != local_field
            }
            canonical_rows.append(row)
        return canonical_rows, mapping

    targets, target_ids = canonicalize_rows(
        raw_plan.get("target_areas"), local_field="target_key", id_prefix="AREA_CAND"
    )
    phases, phase_ids = canonicalize_rows(
        raw_plan.get("analysis_phases"), local_field="phase_key", id_prefix="PHASE_CAND"
    )
    checks, check_ids = canonicalize_rows(
        raw_plan.get("variable_checks"), local_field="check_key", id_prefix="CHK_CAND"
    )

    def resolve_local_ids(
        values: Any,
        mapping: dict[str, str],
        *,
        label: str,
    ) -> list[str]:
        if not isinstance(values, list):
            raise ValueError(f"candidate plan {label} must be an array")
        keys = [str(value) for value in values]
        unknown = sorted(set(keys) - set(mapping))
        if unknown:
            raise ValueError(
                f"candidate plan {label} references unknown local keys: {unknown}"
            )
        return [mapping[key] for key in keys]

    def resolve_local_id(
        value: Any,
        mapping: dict[str, str],
        *,
        label: str,
    ) -> str:
        key = str(value)
        if key not in mapping:
            raise ValueError(
                f"candidate plan {label} references unknown local key: {key!r}"
            )
        return mapping[key]

    for row, canonical_id in zip(targets, target_ids.values(), strict=True):
        row["target_id"] = canonical_id
    for row, canonical_id in zip(phases, phase_ids.values(), strict=True):
        row["phase_id"] = canonical_id
    for row, canonical_id in zip(checks, check_ids.values(), strict=True):
        row["check_id"] = canonical_id
        row["target_ids"] = resolve_local_ids(
            row.pop("target_keys", []),
            target_ids,
            label="variable_check.target_keys",
        )
        row["phase_ids"] = resolve_local_ids(
            row.pop("phase_keys", []),
            phase_ids,
            label="variable_check.phase_keys",
        )

    hypotheses: list[dict[str, Any]] = []
    raw_hypotheses = raw_plan.get("hypotheses")
    if not isinstance(raw_hypotheses, list):
        raise ValueError("candidate plan hypotheses must be an array")
    for index, raw_hypothesis in enumerate(raw_hypotheses, start=1):
        if not isinstance(raw_hypothesis, dict):
            raise ValueError(f"candidate plan hypothesis is not an object index={index}")
        hypotheses.append(
            {
                "hypothesis_id": f"H_CAND{number}_{index:02d}",
                "statement": raw_hypothesis.get("statement"),
                "supporting_check_ids": resolve_local_ids(
                    raw_hypothesis.get("supporting_check_keys", []),
                    check_ids,
                    label="hypothesis.supporting_check_keys",
                ),
                "opposing_check_ids": resolve_local_ids(
                    raw_hypothesis.get("opposing_check_keys", []),
                    check_ids,
                    label="hypothesis.opposing_check_keys",
                ),
            }
        )

    requests: list[dict[str, Any]] = []
    for index, raw_request in enumerate(raw_requests, start=1):
        if not isinstance(raw_request, dict):
            raise ValueError(f"candidate plan request is not an object index={index}")
        request = {
            key: json.loads(json.dumps(item, ensure_ascii=False))
            for key, item in raw_request.items()
            if key not in {"check_key", "target_key", "phase_keys"}
        }
        request.update(
            {
                "request_id": f"REQ_CAND{number}_{index:03d}",
                "candidate_id": candidate_id,
                "plan_id": plan_id,
                "check_id": resolve_local_id(
                    raw_request.get("check_key", ""),
                    check_ids,
                    label="analysis_data_request.check_key",
                ),
                "target_id": resolve_local_id(
                    raw_request.get("target_key", ""),
                    target_ids,
                    label="analysis_data_request.target_key",
                ),
                "phase_ids": resolve_local_ids(
                    raw_request.get("phase_keys", []),
                    phase_ids,
                    label="analysis_data_request.phase_keys",
                ),
            }
        )
        requests.append(request)

    plan_support = (support_catalog or {}).get("analysis_plan_support", {})
    method_order = [
        str(method)
        for method in plan_support.get("supported_analysis_methods", [])
    ]
    if not method_order:
        method_order = [
            "detailed_map",
            "regional_statistics",
            "regional_ranking",
            "regional_max_location",
            "temporal_evolution",
            "variable_cross_check",
        ]
    supported_methods = set(method_order)
    methods_by_data_type = {
        "spatial_field": {"detailed_map", "temporal_evolution", "variable_cross_check"},
        "quantitative_excerpt": {
            "regional_statistics",
            "regional_ranking",
            "regional_max_location",
            "temporal_evolution",
        },
    }
    supported_roles = {
        str(role)
        for role in [
            *plan_support.get("required_variable_roles", []),
            *plan_support.get("recommended_explanatory_roles", []),
            "uncertainty",
        ]
    }
    if supported_roles == {"uncertainty"}:
        supported_roles.update(
            {
                "hazard_magnitude",
                "moisture_supply",
                "low_level_forcing",
                "midlevel_forcing",
                "synoptic_support",
            }
        )

    def method_allowed_for_contract(
        *,
        method: str,
        variable_role: str,
        data_type: str,
    ) -> bool:
        """Apply the catalog's current role and data-type method constraints."""
        return (
            variable_role in supported_roles
            and method in methods_by_data_type.get(data_type, set())
        )

    def canonical_methods(values: Any) -> list[str]:
        if not isinstance(values, list):
            raise ValueError("candidate plan analysis_methods must be an array")
        normalized = {str(method) for method in values}
        return [method for method in method_order if method in normalized] + sorted(
            normalized - set(method_order)
        )

    checks_by_id = {str(check["check_id"]): check for check in checks}
    for request in requests:
        request_id = str(request["request_id"])
        check_id = str(request["check_id"])
        check = checks_by_id.get(check_id)
        if check is None:
            raise ValueError(f"{request_id} references unknown check_id={check_id}")
        variable_role = str(check.get("variable_role"))
        data_type = str(request.get("data_type"))
        if variable_role not in supported_roles:
            raise ValueError(
                f"{request_id} references unsupported variable_role={variable_role}"
            )
        request_methods = canonical_methods(request.get("analysis_methods", []))
        unsupported = sorted(set(request_methods) - supported_methods)
        if unsupported:
            raise ValueError(
                f"{request_id} uses unsupported analysis_methods={unsupported}"
            )
        allowed_for_data_type = methods_by_data_type.get(data_type)
        if allowed_for_data_type is None:
            raise ValueError(f"{request_id} has unsupported data_type={request.get('data_type')!r}")
        request_methods, invalid_for_contract = filter_request_analysis_methods(
            request_methods,
            allowed_methods=allowed_for_data_type,
        )
        if invalid_for_contract:
            if not request_methods:
                raise ValueError(
                    f"{request_id} has no analysis_methods allowed for "
                    f"variable_role={variable_role} data_type={data_type}"
                )
            if canonicalization_events is not None:
                canonicalization_events.append(
                    {
                        "field": f"analysis_data_requests[{request_id}].analysis_methods",
                        "reason": "methods_not_allowed_for_data_type_removed",
                        "removed_values": invalid_for_contract,
                        "remaining_values": request_methods,
                    }
                )
        check_methods = canonical_methods(check.get("analysis_methods", []))
        added = [method for method in request_methods if method not in check_methods]
        if added:
            check["analysis_methods"] = canonical_methods([*check_methods, *added])
            if canonicalization_events is not None:
                canonicalization_events.append(
                    {
                        "field": (
                            "candidate_analysis_plan.variable_checks"
                            f"[{check_id}].analysis_methods"
                        ),
                        "reason": "request_method_missing_from_check",
                        "source_request_id": request_id,
                        "added_values": added,
                    }
                )
        else:
            check["analysis_methods"] = check_methods
        request["analysis_methods"] = request_methods

    requests = _merge_analysis_data_requests(requests, candidate_number=number)

    plan = {
        "plan_id": plan_id,
        "candidate_id": candidate_id,
        "analysis_objective": raw_plan.get("analysis_objective"),
        "hypotheses": hypotheses,
        "target_areas": targets,
        "analysis_phases": phases,
        "variable_checks": checks,
        "forecaster_questions": raw_plan.get("forecaster_questions"),
    }
    _link_unreferenced_variable_checks(
        plan,
        canonicalization_events=canonicalization_events,
    )
    _prune_unreferenced_context_phases(
        plan,
        canonicalization_events=canonicalization_events,
    )
    return {
        "package_id": package_id,
        "candidate_id": candidate_id,
        "candidate_analysis_plan": plan,
        "analysis_data_requests": requests,
    }

# Original func/step04/pass1_engine.py:9553
def validate_candidate_discovery_response(
    response: dict[str, Any],
    *,
    schema: dict[str, Any],
    package_id: str,
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    group_summaries: list[dict[str, Any]] | None = None,
    overview_data: dict[str, Any] | None = None,
    enforce_system_direct_precipitation_coverage: bool = True,
) -> list[str]:
    """Validate candidate discovery without requiring plans or data requests."""
    errors = _validate_jsonschema(response, schema)
    attachment_by_id = _attachment_by_id(attachment_manifest)
    attachment_ids = set(attachment_by_id)
    supported_products = {
        str(item.get("product_id"))
        for item in support_catalog.get("spatial_field_products", [])
        if isinstance(item, dict) and item.get("product_id")
    }
    if response.get("package_id") != package_id:
        errors.append("package_id does not match Step04 input package")
    coverage = response.get("analysis_coverage", {})
    if isinstance(coverage, dict):
        if int(coverage.get("overview_attachment_count", -1)) != len(attachment_ids):
            errors.append("analysis_coverage.overview_attachment_count mismatch")
        if coverage.get("coverage_status") != "complete":
            errors.append("analysis_coverage.coverage_status must be complete")

    candidates = response.get("candidates", [])
    candidate_ids = [str(item.get("candidate_id")) for item in candidates if isinstance(item, dict)]
    candidate_id_set = set(candidate_ids)
    if len(candidate_ids) != len(candidate_id_set):
        errors.append("candidate_id values must be unique")
    summary = response.get("run_summary", {})
    if isinstance(summary, dict) and int(summary.get("candidate_count", -1)) != len(candidates):
        errors.append("run_summary.candidate_count mismatch")
    discovery_orders = [
        int(item.get("discovery_order"))
        for item in candidates
        if isinstance(item, dict) and item.get("discovery_order") is not None
    ]
    if len(discovery_orders) != len(set(discovery_orders)):
        errors.append("discovery_order values must be unique")
    if discovery_orders and sorted(discovery_orders) != list(range(1, len(discovery_orders) + 1)):
        errors.append("discovery_order values must be contiguous from 1")

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id"))
        expected_unit_type = _candidate_unit_type(candidate)
        if candidate.get("candidate_unit_type") != expected_unit_type:
            errors.append(
                f"{candidate_id} candidate_unit_type must be {expected_unit_type!r} "
                f"for detection_basis={candidate.get('detection_basis')!r}"
            )
        related_assets = [str(value) for value in candidate.get("related_overview_asset_ids", [])]
        unknown_assets = sorted(set(related_assets) - attachment_ids)
        if unknown_assets:
            errors.append(f"{candidate_id} references unknown overview assets: {unknown_assets}")
        related_products = {str(value) for value in candidate.get("related_product_ids", [])}
        unknown_products = sorted(related_products - supported_products)
        if unknown_products:
            errors.append(f"{candidate_id} references unsupported product IDs: {unknown_products}")
        actual_products = {
            str(attachment_by_id[asset_id].get("product_id"))
            for asset_id in related_assets
            if asset_id in attachment_by_id
        }
        if not actual_products.issubset(related_products):
            errors.append(f"{candidate_id} related_product_ids do not cover referenced assets")
        bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
        if bbox is None:
            errors.append(f"{candidate_id} candidate_area bbox is invalid")
        elif (
            str(candidate.get("detection_basis") or "") in {"direct_precipitation", "mixed"}
            and isinstance(overview_data, dict)
            and isinstance(overview_data.get("operational_region_catalog"), dict)
        ):
            forecast_regions = _selectable_candidate_operational_regions(
                candidate_bbox=bbox,
                operational_region_catalog=overview_data["operational_region_catalog"],
            )
            if not forecast_regions:
                errors.append(
                    "CANDIDATE_OPERATIONAL_SCOPE_INVALID "
                    f"candidate_id={candidate_id} candidate_area={bbox}; "
                    "direct/mixed candidate_area overlaps no eligible forecast operational_land/marine_macro region; "
                    "source-corridor-only precipitation must remain contextual or precursor evidence"
                )
        window = candidate.get("time_window", {})
        if isinstance(window, dict):
            start = int(window.get("forecast_hour_start", -1))
            end = int(window.get("forecast_hour_end", -1))
            if start > end:
                errors.append(f"{candidate_id} time_window start exceeds end")
            for hour in candidate.get("peak_forecast_hours", []):
                if int(hour) < start or int(hour) > end:
                    errors.append(f"{candidate_id} peak hour {hour} is outside time_window")
        _log_step04_advisories(
            "candidate_discovery.signal_classification",
            [f"{candidate_id} {signal_error}" for signal_error in validate_candidate_signal_contract(candidate)],
        )

    _log_step04_advisories(
        "candidate_discovery.precursor_evidence",
        _precursor_candidate_evidence_advisories(
            [item for item in candidates if isinstance(item, dict)],
            attachment_manifest=attachment_manifest,
        ),
    )
    try:
        _validate_precursor_opportunities(
            response.get("precursor_opportunities"),
            candidates=[item for item in candidates if isinstance(item, dict)],
            attachment_manifest=attachment_manifest,
        )
    except ValueError as exc:
        errors.append(str(exc))
    try:
        _validate_precursor_candidate_assessment(
            response.get("precursor_candidate_assessment"),
            candidates=[item for item in candidates if isinstance(item, dict)],
            attachment_manifest=attachment_manifest,
        )
    except ValueError as exc:
        errors.append(str(exc))
    else:
        _log_step04_advisories(
            "candidate_discovery.precursor_assessment",
            _precursor_candidate_assessment_advisories(
                response.get("precursor_candidate_assessment"),
                candidates=[item for item in candidates if isinstance(item, dict)],
                attachment_manifest=attachment_manifest,
            ),
        )
    if group_summaries is not None:
        _log_step04_advisories(
            "candidate_discovery.precipitation_coverage",
            _significant_precipitation_coverage_advisories(
                response,
                group_summaries=group_summaries,
                attachment_manifest=attachment_manifest,
            ),
        )
    if overview_data is not None:
        system_precipitation_audit = build_system_direct_precipitation_coverage_audit(
            response,
            overview_data=overview_data,
            attachment_manifest=attachment_manifest,
            support_catalog=support_catalog,
        )
        if enforce_system_direct_precipitation_coverage:
            errors.extend(_system_direct_precipitation_coverage_errors(system_precipitation_audit))
        _log_step04_advisories(
            "candidate_discovery.system_direct_precipitation_coverage",
            _system_direct_precipitation_coverage_advisories(system_precipitation_audit),
        )

    evidence_groups = response.get("candidate_evidence", [])
    evidence_candidate_ids = [
        str(item.get("candidate_id")) for item in evidence_groups if isinstance(item, dict)
    ]
    if Counter(evidence_candidate_ids) != Counter(candidate_ids):
        errors.append("candidate_evidence must contain exactly one group for each candidate")
    for group in evidence_groups:
        if not isinstance(group, dict):
            continue
        candidate_id = str(group.get("candidate_id"))
        if candidate_id not in candidate_id_set:
            errors.append(f"candidate_evidence references unknown candidate_id: {candidate_id}")
        for item in group.get("evidence_items", []):
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get("asset_id"))
            source = attachment_by_id.get(asset_id)
            if source is None:
                errors.append(f"candidate evidence references unknown asset_id: {asset_id}")
                continue
            if item.get("product_id") != source.get("product_id"):
                errors.append(f"candidate evidence product mismatch asset_id={asset_id}")
            allowed_hours = {int(value) for value in source.get("forecast_hours", [])}
            invalid_hours = sorted(
                {int(value) for value in item.get("forecast_hours", [])} - allowed_hours
            )
            if invalid_hours:
                errors.append(
                    f"candidate evidence has invalid forecast hours asset_id={asset_id}: {invalid_hours}"
                )

    candidate_by_id = {
        str(item.get("candidate_id")): item
        for item in candidates
        if isinstance(item, dict)
    }
    evidence_by_id = {
        str(item.get("candidate_id")): item
        for item in evidence_groups
        if isinstance(item, dict)
    }
    for candidate_id, candidate in candidate_by_id.items():
        peak_groups = _contiguous_hour_groups(candidate.get("peak_forecast_hours", []))
        if len(peak_groups) <= 1:
            continue
        group = evidence_by_id.get(candidate_id, {})
        evidence_hours = {
            int(hour)
            for item in group.get("evidence_items", [])
            if isinstance(item, dict)
            for hour in item.get("forecast_hours", [])
        }
        related_asset_hours = {
            int(hour)
            for asset_id in candidate.get("related_overview_asset_ids", [])
            for hour in attachment_by_id.get(str(asset_id), {}).get("forecast_hours", [])
        }
        peak_group_notes: list[str] = []
        for peak_group in peak_groups:
            peak_values = set(peak_group)
            if not peak_values & evidence_hours:
                peak_group_notes.append(
                    f"{candidate_id} evidence does not cover separated peak group={peak_group}"
                )
            if not peak_values & related_asset_hours:
                peak_group_notes.append(
                    f"{candidate_id} related overview assets do not cover separated peak group={peak_group}"
                )
        _log_step04_advisories("candidate_discovery.peak_evidence", peak_group_notes)

    pass2_required = response.get("pass2_required")
    if candidates and pass2_required is not True:
        errors.append("pass2_required must be true when candidates exist")
    if not candidates and pass2_required is not False:
        errors.append("pass2_required must be false when no candidates exist")
    errors.extend(validate_candidate_discovery_narrative_language(response))
    return errors

# Original func/step04/pass1_engine.py:9824
def _expanded_plan_quantitative_contract_errors(plan: dict[str, Any]) -> list[str]:
    """Validate already-expanded plans, including previously cached checkpoints.

    Compact generation preflight alone cannot protect resumed Step04 plans.
    Only the one-claim classes that Step07 already enforces are checked; multi-
    claim image interpretations retain their existing shadow-mode treatment.
    """
    errors: list[str] = []
    for check in plan.get("variable_checks", []):
        if not isinstance(check, dict):
            continue
        contract = check.get("atomic_claim_contract")
        claims = contract.get("canonical_claims") if isinstance(contract, dict) else None
        if not isinstance(claims, list) or len(claims) != 1 or not isinstance(claims[0], dict):
            continue
        claim = claims[0]
        state = str(claim.get("expected_state") or "")
        quantifier = str(claim.get("scope_quantifier") or "")
        scope = claim.get("quantitative_scope")
        check_id = str(check.get("check_id") or "")
        if (
            quantifier == "specific"
            and state in {"greater_than", "less_than", "increase", "decrease", "strengthen", "weaken"}
            and not isinstance(scope, dict)
        ) or (
            quantifier == "majority" and state == "not_strengthen"
            and not isinstance(scope, dict)
        ):
            errors.append(
                "QUANTITATIVE_SCOPE_CONTRACT_INVALID "
                f"check_id={check_id} claim_key={claim.get('claim_key')} "
                "enforced quantitative_scope absent in expanded/checkpointed plan"
            )
            continue
        if quantifier == "majority" and state == "not_strengthen" and any(
            opposite_majority_not_strengthen_wording(text) for text in (
                claim.get("claim_statement"), check.get("expected_signal")
            )
        ):
            errors.append(
                "QUANTITATIVE_MAJORITY_POLARITY_MISMATCH "
                f"check_id={check_id} claim_key={claim.get('claim_key')} "
                "positive majority natural-language proposition contradicts not_strengthen expected_state"
            )
        if quantifier == "specific" and state in {"greater_than", "less_than", "increase", "decrease", "strengthen", "weaken"} and isinstance(scope, dict):
            for proposition in dict.fromkeys((
                str(claim.get("claim_statement") or ""),
                str(check.get("expected_signal") or ""),
            )):
                fixed_cutoff = absolute_threshold_in_hour_comparison(proposition)
                if fixed_cutoff is not None:
                    errors.append(
                        "QUANTITATIVE_ABSOLUTE_THRESHOLD_CONTRACT_MISMATCH "
                        f"check_id={check_id} claim_key={claim.get('claim_key')} "
                        f"fixed_cutoff={fixed_cutoff!r}; "
                        "specific-v2 relative time comparisons cannot verify an absolute numeric cutoff; "
                        "regenerate the check preserving the original threshold proposition"
                    )
                    break
        if quantifier == "specific" and state in {"greater_than", "less_than"} and isinstance(scope, dict):
            for proposition in dict.fromkeys((
                str(claim.get("claim_statement") or ""),
                str(check.get("expected_signal") or ""),
            )):
                expected = inconsistent_pairwise_hour_order(
                    proposition, scope.get("comparison_forecast_hours")
                )
                if expected is not None:
                    errors.append(
                        "QUANTITATIVE_SCOPE_COMPARISON_IDENTITY_MISMATCH "
                        f"check_id={check_id} claim_key={claim.get('claim_key')} "
                        f"expected_comparison_forecast_hours={list(expected)} "
                        f"actual={scope.get('comparison_forecast_hours')}; "
                        "repair=regenerate candidate plan without reversing the original hypothesis"
                    )
                    break
    return errors

# Original func/step04/pass1_engine.py:9903
def validate_candidate_plan_response(
    response: dict[str, Any],
    *,
    schema: dict[str, Any],
    final_schema: dict[str, Any],
    package_id: str,
    candidate: dict[str, Any],
    candidate_evidence: dict[str, Any],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    feature_catalog: dict[str, Any],
    operational_region_catalog: dict[str, Any],
    require_system_direct_precipitation_anchor: bool = True,
) -> list[str]:
    """Validate one candidate plan by embedding it in the canonical final contract."""
    errors = _validate_jsonschema(response, schema)
    candidate_id = str(candidate.get("candidate_id"))
    if response.get("package_id") != package_id:
        errors.append("package_id does not match Step04 input package")
    if response.get("candidate_id") != candidate_id:
        errors.append(f"candidate_id must remain immutable: {candidate_id}")
    plan = response.get("candidate_analysis_plan")
    requests = response.get("analysis_data_requests", [])
    if not isinstance(plan, dict):
        return errors + ["candidate_analysis_plan must be an object"]
    if not isinstance(requests, list):
        return errors + ["analysis_data_requests must be an array"]
    errors.extend(_expanded_plan_quantitative_contract_errors(plan))
    errors.extend(_expanded_plan_spatial_support_errors(plan, candidate))

    isolated_candidate = json.loads(json.dumps(candidate, ensure_ascii=False))
    isolated_candidate["candidate_unit_type"] = _candidate_unit_type(isolated_candidate)
    isolated_candidate["discovery_order"] = 1
    isolated_candidate.pop("priority_rank", None)
    isolated_response = {
        "package_id": package_id,
        "analysis_coverage": {
            "coverage_status": "complete",
            "overview_attachment_count": int(attachment_manifest.get("attachment_count", 0)),
        },
        "run_summary": {"candidate_count": 1, "summary_text": "isolated candidate plan validation"},
        "candidates": [isolated_candidate],
        "candidate_evidence": [candidate_evidence],
        "candidate_analysis_plans": [plan],
        "analysis_data_requests": requests,
        "global_uncertainties": [],
        "pass2_required": True,
    }
    errors.extend(
        validate_final_response(
            isolated_response,
            schema=final_schema,
            package_id=package_id,
            attachment_manifest=attachment_manifest,
            support_catalog=support_catalog,
            feature_catalog=feature_catalog,
            operational_region_catalog=operational_region_catalog,
            require_system_direct_precipitation_anchor=require_system_direct_precipitation_anchor,
            validate_discovery_narrative=False,
        )
    )
    return errors

# Original func/step04/pass1_engine.py:10292
def _bbox_contains(outer: dict[str, float], inner: dict[str, float]) -> bool:
    return (
        outer["west"] <= inner["west"] <= inner["east"] <= outer["east"]
        and outer["south"] <= inner["south"] <= inner["north"] <= outer["north"]
    )

# Original func/step04/pass1_engine.py:10299
def _point_in_bbox(longitude: float, latitude: float, bbox: dict[str, float]) -> bool:
    return (
        bbox["west"] <= longitude <= bbox["east"]
        and bbox["south"] <= latitude <= bbox["north"]
    )

# Original func/step04/pass1_engine.py:10306
def _segment_intersects_bbox(
    start_longitude: float,
    start_latitude: float,
    end_longitude: float,
    end_latitude: float,
    bbox: dict[str, float],
) -> bool:
    """Return whether a lon/lat line segment intersects an axis-aligned bbox."""
    dx = end_longitude - start_longitude
    dy = end_latitude - start_latitude
    p = (-dx, dx, -dy, dy)
    q = (
        start_longitude - bbox["west"],
        bbox["east"] - start_longitude,
        start_latitude - bbox["south"],
        bbox["north"] - start_latitude,
    )
    lower, upper = 0.0, 1.0
    for pi, qi in zip(p, q):
        if abs(pi) < 1.0e-12:
            if qi < 0.0:
                return False
            continue
        ratio = qi / pi
        if pi < 0.0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return False
    return True

# Original func/step04/pass1_engine.py:10339
def _normalized_cross_section_line(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    try:
        line = {
            "start_latitude": float(value["start_latitude"]),
            "start_longitude": float(value["start_longitude"]),
            "end_latitude": float(value["end_latitude"]),
            "end_longitude": float(value["end_longitude"]),
            "selection_reason": str(value["selection_reason"]).strip(),
        }
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90.0 <= line["start_latitude"] <= 90.0):
        return None
    if not (-90.0 <= line["end_latitude"] <= 90.0):
        return None
    if not (-180.0 <= line["start_longitude"] <= 180.0):
        return None
    if not (-180.0 <= line["end_longitude"] <= 180.0):
        return None
    if not line["selection_reason"] or len(line["selection_reason"]) > 200:
        return None
    if (
        abs(line["start_latitude"] - line["end_latitude"]) < 1.0e-6
        and abs(line["start_longitude"] - line["end_longitude"]) < 1.0e-6
    ):
        return None
    return line

# Original func/step04/pass1_engine.py:10370
def _cross_section_line_signature(value: Any) -> dict[str, Any] | None:
    line = _normalized_cross_section_line(value)
    if line is None:
        return None
    return {
        "start_latitude": round(float(line["start_latitude"]), 6),
        "start_longitude": round(float(line["start_longitude"]), 6),
        "end_latitude": round(float(line["end_latitude"]), 6),
        "end_longitude": round(float(line["end_longitude"]), 6),
        "selection_reason": re.sub(r"\s+", " ", str(line["selection_reason"])),
    }

# Original func/step04/pass1_engine.py:10385
def _normalized_reference_band_axis(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    try:
        axis = {
            "start_latitude": float(value["start_latitude"]),
            "start_longitude": float(value["start_longitude"]),
            "end_latitude": float(value["end_latitude"]),
            "end_longitude": float(value["end_longitude"]),
        }
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90.0 <= axis["start_latitude"] <= 90.0 and -90.0 <= axis["end_latitude"] <= 90.0):
        return None
    if not (-180.0 <= axis["start_longitude"] <= 180.0 and -180.0 <= axis["end_longitude"] <= 180.0):
        return None
    if abs(axis["start_latitude"] - axis["end_latitude"]) < 1.0e-6 and abs(axis["start_longitude"] - axis["end_longitude"]) < 1.0e-6:
        return None
    return axis

# Original func/step04/pass1_engine.py:10406
def _reference_band_axis_signature(value: Any) -> dict[str, Any] | None:
    axis = _normalized_reference_band_axis(value)
    if axis is None:
        return None
    return {k: round(float(v), 6) for k, v in axis.items()}

# Original func/step04/pass1_engine.py:10413
def _normalized_cross_section_strategy(value: Any) -> str | None:
    if value is None:
        return None
    strategy = str(value).strip()
    allowed = {"across_precipitation_band", "along_precipitation_axis", "along_moisture_inflow"}
    return strategy if strategy in allowed else None

# Original func/step04/pass1_engine.py:10421
def _line_bearing_degrees(start_lat: float, start_lon: float, end_lat: float, end_lon: float) -> float:
    mean_lat = math.radians((start_lat + end_lat) / 2.0)
    dx = (end_lon - start_lon) * math.cos(mean_lat)
    dy = end_lat - start_lat
    angle = math.degrees(math.atan2(dy, dx))
    return angle % 180.0

# Original func/step04/pass1_engine.py:10429
def _line_angle_difference_degrees(first: dict[str, Any], second: dict[str, Any]) -> float:
    a = _line_bearing_degrees(first["start_latitude"], first["start_longitude"], first["end_latitude"], first["end_longitude"])
    b = _line_bearing_degrees(second["start_latitude"], second["start_longitude"], second["end_latitude"], second["end_longitude"])
    diff = abs(a - b) % 180.0
    return min(diff, 180.0 - diff)

# Original func/step04/pass1_engine.py:10436
def _clip_segment_to_bbox(
    start_longitude: float,
    start_latitude: float,
    end_longitude: float,
    end_latitude: float,
    bbox: dict[str, float],
) -> tuple[float, float, float, float] | None:
    """Clip a lon/lat segment to an axis-aligned bbox using Liang-Barsky."""
    dx = end_longitude - start_longitude
    dy = end_latitude - start_latitude
    p = (-dx, dx, -dy, dy)
    q = (
        start_longitude - bbox["west"],
        bbox["east"] - start_longitude,
        start_latitude - bbox["south"],
        bbox["north"] - start_latitude,
    )
    lower, upper = 0.0, 1.0
    for pi, qi in zip(p, q):
        if abs(pi) < 1.0e-12:
            if qi < 0.0:
                return None
            continue
        ratio = qi / pi
        if pi < 0.0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return None
    return (
        start_longitude + lower * dx,
        start_latitude + lower * dy,
        start_longitude + upper * dx,
        start_latitude + upper * dy,
    )

# Original func/step04/pass1_engine.py:10474
def _line_length_km(line: dict[str, Any]) -> float:
    mean_lat = math.radians(
        (float(line["start_latitude"]) + float(line["end_latitude"])) / 2.0
    )
    dx = (
        (float(line["end_longitude"]) - float(line["start_longitude"]))
        * 111.32
        * math.cos(mean_lat)
    )
    dy = (float(line["end_latitude"]) - float(line["start_latitude"])) * 110.57
    return math.hypot(dx, dy)

# Original func/step04/pass1_engine.py:10487
def _expanded_candidate_section_bbox(
    candidate_bbox: dict[str, float],
    selected_bbox: dict[str, float],
) -> dict[str, float] | None:
    lon_span = candidate_bbox["east"] - candidate_bbox["west"]
    lat_span = candidate_bbox["north"] - candidate_bbox["south"]
    lon_margin = min(
        max(lon_span * ACROSS_SECTION_MARGIN_FRACTION, ACROSS_SECTION_MARGIN_MIN_DEGREES),
        ACROSS_SECTION_MARGIN_MAX_DEGREES,
    )
    lat_margin = min(
        max(lat_span * ACROSS_SECTION_MARGIN_FRACTION, ACROSS_SECTION_MARGIN_MIN_DEGREES),
        ACROSS_SECTION_MARGIN_MAX_DEGREES,
    )
    bounded = {
        "west": max(selected_bbox["west"], candidate_bbox["west"] - lon_margin),
        "east": min(selected_bbox["east"], candidate_bbox["east"] + lon_margin),
        "south": max(selected_bbox["south"], candidate_bbox["south"] - lat_margin),
        "north": min(selected_bbox["north"], candidate_bbox["north"] + lat_margin),
    }
    if bounded["west"] >= bounded["east"] or bounded["south"] >= bounded["north"]:
        return None
    return bounded

# Original func/step04/pass1_engine.py:10512
def _canonical_cross_section_line_orientation(
    *,
    line: dict[str, Any],
    strategy: str,
    reference_axis: dict[str, Any],
    selected_bbox: dict[str, float],
    candidate_bbox: dict[str, float],
) -> dict[str, Any] | None:
    """Align and bound an AI-selected vertical section.

    The provider selects the strategy, reference axis, approximate location and
    rationale. Python corrects only contradictory geometry. Across-band cuts are
    additionally limited to the candidate core plus a small margin and to 450 km
    so a broad context domain cannot create a synoptic-scale section.
    """
    if strategy not in {"across_precipitation_band", "along_precipitation_axis"}:
        return line

    original_angle = _line_angle_difference_degrees(line, reference_axis)
    original_length_km = _line_length_km(line)
    angle_is_valid = (
        strategy == "across_precipitation_band"
        and 60.0 <= original_angle <= 120.0
    ) or (strategy == "along_precipitation_axis" and original_angle <= 30.0)
    length_is_valid = not (
        strategy == "across_precipitation_band"
        and original_length_km > ACROSS_SECTION_MAX_LENGTH_KM
    )
    if angle_is_valid and length_is_valid:
        return line

    midpoint_lon = (line["start_longitude"] + line["end_longitude"]) / 2.0
    midpoint_lat = (line["start_latitude"] + line["end_latitude"]) / 2.0
    if _point_in_bbox(midpoint_lon, midpoint_lat, candidate_bbox):
        anchor_lon, anchor_lat = midpoint_lon, midpoint_lat
    else:
        clipped_to_candidate = _clip_segment_to_bbox(
            line["start_longitude"],
            line["start_latitude"],
            line["end_longitude"],
            line["end_latitude"],
            candidate_bbox,
        )
        if clipped_to_candidate is None:
            return None
        c_start_lon, c_start_lat, c_end_lon, c_end_lat = clipped_to_candidate
        anchor_lon = (c_start_lon + c_end_lon) / 2.0
        anchor_lat = (c_start_lat + c_end_lat) / 2.0

    axis_bearing = _line_bearing_degrees(
        reference_axis["start_latitude"],
        reference_axis["start_longitude"],
        reference_axis["end_latitude"],
        reference_axis["end_longitude"],
    )
    target_bearing = (
        (axis_bearing + 90.0) % 180.0
        if strategy == "across_precipitation_band"
        else axis_bearing
    )

    desired_length_km = original_length_km
    effective_bbox = selected_bbox
    if strategy == "across_precipitation_band":
        desired_length_km = min(desired_length_km, ACROSS_SECTION_MAX_LENGTH_KM)
        effective_bbox = _expanded_candidate_section_bbox(candidate_bbox, selected_bbox)
        if effective_bbox is None:
            return None

    target_radians = math.radians(target_bearing)
    unit_x = math.cos(target_radians)
    unit_y = math.sin(target_radians)
    half_length_km = max(desired_length_km / 2.0, 1.0)
    km_per_lon_degree = max(111.32 * abs(math.cos(math.radians(anchor_lat))), 1.0e-6)
    km_per_lat_degree = 110.57
    start_lon = anchor_lon - (half_length_km * unit_x) / km_per_lon_degree
    start_lat = anchor_lat - (half_length_km * unit_y) / km_per_lat_degree
    end_lon = anchor_lon + (half_length_km * unit_x) / km_per_lon_degree
    end_lat = anchor_lat + (half_length_km * unit_y) / km_per_lat_degree
    clipped = _clip_segment_to_bbox(
        start_lon,
        start_lat,
        end_lon,
        end_lat,
        effective_bbox,
    )
    if clipped is None:
        return None
    start_lon, start_lat, end_lon, end_lat = clipped
    canonical = _normalized_cross_section_line(
        {
            "start_latitude": start_lat,
            "start_longitude": start_lon,
            "end_latitude": end_lat,
            "end_longitude": end_lon,
            "selection_reason": line["selection_reason"],
        }
    )
    if canonical is None:
        return None
    if not _segment_intersects_bbox(
        canonical["start_longitude"],
        canonical["start_latitude"],
        canonical["end_longitude"],
        canonical["end_latitude"],
        candidate_bbox,
    ):
        return None
    if (
        strategy == "across_precipitation_band"
        and _line_length_km(canonical) > ACROSS_SECTION_MAX_LENGTH_KM + 1.0
    ):
        return None
    return canonical

# Original func/step04/pass1_engine.py:10627
def _text_mentions_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in keywords)

# Original func/step04/pass1_engine.py:10791
def _vertical_relation_semantic_text(text: str) -> str:
    """Remove only a leading precipitation geometry/time anchor for relation checks."""
    cleaned = str(text or "").strip()
    match = _VERTICAL_PRECIP_CONTEXT_PREFIX.match(cleaned)
    if match:
        cleaned = cleaned[match.end():].lstrip()
    return cleaned

# Original func/step04/pass1_engine.py:10800
def _declared_component_has_spatial_motion_claim(text: str, declared: set[str]) -> bool:
    """Return whether a non-spatial mode asks the declared component itself to move.

    A different meteorological element may still be mentioned as a temporal/context
    anchor (for example, "강수대가 이동한 이후 수렴이 약화한다").  Do not
    mistake that context for a movement claim about the declared component.
    """
    for component in declared:
        for component_pattern in _SIGNAL_TEXT_COMPONENT_PATTERNS.get(component, ()):
            for component_match in component_pattern.finditer(text):
                window_end = min(len(text), component_match.end() + 100)
                window = text[component_match.end():window_end]
                for motion_match in _EXPLICIT_SPATIAL_MOTION.finditer(window):
                    absolute_motion_start = component_match.end() + motion_match.start()
                    absolute_motion_end = component_match.end() + motion_match.end()
                    between = text[component_match.end():absolute_motion_start]
                    # If another supported signal component is named closer to the
                    # motion word, treat that element as the likely context subject.
                    if _mentioned_signal_components(between) - {component}:
                        continue
                    # Synoptic systems may also be temporal anchors rather than the
                    # actual object of this Check.
                    if _SYNOPTIC_MOTION_CONTEXT.search(between):
                        continue
                    after_motion = text[absolute_motion_end:absolute_motion_end + 12]
                    if _SPATIAL_MOTION_CONTEXT_SUFFIX.search(after_motion):
                        continue
                    return True
    return False

# Original func/step04/pass1_engine.py:10832
def _validate_ai_verification_request_intent(row: dict[str, Any]) -> None:
    """Validate only meteorological intent fields authored by the Step04 AI.

    Other variables may appear in expected_signal as temporal/context anchors
    (for example, '강수 최대 시각 이전의 하층 수렴 강화'). Such references
    are not treated as additional evidence requirements. A hard failure is
    raised only when the narrative explicitly asks for a cross-variable
    relationship involving a component that signal_components did not declare.
    """
    check_key = str(row.get("check_key") or "")
    declared = {
        str(value) for value in row.get("signal_components", []) if str(value)
    }
    text = str(row.get("expected_signal") or "").strip()
    if _EXPECTED_SIGNAL_PROCEDURAL_ENDING.search(text):
        raise ValueError(
            "EXPECTED_SIGNAL_PROCEDURAL_TEXT_INVALID "
            f"check_key={check_key} expected_signal={text!r}; "
            "repair=expected_signal must be a testable meteorological state or change, not an instruction such as 확인한다/비교한다/판단한다"
        )
    if _EXPECTED_SIGNAL_VAGUE_COMPARISON.search(text) or (
        _EXPECTED_SIGNAL_VAGUE_CHANGE_ENDING.search(text)
        and not _EXPECTED_SIGNAL_CONCRETE_CHANGE.search(text)
    ):
        raise ValueError(
            "EXPECTED_SIGNAL_VAGUE_COMPARISON_INVALID "
            f"check_key={check_key} expected_signal={text!r}; "
            "repair=state a directional or concrete meteorological proposition, such as which time/area is stronger or weaker, where the center is located, or whether the fields overlap; do not use generic claims such as 서로 다르다/정량적으로 구분된다/변화한다"
        )
    if _EXPECTED_SIGNAL_OPPOSING_ALTERNATIVE.search(text):
        raise ValueError(
            "EXPECTED_SIGNAL_AMBIGUOUS_ALTERNATIVE_INVALID "
            f"check_key={check_key} expected_signal={text!r}; "
            "repair=expected_signal must state one falsifiable expected state; do not join mutually opposing outcomes with 또는/혹은"
        )

    mode = str(row.get("verification_mode") or "")
    spatial_relation_scope = str(row.get("spatial_relation_scope") or "not_applicable")
    domain_scope = str(row.get("domain_scope") or "candidate")
    if spatial_relation_scope not in SPATIAL_RELATION_SCOPES:
        raise ValueError(
            "SPATIAL_RELATION_SCOPE_INVALID "
            f"check_key={check_key} spatial_relation_scope={spatial_relation_scope!r}"
        )
    # `spatial_relation_scope` describes the horizontal relation that the Check must
    # prove, not how wide the rendering domain is.  A vertical section may use a
    # context domain so that its endpoints extend beyond the candidate bbox, but
    # the Check is still a vertical-structure test and therefore has no horizontal
    # spatial-relation claim.  Requiring `not_applicable` here prevents the model
    # from conflating "section uses outside context" with "outside placement or
    # boundary crossing is itself the conclusion".
    if mode in {"vertical_structure", "vertical_time_evolution"} and spatial_relation_scope != "not_applicable":
        raise ValueError(
            "SPATIAL_RELATION_VERTICAL_MODE_SCOPE_MISMATCH "
            f"check_key={check_key} signal_components={sorted(declared)} "
            f"spatial_relation_scope={spatial_relation_scope} domain_scope={domain_scope} "
            f"verification_mode={mode} expected_signal={text[:500]!r}; "
            "repair=vertical_structure/vertical_time_evolution must use spatial_relation_scope=not_applicable. "
            "A vertical_structure Check may still use domain_scope=context when the section line needs a wider domain. "
            "If the intended conclusion is an outside/cross-boundary horizontal relationship, create a separate "
            "spatial_distribution/spatial_evolution/multivariable_alignment request for that relationship instead of "
            "encoding it in the vertical Check"
        )

    relation_text = (
        _vertical_relation_semantic_text(text)
        if mode in {"vertical_structure", "vertical_time_evolution"}
        else text
    )
    mentioned = _mentioned_signal_components(relation_text)
    if (
        mode in {"vertical_structure", "vertical_time_evolution"}
        and "direct_precipitation" in mentioned
        and _EXPLICIT_MULTIVARIABLE_RELATION_WORDS.search(relation_text)
    ):
        raise ValueError(
            "VERIFICATION_VERTICAL_SECTION_PRECIPITATION_CONTEXT_MISMATCH "
            f"check_key={check_key} verification_mode={mode} "
            f"declared_signal_components={sorted(declared)} "
            f"narrative_relation_components={sorted(mentioned)} "
            f"expected_signal={text[:500]!r}; "
            "repair=vertical section/time-height evidence does not directly contain precipitation. "
            "A precipitation band/center/peak may be used only as a clearly subordinate geometry or time anchor. "
            "Do not make precipitation itself a participant in an overlap/alignment conclusion. Rewrite the proposition "
            "around the declared vertical moisture_supply, mid_upper_forcing, and/or organization_persistence components. "
            "If precipitation overlap itself is decision-relevant, create a separate horizontal multivariable_alignment request"
        )

    if spatial_relation_scope in {"candidate_boundary_crossing", "candidate_external_context"}:
        if domain_scope != "context":
            raise ValueError(
                "SPATIAL_RELATION_DOMAIN_SCOPE_MISMATCH "
                f"check_key={check_key} spatial_relation_scope={spatial_relation_scope} "
                f"domain_scope={domain_scope}; repair=use domain_scope=context because this "
                "verification requires evidence outside the candidate bbox or across its boundary"
            )
        if mode not in {"spatial_distribution", "spatial_evolution", "multivariable_alignment"}:
            raise ValueError(
                "SPATIAL_RELATION_VERIFICATION_MODE_MISMATCH "
                f"check_key={check_key} signal_components={sorted(declared)} "
                f"spatial_relation_scope={spatial_relation_scope} domain_scope={domain_scope} "
                f"verification_mode={mode} expected_signal={text[:500]!r}; "
                "repair=outside/cross-boundary horizontal placement must be verified with a spatial map mode. "
                "If the original purpose is vertical structure rather than horizontal placement, keep the vertical mode "
                "but use spatial_relation_scope=not_applicable; split horizontal and vertical purposes into separate "
                "verification requests when both are needed"
            )

    if mode != "vertical_time_evolution" and _CANDIDATE_AGGREGATE_MEAN.search(text):
        raise ValueError(
            "EXPECTED_SIGNAL_CANDIDATE_AGGREGATE_UNAVAILABLE "
            f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
            "repair=horizontal Step05 quantitative evidence is provided per overlapping operational region, not as one aggregate mean over the full candidate bbox; use operational-region mean/max/ranking wording or request a spatial map instead"
        )

    if mode in {"regional_intensity", "temporal_change", "precipitation_amount"} and _SPATIAL_EXTENT_WORDS.search(text):
        raise ValueError(
            "EXPECTED_SIGNAL_SPATIAL_EXTENT_MODE_MISMATCH "
            f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
            "repair=regional/scalar quantitative modes may test mean/max/intensity but not area/width/expansion/contraction; move spatial extent to spatial_distribution or spatial_evolution and keep scalar intensity separate"
        )

    if mode == "multivariable_alignment" and "moisture_supply" in declared:
        if _MULTIVARIABLE_MOISTURE_FLUX_WORDS.search(text):
            raise ValueError(
                "VERIFICATION_MULTIVARIABLE_MOISTURE_SEMANTICS_MISMATCH "
                f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
                "repair=multivariable_composite represents moisture with total-column water (TCW) shading and real 850-hPa wind arrows, not the standalone 850-hPa moisture-transport magnitude field. "
                "Describe the moisture overlap as a high total-column-water/high-moisture area (for example, 대기 전층 수분량(TCW)이 상대적으로 큰 영역) and treat 850-hPa wind only as supplementary flow context; do not ask this check to verify moisture-transport magnitude/vector/inflow-axis alignment"
            )

    if mode in {"spatial_distribution", "regional_intensity", "temporal_change", "spatial_evolution"} and declared == {"moisture_supply"}:
        if _TOTAL_COLUMN_WATER_WORDS.search(text):
            raise ValueError(
                "VERIFICATION_STANDALONE_MOISTURE_SEMANTICS_MISMATCH "
                f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
                "repair=standalone horizontal moisture_supply is executed with QL_MOISTURE_FLUX850_WIND850, whose shading is 850-hPa moisture-transport magnitude and whose arrows are real 850-hPa wind. "
                "Do not ask this check to verify total-column water/TCW. For spatial modes describe moisture-transport magnitude/axis and, if needed, 850-hPa wind direction separately; for regional_intensity/temporal_change use moisture-transport magnitude statistics only"
            )

    if mode in {"vertical_structure", "vertical_time_evolution"} and "moisture_supply" in declared:
        if _TOTAL_COLUMN_WATER_WORDS.search(text) or _VERTICAL_SECTION_HORIZONTAL_MOISTURE_WORDS.search(text):
            raise ValueError(
                "VERIFICATION_VERTICAL_SECTION_MOISTURE_SEMANTICS_MISMATCH "
                f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
                "repair=vertical section/time-height moisture evidence is specific humidity/high-moisture-layer structure, not TCW and not horizontal 850-hPa moisture-transport magnitude. Rewrite the moisture part as specific-humidity/high-moisture-layer vertical structure"
            )

    if mode in {"vertical_structure", "vertical_time_evolution"} and "organization_persistence" in declared:
        if _VERTICAL_SHEAR_INTENT_WORDS.search(text):
            raise ValueError(
                "VERIFICATION_VERTICAL_SECTION_SHEAR_SEMANTICS_MISMATCH "
                f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
                "repair=vertical section/time-height products provide wind speed and along-section wind structure, not the dedicated 850-500 hPa vertical-wind-shear diagnostic. "
                "Remove the shear claim from this section request and create a separate signal_components=[organization_persistence] spatial_distribution/spatial_evolution request when 850-500 hPa shear is decision-relevant"
            )

    if declared == {"moisture_supply"}:
        if _MOISTURE_VECTOR_MISLABEL.search(text) or (
            _MOISTURE_DIRECTION_WITHOUT_WIND.search(text) and not _850_WIND_EXPLICIT.search(text)
        ):
            raise ValueError(
                "VERIFICATION_MOISTURE_VECTOR_SEMANTICS_MISMATCH "
                f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
                "repair=the horizontal moisture-supply panel shades 850-hPa moisture-transport magnitude while arrows are real 850-hPa wind; do not call the arrows moisture-transport vectors/flow/direction. State moisture-transport magnitude/axis and 850-hPa wind direction separately"
            )
        if mode == "temporal_change" and _850_WIND_EXPLICIT.search(text):
            raise ValueError(
                "VERIFICATION_MOISTURE_VECTOR_SEMANTICS_MISMATCH "
                f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
                "repair=temporal_change for moisture_supply is backed by operational-region moisture-transport magnitude time series and cannot establish 850-hPa wind direction; use spatial_distribution/spatial_evolution for wind-direction context"
            )

    if mode in {
        "spatial_distribution",
        "regional_intensity",
        "temporal_change",
        "precipitation_amount",
    } and _declared_component_has_spatial_motion_claim(text, declared):
        raise ValueError(
            "EXPECTED_SIGNAL_SPATIAL_EVOLUTION_MODE_MISMATCH "
            f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
            "repair=movement/reorganization/expansion/contraction of the declared meteorological component requires a separate verification_request with verification_mode=spatial_evolution; keep scalar mean/intensity/amount checks separate"
        )

    undeclared = sorted(mentioned - declared)
    if undeclared and _EXPLICIT_MULTIVARIABLE_RELATION_WORDS.search(relation_text):
        raise ValueError(
            "VERIFICATION_MULTIVARIABLE_INTENT_MISMATCH "
            f"check_key={check_key} verification_mode={str(row.get('verification_mode') or '<missing>')} "
            f"declared_signal_components={sorted(declared)} "
            f"narrative_relation_components={sorted(mentioned)} "
            f"undeclared_relation_components={undeclared} "
            f"expected_signal={text[:500]!r}; "
            "repair=if another variable is only a time/context anchor, rewrite expected_signal without an overlap/alignment claim; "
            "if the cross-variable spatial relationship is the actual target, declare all compared signal_components and use verification_mode=multivariable_alignment"
        )

    # For horizontal organization_persistence checks, Python deterministically
    # maps the request to 850-500 hPa vertical wind shear.  A shear vector is
    # V500-V850, not the actual 850-hPa inflow wind.  Prevent the AI from using
    # this component to ask about a low-level inflow axis or flow direction,
    # which would otherwise invite Step07 to over-interpret shear arrows as
    # real wind vectors.  Low-level moisture inflow belongs to moisture_supply.
    if declared == {"organization_persistence"} and mode in {"spatial_distribution", "spatial_evolution"}:
        if _LOW_LEVEL_INFLOW_INTENT_WORDS.search(text) or not _VERTICAL_SHEAR_INTENT_WORDS.search(text):
            raise ValueError(
                "VERIFICATION_WIND_STRUCTURE_SEMANTICS_MISMATCH "
                f"check_key={check_key} verification_mode={mode} expected_signal={text!r}; "
                "repair=horizontal organization_persistence is executed as 850-500 hPa vertical wind shear, so expected_signal must ask only about shear magnitude/direction/spatial structure; "
                "if the actual target is the low-level inflow axis, 850-hPa flow direction, or moisture transport, use signal_components=[moisture_supply] instead"
            )

# Original func/step04/pass1_engine.py:11057
def _mentioned_signal_components(text: str) -> set[str]:
    return {
        component
        for component, patterns in _SIGNAL_TEXT_COMPONENT_PATTERNS.items()
        if any(pattern.search(text) for pattern in patterns)
    }

# Original func/step04/pass1_engine.py:11065
def _validate_same_check_narrative_contract(check: dict[str, Any]) -> None:
    """Do not derive execution scope from free-form narrative text.

    Step04 now gives the AI explicit structured intent fields such as
    ``signal_components`` and ``verification_mode``. Python deterministically
    translates those fields into ``diagnostic_id``, ``check_scope`` and
    ``analysis_methods``. The derived execution contract therefore remains the
    authoritative source for evidence shape.

    ``expected_signal`` may legitimately contain temporal/context anchors such
    as "강수 최대 시각 이전" or descriptive words such as "강화" even when the
    requested evidence itself is a single-time spatial field. Re-inferring scope
    from those words creates false-positive validation failures and reverses the
    intended AI/Python responsibility boundary.

    Cross-variable intent is validated earlier on the AI-authored
    verification_request by ``_validate_ai_verification_request_intent``.
    Internal execution invariants are validated separately by the deterministic
    scope/execution-contract validators.
    """
    return None

# Original func/step04/pass1_engine.py:11088
def _enforce_component_coverage_for_signal_text(checks: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    normalized_checks = [item for item in checks if isinstance(item, dict)]
    for check in normalized_checks:
        phases = {str(value) for value in check.get("phase_keys", [])}
        if not phases:
            continue
        scope = str(check.get("domain_scope", ""))
        text = " ".join(str(check.get(field, "")) for field in ("purpose", "expected_signal", "decision_relevance"))
        needs_convergence = _text_mentions_any(text, ("925", "수렴", "convergence"))
        needs_midlevel = _text_mentions_any(text, ("700", "오메가", "omega", "상승", "ascent"))
        def same_phase_scope(other: dict[str, Any]) -> bool:
            return (
                str(other.get("domain_scope", "")) == scope
                and bool(phases & {str(v) for v in other.get("phase_keys", [])})
            )

        def covers_convergence(other: dict[str, Any]) -> bool:
            diagnostic_id = str(other.get("diagnostic_id"))
            return same_phase_scope(other) and diagnostic_id in {
                "horizontal_convergence",
                "multivariable_composite",
            }

        def covers_midlevel_ascent(other: dict[str, Any]) -> bool:
            diagnostic_id = str(other.get("diagnostic_id"))
            if not same_phase_scope(other):
                return False
            if diagnostic_id == "multivariable_composite":
                return True
            if (
                diagnostic_id == "detailed_spatial_map"
                and str(other.get("variable_role")) == "midlevel_forcing"
            ):
                return True
            # The dedicated section diagnostics read AIFS pressure-level w and
            # produce omega fields through 700 hPa.  Therefore a section check
            # that mentions 700-hPa ascent is itself direct component evidence
            # on the same phase/scope; requiring an additional 2-D omega map
            # would duplicate the requested evidence.  Horizontal placement can
            # still be requested separately when it is decision-relevant.
            return diagnostic_id in {
                "vertical_cross_section",
                "time_height_section",
            }

        if needs_convergence and not any(covers_convergence(other) for other in normalized_checks):
            errors.append(
                f"check_key={check.get('check_key')} mentions 925 hPa convergence but lacks "
                "horizontal_convergence or multivariable_composite coverage on the same event phase"
            )
        if needs_midlevel and not any(covers_midlevel_ascent(other) for other in normalized_checks):
            errors.append(
                f"check_key={check.get('check_key')} mentions 700 hPa ascent/omega but lacks "
                "direct midlevel-ascent coverage on the same event phase "
                "(detailed_spatial_map midlevel_forcing, multivariable_composite, "
                "vertical_cross_section, or time_height_section)"
            )
    return errors

# Original func/step04/pass1_engine.py:11364
def _compact_local_keys(rows: list[dict[str, Any]], field: str, *, label: str) -> set[str]:
    keys = [str(item.get(field, "")).strip() for item in rows]
    if any(not key for key in keys) or len(keys) != len(set(keys)):
        raise ValueError(f"compact candidate plan {label} has missing or duplicate {field}")
    return set(keys)

# Original func/step04/pass1_engine.py:11406
def _compact_phase_signature(row: dict[str, Any]) -> tuple[str, tuple[int, ...]]:
    return str(row.get("phase_type", "")).strip(), _compact_phase_hours(row)

# Original func/step04/pass1_engine.py:11410
def _contiguous_hour_groups(hours: Iterable[int], *, maximum_gap_hours: int = 12) -> list[list[int]]:
    values = sorted({int(value) for value in hours})
    if not values:
        return []
    groups: list[list[int]] = [[values[0]]]
    for value in values[1:]:
        if value - groups[-1][-1] > maximum_gap_hours:
            groups.append([value])
        else:
            groups[-1].append(value)
    return groups

# Original func/step04/pass1_engine.py:11423
def _forecast_cadence_hours(available_hours: Iterable[int]) -> int:
    values = sorted({int(value) for value in available_hours})
    differences = [right - left for left, right in zip(values, values[1:]) if right > left]
    return min(differences) if differences else 6

# Original func/step04/pass1_engine.py:11429
def _canonical_phase_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove exact phase aliases while preserving the first provider order."""
    retained: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[int, ...]]] = set()
    for row in rows:
        signature = _compact_phase_signature(row)
        if signature in seen:
            continue
        seen.add(signature)
        retained.append(row)
    return retained

# Original func/step04/pass1_engine.py:11442
def _phase_timeline_advisories(
    rows: Iterable[dict[str, Any]],
    *,
    available_hours: Iterable[int],
) -> list[str]:
    """Return non-blocking notes about AI phase segmentation choices."""
    notes: list[str] = []
    cadence = _forecast_cadence_hours(available_hours)
    phases = _canonical_phase_rows(list(rows))
    event_phases: list[tuple[int, int, str, tuple[int, ...], str]] = []
    for row in phases:
        phase_type = str(row.get("phase_type", "")).strip()
        phase_key = str(row.get("phase_key") or row.get("phase_id") or "<unknown>")
        hours = _compact_phase_hours(row)
        if phase_type == "context" or not hours:
            continue
        groups = _contiguous_hour_groups(hours, maximum_gap_hours=cadence)
        if len(groups) > 1:
            notes.append(
                "AI analysis phase contains separated forecast-hour groups "
                f"phase={phase_key} phase_type={phase_type!r} groups={groups}"
            )
        event_phases.append((min(hours), max(hours), phase_type, hours, phase_key))

    event_phases.sort(key=lambda item: (item[0], item[1], item[4]))
    for previous, current in zip(event_phases, event_phases[1:]):
        prev_start, prev_end, _prev_type, _prev_hours, prev_key = previous
        curr_start, curr_end, _curr_type, _curr_hours, curr_key = current
        if curr_start <= prev_end:
            notes.append(
                "AI non-context analysis phases overlap/interleave "
                f"phases={[prev_key, curr_key]} intervals={[(prev_start, prev_end), (curr_start, curr_end)]}"
            )
    return list(dict.fromkeys(notes))

# Original func/step04/pass1_engine.py:11478
def _validate_phase_timeline(
    rows: Iterable[dict[str, Any]],
    *,
    available_hours: Iterable[int],
    candidate_peak_hours: Iterable[int] = (),
) -> list[str]:
    """Validate only executable phase-timeline contract.

    Phase segmentation, overlap, and whether separated peaks share one phase are
    AI planning judgments. Python only hard-fails when an AI-declared candidate
    peak is omitted from every peak phase.
    """
    errors: list[str] = []
    phases = _canonical_phase_rows(list(rows))
    peak_rows: list[tuple[set[int], str]] = []
    for row in phases:
        phase_type = str(row.get("phase_type", "")).strip()
        phase_key = str(row.get("phase_key") or row.get("phase_id") or "<unknown>")
        hours = _compact_phase_hours(row)
        if phase_type == "peak":
            peak_rows.append((set(hours), phase_key))

    candidate_peak_set = {int(value) for value in candidate_peak_hours}
    covered_peak_hours = {hour for hours, _key in peak_rows for hour in hours}
    missing_peak_hours = sorted(candidate_peak_set - covered_peak_hours)
    if missing_peak_hours:
        errors.append(
            "peak phases must cover every AI-declared candidate peak hour "
            f"missing={missing_peak_hours}"
        )
    return errors

# Original func/step04/pass1_engine.py:11510
def _mentioned_forecast_hours(text: str, available_hours: Iterable[int]) -> set[int]:
    """Extract explicit F-hour mentions, expanding ranges on the available schedule."""
    available = sorted({int(value) for value in available_hours})
    mentioned: set[int] = set()
    pattern = re.compile(r"F\s*(\d{1,3})(?:\s*[-~–—]\s*F?\s*(\d{1,3}))?", re.IGNORECASE)
    for match in pattern.finditer(str(text)):
        start = int(match.group(1))
        end_text = match.group(2)
        if end_text is None:
            mentioned.add(start)
            continue
        end = int(end_text)
        lower, upper = sorted((start, end))
        covered = [hour for hour in available if lower <= hour <= upper]
        mentioned.update(covered or {start, end})
    return mentioned

# Original func/step04/pass1_engine.py:11528
def _unique_existing_phase_key_for_hour(
    hour: int,
    *,
    all_phase_rows: Iterable[dict[str, Any]],
) -> str | None:
    """Return the unique existing phase that owns ``hour``.

    Non-context event phases take precedence over context phases. Exact phase
    restatements across continuation chunks are treated as one semantic phase.
    Ambiguous or uncovered hours return ``None``; this helper never invents a
    new phase or forecast hour.
    """
    event_keys: list[str] = []
    context_keys: list[str] = []
    seen_signatures: set[tuple[str, tuple[int, ...]]] = set()
    for row in all_phase_rows:
        phase_key = str(row.get("phase_key", "")).strip()
        if not phase_key or int(hour) not in set(_compact_phase_hours(row)):
            continue
        signature = _compact_phase_signature(row)
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        if str(row.get("phase_type", "")).strip() == "context":
            context_keys.append(phase_key)
        else:
            event_keys.append(phase_key)
    candidates = event_keys or context_keys
    return candidates[0] if len(candidates) == 1 else None

# Original func/step04/pass1_engine.py:11559
def _canonicalize_check_phase_keys_for_explicit_hours(
    check: dict[str, Any],
    *,
    all_phase_rows: Iterable[dict[str, Any]],
    available_hours: Iterable[int],
    candidate_id: str,
) -> list[int]:
    """Attach existing phases needed by explicit F-hour mentions in check text.

    The provider can legitimately describe a comparison such as ``F006 대비
    F012 이후 약화`` while referencing only the post-peak phase.  The text then
    requests evidence at F006, but the executable check would omit it.  When the
    mentioned hour already belongs to a uniquely resolved analysis phase, add
    that phase key deterministically instead of rejecting the complete plan.

    This function never invents a phase or forecast hour.  Hours not represented
    by any analysis phase are returned so the caller can keep the semantic error.
    Non-context event phases take precedence over an overlapping context phase.
    """
    mentioned_hours: set[int] = set()
    for field in ("purpose", "expected_signal", "decision_relevance"):
        mentioned_hours.update(
            _mentioned_forecast_hours(str(check.get(field, "")), available_hours)
        )
    if not mentioned_hours:
        return []

    phase_rows = list(all_phase_rows)
    phase_hours_by_key = {
        str(row.get("phase_key", "")).strip(): set(_compact_phase_hours(row))
        for row in phase_rows
        if str(row.get("phase_key", "")).strip()
    }
    original_keys = [
        str(item).strip()
        for item in check.get("phase_keys", [])
        if str(item).strip()
    ]
    referenced_hours = {
        hour
        for phase_key in original_keys
        for hour in phase_hours_by_key.get(phase_key, set())
    }
    missing_hours = sorted(mentioned_hours - referenced_hours)
    if not missing_hours:
        return []

    additions: list[str] = []
    unresolved: list[int] = []
    for hour in missing_hours:
        phase_key = _unique_existing_phase_key_for_hour(
            hour,
            all_phase_rows=phase_rows,
        )
        if phase_key is None:
            unresolved.append(hour)
            continue
        if phase_key not in original_keys and phase_key not in additions:
            additions.append(phase_key)

    if additions:
        check["phase_keys"] = original_keys + additions
        logging.warning(
            "step04 candidate plan phase references canonicalized from explicit forecast-hour text "
            "candidate_id=%s check_key=%s added_phase_keys=%s mentioned_hours=%s",
            candidate_id,
            check.get("check_key"),
            additions,
            sorted(mentioned_hours),
        )
    return unresolved

# Original func/step04/pass1_engine.py:11632
def _canonicalize_quantitative_scope_phase_keys(
    check: dict[str, Any],
    *,
    all_phase_rows: Iterable[dict[str, Any]],
    candidate_id: str,
) -> list[int]:
    """Align quantitative forecast hours to already-declared phase references.

    The model owns the meteorological choice of forecast hours and phase
    definitions. Python may only connect an already-declared, uniquely resolved
    phase when a quantitative scope explicitly requires one of its hours. Both
    the verification request and the atomic claim receive the same added phase
    reference. Missing or ambiguous hours are returned so the validator can
    escalate to whole candidate-plan chunk regeneration.
    """
    claims = check.get("atomic_claims")
    if not isinstance(claims, list) or not claims:
        return []

    phase_rows = list(all_phase_rows)
    phase_hours_by_key = {
        str(row.get("phase_key", "")).strip(): set(_compact_phase_hours(row))
        for row in phase_rows
        if str(row.get("phase_key", "")).strip()
    }
    check_keys = [
        str(item).strip()
        for item in check.get("phase_keys", [])
        if str(item).strip()
    ]
    unresolved: set[int] = set()
    advisory_rows: list[str] = []

    for claim in claims:
        if not isinstance(claim, dict):
            continue
        scope = claim.get("quantitative_scope")
        if not isinstance(scope, dict):
            continue
        raw_hours = scope.get("forecast_hours")
        if (
            not isinstance(raw_hours, list)
            or any(isinstance(hour, bool) or not isinstance(hour, int) or hour < 0 for hour in raw_hours)
        ):
            continue
        quantitative_hours = set(int(hour) for hour in raw_hours)
        if not quantitative_hours:
            continue

        claim_keys = [
            str(item).strip()
            for item in claim.get("phase_keys", [])
            if str(item).strip()
        ]
        check_hours = {
            hour
            for phase_key in check_keys
            for hour in phase_hours_by_key.get(phase_key, set())
        }
        claim_hours = {
            hour
            for phase_key in claim_keys
            for hour in phase_hours_by_key.get(phase_key, set())
        }
        missing_check_hours = quantitative_hours - check_hours
        missing_claim_hours = quantitative_hours - claim_hours
        additions_check: list[str] = []
        additions_claim: list[str] = []

        for hour in sorted(missing_check_hours | missing_claim_hours):
            phase_key = _unique_existing_phase_key_for_hour(
                hour,
                all_phase_rows=phase_rows,
            )
            if phase_key is None:
                unresolved.add(hour)
                continue
            if hour in missing_check_hours and phase_key not in check_keys and phase_key not in additions_check:
                additions_check.append(phase_key)
            if hour in missing_claim_hours and phase_key not in claim_keys and phase_key not in additions_claim:
                additions_claim.append(phase_key)

        if additions_check:
            check_keys.extend(additions_check)
            check["phase_keys"] = list(dict.fromkeys(check_keys))
        if additions_claim:
            claim_keys.extend(additions_claim)
            claim["phase_keys"] = list(dict.fromkeys(claim_keys))
        if additions_check or additions_claim:
            advisory_rows.append(
                f"check_key={check.get('check_key')!r} claim_key={claim.get('claim_key')!r} "
                f"quantitative_hours={sorted(quantitative_hours)} "
                f"added_check_phase_keys={additions_check} added_claim_phase_keys={additions_claim}"
            )

    if advisory_rows:
        _log_step04_advisories(
            "candidate_plan.quantitative_phase_alignment",
            advisory_rows,
        )
        logging.warning(
            "step04 candidate plan quantitative phase references canonicalized "
            "candidate_id=%s check_key=%s",
            candidate_id,
            check.get("check_key"),
        )
    return sorted(unresolved)

# Original func/step04/pass1_engine.py:11785
def _canonicalize_redundant_temporal_evolution_checks(
    checks: list[dict[str, Any]],
    hypotheses: list[dict[str, Any]],
    *,
    phase_hours_by_key: dict[str, set[int]],
    candidate_id: str,
) -> None:
    """Resolve harmless duplicate time-series intent without failing Step04.

    A dedicated ``diagnostic_id=temporal_evolution`` check and a component
    diagnostic carrying ``analysis_methods=[..., temporal_evolution]`` can
    describe the same executable time series. Keep exactly one time-series
    producer while preserving all non-temporal purposes and hypothesis links.
    """

    def referenced_hours(check: dict[str, Any]) -> set[int]:
        return {
            hour
            for phase_key in check.get("phase_keys", [])
            for hour in phase_hours_by_key.get(str(phase_key), set())
        }

    aliases: dict[str, str] = {}
    remove_keys: set[str] = set()
    dedicated_checks = [
        check
        for check in checks
        if str(check.get("diagnostic_id")) == "temporal_evolution"
    ]
    for dedicated in dedicated_checks:
        dedicated_key = str(dedicated.get("check_key", ""))
        dedicated_hours = referenced_hours(dedicated)
        redundant_others: list[dict[str, Any]] = []
        for other in checks:
            if other is dedicated or str(other.get("diagnostic_id")) == "temporal_evolution":
                continue
            if str(other.get("variable_role")) != str(dedicated.get("variable_role")):
                continue
            if str(other.get("evidence_role") or "context") != str(dedicated.get("evidence_role") or "context"):
                continue
            if not _compact_checks_have_compatible_atomic_claims(
                dedicated,
                other,
                phase_hours_by_key=phase_hours_by_key,
            ):
                continue
            if str(other.get("domain_scope")) != str(dedicated.get("domain_scope")):
                continue
            methods = [str(item) for item in other.get("analysis_methods", [])]
            if "temporal_evolution" not in methods:
                continue
            other_hours = referenced_hours(other)
            if dedicated_hours and dedicated_hours.issubset(other_hours):
                redundant_others.append(other)

        if not redundant_others:
            continue

        time_series_only = next(
            (
                other
                for other in redundant_others
                if set(str(item) for item in other.get("analysis_methods", []))
                == {"temporal_evolution"}
            ),
            None,
        )
        if time_series_only is not None:
            replacement_key = str(time_series_only.get("check_key", ""))
            aliases[dedicated_key] = replacement_key
            remove_keys.add(dedicated_key)
            logging.info(
                "step04 redundant temporal diagnostic canonicalized "
                "candidate_id=%s removed_check_key=%s retained_check_key=%s mode=retain_component_time_series",
                candidate_id,
                dedicated_key,
                replacement_key,
            )
            continue

        for other in redundant_others:
            original_methods = list(
                dict.fromkeys(str(item) for item in other.get("analysis_methods", []))
            )
            canonical_methods = [
                item for item in original_methods if item != "temporal_evolution"
            ]
            if canonical_methods:
                other["analysis_methods"] = canonical_methods
                logging.info(
                    "step04 redundant temporal method canonicalized "
                    "candidate_id=%s dedicated_check_key=%s component_check_key=%s "
                    "original=%s canonical=%s",
                    candidate_id,
                    dedicated_key,
                    other.get("check_key"),
                    original_methods,
                    canonical_methods,
                )

    if not remove_keys:
        return

    checks[:] = [
        check for check in checks if str(check.get("check_key", "")) not in remove_keys
    ]
    for hypothesis in hypotheses:
        for field in ("supporting_check_keys", "opposing_check_keys"):
            hypothesis[field] = list(
                dict.fromkeys(
                    aliases.get(str(key), str(key))
                    for key in hypothesis.get(field, [])
                )
            )

# Original func/step04/pass1_engine.py:11901
def _compact_check_fingerprint(
    row: dict[str, Any],
    phase_aliases: dict[str, str],
) -> str:
    """Return a stable semantic fingerprint for one compact diagnostic check.

    Local identifiers are excluded. Continuation phase aliases are canonicalized so
    an exact restatement can be safely reused instead of causing a provider failure.
    """
    normalized = {
        "diagnostic_id": str(row.get("diagnostic_id", "")),
        "variable_role": str(row.get("variable_role", "")),
        "domain_scope": str(row.get("domain_scope", "")),
        "phase_keys": sorted(
            {phase_aliases.get(str(key), str(key)) for key in row.get("phase_keys", [])}
        ),
        "representative_spatial_hours": sorted({
            int(value)
            for value in row.get("representative_spatial_hours", [])
            if isinstance(value, int) and not isinstance(value, bool)
        }),
        "analysis_methods": sorted({str(item) for item in row.get("analysis_methods", [])}),
        "expected_signal": re.sub(r"\s+", " ", str(row.get("expected_signal", "")).strip()),
        "decision_relevance": re.sub(r"\s+", " ", str(row.get("decision_relevance", "")).strip()),
        "priority": str(row.get("priority", "")),
        "evidence_role": str(row.get("evidence_role", "")),
        "coverage_roles": sorted(_effective_compact_check_coverage_roles(row)),
        "purpose": re.sub(r"\s+", " ", str(row.get("purpose", "")).strip()),
        "cross_section_line": _cross_section_line_signature(row.get("cross_section_line")),
        "cross_section_strategy": _normalized_cross_section_strategy(row.get("cross_section_strategy")),
        "reference_band_axis": _reference_band_axis_signature(row.get("reference_band_axis")),
    }
    return canonical_hash(normalized)

# Original func/step04/pass1_engine.py:11936
def _canonicalize_compact_continuation_domain_fields(
    chunk: dict[str, Any],
    previous_chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Use the first compact chunk as the canonical context-domain contract.

    Continuation calls often omit ``context_domain_reason`` or paraphrase it even
    though the executable bbox is unchanged.  The reason is descriptive metadata,
    not a new planning decision.  Preserve the first chunk's bbox/reason
    deterministically so a harmless continuation wording difference does not fail
    the complete candidate plan.  A genuinely different non-null bbox is left
    untouched and is still rejected by semantic validation.
    """
    if not previous_chunks:
        return chunk

    value = copy.deepcopy(chunk)
    first = previous_chunks[0]
    canonical_context = _normalized_bbox_for_validation(first.get("context_domain"))
    canonical_reason_raw = first.get("context_domain_reason")
    canonical_reason = (
        str(canonical_reason_raw).strip() if canonical_reason_raw is not None else None
    )
    current_context = _normalized_bbox_for_validation(value.get("context_domain"))

    if canonical_context is None:
        # The first chunk established that no context domain is used.  Ignore only
        # stray reason text on a null continuation; a newly introduced bbox remains
        # visible so the existing invariant check rejects it.
        if current_context is None:
            value["context_domain"] = None
            value["context_domain_reason"] = None
        return value

    if current_context is None:
        # Missing/null continuation metadata means "reuse the established domain".
        value["context_domain"] = copy.deepcopy(canonical_context)
    elif current_context != canonical_context:
        # Do not silently rewrite a real spatial change.
        return value

    value["context_domain_reason"] = canonical_reason
    return value

# Original func/step04/pass1_engine.py:11981
def _namespace_compact_chunk_local_keys(
    chunk: dict[str, Any],
    *,
    chunk_index: int,
) -> dict[str, Any]:
    """Give one compact transport chunk a deterministic internal key namespace.

    ``phase_key``, ``check_key``, and ``hypothesis_key`` are provider-authored
    transport keys, not canonical plan identifiers.  A continuation is therefore
    allowed to restart its local numbering (P1/C1/H1).  Namespace declarations
    before cross-chunk validation and remap references to declarations in the same
    chunk.  References to an already namespaced key from a previous chunk are left
    unchanged, including when the provider restates that key in a declaration.

    Duplicate declarations *within* one chunk intentionally remain duplicates
    after mapping so the chunk-level validator can reject the ambiguous response.
    """
    if chunk_index < 1:
        raise ValueError("compact candidate plan chunk index must be positive")
    value = copy.deepcopy(chunk)
    prefix = f"chunk{chunk_index:02d}:"

    def declaration_map(rows: list[dict[str, Any]], field: str) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for row in rows:
            local_key = str(row.get(field, "")).strip()
            if not local_key:
                continue
            mapping[local_key] = (
                local_key
                if re.fullmatch(r"chunk\d+:.+", local_key)
                else f"{prefix}{local_key}"
            )
            row[field] = mapping[local_key]
        return mapping

    phases = _compact_plan_rows(value, "analysis_phases")
    checks = _compact_plan_rows(value, "diagnostic_checks")
    hypotheses = _compact_plan_rows(value, "hypotheses")
    phase_keys = declaration_map(phases, "phase_key")
    check_keys = declaration_map(checks, "check_key")
    declaration_map(hypotheses, "hypothesis_key")

    for check in checks:
        check["phase_keys"] = [
            phase_keys.get(str(key).strip(), str(key).strip())
            for key in check.get("phase_keys", [])
        ]
        for claim in check.get("atomic_claims", []):
            if not isinstance(claim, dict):
                continue
            claim["phase_keys"] = [
                phase_keys.get(str(key).strip(), str(key).strip())
                for key in claim.get("phase_keys", [])
            ]
    for hypothesis in hypotheses:
        for field in ("supporting_check_keys", "opposing_check_keys"):
            hypothesis[field] = [
                check_keys.get(str(key).strip(), str(key).strip())
                for key in hypothesis.get(field, [])
            ]
    return value

# Original func/step04/pass1_engine.py:12045
def _deduplicate_compact_chunk_phase_aliases(value: dict[str, Any]) -> dict[str, Any]:
    """Collapse exact phase restatements inside one provider chunk.

    Identical ``phase_type + forecast_hours`` rows are representational duplicates,
    not meteorological disagreements. The first row is retained and diagnostic
    ``phase_keys`` references are redirected deterministically. Reusing one key for
    different phase definitions is still left for contract validation to reject.
    """
    result = copy.deepcopy(value)
    phases = _compact_plan_rows(result, "analysis_phases")
    checks = _compact_plan_rows(result, "diagnostic_checks")
    canonical_by_signature: dict[tuple[str, tuple[int, ...]], str] = {}
    phase_signature_by_key: dict[str, tuple[str, tuple[int, ...]]] = {}
    aliases: dict[str, str] = {}
    retained: list[dict[str, Any]] = []
    for phase in phases:
        phase_key = str(phase.get("phase_key", "")).strip()
        signature = _compact_phase_signature(phase)
        previous_signature = phase_signature_by_key.setdefault(phase_key, signature)
        if previous_signature != signature:
            retained.append(phase)
            continue
        canonical_key = canonical_by_signature.setdefault(signature, phase_key)
        aliases[phase_key] = canonical_key
        if canonical_key == phase_key:
            retained.append(phase)
        else:
            logging.info(
                "step04 recoverable duplicate phase collapsed phase_key=%s canonical_phase_key=%s signature=%s",
                phase_key,
                canonical_key,
                signature,
            )
    result["analysis_phases"] = retained
    if aliases:
        for check in checks:
            check["phase_keys"] = list(
                dict.fromkeys(
                    aliases.get(str(key).strip(), str(key).strip())
                    for key in check.get("phase_keys", [])
                )
            )
            for claim in check.get("atomic_claims", []):
                if not isinstance(claim, dict):
                    continue
                claim["phase_keys"] = list(
                    dict.fromkeys(
                        aliases.get(str(key).strip(), str(key).strip())
                        for key in claim.get("phase_keys", [])
                    )
                )
    return result

# Original func/step04/pass1_engine.py:12099
def _normalize_compact_chunk_for_validation(
    chunk: dict[str, Any],
    previous_chunks: list[dict[str, Any]],
    *,
    chunk_index: int,
) -> dict[str, Any]:
    """Normalize recoverable provider representation issues before validation."""
    value = _canonicalize_compact_continuation_domain_fields(chunk, previous_chunks)
    value = _namespace_compact_chunk_local_keys(value, chunk_index=chunk_index)
    return _deduplicate_compact_chunk_phase_aliases(value)

# Original func/step04/pass1_engine.py:12111
def _normalize_compact_continuation_chunk(
    chunk: dict[str, Any],
    previous_chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Remove exact continuation restatements and remap their local check keys.

    Structured-output continuation may restate phases or diagnostic checks even when
    the prompt asks it not to. Exact restatements are harmless: the prior check is
    reused and hypotheses are redirected to its canonical key. Checks with different
    analytical intent remain distinct.
    """
    if not previous_chunks:
        return chunk
    value = _canonicalize_compact_continuation_domain_fields(chunk, previous_chunks)
    phase_aliases = _compact_phase_key_aliases([*previous_chunks, value])
    used_check_keys = {
        str(row.get("check_key", ""))
        for previous in previous_chunks
        for row in _compact_plan_rows(previous, "diagnostic_checks")
    }
    fingerprint_to_key: dict[str, str] = {}
    for previous in previous_chunks:
        for row in _compact_plan_rows(previous, "diagnostic_checks"):
            fingerprint_to_key.setdefault(
                _compact_check_fingerprint(row, phase_aliases),
                str(row.get("check_key", "")),
            )

    aliases: dict[str, str] = {}
    retained: list[dict[str, Any]] = []
    for index, raw_row in enumerate(_compact_plan_rows(value, "diagnostic_checks"), start=1):
        row = copy.deepcopy(raw_row)
        old_key = str(row.get("check_key", "")).strip()
        row["phase_keys"] = list(
            dict.fromkeys(phase_aliases.get(str(key), str(key)) for key in row.get("phase_keys", []))
        )
        for claim in row.get("atomic_claims", []):
            if not isinstance(claim, dict):
                continue
            claim["phase_keys"] = list(
                dict.fromkeys(
                    phase_aliases.get(str(key), str(key)) for key in claim.get("phase_keys", [])
                )
            )
        fingerprint = _compact_check_fingerprint(row, phase_aliases)
        canonical_key = fingerprint_to_key.get(fingerprint)
        if canonical_key:
            aliases[old_key] = canonical_key
            continue
        new_key = old_key
        if not new_key or new_key in used_check_keys:
            base = f"CHK_CONT_{int(value.get('plan_chunk_index', 0)):02d}_{index:02d}"
            new_key = base
            suffix = 1
            while new_key in used_check_keys:
                suffix += 1
                new_key = f"{base}_{suffix}"
        aliases[old_key] = new_key
        row["check_key"] = new_key
        retained.append(row)
        used_check_keys.add(new_key)
        fingerprint_to_key[fingerprint] = new_key
    value["diagnostic_checks"] = retained

    for hypothesis in _compact_plan_rows(value, "hypotheses"):
        for field in ("supporting_check_keys", "opposing_check_keys"):
            hypothesis[field] = list(
                dict.fromkeys(aliases.get(str(key), str(key)) for key in hypothesis.get(field, []))
            )
    return value

# Original func/step04/pass1_engine.py:12190
def _compact_check_forecast_hours(
    row: dict[str, Any],
    phase_hours_by_key: dict[str, set[int]],
) -> set[int]:
    return {
        int(hour)
        for phase_key in row.get("phase_keys", [])
        for hour in phase_hours_by_key.get(str(phase_key), set())
    }

# Original func/step04/pass1_engine.py:12201
def _compact_atomic_claim_semantic_signature(
    row: dict[str, Any],
    *,
    phase_hours_by_key: dict[str, set[int]],
) -> tuple[Any, ...]:
    """Return a conservative semantic signature for atomic claims.

    Semantic duplicate consolidation is allowed only when the structured claim
    meaning is equivalent.  Narrative similarity and one overlapping forecast
    hour are not sufficient because they can collapse a supporting pre-peak
    tendency and an opposing post-peak tendency into one contradictory Check.
    """
    claims = row.get("atomic_claims")
    if not isinstance(claims, list):
        return ()
    signatures: list[tuple[Any, ...]] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        phase_hours = tuple(sorted({
            int(hour)
            for phase_key in claim.get("phase_keys", [])
            for hour in phase_hours_by_key.get(str(phase_key), set())
        }))
        scope = claim.get("quantitative_scope")
        if isinstance(scope, dict):
            quantitative_signature: tuple[Any, ...] | None = (
                str(scope.get("feature_id") or ""),
                str(scope.get("region_scope") or ""),
                tuple(str(value) for value in scope.get("region_ids", []) if str(value)),
                tuple(int(value) for value in scope.get("forecast_hours", []) if isinstance(value, int) and not isinstance(value, bool)),
                tuple(int(value) for value in scope.get("comparison_forecast_hours", []) if isinstance(value, int) and not isinstance(value, bool))
                if isinstance(scope.get("comparison_forecast_hours"), list)
                else None,
                str(scope.get("statistic") or ""),
            )
        else:
            quantitative_signature = None
        signatures.append((
            tuple(sorted(str(value) for value in claim.get("claim_components", []) if str(value))),
            str(claim.get("expected_state") or ""),
            str(claim.get("scope_quantifier") or ""),
            phase_hours,
            quantitative_signature,
        ))
    return tuple(sorted(signatures, key=repr))

# Original func/step04/pass1_engine.py:12257
def _positive_alignment_scope_identity(claim: dict[str, Any]) -> tuple[str, str]:
    """Return a scope identity that preserves genuinely distinct subareas.

    Generic candidate-wide wording is normalized so the same core overlap cannot
    escape duplicate detection merely by switching ``all``/``specific``/
    ``at_least_one``.  A concrete ``specific`` subarea remains distinct, allowing
    separate east/west (or otherwise named) alignment checks when they are truly
    different verification targets.
    """
    quantifier = str(claim.get("scope_quantifier") or "")
    description = " ".join(str(claim.get("scope_description") or "").split())
    compact = description.replace(" ", "")
    generic_candidate_scopes = {
        "후보영역",
        "후보영역전체",
        "후보영역내",
        "후보영역내결합영역",
    }
    if quantifier in {"all", "at_least_one"} or compact in generic_candidate_scopes:
        return ("candidate_overlap", "")
    return (quantifier or "unspecified", description)

# Original func/step04/pass1_engine.py:12280
def _positive_alignment_claim_identity(
    check: dict[str, Any],
    claim: dict[str, Any],
    *,
    phase_hours_by_key: dict[str, set[int]],
) -> tuple[Any, ...]:
    """Return the semantic identity of one positive overlap claim.

    Quantifier differences alone do not make two generic candidate-overlap claims
    independent.  Concrete ``specific`` subareas, however, remain distinct.
    """
    components = tuple(sorted(
        str(value) for value in claim.get("claim_components", []) if str(value)
    ))
    phase_hours = tuple(sorted({
        int(hour)
        for phase_key in claim.get("phase_keys", [])
        for hour in phase_hours_by_key.get(str(phase_key), set())
    }))
    return (
        components,
        phase_hours,
        str(check.get("domain_scope") or "candidate"),
        str(check.get("spatial_relation_scope") or "not_applicable"),
        _positive_alignment_scope_identity(claim),
    )

# Original func/step04/pass1_engine.py:12318
def _validate_complementary_quantitative_checks(
    checks: list[dict[str, Any]],
) -> list[str]:
    """Reject duplicate quantitative Checks that ask the exact opposite question.

    Step07 already supports ``opposite_observed``.  Asking both "increase" and
    "decrease" for the same feature, region, statistic, and time pair therefore
    duplicates one physical fact and can double-count it downstream.
    """
    errors: list[str] = []
    seen: dict[tuple[Any, ...], tuple[str, str]] = {}
    for check in checks:
        if not isinstance(check, dict):
            continue
        check_key = str(check.get("check_key") or "")
        claims = check.get("atomic_claims")
        if not isinstance(claims, list):
            continue
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            state = str(claim.get("expected_state") or "")
            opposite = _COMPLEMENTARY_QUANTITATIVE_STATES.get(state)
            scope = claim.get("quantitative_scope")
            if opposite is None or not isinstance(scope, dict):
                continue
            region_ids = tuple(sorted(str(value) for value in scope.get("region_ids", []) if str(value)))
            forecast_hours = tuple(
                int(value) for value in scope.get("forecast_hours", [])
                if isinstance(value, int) and not isinstance(value, bool)
            )
            comparison_hours = tuple(
                int(value) for value in (scope.get("comparison_forecast_hours") or [])
                if isinstance(value, int) and not isinstance(value, bool)
            )
            components = tuple(sorted(
                str(value) for value in claim.get("claim_components", []) if str(value)
            ))
            identity = (
                str(scope.get("feature_id") or ""),
                str(scope.get("region_scope") or ""),
                region_ids,
                forecast_hours,
                comparison_hours,
                str(scope.get("statistic") or ""),
                components,
            )
            previous = seen.get(identity)
            if previous is not None and previous[1] == opposite:
                errors.append(
                    "QUANTITATIVE_COMPLEMENTARY_DUPLICATE_CHECK "
                    f"check_key={check_key} duplicate_of={previous[0]} "
                    f"expected_state={state} duplicate_expected_state={previous[1]}; "
                    "repair=keep only one directional quantitative proposition for the same feature/region/time/statistic; "
                    "Step07 opposite_observed handles the opposite outcome"
                )
            else:
                seen[identity] = (check_key, state)
    return list(dict.fromkeys(errors))

# Original func/step04/pass1_engine.py:12379
def _validate_positive_alignment_contracts(
    checks: list[dict[str, Any]],
    *,
    phase_hours_by_key: dict[str, set[int]],
) -> list[str]:
    """Validate Step04 positive precipitation/environment alignment intents.

    A Check that is intended to establish positive precipitation-environment
    alignment must stay *pure*: it contains only positive ``overlap`` claims.
    Follow-up weakening/loss/persistence limitations belong in separate Checks.
    This preserves the existing Step07 Check-level localization contract instead
    of requiring claim-level localization downstream.
    """
    errors: list[str] = []
    seen_positive_claims: dict[tuple[Any, ...], tuple[str, str]] = {}

    for check in checks:
        if not isinstance(check, dict):
            continue
        check_key = str(check.get("check_key") or "")
        verification_mode = str(
            check.get("verification_mode")
            or _verification_mode_from_diagnostic_check(check)
        )
        if verification_mode != "multivariable_alignment":
            continue
        if str(check.get("evidence_role") or "") != "supporting":
            continue

        raw_claims = check.get("atomic_claims")
        claims = [claim for claim in raw_claims if isinstance(claim, dict)] if isinstance(raw_claims, list) else []
        positive_claims: list[dict[str, Any]] = []
        for claim in claims:
            components = {
                str(value) for value in claim.get("claim_components", []) if str(value)
            }
            if (
                str(claim.get("expected_state") or "") == "overlap"
                and "direct_precipitation" in components
                and bool(components & _POSITIVE_ALIGNMENT_ENVIRONMENT_COMPONENTS)
            ):
                positive_claims.append(claim)

        if not positive_claims:
            continue

        non_overlap_states = sorted({
            str(claim.get("expected_state") or "")
            for claim in claims
            if str(claim.get("expected_state") or "") != "overlap"
        })
        limiting_states = sorted({
            state for state in non_overlap_states
            if state in NON_POSITIVE_PRECURSOR_EXPECTED_STATES
        })
        if limiting_states:
            errors.append(
                "POSITIVE_ALIGNMENT_MIXED_POLARITY "
                f"check_key={check_key} limiting_states={limiting_states}; "
                "repair=keep the core positive precipitation-environment overlap in one pure "
                "multivariable_alignment verification_request and move weakening/decrease/"
                "not_overlap/split/reorganization persistence limits to separate verification_requests"
            )
        elif non_overlap_states:
            errors.append(
                "POSITIVE_ALIGNMENT_NON_OVERLAP_CLAIM "
                f"check_key={check_key} other_states={non_overlap_states}; "
                "repair=a positive precipitation-environment alignment Check must contain only "
                "overlap atomic_claims; move other decision axes to separate verification_requests"
            )

        for claim in positive_claims:
            quantifier = str(claim.get("scope_quantifier") or "")
            if quantifier == "all":
                errors.append(
                    "POSITIVE_ALIGNMENT_SCOPE_TOO_BROAD "
                    f"check_key={check_key} claim_key={claim.get('claim_key')} scope_quantifier=all; "
                    "repair=use at_least_one for an operationally meaningful overlap somewhere "
                    "inside the candidate, or specific when a concrete subarea is explicitly the target. "
                    "Do not require positive alignment over the entire candidate bbox"
                )

            identity = _positive_alignment_claim_identity(
                check, claim, phase_hours_by_key=phase_hours_by_key
            )
            previous = seen_positive_claims.get(identity)
            current_claim_key = str(claim.get("claim_key") or "")
            if previous is not None and previous != (check_key, current_claim_key):
                errors.append(
                    "POSITIVE_ALIGNMENT_DUPLICATE_ATOMIC_CLAIM "
                    f"check_key={check_key} duplicate_of={previous[0]} "
                    f"claim_key={current_claim_key} duplicate_claim_key={previous[1]}; "
                    "repair=retain exactly one pure positive multivariable_alignment Check/claim for the "
                    "same component/time/domain overlap. Put later weakening or persistence questions "
                    "in separate non-duplicate verification_requests"
                )
            else:
                seen_positive_claims[identity] = (check_key, current_claim_key)

    return list(dict.fromkeys(errors))

# Original func/step04/pass1_engine.py:12481
def _compact_checks_have_compatible_atomic_claims(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    phase_hours_by_key: dict[str, set[int]],
) -> bool:
    left_signature = _compact_atomic_claim_semantic_signature(
        left, phase_hours_by_key=phase_hours_by_key
    )
    right_signature = _compact_atomic_claim_semantic_signature(
        right, phase_hours_by_key=phase_hours_by_key
    )
    if bool(left_signature) != bool(right_signature):
        return False
    return left_signature == right_signature

# Original func/step04/pass1_engine.py:12498
def _compact_check_signal_families(row: dict[str, Any]) -> set[str]:
    """Return broad physical signal families stated by a compact check.

    The check's diagnostic and variable role are the primary contract. Narrative
    terms are used only for the narrow temporal-evolution role compatibility case,
    where ``hazard_magnitude`` and ``uncertainty`` may describe the same precipitation
    time series with different wording.
    """
    text = " ".join(
        str(row.get(field, "")).casefold()
        for field in ("expected_signal", "decision_relevance", "purpose")
    )
    families: set[str] = set()
    patterns: dict[str, tuple[str, ...]] = {
        "precipitation": ("강수", "호우", "강수량", "누적", "피크", "peak"),
        "moisture": ("수분", "수증기", "플럭스", "습윤", "850 hpa"),
        "convergence": ("수렴", "925 hpa", "하층", "저층"),
        "ascent": ("상승", "오메가", "omega", "700 hpa", "중층"),
        "synoptic": ("저기압", "기압골", "전선", "제트", "종관", "상층"),
        "evolution": ("이동", "변화", "약화", "강화", "재강화", "지속", "확장", "후퇴"),
    }
    for family, terms in patterns.items():
        if any(term in text for term in terms):
            families.add(family)
    return families

# Original func/step04/pass1_engine.py:12525
def _compact_cross_section_contract_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _cross_section_line_signature(row.get("cross_section_line")),
        _normalized_cross_section_strategy(row.get("cross_section_strategy")),
        _reference_band_axis_signature(row.get("reference_band_axis")),
    )

# Original func/step04/pass1_engine.py:12533
def _compact_checks_are_semantic_duplicates(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    phase_hours_by_key: dict[str, set[int]],
) -> bool:
    """Return True only when two checks represent one executable diagnosis.

    Continuation output commonly restates a prior check with broader phases or
    methods and slightly different Korean wording. The executable identity is the
    diagnostic, selected domain, physical role, and overlapping event phase. A
    distinct cross-section geometry remains a distinct check.
    """
    diagnostic_id = str(left.get("diagnostic_id", ""))
    if diagnostic_id != str(right.get("diagnostic_id", "")):
        return False
    if str(left.get("verification_mode", "")) != str(right.get("verification_mode", "")):
        return False
    if str(left.get("check_scope", "")) != str(right.get("check_scope", "")):
        return False
    if {str(value) for value in left.get("required_signal_components", [])} != {
        str(value) for value in right.get("required_signal_components", [])
    }:
        return False
    if str(left.get("domain_scope", "")) != str(right.get("domain_scope", "")):
        return False
    if str(left.get("spatial_relation_scope", "not_applicable")) != str(
        right.get("spatial_relation_scope", "not_applicable")
    ):
        return False
    if str(left.get("evidence_role") or "context") != str(right.get("evidence_role") or "context"):
        return False
    if not _compact_checks_have_compatible_atomic_claims(
        left, right, phase_hours_by_key=phase_hours_by_key
    ):
        return False
    if diagnostic_id == "vertical_cross_section":
        if (
            _compact_cross_section_contract_signature(left)
            != _compact_cross_section_contract_signature(right)
        ):
            return False

    left_hours = _compact_check_forecast_hours(left, phase_hours_by_key)
    right_hours = _compact_check_forecast_hours(right, phase_hours_by_key)
    if not left_hours or not right_hours or not (left_hours & right_hours):
        return False

    left_role = str(left.get("variable_role", ""))
    right_role = str(right.get("variable_role", ""))
    if left_role == right_role:
        return True

    # A precipitation temporal series is frequently labelled once as hazard
    # magnitude and once as uncertainty. It is still the same physical output.
    if diagnostic_id == "temporal_evolution" and {
        left_role,
        right_role,
    } <= {"hazard_magnitude", "uncertainty"}:
        left_families = _compact_check_signal_families(left)
        right_families = _compact_check_signal_families(right)
        return "precipitation" in left_families and "precipitation" in right_families
    return False

# Original func/step04/pass1_engine.py:12598
def _compact_narrative_specificity(value: Any) -> tuple[int, int, int, int]:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    forecast_hour_count = len(set(re.findall(r"(?i)(?:F|\+)\s*\d{1,3}", text)))
    pressure_level_count = len(set(re.findall(r"\b\d{3,4}\s*hPa\b", text, flags=re.I)))
    token_count = len(re.findall(r"[0-9a-z가-힣_]+", text.casefold()))
    return (forecast_hour_count, pressure_level_count, token_count, len(text))

# Original func/step04/pass1_engine.py:12606
def _more_specific_compact_narrative(first: Any, second: Any) -> str:
    left = str(first or "").strip()
    right = str(second or "").strip()
    if not left:
        return right
    if not right:
        return left
    if _compact_narrative_specificity(right) > _compact_narrative_specificity(left):
        return right
    return left

# Original func/step04/pass1_engine.py:12618
def _source_check_intent_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    """Capture one pre-merge Step04 verification request for lossless provenance."""
    return {
        "check_key": str(row.get("check_key") or ""),
        "signal_components": list(dict.fromkeys(
            str(value)
            for value in row.get("required_signal_components", [])
            if str(value)
        )),
        "verification_mode": str(
            row.get("verification_mode")
            or _verification_mode_from_diagnostic_check(row)
        ),
        "check_scope": str(row.get("check_scope") or ""),
        "domain_scope": str(row.get("domain_scope") or "candidate"),
        "spatial_relation_scope": str(row.get("spatial_relation_scope") or "not_applicable"),
        "phase_keys": [str(value) for value in row.get("phase_keys", []) if str(value)],
        "representative_spatial_hours": [
            int(value)
            for value in row.get("representative_spatial_hours", [])
            if isinstance(value, int) and not isinstance(value, bool)
        ],
        "direct_precipitation_anchor": _is_system_direct_precipitation_anchor_row(row),
        "expected_signal": row.get("expected_signal"),
        "decision_relevance": row.get("decision_relevance"),
        "purpose": row.get("purpose"),
        "priority": row.get("priority"),
        "evidence_role": row.get("evidence_role"),
        "coverage_roles": _effective_compact_check_coverage_roles(row),
        "cross_section_line": _cross_section_line_signature(row.get("cross_section_line")),
        "cross_section_strategy": _normalized_cross_section_strategy(row.get("cross_section_strategy")),
        "reference_band_axis": _reference_band_axis_signature(row.get("reference_band_axis")),
    }

# Original func/step04/pass1_engine.py:12653
def _source_atomic_claim_set(row: dict[str, Any]) -> dict[str, Any]:
    """Preserve the model-declared atomic claims separately from operational intent lineage."""
    claims = row.get("atomic_claims")
    if not isinstance(claims, list):
        claims = []
    return {
        "source_check_key": str(row.get("check_key") or ""),
        "atomic_claims": copy.deepcopy([item for item in claims if isinstance(item, dict)]),
    }

# Original func/step04/pass1_engine.py:12664
def _normalize_atomic_claims_for_shadow(row: dict[str, Any]) -> list[dict[str, Any]]:
    claims = row.get("atomic_claims")
    if not isinstance(claims, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in claims:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "claim_key": str(item.get("claim_key") or "").strip(),
            "claim_statement": str(item.get("claim_statement") or "").strip(),
            "claim_components": list(dict.fromkeys(
                str(value) for value in item.get("claim_components", []) if str(value)
            )),
            "phase_keys": list(dict.fromkeys(
                str(value) for value in item.get("phase_keys", []) if str(value)
            )),
            "expected_state": str(item.get("expected_state") or "").strip(),
            "scope_quantifier": str(item.get("scope_quantifier") or "").strip(),
            "scope_description": str(item.get("scope_description") or "").strip(),
            "quantitative_scope": copy.deepcopy(item.get("quantitative_scope"))
            if isinstance(item.get("quantitative_scope"), dict) else None,
        })
    return normalized

# Original func/step04/pass1_engine.py:12690
def _build_atomic_claim_contract(
    row: dict[str, Any],
    *,
    check_id: str,
) -> dict[str, Any]:
    source_sets = row.get("_source_atomic_claim_sets")
    if not isinstance(source_sets, list) or not source_sets:
        source_sets = [_source_atomic_claim_set(row)]
    return {
        "contract_version": ATOMIC_CLAIM_CONTRACT_VERSION,
        "mode": ATOMIC_CLAIM_VALIDATION_MODE,
        "quantitative_enforcement": "specific-v2+convergence-majority-v1",
        "check_id": check_id,
        "source_check_key": str(row.get("check_key") or ""),
        "inherited_evidence_role": str(row.get("evidence_role") or "context"),
        "verification_mode": str(
            row.get("verification_mode") or _verification_mode_from_diagnostic_check(row)
        ),
        "canonical_claims": _normalize_atomic_claims_for_shadow(row),
        "source_claim_sets": copy.deepcopy([
            item for item in source_sets if isinstance(item, dict)
        ]),
    }

# Original func/step04/pass1_engine.py:12715
def _shadow_validate_atomic_claim_contract(
    *,
    check_id: str,
    row: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Audit claim atomicity and the supported quantitative contract shapes.

    General claim semantics remain shadow-only.  Step07 deterministically
    enforces only the explicitly supported quantitative shapes declared here.
    """
    warnings: list[str] = []
    claims = contract.get("canonical_claims", [])
    request_components = {
        str(value) for value in row.get("required_signal_components", []) if str(value)
    }
    request_phases = {str(value) for value in row.get("phase_keys", []) if str(value)}
    mode = str(contract.get("verification_mode") or "")
    if not claims:
        warnings.append("ATOMIC_CLAIMS_MISSING")
    seen_keys: set[str] = set()
    for claim in claims:
        key = str(claim.get("claim_key") or "")
        statement = str(claim.get("claim_statement") or "")
        components = {str(value) for value in claim.get("claim_components", []) if str(value)}
        phases = {str(value) for value in claim.get("phase_keys", []) if str(value)}
        state = str(claim.get("expected_state") or "")
        quantifier = str(claim.get("scope_quantifier") or "")
        quantitative_scope = claim.get("quantitative_scope")
        if not key or not statement or not components or not phases or not state or not quantifier:
            warnings.append("ATOMIC_CLAIM_FIELDS_INCOMPLETE")
            continue

        specific_states = {"greater_than", "less_than", "increase", "decrease", "strengthen", "weaken", "peak_at"}
        if quantifier == "specific" and state in specific_states:
            if not isinstance(quantitative_scope, dict):
                warnings.append("ATOMIC_QUANTITATIVE_SCOPE_MISSING")
            else:
                feature_id = quantitative_scope.get("feature_id")
                region_scope = quantitative_scope.get("region_scope")
                region_ids = quantitative_scope.get("region_ids")
                forecast_hours = quantitative_scope.get("forecast_hours")
                comparison_hours = quantitative_scope.get("comparison_forecast_hours")
                statistic = quantitative_scope.get("statistic")
                invalid = (
                    not isinstance(feature_id, str) or not feature_id
                    or region_scope != "explicit_regions"
                    or not isinstance(region_ids, list) or len(region_ids) != 1
                    or not isinstance(region_ids[0], str) or not region_ids[0]
                    or not isinstance(forecast_hours, list) or len(forecast_hours) < 2
                    or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in forecast_hours)
                    or forecast_hours != sorted(set(forecast_hours))
                    or statistic not in {"min", "max", "mean", "p95"}
                )
                if state in {"greater_than", "less_than"}:
                    invalid = invalid or (
                        not isinstance(comparison_hours, list) or len(comparison_hours) != 2
                        or any(isinstance(value, bool) or not isinstance(value, int) for value in comparison_hours)
                        or len(set(comparison_hours)) != 2
                        or any(value not in forecast_hours for value in comparison_hours)
                    )
                elif comparison_hours is not None:
                    invalid = True
                if invalid:
                    warnings.append("ATOMIC_QUANTITATIVE_SCOPE_INVALID")

        if quantifier == "majority" and state == "not_strengthen":
            if not isinstance(quantitative_scope, dict):
                warnings.append("ATOMIC_QUANTITATIVE_SCOPE_MISSING")
            else:
                feature_id = quantitative_scope.get("feature_id")
                region_scope = quantitative_scope.get("region_scope")
                region_ids = quantitative_scope.get("region_ids")
                forecast_hours = quantitative_scope.get("forecast_hours")
                comparison_hours = quantitative_scope.get("comparison_forecast_hours")
                statistic = quantitative_scope.get("statistic")
                if (
                    feature_id != "convergence925_1e5_s"
                    or region_scope != "candidate_overlap"
                    or region_ids != []
                    or not isinstance(forecast_hours, list) or len(forecast_hours) != 2
                    or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in forecast_hours)
                    or forecast_hours != sorted(set(forecast_hours))
                    or comparison_hours is not None
                    or statistic != "mean"
                ):
                    warnings.append("ATOMIC_QUANTITATIVE_SCOPE_INVALID")

        if key in seen_keys:
            warnings.append("ATOMIC_CLAIM_KEY_DUPLICATE")
        seen_keys.add(key)
        if not components.issubset(request_components):
            warnings.append("ATOMIC_CLAIM_COMPONENT_OUT_OF_REQUEST")
        if not phases.issubset(request_phases):
            warnings.append("ATOMIC_CLAIM_PHASE_OUT_OF_REQUEST")
        if re.search(r"(?:또는|혹은|거나)", statement):
            warnings.append("ATOMIC_CLAIM_LOGICAL_ALTERNATIVE")
        if state in {"overlap", "not_overlap"}:
            if mode != "multivariable_alignment" or len(components) < 2:
                warnings.append("ATOMIC_CLAIM_ALIGNMENT_MODE_MISMATCH")
        if state in {"move", "split", "merge", "expand", "contract", "reorganize"}:
            if mode != "spatial_evolution":
                warnings.append("ATOMIC_CLAIM_SPATIAL_EVOLUTION_MODE_MISMATCH")
    warning_codes = list(dict.fromkeys(warnings))
    result = {
        "mode": ATOMIC_CLAIM_VALIDATION_MODE,
        "status": "pass" if not warning_codes else "warning",
        "warning_codes": warning_codes,
    }
    if warning_codes:
        logging.warning(
            "STEP04_ATOMIC_CLAIM_WARNING check_id=%s codes=%s",
            check_id,
            ",".join(warning_codes),
        )
    return result

# Original func/step04/pass1_engine.py:12834
def _merge_compact_semantic_check(
    canonical: dict[str, Any],
    duplicate: dict[str, Any],
) -> None:
    source_intents = canonical.get("_source_intents")
    if not isinstance(source_intents, list):
        source_intents = [_source_check_intent_snapshot(canonical)]
        canonical["_source_intents"] = source_intents
    source_intents.append(_source_check_intent_snapshot(duplicate))
    source_claim_sets = canonical.get("_source_atomic_claim_sets")
    if not isinstance(source_claim_sets, list):
        source_claim_sets = [_source_atomic_claim_set(canonical)]
        canonical["_source_atomic_claim_sets"] = source_claim_sets
    source_claim_sets.append(_source_atomic_claim_set(duplicate))
    canonical["phase_keys"] = list(
        dict.fromkeys(
            [str(value) for value in canonical.get("phase_keys", [])]
            + [str(value) for value in duplicate.get("phase_keys", [])]
        )
    )
    canonical["analysis_methods"] = list(
        dict.fromkeys(
            [str(value) for value in canonical.get("analysis_methods", [])]
            + [str(value) for value in duplicate.get("analysis_methods", [])]
        )
    )
    canonical["representative_spatial_hours"] = sorted(set(
        [
            int(value)
            for value in canonical.get("representative_spatial_hours", [])
            if isinstance(value, int) and not isinstance(value, bool)
        ]
        + [
            int(value)
            for value in duplicate.get("representative_spatial_hours", [])
            if isinstance(value, int) and not isinstance(value, bool)
        ]
    ))
    canonical["coverage_roles"] = list(
        dict.fromkeys(
            [str(value) for value in canonical.get("coverage_roles", [])]
            + [str(value) for value in duplicate.get("coverage_roles", [])]
        )
    )
    if str(duplicate.get("priority")) == "required":
        canonical["priority"] = "required"
    canonical_role = str(canonical.get("evidence_role", "context"))
    duplicate_role = str(duplicate.get("evidence_role", "context"))
    if _COMPACT_EVIDENCE_ROLE_RANK.get(
        duplicate_role, -1
    ) > _COMPACT_EVIDENCE_ROLE_RANK.get(canonical_role, -1):
        canonical["evidence_role"] = duplicate_role

    if (
        str(canonical.get("diagnostic_id")) == "temporal_evolution"
        and str(canonical.get("variable_role")) == "uncertainty"
        and str(duplicate.get("variable_role")) == "hazard_magnitude"
    ):
        canonical["variable_role"] = "hazard_magnitude"

    for field in ("expected_signal", "decision_relevance", "purpose"):
        canonical[field] = _more_specific_compact_narrative(
            canonical.get(field), duplicate.get(field)
        )

# Original func/step04/pass1_engine.py:12900
def _validate_compact_merged_check_coherence(
    row: dict[str, Any],
    *,
    phase_hours_by_key: dict[str, set[int]],
) -> None:
    """Fail closed when a merged Check contains contradictory source intent."""
    source_intents = row.get("_source_intents")
    if isinstance(source_intents, list) and source_intents:
        roles = {
            str(item.get("evidence_role") or "context")
            for item in source_intents
            if isinstance(item, dict)
        }
        canonical_role = str(row.get("evidence_role") or "context")
        if len(roles) > 1 or (roles and canonical_role not in roles):
            raise ValueError(
                "COMPACT_CHECK_MERGE_CONTRACT_CONFLICT "
                f"check_key={row.get('check_key')} source_evidence_roles={sorted(roles)} "
                f"canonical_evidence_role={canonical_role!r}; "
                "repair=split supporting/opposing/context propositions into separate verification_requests"
            )

    source_claim_sets = row.get("_source_atomic_claim_sets")
    if isinstance(source_claim_sets, list) and source_claim_sets:
        signatures: list[tuple[Any, ...]] = []
        for item in source_claim_sets:
            if not isinstance(item, dict):
                continue
            signatures.append(
                _compact_atomic_claim_semantic_signature(
                    {"atomic_claims": item.get("atomic_claims")},
                    phase_hours_by_key=phase_hours_by_key,
                )
            )
        if signatures and any(signature != signatures[0] for signature in signatures[1:]):
            raise ValueError(
                "COMPACT_CHECK_MERGE_CONTRACT_CONFLICT "
                f"check_key={row.get('check_key')} source_atomic_claims_are_not_equivalent; "
                "repair=keep distinct expected_state/time-window/quantitative-scope claims as separate verification_requests"
            )
        canonical_signature = _compact_atomic_claim_semantic_signature(
            row, phase_hours_by_key=phase_hours_by_key
        )
        if signatures and canonical_signature != signatures[0]:
            raise ValueError(
                "COMPACT_CHECK_MERGE_CONTRACT_CONFLICT "
                f"check_key={row.get('check_key')} canonical_atomic_claim_differs_from_sources; "
                "repair=regenerate the complete verification_request rather than merging incompatible fields"
            )

# Original func/step04/pass1_engine.py:12951
def _compact_hypothesis_reference_sets(
    row: dict[str, Any],
) -> tuple[set[str], set[str]]:
    supporting = {
        str(value)
        for value in row.get("supporting_check_keys", [])
        if str(value)
    }
    opposing = {
        str(value)
        for value in row.get("opposing_check_keys", [])
        if str(value)
    }
    return supporting, opposing

# Original func/step04/pass1_engine.py:12967
def _compact_hypotheses_are_semantic_duplicates(
    left: dict[str, Any],
    right: dict[str, Any],
) -> bool:
    left_supporting, left_opposing = _compact_hypothesis_reference_sets(left)
    right_supporting, right_opposing = _compact_hypothesis_reference_sets(right)
    if not left_supporting or not right_supporting:
        return False
    if (left_supporting & right_opposing) or (
        right_supporting & left_opposing
    ):
        return False
    supporting_union = left_supporting | right_supporting
    all_left = left_supporting | left_opposing
    all_right = right_supporting | right_opposing
    all_union = all_left | all_right
    if not supporting_union or not all_union:
        return False
    supporting_jaccard = len(left_supporting & right_supporting) / len(supporting_union)
    all_jaccard = len(all_left & all_right) / len(all_union)
    left_statement = re.sub(r"\s+", "", str(left.get("statement", "")).casefold())
    right_statement = re.sub(r"\s+", "", str(right.get("statement", "")).casefold())
    statement_similarity = SequenceMatcher(
        None, left_statement, right_statement, autojunk=False
    ).ratio()
    return (
        supporting_jaccard >= 0.67
        and all_jaccard >= 0.60
        and statement_similarity >= 0.38
    )

# Original func/step04/pass1_engine.py:12999
def _merge_semantic_duplicate_compact_hypotheses(
    rows: list[dict[str, Any]],
    *,
    candidate_id: str,
) -> list[dict[str, Any]]:
    retained: list[dict[str, Any]] = []
    for raw in rows:
        row = copy.deepcopy(raw)
        duplicate_of = next(
            (
                existing
                for existing in retained
                if _compact_hypotheses_are_semantic_duplicates(existing, row)
            ),
            None,
        )
        if duplicate_of is None:
            retained.append(row)
            continue
        duplicate_of["supporting_check_keys"] = list(
            dict.fromkeys(
                [str(value) for value in duplicate_of.get("supporting_check_keys", [])]
                + [str(value) for value in row.get("supporting_check_keys", [])]
            )
        )
        duplicate_of["opposing_check_keys"] = list(
            dict.fromkeys(
                [str(value) for value in duplicate_of.get("opposing_check_keys", [])]
                + [str(value) for value in row.get("opposing_check_keys", [])]
            )
        )
        overlap = set(duplicate_of["supporting_check_keys"]) & set(duplicate_of["opposing_check_keys"])
        if overlap:
            duplicate_of["opposing_check_keys"] = [
                value for value in duplicate_of["opposing_check_keys"] if value not in overlap
            ]
        duplicate_of["statement"] = _more_specific_compact_narrative(
            duplicate_of.get("statement"), row.get("statement")
        )
        logging.info(
            "step04 continuation hypothesis semantic duplicate merged "
            "candidate_id=%s retained_hypothesis_key=%s removed_hypothesis_key=%s",
            candidate_id,
            duplicate_of.get("hypothesis_key"),
            row.get("hypothesis_key"),
        )
    return retained

# Original func/step04/pass1_engine.py:13048
def _canonicalize_semantic_duplicate_compact_plan(
    check_rows: list[dict[str, Any]],
    hypothesis_rows: list[dict[str, Any]],
    phase_rows: list[dict[str, Any]],
    *,
    candidate_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge continuation checks that request the same physical diagnosis.

    The first check key is canonical. Phase and method coverage are unioned, the
    more specific narratives are retained, and all hypothesis references are
    redirected before duplicate hypotheses are consolidated.
    """
    phase_hours_by_key = {
        str(row.get("phase_key")): set(_compact_phase_hours(row))
        for row in phase_rows
        if str(row.get("phase_key", "")).strip()
    }
    retained: list[dict[str, Any]] = []
    aliases: dict[str, str] = {}
    for raw in check_rows:
        row = copy.deepcopy(raw)
        local_key = str(row.get("check_key", "")).strip()
        duplicate_of = next(
            (
                existing
                for existing in retained
                if _compact_checks_are_semantic_duplicates(
                    existing,
                    row,
                    phase_hours_by_key=phase_hours_by_key,
                )
            ),
            None,
        )
        if duplicate_of is None:
            retained.append(row)
            aliases[local_key] = local_key
            continue
        canonical_key = str(duplicate_of.get("check_key", "")).strip()
        aliases[local_key] = canonical_key
        _merge_compact_semantic_check(duplicate_of, row)
        _validate_compact_merged_check_coherence(
            duplicate_of,
            phase_hours_by_key=phase_hours_by_key,
        )
        logging.info(
            "step04 continuation diagnostic semantic duplicate merged "
            "candidate_id=%s retained_check_key=%s removed_check_key=%s diagnostic_id=%s",
            candidate_id,
            canonical_key,
            local_key,
            row.get("diagnostic_id"),
        )

    remapped_hypotheses: list[dict[str, Any]] = []
    for raw in hypothesis_rows:
        hypothesis = copy.deepcopy(raw)
        for field in ("supporting_check_keys", "opposing_check_keys"):
            hypothesis[field] = list(
                dict.fromkeys(
                    aliases.get(str(value), str(value))
                    for value in hypothesis.get(field, [])
                )
            )
        remapped_hypotheses.append(hypothesis)
    _canonicalize_redundant_temporal_evolution_checks(
        retained,
        remapped_hypotheses,
        phase_hours_by_key=phase_hours_by_key,
        candidate_id=candidate_id,
    )
    _canonicalize_disjoint_hypothesis_check_references(
        remapped_hypotheses,
        candidate_id=candidate_id,
    )
    remapped_hypotheses = _drop_hypotheses_without_supporting_checks(
        remapped_hypotheses,
        candidate_id=candidate_id,
        field_suffix="keys",
        require_one=True,
    )
    remapped_hypotheses = _merge_semantic_duplicate_compact_hypotheses(
        remapped_hypotheses,
        candidate_id=candidate_id,
    )
    return retained, remapped_hypotheses

# Original func/step04/pass1_engine.py:13136
def _canonicalize_disjoint_hypothesis_check_references(
    hypotheses: list[dict[str, Any]],
    *,
    candidate_id: str,
) -> None:
    """Reject a Check that is assigned both supporting and opposing polarity."""
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            continue
        supporting = list(
            dict.fromkeys(
                str(value)
                for value in hypothesis.get(
                    "supporting_check_keys",
                    hypothesis.get("supporting_check_ids", []),
                )
            )
        )
        opposing = list(
            dict.fromkeys(
                str(value)
                for value in hypothesis.get(
                    "opposing_check_keys",
                    hypothesis.get("opposing_check_ids", []),
                )
            )
        )
        overlap = set(supporting) & set(opposing)
        if not overlap:
            continue
        raise ValueError(
            "HYPOTHESIS_CHECK_ROLE_CONFLICT "
            f"candidate_id={candidate_id} "
            f"hypothesis={hypothesis.get('hypothesis_key', hypothesis.get('hypothesis_id'))} "
            f"check_ids={sorted(overlap)}; "
            "repair=one Check cannot be both supporting and opposing for the same hypothesis; "
            "split the physical propositions into separate Checks or keep only the intended evidence role"
        )

# Original func/step04/pass1_engine.py:13176
def _drop_hypotheses_without_supporting_checks(
    hypotheses: list[dict[str, Any]],
    *,
    candidate_id: str,
    field_suffix: str,
    require_one: bool,
) -> list[dict[str, Any]]:
    """Drop provider hypotheses that have no positive evaluation path.

    The canonical contract requires at least one supporting check for every
    hypothesis.  A continuation model can still emit an empty supporting
    array because the OpenAI transport schema intentionally avoids some array
    cardinality keywords.  Promoting an opposing check to supporting would
    invent semantics, so unsupported hypotheses are removed instead.
    """
    supporting_field = f"supporting_check_{field_suffix}"
    retained: list[dict[str, Any]] = []
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            continue
        supporting = [
            str(value).strip()
            for value in hypothesis.get(supporting_field, [])
            if str(value).strip()
        ]
        hypothesis[supporting_field] = list(dict.fromkeys(supporting))
        if hypothesis[supporting_field]:
            retained.append(hypothesis)
            continue
        logging.warning(
            "step04 candidate plan unsupported hypothesis removed "
            "candidate_id=%s hypothesis=%s field=%s",
            candidate_id,
            hypothesis.get("hypothesis_key", hypothesis.get("hypothesis_id")),
            supporting_field,
        )
    if require_one and not retained:
        raise ValueError(
            "candidate plan requires at least one hypothesis with a supporting check"
        )
    return retained

# Original func/step04/pass1_engine.py:13219
def _hypothesis_check_role_errors(
    hypotheses: Iterable[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            continue
        supporting = {str(value) for value in hypothesis.get("supporting_check_ids", [])}
        opposing = {str(value) for value in hypothesis.get("opposing_check_ids", [])}
        overlap = sorted(supporting & opposing)
        if overlap:
            errors.append(
                f"{hypothesis.get('hypothesis_id')} uses the same check as "
                f"supporting and opposing: {overlap}"
            )
    return errors

# Original func/step04/pass1_engine.py:13247
def _narrative_tokens(*values: Any) -> set[str]:
    """Return stable Korean/English tokens for local semantic matching."""
    text = " ".join(str(value) for value in values if value is not None).casefold()
    return {
        token
        for token in re.findall(r"[0-9a-z가-힣_]+", text)
        if len(token) >= 2
    }

# Original func/step04/pass1_engine.py:13257
def _hypothesis_check_link_score(
    hypothesis: dict[str, Any],
    check: dict[str, Any],
    *,
    checks_by_id: dict[str, dict[str, Any]],
) -> tuple[int, int, int]:
    """Rank the most defensible hypothesis for an otherwise unlinked check.

    The score uses only information already present in the validated plan:
    narrative overlap, variable-role terms, and event-phase overlap with checks
    already referenced by the hypothesis.  No new meteorological assertion is
    invented.
    """
    statement = str(hypothesis.get("statement", ""))
    hypothesis_tokens = _narrative_tokens(statement)
    check_tokens = _narrative_tokens(
        check.get("expected_signal"),
        check.get("decision_relevance"),
        check.get("diagnostic_id"),
        check.get("variable_role"),
    )
    lexical_overlap = len(hypothesis_tokens & check_tokens)

    role = str(check.get("variable_role", ""))
    role_terms = _VARIABLE_ROLE_HYPOTHESIS_TERMS.get(role, ())
    role_hits = sum(1 for term in role_terms if term.casefold() in statement.casefold())

    referenced_ids = {
        str(value)
        for field in ("supporting_check_ids", "opposing_check_ids")
        for value in hypothesis.get(field, [])
    }
    referenced_phases = {
        str(phase_id)
        for check_id in referenced_ids
        for phase_id in checks_by_id.get(check_id, {}).get("phase_ids", [])
    }
    orphan_phases = {str(value) for value in check.get("phase_ids", [])}
    phase_overlap = len(referenced_phases & orphan_phases)
    return (role_hits * 8 + lexical_overlap * 2 + phase_overlap * 5, phase_overlap, lexical_overlap)

# Original func/step04/pass1_engine.py:13299
def _link_unreferenced_variable_checks(
    plan: dict[str, Any],
    *,
    canonicalization_events: list[dict[str, Any]] | None = None,
) -> None:
    """Ensure every generated diagnostic check has an explicit hypothesis use.

    Provider output occasionally creates a valid and useful diagnostic but omits
    its hypothesis reference.  Dropping such a check loses requested evidence;
    leaving it orphaned makes Pass2 intent ambiguous.  Attach each orphan to the
    best matching hypothesis using deterministic local evidence already present
    in the plan.  The check is added as supporting evidence because its
    ``expected_signal`` states the positive condition it is designed to test.
    """
    hypotheses = [item for item in plan.get("hypotheses", []) if isinstance(item, dict)]
    checks = [item for item in plan.get("variable_checks", []) if isinstance(item, dict)]
    if not hypotheses or not checks:
        return

    checks_by_id = {
        str(check.get("check_id")): check
        for check in checks
        if str(check.get("check_id", "")).strip()
    }
    referenced = {
        str(value)
        for hypothesis in hypotheses
        for field in ("supporting_check_ids", "opposing_check_ids")
        for value in hypothesis.get(field, [])
    }
    for check_index, check in enumerate(checks):
        check_id = str(check.get("check_id", "")).strip()
        if not check_id or check_id in referenced:
            continue
        if _is_system_direct_precipitation_anchor_check(check):
            # System validation contracts are deliberately independent of
            # model-authored hypotheses.
            continue
        ranked = sorted(
            enumerate(hypotheses),
            key=lambda item: (
                _hypothesis_check_link_score(
                    item[1],
                    check,
                    checks_by_id=checks_by_id,
                ),
                -item[0],
            ),
            reverse=True,
        )
        target_index, target = ranked[0]
        supporting = [str(value) for value in target.get("supporting_check_ids", [])]
        target["supporting_check_ids"] = list(dict.fromkeys([*supporting, check_id]))
        referenced.add(check_id)
        event = {
            "field": (
                "candidate_analysis_plan.variable_checks"
                f"[{check_index}].hypothesis_reference"
            ),
            "reason": "unreferenced_variable_check_linked_to_best_matching_hypothesis",
            "check_id": check_id,
            "hypothesis_id": target.get("hypothesis_id"),
            "score": list(
                _hypothesis_check_link_score(
                    target,
                    check,
                    checks_by_id=checks_by_id,
                )
            ),
        }
        if canonicalization_events is not None:
            canonicalization_events.append(event)
        logging.warning(
            "step04 candidate plan unreferenced check linked "
            "candidate_id=%s check_id=%s hypothesis_id=%s score=%s",
            plan.get("candidate_id"),
            check_id,
            target.get("hypothesis_id"),
            event["score"],
        )

# Original func/step04/pass1_engine.py:13381
def _unreferenced_variable_check_errors(plan: dict[str, Any]) -> list[str]:
    hypotheses = [item for item in plan.get("hypotheses", []) if isinstance(item, dict)]
    checks = [item for item in plan.get("variable_checks", []) if isinstance(item, dict)]
    referenced = {
        str(value)
        for hypothesis in hypotheses
        for field in ("supporting_check_ids", "opposing_check_ids")
        for value in hypothesis.get(field, [])
    }
    orphan_ids = sorted(
        str(check.get("check_id"))
        for check in checks
        if str(check.get("check_id", "")).strip()
        and str(check.get("check_id")) not in referenced
        and not _is_system_direct_precipitation_anchor_check(check)
    )
    if not orphan_ids:
        return []
    return [
        "candidate analysis plan contains variable_checks not linked to any hypothesis: "
        f"{orphan_ids}"
    ]

# Original func/step04/pass1_engine.py:13552
def validate_compact_candidate_plan_response(
    value: dict[str, Any],
    *,
    schema: dict[str, Any] | None,
    candidate: dict[str, Any],
    attachment_manifest: dict[str, Any] | None = None,
    support_catalog: dict[str, Any] | None = None,
    operational_region_catalog: dict[str, Any] | None = None,
    expected_chunk_index: int,
    previous_chunks: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    if schema is not None:
        errors.extend(_validate_jsonschema(value, schema))
        if errors:
            return errors
    errors.extend(validate_step04_compact_plan_narrative(value))
    if errors:
        return errors
    if int(value.get("plan_chunk_index", -1)) != expected_chunk_index:
        return [f"compact candidate plan chunk index must be {expected_chunk_index}"]
    try:
        phases = _compact_plan_rows(value, "analysis_phases")
        checks = _compact_plan_rows(value, "diagnostic_checks")
        hypotheses = _compact_plan_rows(value, "hypotheses")
        phase_keys = _compact_local_keys(phases, "phase_key", label="analysis_phases")
        check_keys = _compact_local_keys(checks, "check_key", label="diagnostic_checks")
        _compact_local_keys(hypotheses, "hypothesis_key", label="hypotheses")

        signatures = [_compact_phase_signature(item) for item in phases]
        if len(signatures) != len(set(signatures)):
            raise ValueError("compact candidate plan repeats an identical phase_type/forecast_hours phase")
        phase_segmentation_notes: list[str] = []
        for index, (phase_type, hours) in enumerate(signatures):
            if not phase_type:
                raise ValueError("compact analysis phase requires phase_type")
            if len(_contiguous_hour_groups(hours)) > 1:
                phase_segmentation_notes.append(
                    "AI analysis phase contains temporally separated forecast hours "
                    f"phase_type={phase_type!r} hours={list(hours)}"
                )
            for other_type, other_hours in signatures[index + 1 :]:
                if phase_type == other_type and set(hours) & set(other_hours):
                    phase_segmentation_notes.append(
                        "AI phases with the same phase_type overlap forecast_hours "
                        f"phase_type={phase_type!r}"
                    )
        _log_step04_advisories("candidate_plan.phase_segmentation", phase_segmentation_notes)

        if len(str(value.get("analysis_objective", ""))) > 240:
            raise ValueError("analysis_objective must be concise (<=240 characters)")
        for hypothesis in hypotheses:
            if len(str(hypothesis.get("statement", ""))) > 250:
                raise ValueError("hypothesis statement must be concise (<=250 characters)")
        for check in checks:
            for field in ("purpose", "expected_signal", "decision_relevance"):
                if len(str(check.get(field, ""))) > 180:
                    raise ValueError(f"diagnostic check {field} must be concise (<=180 characters)")
        for question in value.get("forecaster_questions", []):
            if len(str(question)) > 180:
                raise ValueError("forecaster question must be concise (<=180 characters)")

        candidate_bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
        if candidate_bbox is None:
            raise ValueError("candidate_area bbox is invalid")
        selectable_operational_regions = None
        if operational_region_catalog is not None:
            selectable_operational_regions = _selectable_candidate_operational_regions(
                candidate_bbox=candidate_bbox,
                operational_region_catalog=operational_region_catalog,
            )
        raw_context = value.get("context_domain")
        context_bbox = _normalized_bbox_for_validation(raw_context)
        reason = value.get("context_domain_reason")
        reason_text = str(reason).strip() if reason is not None else ""
        if raw_context is None:
            if reason is not None:
                raise ValueError("context_domain_reason must be null when context_domain is null")
        else:
            if context_bbox is None:
                raise ValueError("context_domain bbox is invalid")
            if not _bbox_contains(context_bbox, candidate_bbox):
                raise ValueError("context_domain must contain the complete candidate_area")
            if not reason_text:
                raise ValueError("context_domain requires a concise context_domain_reason")
            if len(reason_text) > 200:
                raise ValueError("context_domain_reason must be concise (<=200 characters)")

        previous_phase_keys: set[str] = set()
        previous_phase_signature_by_key: dict[str, tuple[str, tuple[int, ...]]] = {}
        previous_check_keys: set[str] = set()
        previous_context: dict[str, float] | None = None
        previous_reason: str | None = None
        all_phase_rows: list[dict[str, Any]] = []
        for previous in previous_chunks:
            previous_phase_rows = _compact_plan_rows(previous, "analysis_phases")
            all_phase_rows.extend(previous_phase_rows)
            previous_phase_keys.update(
                _compact_local_keys(previous_phase_rows, "phase_key", label="previous analysis_phases")
            )
            for previous_phase in previous_phase_rows:
                previous_key = str(previous_phase.get("phase_key", "")).strip()
                previous_signature = _compact_phase_signature(previous_phase)
                known_signature = previous_phase_signature_by_key.setdefault(
                    previous_key, previous_signature
                )
                if known_signature != previous_signature:
                    raise ValueError(
                        "previous compact candidate plan reuses phase_key with a different phase definition "
                        f"phase_key={previous_key!r}"
                    )
            previous_rows = _compact_plan_rows(previous, "diagnostic_checks")
            previous_check_keys.update(
                _compact_local_keys(previous_rows, "check_key", label="previous diagnostic_checks")
            )
            previous_bbox = _normalized_bbox_for_validation(previous.get("context_domain"))
            if previous_bbox is not None and previous_context is None:
                previous_context = previous_bbox
                previous_value = previous.get("context_domain_reason")
                previous_reason = str(previous_value).strip() if previous_value is not None else None
        all_phase_rows.extend(phases)

        for phase in phases:
            current_key = str(phase.get("phase_key", "")).strip()
            current_signature = _compact_phase_signature(phase)
            previous_signature = previous_phase_signature_by_key.get(current_key)
            if previous_signature is not None and previous_signature != current_signature:
                raise ValueError(
                    "candidate_id="
                    f"{candidate.get('candidate_id')} chunk={expected_chunk_index} object=analysis_phase "
                    "reason=conflicting phase definition for an existing continuation key "
                    f"phase_key={current_key!r} previous={previous_signature!r} current={current_signature!r}"
                )
        if previous_chunks:
            if previous_context != context_bbox:
                raise ValueError("continuation chunk must preserve the original context_domain")
            if (previous_reason or None) != (reason_text or None):
                raise ValueError("continuation chunk must preserve context_domain_reason")

        available_hours = (
            _available_candidate_forecast_hours(candidate, attachment_manifest)
            if attachment_manifest is not None
            else sorted({hour for row in all_phase_rows for hour in _compact_phase_hours(row)})
        )
        available_hour_set = set(available_hours)
        for phase in phases:
            invalid_hours = sorted(set(_compact_phase_hours(phase)) - available_hour_set)
            if invalid_hours:
                raise ValueError(
                    "compact analysis phase uses unavailable forecast_hours="
                    f"{invalid_hours}; available={available_hours}"
                )

        _log_step04_advisories(
            "candidate_plan.phase_timeline",
            _phase_timeline_advisories(
                all_phase_rows,
                available_hours=available_hours,
            ),
        )
        timeline_errors = _validate_phase_timeline(
            all_phase_rows,
            available_hours=available_hours,
            candidate_peak_hours=candidate.get("peak_forecast_hours", []),
        )
        if timeline_errors:
            raise ValueError(" | ".join(timeline_errors))

        # Across continuation chunks, exact restatements are aliases. Distinct
        # same-type phases must remain temporally disjoint.
        signature_to_key: dict[tuple[str, tuple[int, ...]], str] = {}
        type_hours: dict[str, list[tuple[str, set[int]]]] = defaultdict(list)
        for phase in all_phase_rows:
            key = str(phase.get("phase_key", "")).strip()
            signature = _compact_phase_signature(phase)
            canonical_key = signature_to_key.setdefault(signature, key)
            if canonical_key != key and key in previous_phase_signature_by_key:
                continue
            phase_type, hours = signature
            current_hours = set(hours)
            for other_key, other_hours in type_hours[phase_type]:
                if current_hours & other_hours and signature_to_key.get(signature) != other_key:
                    _log_step04_advisories(
                        "candidate_plan.phase_segmentation",
                        [
                            "AI phases with the same phase_type overlap across chunks "
                            f"phase_type={phase_type!r} keys={[other_key, key]}"
                        ],
                    )
            type_hours[phase_type].append((canonical_key, current_hours))

        available_phase_keys = previous_phase_keys | phase_keys
        phase_hours_by_key = {
            str(row.get("phase_key", "")): set(_compact_phase_hours(row))
            for row in all_phase_rows
        }

        # One deterministic normalization layer owns all representation-level
        # candidate-plan cleanup before strict semantic validation.  This path is
        # also executed after full-Check repair merges because every semantic
        # validation pass re-enters this function.
        _normalize_compact_candidate_plan_contracts(
            value,
            all_phase_rows=all_phase_rows,
            available_hours=available_hours,
            candidate_id=str(candidate.get("candidate_id", "")),
        )
        checks = _compact_plan_rows(value, "diagnostic_checks")
        hypotheses = _compact_plan_rows(value, "hypotheses")

        for check in checks:
            references = {str(item) for item in check.get("phase_keys", [])}
            unknown = sorted(references - available_phase_keys)
            if unknown:
                raise ValueError(
                    "candidate_id="
                    f"{candidate.get('candidate_id')} chunk={expected_chunk_index} "
                    "object=diagnostic_check reason=unresolved phase reference after normalization "
                    f"phase_keys={unknown}"
                )
            diagnostic_id = str(check.get("diagnostic_id"))
            if diagnostic_id not in OPERATIONAL_DIAGNOSTIC_IDS:
                raise ValueError(
                    f"compact diagnostic check has unsupported diagnostic_id={diagnostic_id!r}"
                )
            if support_catalog is not None:
                supported_ids = {
                    str(item)
                    for item in support_catalog.get("analysis_plan_support", {}).get(
                        "supported_diagnostic_ids", []
                    )
                }
                if supported_ids and diagnostic_id not in supported_ids:
                    raise ValueError(
                        f"compact diagnostic check is not executable by the active catalog "
                        f"diagnostic_id={diagnostic_id!r}"
                    )
            regional_methods = {str(item) for item in check.get("analysis_methods", [])} & REGIONAL_ANALYSIS_METHODS
            if regional_methods and selectable_operational_regions == []:
                raise ValueError(
                    _operational_region_target_empty_error(
                        candidate_id=str(candidate.get("candidate_id") or ""),
                        candidate_bbox=candidate_bbox,
                        check_keys=[str(check.get("check_key") or "")],
                    )
                )
            variable_role = _canonical_variable_role_for_diagnostic(
                diagnostic_id, str(check.get("variable_role"))
            )
            check["variable_role"] = variable_role
            required_roles = DIAGNOSTIC_REQUIRED_VARIABLE_ROLES.get(diagnostic_id)
            if required_roles is not None and variable_role not in required_roles:
                raise ValueError(
                    "compact diagnostic check has incompatible diagnostic_id/variable_role "
                    f"diagnostic_id={diagnostic_id!r} variable_role={variable_role!r} "
                    f"allowed={sorted(required_roles)}"
                )
            scope = str(check.get("domain_scope"))
            if scope == "context" and context_bbox is None:
                raise ValueError("domain_scope=context requires context_domain")
            if diagnostic_id in CANDIDATE_SCOPE_ONLY_DIAGNOSTIC_IDS and scope != "candidate":
                raise ValueError(
                    f"{diagnostic_id} must use domain_scope=candidate because the standard "
                    "Step05 contract aggregates the candidate core area"
                )
            raw_cross_section_line = check.get("cross_section_line")
            cross_section_line = _normalized_cross_section_line(raw_cross_section_line)
            cross_section_strategy = _normalized_cross_section_strategy(
                check.get("cross_section_strategy")
            )
            reference_band_axis = _normalized_reference_band_axis(
                check.get("reference_band_axis")
            )
            if diagnostic_id == "vertical_cross_section":
                if cross_section_line is None:
                    raise ValueError(
                        "vertical_cross_section requires a valid cross_section_line with start/end "
                        "latitude/longitude and selection_reason"
                    )
                if cross_section_strategy is None:
                    raise ValueError(
                        "vertical_cross_section requires a valid cross_section_strategy"
                    )
                if reference_band_axis is None:
                    raise ValueError(
                        "vertical_cross_section requires a valid reference_band_axis"
                    )
                selected_bbox = candidate_bbox if scope == "candidate" else context_bbox
                if selected_bbox is None:
                    raise ValueError("vertical_cross_section has no resolved selected domain")
                endpoints = (
                    (cross_section_line["start_longitude"], cross_section_line["start_latitude"]),
                    (cross_section_line["end_longitude"], cross_section_line["end_latitude"]),
                )
                if any(not _point_in_bbox(lon, lat, selected_bbox) for lon, lat in endpoints):
                    raise ValueError(
                        "vertical_cross_section endpoints must be inside the selected domain_scope bbox"
                    )
                if not _segment_intersects_bbox(
                    cross_section_line["start_longitude"],
                    cross_section_line["start_latitude"],
                    cross_section_line["end_longitude"],
                    cross_section_line["end_latitude"],
                    candidate_bbox,
                ):
                    raise ValueError(
                        "vertical_cross_section line must intersect the candidate_area"
                    )
                original_angle_diff = _line_angle_difference_degrees(
                    cross_section_line, reference_band_axis
                )
                canonical_cross_section_line = _canonical_cross_section_line_orientation(
                    line=cross_section_line,
                    strategy=cross_section_strategy,
                    reference_axis=reference_band_axis,
                    selected_bbox=selected_bbox,
                    candidate_bbox=candidate_bbox,
                )
                if canonical_cross_section_line is None:
                    raise ValueError(
                        "vertical_cross_section line orientation could not be canonicalized "
                        "from cross_section_strategy and reference_band_axis"
                    )
                cross_section_line = canonical_cross_section_line
                canonical_angle_diff = _line_angle_difference_degrees(
                    cross_section_line, reference_band_axis
                )
                if (
                    cross_section_strategy == "across_precipitation_band"
                    and not (60.0 <= canonical_angle_diff <= 120.0)
                ):
                    raise ValueError(
                        "vertical_cross_section with "
                        "cross_section_strategy=across_precipitation_band must be broadly "
                        "perpendicular to reference_band_axis"
                    )
                if (
                    cross_section_strategy == "along_precipitation_axis"
                    and canonical_angle_diff > 30.0
                ):
                    raise ValueError(
                        "vertical_cross_section with "
                        "cross_section_strategy=along_precipitation_axis must be broadly "
                        "aligned with reference_band_axis"
                    )
                if abs(canonical_angle_diff - original_angle_diff) > 1.0e-6:
                    logging.warning(
                        "step04 candidate plan cross-section orientation canonicalized "
                        "candidate_id=%s check_key=%s strategy=%s "
                        "original_angle_diff=%.2f canonical_angle_diff=%.2f",
                        candidate.get("candidate_id"),
                        check.get("check_key"),
                        cross_section_strategy,
                        original_angle_diff,
                        canonical_angle_diff,
                    )
                original_length_km = _line_length_km(
                    _normalized_cross_section_line(raw_cross_section_line)
                    or cross_section_line
                )
                canonical_length_km = _line_length_km(cross_section_line)
                if canonical_length_km + 1.0 < original_length_km:
                    logging.warning(
                        "step04 candidate plan cross-section length bounded "
                        "candidate_id=%s check_key=%s strategy=%s "
                        "original_length_km=%.1f canonical_length_km=%.1f",
                        candidate.get("candidate_id"),
                        check.get("check_key"),
                        cross_section_strategy,
                        original_length_km,
                        canonical_length_km,
                    )
                if any(
                    not _point_in_bbox(lon, lat, selected_bbox)
                    for lon, lat in (
                        (cross_section_line["start_longitude"], cross_section_line["start_latitude"]),
                        (cross_section_line["end_longitude"], cross_section_line["end_latitude"]),
                    )
                ):
                    raise ValueError(
                        "canonicalized vertical_cross_section endpoints must remain inside "
                        "the selected domain_scope bbox"
                    )
                if not _segment_intersects_bbox(
                    cross_section_line["start_longitude"],
                    cross_section_line["start_latitude"],
                    cross_section_line["end_longitude"],
                    cross_section_line["end_latitude"],
                    candidate_bbox,
                ):
                    raise ValueError(
                        "canonicalized vertical_cross_section line must intersect the "
                        "candidate_area"
                    )
                check["cross_section_line"] = cross_section_line
                check["cross_section_strategy"] = cross_section_strategy
                check["reference_band_axis"] = reference_band_axis
            else:
                if raw_cross_section_line is not None:
                    raise ValueError(
                        "cross_section_line must be null unless diagnostic_id=vertical_cross_section"
                    )
                if check.get("cross_section_strategy") is not None:
                    raise ValueError("cross_section_strategy must be null unless diagnostic_id=vertical_cross_section")
                if check.get("reference_band_axis") is not None:
                    raise ValueError("reference_band_axis must be null unless diagnostic_id=vertical_cross_section")
            original_methods = list(dict.fromkeys(
                str(item) for item in check.get("analysis_methods", [])
            ))
            normalized_methods = _canonical_analysis_methods_for_diagnostic(
                diagnostic_id,
                original_methods,
            )
            check["analysis_methods"] = normalized_methods
            if normalized_methods != original_methods:
                logging.info(
                    "step04 candidate plan analysis methods canonicalized "
                    "candidate_id=%s check_key=%s diagnostic_id=%s original=%s canonical=%s",
                    candidate.get("candidate_id"),
                    check.get("check_key"),
                    diagnostic_id,
                    original_methods,
                    normalized_methods,
                )
            methods = set(normalized_methods)
            if not methods:
                raise ValueError("compact diagnostic check requires analysis_methods")
            if not methods <= (SPATIAL_ANALYSIS_METHODS | REGIONAL_ANALYSIS_METHODS):
                raise ValueError(
                    f"compact diagnostic check has unsupported analysis_methods={sorted(methods)}"
                )
            _validate_check_scope_contract(
                check_key=str(check.get("check_key", "")),
                diagnostic_id=diagnostic_id,
                analysis_methods=normalized_methods,
                check_scope=str(check.get("check_scope") or ""),
                required_signal_components=check.get("required_signal_components", []),
            )
            if support_catalog is not None:
                _validate_compact_check_execution_contract(
                    check_key=str(check.get("check_key", "")),
                    diagnostic_id=diagnostic_id,
                    required_signal_components=check.get("required_signal_components", []),
                    support_catalog=support_catalog,
                )
            _validate_same_check_narrative_contract(check)
            references = {str(item) for item in check.get("phase_keys", [])}
            referenced_hours = {
                hour for phase_key in references for hour in phase_hours_by_key.get(phase_key, set())
            }
            representative_hours_raw = check.get("representative_spatial_hours", [])
            if not isinstance(representative_hours_raw, list) or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in representative_hours_raw
            ):
                raise ValueError(
                    f"check_key={check.get('check_key')!r} representative_spatial_hours must be a non-negative integer array"
                )
            representative_hours = list(dict.fromkeys(int(value) for value in representative_hours_raw))
            check["representative_spatial_hours"] = representative_hours
            verification_mode = str(
                check.get("verification_mode")
                or _verification_mode_from_diagnostic_check(check)
            )
            spatial_support_error = _spatial_analysis_support_error(
                candidate_id=str(candidate.get("candidate_id", "")),
                check_id=str(check.get("check_key", "")),
                mode=verification_mode,
                scope=str(check.get("domain_scope") or ""),
                candidate_bbox=candidate_bbox,
                context_bbox=context_bbox,
            )
            if spatial_support_error:
                raise ValueError(spatial_support_error)
            if verification_mode == "temporal_change":
                if not representative_hours:
                    raise ValueError(
                        f"check_key={check.get('check_key')!r} temporal_change requires at least one AI-selected representative_spatial_hour"
                    )
                invalid_representative_hours = sorted(set(representative_hours) - referenced_hours)
                if invalid_representative_hours:
                    raise ValueError(
                        f"check_key={check.get('check_key')!r} representative_spatial_hours must belong to referenced phase hours; "
                        f"invalid={invalid_representative_hours} referenced={sorted(referenced_hours)}"
                    )
            elif representative_hours:
                raise ValueError(
                    f"check_key={check.get('check_key')!r} representative_spatial_hours is only allowed for temporal_change"
                )
            minimum_hours = _minimum_forecast_hours_for_check(
                diagnostic_id,
                str(check.get("check_scope") or ""),
            )
            if len(referenced_hours) < minimum_hours:
                raise ValueError(
                    "compact diagnostic check has insufficient forecast-hour coverage "
                    f"candidate_id={candidate.get('candidate_id')!r} "
                    f"chunk={expected_chunk_index} "
                    f"check_key={check.get('check_key')!r} "
                    f"verification_mode={verification_mode!r} "
                    f"check_scope={check.get('check_scope')!r} "
                    f"diagnostic_id={diagnostic_id!r} "
                    f"phase_keys={sorted(references)!r} "
                    f"referenced_hours={sorted(referenced_hours)!r} "
                    f"allowed_hours={available_hours!r} "
                    f"required={minimum_hours} actual={len(referenced_hours)}"
                )
            mentioned_hours = set()
            for field in ("purpose", "expected_signal", "decision_relevance"):
                mentioned_hours.update(
                    _mentioned_forecast_hours(str(check.get(field, "")), available_hours)
                )
            missing_hours = sorted(mentioned_hours - referenced_hours)
            if missing_hours:
                _log_step04_advisories(
                    "candidate_plan.text_phase_alignment",
                    [
                        "diagnostic check text mentions forecast hours not covered by phase_keys "
                        f"check_key={check.get('check_key')!r} missing={missing_hours}"
                    ],
                )
            if support_catalog is not None:
                _validate_compact_quantitative_atomic_claims(
                    check,
                    support_catalog=support_catalog,
                    candidate=candidate,
                    selectable_operational_regions=selectable_operational_regions,
                    referenced_hours=referenced_hours,
                    phase_hours_by_key=phase_hours_by_key,
                )

        all_alignment_checks = [
            check
            for previous in previous_chunks
            for check in _compact_plan_rows(previous, "diagnostic_checks")
        ] + list(checks)
        complementary_quantitative_errors = _validate_complementary_quantitative_checks(
            all_alignment_checks
        )
        if complementary_quantitative_errors:
            raise ValueError(" | ".join(complementary_quantitative_errors))

        positive_alignment_errors = _validate_positive_alignment_contracts(
            all_alignment_checks,
            phase_hours_by_key=phase_hours_by_key,
        )
        if positive_alignment_errors:
            raise ValueError(" | ".join(positive_alignment_errors))

        _log_step04_advisories(
            "candidate_plan.signal_coverage",
            _enforce_component_coverage_for_signal_text(checks),
        )

        _canonicalize_redundant_temporal_evolution_checks(
            checks,
            hypotheses,
            phase_hours_by_key=phase_hours_by_key,
            candidate_id=str(candidate.get("candidate_id", "")),
        )
        _canonicalize_disjoint_hypothesis_check_references(
            hypotheses,
            candidate_id=str(candidate.get("candidate_id", "")),
        )
        previous_supported_hypothesis_count = sum(
            1
            for previous in previous_chunks
            for previous_hypothesis in _compact_plan_rows(previous, "hypotheses")
            if previous_hypothesis.get("supporting_check_keys")
        )
        hypotheses = _drop_hypotheses_without_supporting_checks(
            hypotheses,
            candidate_id=str(candidate.get("candidate_id", "")),
            field_suffix="keys",
            require_one=not bool(previous_supported_hypothesis_count),
        )
        value["diagnostic_checks"] = checks
        value["hypotheses"] = hypotheses


        check_keys = _compact_local_keys(
            checks, "check_key", label="diagnostic_checks"
        )

        for hypothesis in hypotheses:
            referenced = {
                str(item)
                for field in ("supporting_check_keys", "opposing_check_keys")
                for item in hypothesis.get(field, [])
            }
            unknown = sorted(referenced - (previous_check_keys | check_keys))
            if unknown:
                raise ValueError(
                    "candidate_id="
                    f"{candidate.get('candidate_id')} chunk={expected_chunk_index} "
                    "object=hypothesis reason=unresolved check reference after normalization "
                    f"check_keys={unknown}"
                )

        all_phase_hours = {hour for row in all_phase_rows for hour in _compact_phase_hours(row)}
        for question in value.get("forecaster_questions", []):
            mentioned = _mentioned_forecast_hours(str(question), available_hours)
            if not mentioned.issubset(all_phase_hours):
                _log_step04_advisories(
                    "candidate_plan.forecaster_question_alignment",
                    [
                        "forecaster question mentions forecast hours not represented by analysis_phases "
                        f"missing={sorted(mentioned - all_phase_hours)}"
                    ],
                )

        if not previous_chunks:
            if len(phases) < 1:
                raise ValueError("first compact candidate plan chunk requires analysis_phases")
            if len(checks) < 3:
                raise ValueError("first compact candidate plan chunk requires at least 3 diagnostic_checks")
            if len(value.get("forecaster_questions", [])) < 1:
                raise ValueError("first compact candidate plan chunk requires a forecaster question")
            peak_phase_hours = {
                hour
                for phase in phases
                if str(phase.get("phase_type")) == "peak"
                for hour in _compact_phase_hours(phase)
            }
            candidate_peak_hours = {
                int(value) for value in candidate.get("peak_forecast_hours", [])
            }
            if not candidate_peak_hours.issubset(peak_phase_hours):
                raise ValueError(
                    "first compact candidate plan peak phases must cover all candidate peak_forecast_hours"
                )

            # Do not force a Python-defined split/merge of AI-declared peaks.
            # The AI may use one continuous peak analysis phase spanning multiple
            # declared peaks, or separate peak phases, as long as every declared
            # peak hour is represented.  The number of forecaster questions is also
            # an AI planning choice within the schema limits.

        # A model-declared final chunk cannot reserve a phase for an absent future
        # continuation. Surface this as a chunk-level contract error before
        # expansion assigns canonical phase IDs, so the complete chunk can be
        # regenerated from the original candidate context.
        if bool(value.get("is_complete")):
            unreferenced_phase_keys = _unreferenced_compact_phase_keys(
                [*previous_chunks, value]
            )
            if unreferenced_phase_keys:
                raise ValueError(
                    "COMPACT_PLAN_UNREFERENCED_PHASE "
                    f"phase_keys={unreferenced_phase_keys}"
                )

        if attachment_manifest is not None:
            available_phase_types = set(_compact_candidate_phase_hours(candidate, attachment_manifest))
            unsupported_phase_types = sorted(
                {
                    str(item.get("phase_type"))
                    for item in phases
                    if str(item.get("phase_type")) not in available_phase_types
                }
            )
            if unsupported_phase_types:
                raise ValueError(
                    "compact candidate plan uses unavailable phase_type(s)="
                    f"{unsupported_phase_types}; allowed={sorted(available_phase_types)}"
                )

        # Continuation exists only to close required coverage gaps.  Reject a
        # semantically valid-looking continuation before it is accepted when it
        # fails to reduce even one previously missing required role.  This sends
        # the whole chunk through the bounded candidate-chunk regeneration path
        # instead of consuming outer continuation attempts with the same gap.
        if previous_chunks:
            before_coverage = evaluate_compact_candidate_plan_coverage(
                previous_chunks,
                candidate=candidate,
            )
            before_missing = sorted(
                role
                for role, status in before_coverage.items()
                if status == "missing_but_relevant"
            )
            if before_missing:
                # Measure progress against the same post-validation continuation
                # normalization used by create_compact_candidate_plan. Exact
                # restatements must not appear to make progress merely because a
                # duplicate Check carries a newly declared non-structural role.
                trial_value = _normalize_compact_continuation_chunk(
                    copy.deepcopy(value),
                    previous_chunks,
                )
                trial_chunks = [*previous_chunks, trial_value]
                after_coverage = evaluate_compact_candidate_plan_coverage(
                    trial_chunks,
                    candidate=candidate,
                )
                after_missing = sorted(
                    role
                    for role, status in after_coverage.items()
                    if status == "missing_but_relevant"
                )
                if not (set(before_missing) - set(after_missing)):
                    raise ValueError(
                        "COMPACT_PLAN_CONTINUATION_NO_PROGRESS "
                        f"candidate_id={candidate.get('candidate_id')} "
                        f"remaining_roles={before_missing} "
                        f"after_remaining_roles={after_missing}; "
                        "repair=regenerate the continuation chunk so at least one previously missing "
                        "coverage role is satisfied by an executable verification structure"
                    )
    except (TypeError, ValueError) as exc:
        errors.append(str(exc))
    return errors

# Original func/step04/pass1_engine.py:14323
def normalize_compact_plan_completion_state(
    chunks: list[dict[str, Any]],
    *,
    candidate: dict[str, Any],
) -> tuple[dict[str, str], list[str], bool, list[str]]:
    """Make completion fields reflect merged diagnostic-check coverage.

    ``is_complete`` and ``remaining_roles`` are model-provided transport fields.
    The canonical values are derived only after duplicate continuation checks have
    been normalized and the merged diagnostic checks are available.
    """
    if not chunks:
        raise ValueError("compact candidate plan completion requires at least one chunk")
    latest = chunks[-1]
    raw_remaining = latest.get("remaining_roles", [])
    model_remaining_roles = (
        [str(role) for role in raw_remaining] if isinstance(raw_remaining, list) else []
    )
    model_is_complete = bool(latest.get("is_complete"))
    coverage = evaluate_compact_candidate_plan_coverage(chunks, candidate=candidate)
    actual_remaining_roles = sorted(
        role for role, status in coverage.items() if status == "missing_but_relevant"
    )
    latest["is_complete"] = not actual_remaining_roles
    latest["remaining_roles"] = actual_remaining_roles
    return coverage, actual_remaining_roles, model_is_complete, model_remaining_roles

# Original func/step04/pass1_engine.py:14381
def _candidate_temporal_feasibility_contract(
    candidate: dict[str, Any],
    attachment_manifest: dict[str, Any],
    *,
    support_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expand a one-hour verification window by one *observed* adjacent cadence.

    Use only an immutable candidate's explicitly related overview assets.  A
    before/after comparison must be visible in at least one of those assets,
    including the original peak hour.  This is a planning allowance, not a
    change to the discovered event window, precipitation anchor, or source data.
    """
    window = candidate.get("time_window") or {}
    start = int(window.get("forecast_hour_start", 0))
    end = int(window.get("forecast_hour_end", 0))
    envelope = candidate.get("analysis_envelope_request") or {}
    padding = max(0, int(envelope.get("temporal_padding_hours", 0)))
    related_ids = {str(value) for value in candidate.get("related_overview_asset_ids", [])}
    all_assets = [
        asset for asset in attachment_manifest.get("attachments", [])
        if isinstance(asset, dict)
    ]
    manifest_hours = {
        int(hour) for asset in all_assets for hour in asset.get("forecast_hours", [])
    }
    original = sorted(hour for hour in manifest_hours if start - padding <= hour <= end + padding)
    peaks = {int(hour) for hour in candidate.get("peak_forecast_hours", [])}
    # The preexisting calculation includes explicitly declared peak hours.  Do
    # not fabricate a missing peak to justify extending to another hour.
    requested = sorted(set(original) | peaks)
    extended = list(requested)
    source_asset_ids: list[str] = []
    if len(requested) < 2 and requested and related_ids:
        base_hour = requested[0]
        options: list[tuple[int, int, str]] = []
        for asset in all_assets:
            asset_id = str(asset.get("asset_id") or "")
            if asset_id not in related_ids:
                continue
            asset_hours = sorted({int(hour) for hour in asset.get("forecast_hours", [])})
            if base_hour not in asset_hours:
                continue
            # Do not extend with an overview whose product cannot be requested
            # at both times in Step05.  Missing catalog schedules use the
            # existing legacy unconstrained-product policy.
            product_hours = (
                _allowed_forecast_hours_for_product(
                    str(asset.get("product_id") or ""), support_catalog
                )
                if support_catalog is not None else None
            )
            cadence = int(asset.get("cadence_hours") or 0)
            if cadence <= 0:
                continue
            for hour in asset_hours:
                delta = abs(hour - base_hour)
                if delta == cadence and (
                    product_hours is None
                    or {base_hour, hour}.issubset(product_hours)
                ):
                    # Prefer the preceding time to preserve a pre-peak comparison.
                    options.append((delta, hour, asset_id))
        if options:
            _, neighbor, source_id = min(options, key=lambda row: (row[0], row[1], row[2]))
            extended = sorted({base_hour, neighbor})
            source_asset_ids = [source_id]
    return {
        "requested_hours": requested,
        "effective_hours": extended,
        "added_hours": sorted(set(extended) - set(requested)),
        "source_asset_ids": source_asset_ids,
        "minimum_temporal_hours": 2,
        "temporal_comparison_available": len(extended) >= 2,
    }

# Original func/step04/pass1_engine.py:14487
def _preferred_product_id_for_check(
    diagnostic_id: str,
    role: str,
    required_signal_components: list[str] | tuple[str, ...] | set[str],
    support_catalog: dict[str, Any],
    candidate: dict[str, Any],
) -> str:
    """Select only a catalog-compatible execution product for one check."""
    products = [
        str(item.get("product_id"))
        for item in support_catalog.get("spatial_field_products", [])
        if isinstance(item, dict) and item.get("product_id")
    ]
    execution_support = support_catalog.get("diagnostic_execution_support", {})
    contract = (
        execution_support.get(diagnostic_id, {})
        if isinstance(execution_support, dict)
        else {}
    )
    if not isinstance(contract, dict) or diagnostic_id not in execution_support:
        raise ValueError(
            "compact candidate plan diagnostic has no executable catalog contract "
            f"diagnostic_id={diagnostic_id!r}"
        )
    compatible = [
        str(value)
        for value in contract.get("compatible_product_ids", [])
        if str(value) in products
    ]
    required = {str(value) for value in required_signal_components if str(value)}
    component_compatible = [
        product_id
        for product_id in compatible
        if not _product_signal_components_from_support(product_id, support_catalog)
        or required.issubset(_product_signal_components_from_support(product_id, support_catalog))
    ]
    if component_compatible:
        compatible = component_compatible
    elif compatible:
        raise ValueError(
            "compact candidate plan diagnostic has no compatible product providing required signal components "
            f"diagnostic_id={diagnostic_id!r} required_signal_components={sorted(required)} "
            f"compatible_product_ids={compatible}"
        )
    if not compatible:
        raise ValueError(
            "compact candidate plan diagnostic has no available compatible product "
            f"diagnostic_id={diagnostic_id!r}"
        )
    preferred = str(contract.get("preferred_product_id") or "")
    if preferred and preferred in compatible:
        return preferred

    candidate_products = [str(item) for item in candidate.get("related_product_ids", [])]
    ordered = [
        item
        for item in dict.fromkeys([*candidate_products, *products])
        if item in compatible
    ]
    patterns = {
        "hazard_magnitude": ("PRECIP",),
        "moisture_supply": ("TCW", "WIND850", "MOISTURE"),
        "low_level_forcing": ("CONVERGENCE", "WIND925"),
        "midlevel_forcing": ("OMEGA",),
        "synoptic_support": ("MSLP", "JET", "MULTIVARIABLE"),
        "uncertainty": ("MULTIVARIABLE", "PRECIP", "TCW", "OMEGA", "CONVERGENCE", "MSLP"),
    }.get(role, ())
    for pattern in patterns:
        selected = next((item for item in ordered if pattern in item), None)
        if selected is not None:
            return selected
    if len(compatible) == 1:
        return compatible[0]
    raise ValueError(
        "compact candidate plan cannot resolve an unambiguous compatible product "
        f"diagnostic_id={diagnostic_id!r} variable_role={role!r} "
        f"compatible_product_ids={compatible}"
    )

# Original func/step04/pass1_engine.py:14567
def _allowed_forecast_hours_for_product(
    product_id: str,
    support_catalog: dict[str, Any],
) -> set[int] | None:
    """Return a product schedule when the catalog declares one.

    A missing schedule is intentionally treated as unconstrained for legacy
    catalog fixtures. Production spatial-field products declare the schedule.
    """
    for item in support_catalog.get("spatial_field_products", []):
        if not isinstance(item, dict) or str(item.get("product_id")) != product_id:
            continue
        allowed_hours = item.get("allowed_forecast_hours")
        if not isinstance(allowed_hours, list):
            return None
        return {int(value) for value in allowed_hours}
    return None

# Original func/step04/pass1_engine.py:14632
def _stable_unique_nonempty_strings(values: Any) -> list[str]:
    """Return exact string identifiers once, preserving their first occurrence.

    This helper is intentionally conservative: it does not fuzzy-match, rename,
    or drop unknown identifiers.  It only removes representational duplicates.
    """
    if not isinstance(values, list):
        return values
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result

# Original func/step04/pass1_engine.py:14651
def _atomic_claim_normalization_signature(claim: dict[str, Any]) -> str:
    """Fingerprint an atomic claim excluding its transport-only claim_key.

    Two claims are collapsed only when every semantic field is identical after
    deterministic normalization.  Similar or paraphrased claims remain distinct
    and are left for strict atomicity validation.
    """
    return canonical_hash({
        "claim_statement": str(claim.get("claim_statement") or "").strip(),
        "claim_components": list(claim.get("claim_components", [])),
        "phase_keys": list(claim.get("phase_keys", [])),
        "expected_state": str(claim.get("expected_state") or "").strip(),
        "scope_quantifier": str(claim.get("scope_quantifier") or "").strip(),
        "scope_description": str(claim.get("scope_description") or "").strip(),
        "quantitative_scope": copy.deepcopy(claim.get("quantitative_scope")),
    })

# Original func/step04/pass1_engine.py:14669
def _normalize_candidate_plan_representation_fields(value: dict[str, Any]) -> list[str]:
    """Normalize representation-only Step04 plan fields in either transport form.

    This helper is deliberately usable *before* JSON Schema validation and again
    after transport-to-diagnostic conversion.  It therefore only touches fields
    whose duplicate/order representation cannot change meteorological intent.
    It never invents identifiers, forecast hours, phases, regions, or comparator
    direction.
    """
    changes: list[str] = []

    phase_rows = value.get("analysis_phases")
    if isinstance(phase_rows, list):
        for phase in phase_rows:
            if not isinstance(phase, dict):
                continue
            raw_hours = phase.get("forecast_hours")
            if (
                isinstance(raw_hours, list)
                and raw_hours
                and all(
                    not isinstance(hour, bool) and isinstance(hour, int) and hour >= 0
                    for hour in raw_hours
                )
            ):
                canonical_hours = sorted(set(int(hour) for hour in raw_hours))
                if canonical_hours != raw_hours:
                    phase["forecast_hours"] = canonical_hours
                    changes.append(f"phase={phase.get('phase_key')!r}:forecast_hours")

    collection_name = None
    rows: Any = None
    if isinstance(value.get("verification_requests"), list):
        collection_name = "verification_requests"
        rows = value.get("verification_requests")
    elif isinstance(value.get("diagnostic_checks"), list):
        collection_name = "diagnostic_checks"
        rows = value.get("diagnostic_checks")

    if isinstance(rows, list):
        for check in rows:
            if not isinstance(check, dict):
                continue
            check_key = str(check.get("check_key") or "")
            set_like_fields = [
                "phase_keys",
                "analysis_methods",
                "coverage_roles",
            ]
            if collection_name == "verification_requests":
                set_like_fields.append("signal_components")
            else:
                set_like_fields.append("required_signal_components")
            for field in set_like_fields:
                original = check.get(field)
                canonical = _stable_unique_nonempty_strings(original)
                if isinstance(original, list) and canonical != original:
                    check[field] = canonical
                    changes.append(f"check={check_key!r}:{field}")

            representative = check.get("representative_spatial_hours")
            if (
                isinstance(representative, list)
                and all(
                    not isinstance(hour, bool) and isinstance(hour, int) and hour >= 0
                    for hour in representative
                )
            ):
                canonical_representative = sorted(set(int(hour) for hour in representative))
                if canonical_representative != representative:
                    check["representative_spatial_hours"] = canonical_representative
                    changes.append(f"check={check_key!r}:representative_spatial_hours")

            claims = check.get("atomic_claims")
            if not isinstance(claims, list):
                continue
            normalized_claims: list[Any] = []
            for claim in claims:
                if not isinstance(claim, dict):
                    normalized_claims.append(claim)
                    continue
                for field in ("claim_components", "phase_keys"):
                    original = claim.get(field)
                    canonical = _stable_unique_nonempty_strings(original)
                    if isinstance(original, list) and canonical != original:
                        claim[field] = canonical
                        changes.append(
                            f"check={check_key!r}:claim={claim.get('claim_key')!r}:{field}"
                        )

                scope = claim.get("quantitative_scope")
                if isinstance(scope, dict):
                    region_ids = scope.get("region_ids")
                    canonical_regions = _stable_unique_nonempty_strings(region_ids)
                    if isinstance(region_ids, list) and canonical_regions != region_ids:
                        scope["region_ids"] = canonical_regions
                        changes.append(
                            f"check={check_key!r}:claim={claim.get('claim_key')!r}:region_ids"
                        )

                    comparison = scope.get("comparison_forecast_hours")
                    if (
                        isinstance(comparison, list)
                        and all(
                            not isinstance(hour, bool)
                            and isinstance(hour, int)
                            and hour >= 0
                            for hour in comparison
                        )
                    ):
                        canonical_comparison = list(dict.fromkeys(int(hour) for hour in comparison))
                        if len(canonical_comparison) == 2 and canonical_comparison != comparison:
                            scope["comparison_forecast_hours"] = canonical_comparison
                            changes.append(
                                f"check={check_key!r}:claim={claim.get('claim_key')!r}:comparison_forecast_hours"
                            )
                normalized_claims.append(claim)

            check["atomic_claims"] = normalized_claims
            _canonicalize_compact_quantitative_atomic_claims(check)

            seen_claims: set[str] = set()
            retained_claims: list[Any] = []
            for claim in check.get("atomic_claims", []):
                if not isinstance(claim, dict):
                    retained_claims.append(claim)
                    continue
                signature = _atomic_claim_normalization_signature(claim)
                if signature in seen_claims:
                    changes.append(
                        f"check={check_key!r}:duplicate_atomic_claim={claim.get('claim_key')!r}"
                    )
                    continue
                seen_claims.add(signature)
                retained_claims.append(claim)
            check["atomic_claims"] = retained_claims

    hypotheses = value.get("hypotheses")
    if isinstance(hypotheses, list):
        for hypothesis in hypotheses:
            if not isinstance(hypothesis, dict):
                continue
            for field in ("supporting_check_keys", "opposing_check_keys"):
                original = hypothesis.get(field)
                canonical = _stable_unique_nonempty_strings(original)
                if isinstance(original, list) and canonical != original:
                    hypothesis[field] = canonical
                    changes.append(
                        f"hypothesis={hypothesis.get('hypothesis_key')!r}:{field}"
                    )

    questions = value.get("forecaster_questions")
    if isinstance(questions, list):
        retained_questions: list[Any] = []
        seen_questions: set[str] = set()
        for question in questions:
            if not isinstance(question, str):
                retained_questions.append(question)
                continue
            stripped = question.strip()
            signature = re.sub(r"\s+", " ", stripped).casefold()
            if signature in seen_questions:
                changes.append("forecaster_questions:exact_duplicate")
                continue
            seen_questions.add(signature)
            retained_questions.append(stripped)
        value["forecaster_questions"] = retained_questions

    return changes

# Original func/step04/pass1_engine.py:14840
def _normalize_compact_candidate_plan_contracts(
    value: dict[str, Any],
    *,
    all_phase_rows: Iterable[dict[str, Any]],
    available_hours: Iterable[int],
    candidate_id: str,
) -> dict[str, Any]:
    """Central deterministic normalization layer before strict Step04 validation.

    The LLM owns meteorological intent. Python owns only representation-level
    normalization that cannot change that intent. The same representation helper
    is used before provider-schema validation and here after Check repair merges,
    so recurring structural errors have one implementation path.

    In addition to representation normalization, this stage may connect an
    explicit/quantitative forecast hour to an already-declared *unique* phase.
    It never invents forecast hours/phases, changes region identity, changes
    comparator direction, or converts one meteorological hypothesis into another.
    Ambiguous cases remain visible to strict validation and bounded regeneration.
    """
    phase_rows = list(all_phase_rows)
    allowed_hours = {int(value) for value in available_hours}
    changes = _normalize_candidate_plan_representation_fields(value)
    unresolved_text_rows: list[str] = []
    unresolved_quant_rows: list[str] = []

    for check in _compact_plan_rows(value, "diagnostic_checks"):
        check_key = str(check.get("check_key") or "")
        unresolved_text = _canonicalize_check_phase_keys_for_explicit_hours(
            check,
            all_phase_rows=phase_rows,
            available_hours=allowed_hours,
            candidate_id=candidate_id,
        )
        if unresolved_text:
            unresolved_text_rows.append(
                f"check_key={check_key!r} missing={unresolved_text}"
            )

        unresolved_quant = _canonicalize_quantitative_scope_phase_keys(
            check,
            all_phase_rows=phase_rows,
            candidate_id=candidate_id,
        )
        if unresolved_quant:
            unresolved_quant_rows.append(
                f"check_key={check_key!r} missing_or_ambiguous={unresolved_quant}"
            )

    if changes:
        _log_step04_advisories(
            "candidate_plan.contract_normalization",
            list(dict.fromkeys(changes)),
        )
        logging.info(
            "step04 candidate-plan deterministic normalization candidate_id=%s count=%s version=%s",
            candidate_id,
            len(changes),
            COMPACT_CANDIDATE_PLAN_NORMALIZATION_VERSION,
        )
    if unresolved_text_rows:
        _log_step04_advisories(
            "candidate_plan.contract_normalization.text_phase_alignment",
            [
                "diagnostic check text mentions forecast hours not represented by a unique analysis phase "
                + row
                for row in unresolved_text_rows
            ],
        )
    if unresolved_quant_rows:
        _log_step04_advisories(
            "candidate_plan.contract_normalization.quantitative_phase_alignment",
            [
                "quantitative_scope forecast hours are not represented by one unique existing analysis phase "
                + row
                for row in unresolved_quant_rows
            ],
        )
    return value

# Original func/step04/pass1_engine.py:14922
def _quantitative_feature_ids_for_product(
    product_id: str,
    support_catalog: dict[str, Any],
) -> list[str]:
    support = support_catalog.get("quantitative_excerpt_support", {})
    mapping = support.get("feature_ids_by_product", {}) if isinstance(support, dict) else {}
    values = mapping.get(product_id, []) if isinstance(mapping, dict) else []
    return list(dict.fromkeys(str(value) for value in values if str(value)))

# Original func/step04/pass1_engine.py:14932
def _quantitative_feature_ids_for_check(
    diagnostic_id: str,
    product_id: str,
    support_catalog: dict[str, Any],
) -> list[str]:
    """Prefer the diagnostic/product semantic contract over broad product features.

    A logical overview product may expose several quantitative fields, while one
    Step05 diagnostic is intended to validate only a specific observable. The
    capability contract therefore owns any narrowed feature list.
    """
    profile = _capability_profile_for_check(
        support_catalog,
        diagnostic_id=str(diagnostic_id),
        product_id=str(product_id),
    )
    values = profile.get("quantitative_feature_ids") if isinstance(profile, dict) else None
    if isinstance(values, list) and values:
        return list(dict.fromkeys(str(value) for value in values if str(value)))
    return _quantitative_feature_ids_for_product(product_id, support_catalog)

# Original func/step04/pass1_engine.py:14954
def _canonicalize_compact_quantitative_atomic_claims(check: dict[str, Any]) -> None:
    """Canonicalize only semantically redundant quantitative transport fields.

    Python may normalize fields that are fully implied by the declared claim and
    therefore cannot change the meteorological meaning. Material model choices
    (feature identity, target region, forecast-hour set, or comparator direction)
    remain visible to the semantic validator.

    Two narrow normalizations are supported:

    1. ``quantitative_scope.forecast_hours`` is a time-set contract, so valid
       non-negative integer hours may be sorted and de-duplicated when at least
       two unique hours remain.  This does not alter comparator direction;
       explicit left/right ordering is carried by ``comparison_forecast_hours``.

    2. ``increase/decrease/strengthen/weaken`` with exactly two chronological
       ``forecast_hours`` may occasionally repeat the same two hours in
       ``comparison_forecast_hours``. The latter is redundant because temporal
       change is defined by chronological ``forecast_hours``; it is normalized
       to ``null`` only when the list is exactly identical. Reversed, partial,
       or otherwise different comparison lists are not changed.

    3. ``majority + not_strengthen`` on 925 hPa convergence has a deterministic
       aggregation contract. Region scope, statistic, hour ordering, and a
       redundant same-hour comparison list are canonicalized as before.
    """
    claims = check.get("atomic_claims")
    if not isinstance(claims, list):
        return
    advisories: list[str] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        scope = claim.get("quantitative_scope")
        if not isinstance(scope, dict):
            continue

        state = str(claim.get("expected_state") or "")
        changed: list[str] = []

        # ``forecast_hours`` is an executable time-set contract: it represents
        # the forecast hours used by the claim and must be unique and
        # chronological.  Ordering/duplication is transport syntax rather than
        # meteorological intent because comparator direction, when relevant, is
        # carried separately by ``comparison_forecast_hours``.  Therefore we may
        # deterministically sort and de-duplicate a valid integer list *only*
        # when at least two unique hours remain.  A list collapsing to fewer
        # than two unique hours is semantically incomplete and is intentionally
        # left for model regeneration/validation.
        hours = scope.get("forecast_hours")
        if (
            isinstance(hours, list)
            and all(
                not isinstance(value, bool) and isinstance(value, int) and value >= 0
                for value in hours
            )
        ):
            canonical_hours = sorted(set(hours))
            if len(canonical_hours) >= 2 and canonical_hours != hours:
                scope["forecast_hours"] = canonical_hours
                hours = canonical_hours
                changed.append("forecast_hours")

        comparison_hours = scope.get("comparison_forecast_hours")
        if (
            state in {"increase", "decrease", "strengthen", "weaken"}
            and isinstance(hours, list)
            and len(hours) == 2
            and all(not isinstance(value, bool) and isinstance(value, int) and value >= 0 for value in hours)
            and hours == sorted(set(hours))
            and isinstance(comparison_hours, list)
            and all(not isinstance(value, bool) and isinstance(value, int) and value >= 0 for value in comparison_hours)
            and comparison_hours == hours
        ):
            scope["comparison_forecast_hours"] = None
            changed.append("comparison_forecast_hours")
            comparison_hours = None

        if (
            claim.get("scope_quantifier") == "majority"
            and state == "not_strengthen"
            and str(scope.get("feature_id") or "") == "convergence925_1e5_s"
        ):
            if (
                isinstance(hours, list)
                and len(hours) == 2
                and all(not isinstance(value, bool) and isinstance(value, int) and value >= 0 for value in hours)
                and len(set(hours)) == 2
            ):
                chronological_hours = sorted(hours)
                if chronological_hours != hours:
                    scope["forecast_hours"] = chronological_hours
                    hours = chronological_hours
                    changed.append("forecast_hours")

            if scope.get("region_scope") != "candidate_overlap":
                scope["region_scope"] = "candidate_overlap"
                changed.append("region_scope")
            if scope.get("region_ids") != []:
                scope["region_ids"] = []
                changed.append("region_ids")
            if scope.get("statistic") != "mean":
                scope["statistic"] = "mean"
                changed.append("statistic")

            comparison_hours = scope.get("comparison_forecast_hours")
            canonical_hours = scope.get("forecast_hours")
            if comparison_hours is not None:
                if (
                    isinstance(comparison_hours, list)
                    and len(comparison_hours) == 2
                    and isinstance(canonical_hours, list)
                    and len(canonical_hours) == 2
                    and set(comparison_hours) == set(canonical_hours)
                ):
                    scope["comparison_forecast_hours"] = None
                    changed.append("comparison_forecast_hours")
                # For this majority contract the comparison field is not part
                # of the semantics; the same two hours in either order are
                # redundant. Different hours remain visible to the validator.

        if changed:
            advisories.append(
                f"check_key={check.get('check_key')!r} claim_key={claim.get('claim_key')!r} "
                f"canonicalized={','.join(dict.fromkeys(changed))}"
            )

    if advisories:
        _log_step04_advisories("candidate_plan.contract_normalization.quantitative_scope", advisories)

# Original func/step04/pass1_engine.py:15086
def _validate_quantitative_region_identity(
    *,
    check_key: str,
    claim_key: str,
    statement: str,
    region_id: str,
    selectable_operational_regions: list[dict[str, Any]] | None,
) -> None:
    """Reject a specific quantitative claim whose named region contradicts region_id.

    The structured region_id is the executable selector.  If the natural-language
    claim explicitly names an operational region, that name must resolve to the
    same region_id.  Claims using generic wording such as ``해당 권역`` are left
    unchanged because there is no textual identity to validate.
    """
    text = str(statement or "").strip()
    if not text or not region_id:
        return
    mentioned_ids: set[str] = set()
    for item in selectable_operational_regions or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("region_id") or "")
        if not item_id:
            continue
        names = [str(item.get("name_ko") or "").strip()]
        aliases = item.get("aliases")
        if isinstance(aliases, list):
            names.extend(str(value).strip() for value in aliases if isinstance(value, str))
        for name in names:
            if len(name) >= 2 and name in text:
                mentioned_ids.add(item_id)
                break
    if mentioned_ids and mentioned_ids != {region_id}:
        region_names = {
            str(item.get("region_id") or ""): str(item.get("name_ko") or "").strip()
            for item in selectable_operational_regions or []
            if isinstance(item, dict) and str(item.get("region_id") or "")
        }
        selected_name = region_names.get(region_id, "")
        mentioned_regions = [
            {"region_id": item_id, "name_ko": region_names.get(item_id, "")}
            for item_id in sorted(mentioned_ids)
        ]
        raise ValueError(
            "QUANTITATIVE_SCOPE_REGION_IDENTITY_MISMATCH "
            f"check_key={check_key} claim_key={claim_key} "
            f"selected_region_id={region_id!r} selected_region_name_ko={selected_name!r} "
            f"natural_language_regions={mentioned_regions!r}; "
            "repair=regenerate the complete verification request so the structured region_id and every explicit operational-region name refer to one consistent region; "
            "if the structured region_id is retained, use selected_region_name_ko as its official name"
        )

# Original func/step04/pass1_engine.py:15140
def _validate_quantitative_comparison_identity(
    *,
    check_key: str,
    claim_key: str,
    statement: str,
    comparison_hours: list[int],
) -> None:
    """Reject reversed left/right F-hours in explicitly two-hour Korean comparisons.

    Handles both `F018이 F006보다 크다` and `F006보다 F018에 크다`.
    The latter was not detected by the former before-`보다`-only parser.
    No automatic comparator reversal is permitted.
    """
    expected = inconsistent_pairwise_hour_order(statement, comparison_hours)
    if expected is not None:
        raise ValueError(
            "QUANTITATIVE_SCOPE_COMPARISON_IDENTITY_MISMATCH "
            f"check_key={check_key} claim_key={claim_key} "
            f"claim_hour_order={list(expected)} comparison_forecast_hours={comparison_hours}; "
            "repair=regenerate the verification request with comparison_forecast_hours=[left_hour,right_hour] "
            "matching the two sides of the Korean proposition"
        )

# Original func/step04/pass1_engine.py:15164
def _validate_compact_quantitative_atomic_claims(
    check: dict[str, Any],
    *,
    support_catalog: dict[str, Any],
    candidate: dict[str, Any],
    selectable_operational_regions: list[dict[str, Any]] | None,
    referenced_hours: set[int],
    phase_hours_by_key: dict[str, set[int]],
) -> None:
    """Validate the Step04 quantitative claim metadata before expansion.

    The transport schema validates field types only.  This semantic preflight
    additionally guarantees that a model-declared quantitative feature is
    executable by the selected diagnostic/product, that specific claims target
    one actually overlapping operational region, and that comparison hours are
    owned by the Check.  Errors are raised during the model-repair stage rather
    than surfacing later as an unrecoverable expansion failure.
    """
    claims = check.get("atomic_claims")
    if not isinstance(claims, list) or not claims:
        return
    claims = check.get("atomic_claims")
    quantitative_claims = [
        claim for claim in claims
        if isinstance(claim, dict) and claim.get("quantitative_scope") is not None
    ]
    # Step07 *requires* an executable quantitative contract for these
    # specific states even when Step04 was formerly shadow-only. Reject during
    # AI plan generation, not after expensive Step05/07 execution. No attempt
    # is made to change the meteorological hypothesis or fabricate a scope.
    specific_states_enforced = {
        "greater_than", "less_than", "increase", "decrease", "strengthen", "weaken",
    }
    # Only one-claim Checks are subject to the existing Step07 specific
    # quantitative executor. Multi-claim spatial/image interpretation remains
    # shadow-validated; it must not be silently reinterpreted as numeric.
    enforced_single = len(claims) == 1 and isinstance(claims[0], dict)
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        state = str(claim.get("expected_state") or "")
        quantifier = str(claim.get("scope_quantifier") or "")
        if enforced_single and quantifier == "specific" and state in specific_states_enforced and not isinstance(claim.get("quantitative_scope"), dict):
            raise ValueError(
                "QUANTITATIVE_SCOPE_CONTRACT_INVALID "
                f"check_key={check.get('check_key')} claim_key={claim.get('claim_key')} "
                f"specific expected_state={state!r} requires an executable quantitative_scope; "
                "repair=regenerate the entire verification request with a supported quantitative feature, "
                "single operational region, chronological hours and statistic. "
                "If the original evidence is only visual, write a genuinely qualitative hypothesis "
                "with the appropriate spatial/area state and quantifier; do not fabricate values or alter the physical meaning."
            )
        if enforced_single and quantifier == "majority" and state == "not_strengthen":
            if any(opposite_majority_not_strengthen_wording(text) for text in (
                claim.get("claim_statement"), check.get("expected_signal")
            )):
                raise ValueError(
                    "QUANTITATIVE_MAJORITY_POLARITY_MISMATCH "
                    f"check_key={check.get('check_key')} claim_key={claim.get('claim_key')}; "
                    "the text claims majority convergence strengthening, but expected_state=not_strengthen. "
                    "repair=regenerate a coherent verification request; do not negate the original hypothesis "
                    "just to satisfy the available quantitative executor"
                )
        if quantifier == "majority" and state == "not_strengthen" and not selectable_operational_regions:
            raise ValueError(
                "CANDIDATE_QUANTITATIVE_SCOPE_UNAVAILABLE "
                f"check_key={check.get('check_key')} claim_key={claim.get('claim_key')} "
                "majority not_strengthen cannot be evaluated because the immutable candidate_area "
                "overlaps no Step05-eligible operational_land or marine_macro region; "
                "repair_unit=candidate_plan_chunk"
            )
        if enforced_single and quantifier == "majority" and state == "not_strengthen" and not isinstance(claim.get("quantitative_scope"), dict):
            raise ValueError(
                "QUANTITATIVE_SCOPE_CONTRACT_INVALID "
                f"check_key={check.get('check_key')} claim_key={claim.get('claim_key')} "
                "majority not_strengthen requires a two-hour 925 hPa convergence quantitative_scope; "
                "repair=regenerate the complete verification request"
            )
    if not quantitative_claims:
        return
    if len(claims) != 1 or len(quantitative_claims) != 1:
        raise ValueError(
            "QUANTITATIVE_ATOMICITY_INVALID "
            f"check_key={check.get('check_key')} atomic_claim_count={len(claims)} "
            f"quantitative_claim_count={len(quantitative_claims)}; "
            "repair=a verification_request with an enforced quantitative_scope must contain exactly one atomic claim; split other criteria into separate verification_requests"
        )

    methods = {str(value) for value in check.get("analysis_methods", [])}
    if not (methods & REGIONAL_ANALYSIS_METHODS):
        raise ValueError(
            "QUANTITATIVE_SCOPE_EXECUTION_UNAVAILABLE "
            f"check_key={check.get('check_key')} analysis_methods={sorted(methods)}; "
            "repair=a quantitative_scope requires a verification mode that produces operational-region quantitative evidence "
            "(regional_intensity, temporal_change, or precipitation_amount), otherwise set quantitative_scope=null"
        )

    diagnostic_id = str(check.get("diagnostic_id") or "")
    role = str(check.get("variable_role") or "")
    components = [
        str(value) for value in check.get("required_signal_components", []) if str(value)
    ]
    product_id = _preferred_product_id_for_check(
        diagnostic_id, role, components, support_catalog, candidate
    )
    allowed_features = (
        []
        if diagnostic_id in SPATIAL_ONLY_DIAGNOSTIC_IDS
        else _quantitative_feature_ids_for_check(diagnostic_id, product_id, support_catalog)
    )
    selectable_ids = {
        str(item.get("region_id"))
        for item in selectable_operational_regions or []
        if isinstance(item, dict) and item.get("region_id")
    }
    specific_states = {
        "greater_than", "less_than", "increase", "decrease",
        "strengthen", "weaken", "peak_at",
    }

    for claim in quantitative_claims:
        scope = claim.get("quantitative_scope")
        if not isinstance(scope, dict):
            raise ValueError(
                "QUANTITATIVE_SCOPE_CONTRACT_INVALID quantitative_scope must be object or null"
            )
        claim_key = str(claim.get("claim_key") or "")
        state = str(claim.get("expected_state") or "")
        quantifier = str(claim.get("scope_quantifier") or "")
        feature_id = str(scope.get("feature_id") or "")
        region_scope = str(scope.get("region_scope") or "")
        region_ids = scope.get("region_ids")
        hours = scope.get("forecast_hours")
        comparison_hours = scope.get("comparison_forecast_hours")
        statistic = str(scope.get("statistic") or "")

        if feature_id not in allowed_features:
            raise ValueError(
                "QUANTITATIVE_SCOPE_FEATURE_UNAVAILABLE "
                f"check_key={check.get('check_key')} claim_key={claim_key} "
                f"feature_id={feature_id!r} executable_features={allowed_features}; "
                "repair=use the exact quantitative feature produced by this verification request, "
                "or set quantitative_scope=null when the claim is qualitative"
            )
        if (
            not isinstance(hours, list)
            or len(hours) < 2
            or any(isinstance(hour, bool) or not isinstance(hour, int) or hour < 0 for hour in hours)
            or hours != sorted(set(hours))
        ):
            raise ValueError(
                "QUANTITATIVE_SCOPE_CONTRACT_INVALID "
                f"check_key={check.get('check_key')} claim_key={claim_key} "
                "forecast_hours must be unique chronological forecast hours"
            )
        claim_phase_keys = [
            str(value).strip()
            for value in claim.get("phase_keys", [])
            if str(value).strip()
        ]
        claim_referenced_hours = {
            hour
            for phase_key in claim_phase_keys
            for hour in phase_hours_by_key.get(phase_key, set())
        }
        missing_request_hours = sorted(set(hours) - referenced_hours)
        missing_claim_hours = sorted(set(hours) - claim_referenced_hours)
        if missing_request_hours or missing_claim_hours:
            missing_hours = sorted(set(missing_request_hours) | set(missing_claim_hours))
            covering_phase_keys = {
                hour: sorted(
                    phase_key
                    for phase_key, phase_hours in phase_hours_by_key.items()
                    if hour in phase_hours
                )
                for hour in missing_hours
            }
            raise ValueError(
                "QUANTITATIVE_SCOPE_PHASE_MISMATCH "
                f"check_key={check.get('check_key')} claim_key={claim_key} "
                f"forecast_hours={hours} request_phase_keys={list(check.get('phase_keys', []))} "
                f"claim_phase_keys={claim_phase_keys} referenced_phase_hours={sorted(referenced_hours)} "
                f"claim_referenced_phase_hours={sorted(claim_referenced_hours)} "
                f"missing_forecast_hours={missing_hours} declared_covering_phase_keys={covering_phase_keys} "
                "repair_unit=candidate_plan_chunk; "
                "repair=regenerate the candidate-plan chunk so every quantitative forecast hour is owned by "
                "the verification request and atomic claim phase_keys; do not delete a forecast hour merely to fit the current phase graph"
            )
        if statistic not in {"min", "max", "mean", "p95"}:
            raise ValueError(
                "QUANTITATIVE_SCOPE_CONTRACT_INVALID "
                f"check_key={check.get('check_key')} claim_key={claim_key} unsupported statistic={statistic!r}"
            )

        if quantifier == "specific":
            if state not in specific_states:
                raise ValueError(
                    "QUANTITATIVE_SCOPE_CONTRACT_INVALID "
                    f"check_key={check.get('check_key')} claim_key={claim_key} "
                    f"specific quantitative_scope does not support expected_state={state!r}; "
                    "repair=set quantitative_scope=null for qualitative claims"
                )
            if region_scope != "explicit_regions" or not isinstance(region_ids, list) or len(region_ids) != 1:
                raise ValueError(
                    "QUANTITATIVE_SCOPE_CONTRACT_INVALID "
                    f"check_key={check.get('check_key')} claim_key={claim_key} "
                    "specific quantitative_scope requires region_scope=explicit_regions and exactly one region_id"
                )
            region_id = str(region_ids[0] or "")
            if selectable_ids and region_id not in selectable_ids:
                raise ValueError(
                    "QUANTITATIVE_SCOPE_REGION_UNAVAILABLE "
                    f"check_key={check.get('check_key')} claim_key={claim_key} region_id={region_id!r} "
                    f"selectable_region_ids={sorted(selectable_ids)}; "
                    "repair=select one operational region that overlaps the candidate area"
                )
            _validate_quantitative_region_identity(
                check_key=str(check.get("check_key") or ""),
                claim_key=claim_key,
                statement=str(claim.get("claim_statement") or check.get("expected_signal") or ""),
                region_id=region_id,
                selectable_operational_regions=selectable_operational_regions,
            )
            if state in {"greater_than", "less_than", "increase", "decrease", "strengthen", "weaken"}:
                for proposition in dict.fromkeys((
                    str(claim.get("claim_statement") or ""),
                    str(check.get("expected_signal") or ""),
                )):
                    fixed_cutoff = absolute_threshold_in_hour_comparison(proposition)
                    if fixed_cutoff is not None:
                        raise ValueError(
                            "QUANTITATIVE_ABSOLUTE_THRESHOLD_CONTRACT_MISMATCH "
                            f"check_key={check.get('check_key')} claim_key={claim_key} "
                            f"fixed_cutoff={fixed_cutoff!r} expected_state={state!r}; "
                            "repair=relative time comparison quantitative_scope cannot represent a fixed numeric threshold; "
                            "regenerate the verification request preserving the original threshold hypothesis "
                            "and using an actually supported evidence contract; do not substitute an F-hour comparison"
                        )
            if state in {"greater_than", "less_than"}:
                if (
                    not isinstance(comparison_hours, list)
                    or len(comparison_hours) != 2
                    or any(isinstance(hour, bool) or not isinstance(hour, int) for hour in comparison_hours)
                    or len(set(comparison_hours)) != 2
                    or any(hour not in hours for hour in comparison_hours)
                ):
                    raise ValueError(
                        "QUANTITATIVE_SCOPE_COMPARISON_INVALID "
                        f"check_key={check.get('check_key')} claim_key={claim_key} "
                        "greater_than/less_than requires comparison_forecast_hours=[left_hour,right_hour] "
                        "with both hours contained in chronological forecast_hours"
                    )
                for proposition in dict.fromkeys((
                    str(claim.get("claim_statement") or ""),
                    str(check.get("expected_signal") or ""),
                )):
                    if proposition:
                        _validate_quantitative_comparison_identity(
                            check_key=str(check.get("check_key") or ""),
                            claim_key=claim_key,
                            statement=proposition,
                            comparison_hours=comparison_hours,
                        )
            elif comparison_hours is not None:
                raise ValueError(
                    "QUANTITATIVE_SCOPE_COMPARISON_INVALID "
                    f"check_key={check.get('check_key')} claim_key={claim_key} "
                    f"expected_state={state!r} requires comparison_forecast_hours=null"
                )
            continue

        if quantifier == "majority" and state == "not_strengthen":
            if (
                feature_id != "convergence925_1e5_s"
                or region_scope != "candidate_overlap"
                or region_ids != []
                or len(hours) != 2
                or comparison_hours is not None
                or statistic != "mean"
            ):
                raise ValueError(
                    "QUANTITATIVE_SCOPE_CONTRACT_INVALID "
                    f"check_key={check.get('check_key')} claim_key={claim_key}; "
                    "majority not_strengthen is currently supported only for convergence925_1e5_s, "
                    "region_scope=candidate_overlap, region_ids=[], two chronological hours, mean statistic; "
                    f"actual_feature_id={feature_id!r} actual_region_scope={region_scope!r} "
                    f"actual_region_ids={region_ids!r} actual_forecast_hours={hours!r} "
                    f"actual_comparison_forecast_hours={comparison_hours!r} actual_statistic={statistic!r}"
                )
            continue

        raise ValueError(
            "QUANTITATIVE_SCOPE_CONTRACT_INVALID "
            f"check_key={check.get('check_key')} claim_key={claim_key} "
            f"unsupported quantitative contract quantifier={quantifier!r} expected_state={state!r}; "
            "repair=set quantitative_scope=null unless the claim matches a supported deterministic contract"
        )

# Original func/step04/pass1_engine.py:15463
def _analysis_request_merge_key(request: dict[str, Any]) -> tuple[Any, ...]:
    requested_domain = request.get("requested_domain")
    domain_key = (
        json.dumps(requested_domain, sort_keys=True, separators=(",", ":"))
        if isinstance(requested_domain, dict)
        else None
    )
    cross_section_line = request.get("cross_section_line")
    line_key = (
        json.dumps(cross_section_line, sort_keys=True, separators=(",", ":"))
        if isinstance(cross_section_line, dict)
        else None
    )
    return (
        str(request.get("candidate_id")),
        str(request.get("data_type")),
        str(request.get("product_id")),
        str(request.get("target_id")),
        domain_key,
        line_key,
    )

# Original func/step04/pass1_engine.py:15486
def _merge_output_specs(first: Any, second: Any) -> dict[str, Any] | None:
    if not isinstance(first, dict) and not isinstance(second, dict):
        return None
    left = first if isinstance(first, dict) else {}
    right = second if isinstance(second, dict) else {}
    statistics = list(
        dict.fromkeys(
            [str(value) for value in left.get("statistics", [])]
            + [str(value) for value in right.get("statistics", [])]
        )
    )
    return {
        "statistics": statistics,
        "ranking_statistic": str(
            left.get("ranking_statistic") or right.get("ranking_statistic") or "p95"
        ),
        "top_n": max(int(left.get("top_n", 0)), int(right.get("top_n", 0)), 1),
        "include_time_series": bool(
            left.get("include_time_series") or right.get("include_time_series")
        ),
        "include_max_location": bool(
            left.get("include_max_location") or right.get("include_max_location")
        ),
    }

# Original func/step04/pass1_engine.py:15512
def _request_primary_rank(request: dict[str, Any]) -> tuple[int, int, str]:
    """Prefer a concrete field diagnostic over a derived temporal view."""
    diagnostic_id = str(request.get("diagnostic_id", ""))
    derived_rank = 1 if diagnostic_id == "temporal_evolution" else 0
    priority_rank = 0 if str(request.get("priority")) == "required" else 1
    return (derived_rank, priority_rank, str(request.get("check_id", "")))

# Original func/step04/pass1_engine.py:15520
def _merge_analysis_data_requests(
    requests: list[dict[str, Any]],
    *,
    candidate_number: str,
    non_mergeable_check_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Merge repeated physical outputs while preserving explicitly isolated Checks.

    ``non_mergeable_check_ids`` is execution-only state.  It is intentionally not
    written into the JSON contract so the canonical schemas remain unchanged.
    A request linked to one of these Checks receives a unique merge key and can
    therefore never be absorbed into, or absorb, another physical request.
    """
    isolated_ids = {str(value) for value in (non_mergeable_check_ids or set()) if str(value)}
    merged_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for source_index, raw in enumerate(requests):
        request = copy.deepcopy(raw)
        source_check_ids = [
            str(value)
            for value in request.get("source_check_ids", [request.get("check_id")])
            if str(value)
        ]
        request["source_check_ids"] = list(dict.fromkeys(source_check_ids))
        key = _analysis_request_merge_key(request)
        if isolated_ids.intersection(request["source_check_ids"]):
            key = (*key, "__NON_MERGEABLE_CHECK__", source_index)
        existing = merged_by_key.get(key)
        if existing is None:
            merged_by_key[key] = request
            order.append(key)
            continue
        if _request_primary_rank(request) < _request_primary_rank(existing):
            existing["check_id"] = request.get("check_id")
            existing["diagnostic_id"] = request.get("diagnostic_id")
            existing["purpose"] = request.get("purpose")
        existing["source_check_ids"] = list(
            dict.fromkeys(
                [*existing.get("source_check_ids", []), *request.get("source_check_ids", [])]
            )
        )
        existing["forecast_hours"] = sorted(
            {int(value) for value in existing.get("forecast_hours", [])}
            | {int(value) for value in request.get("forecast_hours", [])}
        )
        existing["phase_ids"] = list(
            dict.fromkeys(
                [str(value) for value in existing.get("phase_ids", [])]
                + [str(value) for value in request.get("phase_ids", [])]
            )
        )
        existing["analysis_methods"] = list(
            dict.fromkeys(
                [str(value) for value in existing.get("analysis_methods", [])]
                + [str(value) for value in request.get("analysis_methods", [])]
            )
        )
        existing["feature_ids"] = list(
            dict.fromkeys(
                [str(value) for value in existing.get("feature_ids", [])]
                + [str(value) for value in request.get("feature_ids", [])]
            )
        )
        if str(request.get("priority")) == "required":
            existing["priority"] = "required"
        merged_spec = _merge_output_specs(existing.get("output_spec"), request.get("output_spec"))
        if merged_spec is not None:
            existing["output_spec"] = merged_spec
    merged = [merged_by_key[key] for key in order]
    for index, request in enumerate(merged, start=1):
        request["request_id"] = f"REQ_CAND{candidate_number}_{index:03d}"
    return merged

# Original func/step04/pass1_engine.py:15594
def _build_check_intent_contract(
    row: dict[str, Any],
    *,
    check_id: str,
    phase_ids: list[str],
) -> dict[str, Any]:
    """Preserve the Step04 meteorological verification intent without reinterpretation."""
    verification_mode = str(
        row.get("verification_mode")
        or _verification_mode_from_diagnostic_check(row)
    )
    source_intents = row.get("_source_intents")
    if not isinstance(source_intents, list) or not source_intents:
        source_intents = [_source_check_intent_snapshot(row)]
    source_intents = [copy.deepcopy(item) for item in source_intents if isinstance(item, dict)]
    return {
        "contract_version": CHECK_INTENT_CONTRACT_VERSION,
        "check_id": check_id,
        "source_intents": source_intents,
        "merged_source_intent_count": len(source_intents),
        "source_check_key": str(row.get("check_key") or ""),
        "signal_components": list(dict.fromkeys(
            str(value)
            for value in row.get("required_signal_components", [])
            if str(value)
        )),
        "verification_mode": verification_mode,
        "domain_scope": str(row.get("domain_scope") or "candidate"),
        "spatial_relation_scope": str(row.get("spatial_relation_scope") or "not_applicable"),
        "source_phase_keys": [str(value) for value in row.get("phase_keys", []) if str(value)],
        "phase_ids": list(phase_ids),
        "representative_spatial_hours": [
            int(value)
            for value in row.get("representative_spatial_hours", [])
            if isinstance(value, int) and not isinstance(value, bool)
        ],
        "direct_precipitation_anchor": _is_system_direct_precipitation_anchor_row(row),
        "expected_signal": row.get("expected_signal"),
        "decision_relevance": row.get("decision_relevance"),
        "purpose": row.get("purpose"),
        "priority": row.get("priority"),
        "evidence_role": row.get("evidence_role"),
        "coverage_roles": _effective_compact_check_coverage_roles(row),
        "cross_section_line": _cross_section_line_signature(row.get("cross_section_line")),
        "cross_section_strategy": _normalized_cross_section_strategy(row.get("cross_section_strategy")),
        "reference_band_axis": _reference_band_axis_signature(row.get("reference_band_axis")),
    }

# Original func/step04/pass1_engine.py:15663
def _shadow_validate_check_capability(
    *,
    check_id: str,
    intent_contract: dict[str, Any],
    capability_profile: dict[str, Any],
) -> dict[str, Any]:
    """Audit intent/execution compatibility without changing operational decisions."""
    warning_codes: list[str] = []
    if not capability_profile:
        warning_codes.append("CAPABILITY_PROFILE_MISSING")
    else:
        mode = str(intent_contract.get("verification_mode") or "")
        supported_modes = {
            str(value)
            for value in capability_profile.get("supported_verification_modes", [])
            if str(value)
        }
        if supported_modes and mode not in supported_modes:
            warning_codes.append("VERIFICATION_MODE_UNSUPPORTED_BY_CAPABILITY")
        required = {
            str(value)
            for value in intent_contract.get("signal_components", [])
            if str(value)
        }
        available = {
            str(value)
            for value in capability_profile.get("signal_components", [])
            if str(value)
        }
        if available and not required.issubset(available):
            warning_codes.append("SIGNAL_COMPONENT_UNSUPPORTED_BY_CAPABILITY")
        if not capability_profile.get("observable_ids"):
            warning_codes.append("CAPABILITY_OBSERVABLES_MISSING")
    status = "pass" if not warning_codes else "warning"
    result = {
        "mode": CAPABILITY_VALIDATION_MODE,
        "status": status,
        "warning_codes": warning_codes,
    }
    if warning_codes:
        logging.warning(
            "STEP04_CAPABILITY_WARNING check_id=%s codes=%s",
            check_id,
            ",".join(warning_codes),
        )
    return result

# Original func/step04/pass1_engine.py:16126
def split_payload_diagnostics(
    *,
    model: str,
    messages: list[dict[str, Any]],
    input_value: dict[str, Any],
    response_schema: dict[str, Any],
    schema_name: str,
    max_tokens: int,
    temperature: float,
    top_p: float,
    enable_thinking: bool,
) -> dict[str, int]:
    payload = build_http_payload(
        model=model,
        messages=messages,
        response_schema=response_schema,
        schema_name=schema_name,
        structured_output=True,
        enable_thinking=enable_thinking,
        extensions=True,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
    )
    return {
        "http_json_utf8_bytes": json_utf8_bytes(payload),
        "messages_utf8_bytes": json_utf8_bytes(messages),
        "input_utf8_bytes": json_utf8_bytes(input_value),
        "response_schema_utf8_bytes": json_utf8_bytes(response_schema),
    }

# Original func/step04/pass1_engine.py:16176
def _checkpoint_contract(base: dict[str, Any]) -> dict[str, Any]:
    return {**base, "fingerprint": canonical_hash(base)}

# Original func/step04/pass1_engine.py:17327
def _system_direct_precipitation_anchor_errors(
    value: dict[str, Any],
    *,
    candidate: dict[str, Any],
) -> list[str]:
    """Validate the complete system-owned Check -> request invariant."""
    if str(candidate.get("detection_basis") or "") not in {
        "direct_precipitation",
        "precursor_environment",
        "mixed",
    }:
        return []
    candidate_id = str(candidate.get("candidate_id") or "")
    plan = value.get("candidate_analysis_plan")
    requests = value.get("analysis_data_requests")
    if not isinstance(plan, dict):
        return [
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID candidate plan is missing"
        ]
    if not isinstance(requests, list):
        return [
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID analysis_data_requests is missing"
        ]

    errors: list[str] = []
    anchors = [
        check
        for check in plan.get("variable_checks", [])
        if isinstance(check, dict)
        and _is_system_direct_precipitation_anchor_check(check)
    ]
    if len(anchors) != 1:
        return [
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID "
            f"candidate_id={candidate_id} expected=1 actual={len(anchors)}"
        ]
    anchor = anchors[0]
    anchor_id = str(anchor.get("check_id") or "")
    intent = anchor.get("intent_contract")
    intent = intent if isinstance(intent, dict) else {}
    claims = anchor.get("atomic_claim_contract", {}).get("canonical_claims")
    if not isinstance(claims, list):
        claims = []

    structural_expectations = {
        "required_signal_components": ["direct_precipitation"],
        "check_scope": "single_variable",
        "diagnostic_id": "detailed_spatial_map",
        "product_id": "QL_PRECIP_6H",
        "priority": "required",
    }
    for field, expected in structural_expectations.items():
        if anchor.get(field) != expected:
            errors.append(
                "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID "
                f"candidate_id={candidate_id} check_id={anchor_id} "
                f"field={field} expected={expected!r} actual={anchor.get(field)!r}"
            )
    for field, expected in (
        ("direct_precipitation_anchor", True),
        ("source_check_key", SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY),
        ("signal_components", ["direct_precipitation"]),
        ("verification_mode", "spatial_distribution"),
        ("domain_scope", "candidate"),
        ("priority", "required"),
        ("evidence_role", "supporting"),
    ):
        if intent.get(field) != expected:
            errors.append(
                "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID "
                f"candidate_id={candidate_id} check_id={anchor_id} "
                f"intent.{field} expected={expected!r} actual={intent.get(field)!r}"
            )
    if len(claims) != 1:
        errors.append(
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID "
            f"candidate_id={candidate_id} check_id={anchor_id} "
            f"atomic_claim_count expected=1 actual={len(claims)}"
        )
    else:
        claim = claims[0]
        for field, expected in (
            ("claim_components", ["direct_precipitation"]),
            ("expected_state", "present"),
            ("scope_quantifier", "at_least_one"),
        ):
            if claim.get(field) != expected:
                errors.append(
                    "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID "
                    f"candidate_id={candidate_id} check_id={anchor_id} "
                    f"claim.{field} expected={expected!r} actual={claim.get(field)!r}"
                )

    raw_peak_hours = candidate.get("peak_forecast_hours")
    expected_hours = sorted(
        {
            int(value)
            for value in raw_peak_hours
            if isinstance(value, int) and not isinstance(value, bool)
        }
    ) if isinstance(raw_peak_hours, list) else []
    expected_domain = _normalized_bbox_for_validation(candidate.get("candidate_area"))
    linked_requests = []
    for request in requests:
        if not isinstance(request, dict):
            continue
        raw_source_ids = request.get("source_check_ids")
        source_ids = (
            [str(value) for value in raw_source_ids if str(value)]
            if isinstance(raw_source_ids, list)
            else [str(request.get("check_id") or "")]
        )
        if anchor_id in source_ids:
            linked_requests.append((request, source_ids))
    if len(linked_requests) != 1:
        errors.append(
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID "
            f"candidate_id={candidate_id} check_id={anchor_id} "
            f"anchor_request_count expected=1 actual={len(linked_requests)}"
        )
        return errors

    request, source_ids = linked_requests[0]
    request_id = str(request.get("request_id") or "")
    request_expectations = {
        "source_check_ids": [anchor_id],
        "check_id": anchor_id,
        "data_type": "spatial_field",
        "diagnostic_id": "detailed_spatial_map",
        "product_id": "QL_PRECIP_6H",
        "priority": "required",
    }
    actual_values = {
        "source_check_ids": source_ids,
        **{field: request.get(field) for field in request_expectations if field != "source_check_ids"},
    }
    for field, expected in request_expectations.items():
        if actual_values[field] != expected:
            errors.append(
                "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID "
                f"candidate_id={candidate_id} request_id={request_id} "
                f"field={field} expected={expected!r} actual={actual_values[field]!r}"
            )
    actual_hours = sorted(
        {
            int(value)
            for value in request.get("forecast_hours", [])
            if isinstance(value, int) and not isinstance(value, bool)
        }
    )
    if actual_hours != expected_hours:
        errors.append(
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID "
            f"candidate_id={candidate_id} request_id={request_id} "
            f"forecast_hours expected={expected_hours} actual={actual_hours}"
        )
    actual_domain = _normalized_bbox_for_validation(request.get("requested_domain"))
    if expected_domain is None or actual_domain != expected_domain:
        errors.append(
            "SYSTEM_DIRECT_PRECIPITATION_ANCHOR_INVALID "
            f"candidate_id={candidate_id} request_id={request_id} "
            f"requested_domain expected={expected_domain!r} actual={actual_domain!r}"
        )
    return errors

# Original func/step04/pass1_engine.py:17493
def system_direct_precipitation_anchor_final_errors(
    response: dict[str, Any],
) -> list[str]:
    """Validate SYSTEM Anchor invariants on a completed Step04 document."""
    candidates = [
        item for item in response.get("candidates", []) if isinstance(item, dict)
    ]
    plans = {
        str(item.get("candidate_id") or ""): item
        for item in response.get("candidate_analysis_plans", [])
        if isinstance(item, dict)
    }
    requests = [
        item for item in response.get("analysis_data_requests", []) if isinstance(item, dict)
    ]
    errors: list[str] = []
    for candidate in candidates:
        if str(candidate.get("detection_basis") or "") not in {
            "direct_precipitation",
            "precursor_environment",
            "mixed",
        }:
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        plan = plans.get(candidate_id)
        candidate_requests = [
            request for request in requests if str(request.get("candidate_id") or "") == candidate_id
        ]
        errors.extend(
            _system_direct_precipitation_anchor_errors(
                {
                    "candidate_analysis_plan": plan,
                    "analysis_data_requests": candidate_requests,
                },
                candidate=candidate,
            )
        )
    return errors

# Original func/step04/pass1_engine.py:18031
def hierarchical_payload_diagnostics(
    *,
    model: str,
    messages: list[dict[str, Any]],
    synthesis_input: dict[str, Any],
    final_schema: dict[str, Any],
    max_tokens: int,
    temperature: float,
    top_p: float,
    enable_thinking: bool,
) -> dict[str, int]:
    payload = build_http_payload(
        model=model,
        messages=messages,
        response_schema=final_schema,
        schema_name="AIFSPass1CandidateResponse",
        structured_output=True,
        enable_thinking=enable_thinking,
        extensions=True,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
    )
    diagnostics = {
        "http_json_utf8_bytes": json_utf8_bytes(payload),
        "synthesis_input_utf8_bytes": json_utf8_bytes(synthesis_input),
        "group_summaries_utf8_bytes": sum(
            json_utf8_bytes(item) for item in synthesis_input["group_summaries"]
        ),
        "asset_index_utf8_bytes": json_utf8_bytes(synthesis_input["asset_index"]),
        "feature_index_utf8_bytes": json_utf8_bytes(synthesis_input["feature_index"]),
        "operational_region_index_utf8_bytes": json_utf8_bytes(
            synthesis_input["operational_region_index"]
        ),
        "final_schema_utf8_bytes": json_utf8_bytes(final_schema),
    }
    return diagnostics


# Step04 Python-only JSON parse, normalization and semantic validation.
def _step04_parse_normalize_validate(
    raw_text: str, *, stage: str, fixed_field_expectations,
    normalizer, semantic_validator, fixed_field_mismatches,
) -> tuple[dict[str, Any], list[str]]:
    try:
        parsed_value = extract_json_object(raw_text)
        if fixed_field_expectations is not None:
            expected_fields = (
                fixed_field_expectations(parsed_value)
                if callable(fixed_field_expectations)
                else fixed_field_expectations
            )
            mismatches = inspect_fixed_fields(parsed_value, expected_fields)
            if mismatches:
                fixed_field_mismatches.append({"stage": stage, "mismatches": mismatches})
        if normalizer is not None:
            parsed_value = normalizer(parsed_value)
        return parsed_value, semantic_validator(parsed_value)
    except Exception as exc:  # noqa: BLE001
        return {}, [str(exc)]


def _step04_is_json_parse_error(validation_errors: list[str]) -> bool:
    return any("valid JSON object" in error for error in validation_errors)


# Python-only parser and fixed-field checks moved from workflow.
def inspect_fixed_fields(value: dict[str, Any], expected: dict[str, Any]) -> list[dict[str, Any]]:
    """Record provider identity mismatches before canonical system repair."""
    if not isinstance(value, dict):
        return [{"field": "$", "expected": "object", "actual": _safe_identity_value(value)}]
    mismatches: list[dict[str, Any]] = []
    for field, expected_value in expected.items():
        present, actual_value = _nested_value(value, field)
        if not present:
            continue
        if actual_value != expected_value:
            mismatches.append(
                {
                    "field": field,
                    "expected": _safe_identity_value(expected_value),
                    "actual": _safe_identity_value(actual_value),
                    "provider_value": _safe_identity_value(actual_value),
                    "canonical_value": _safe_identity_value(expected_value),
                }
            )
    return mismatches


# Python-only parser and fixed-field checks moved from workflow.
def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip().lstrip("\ufeff")
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    candidates: list[tuple[int, dict[str, Any]]] = []
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, end = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append((end, value))
    if not candidates:
        raise ValueError("provider response does not contain a valid JSON object")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]
