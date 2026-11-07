"""Step step04 workflow responsibility; original function bodies preserved."""
from __future__ import annotations
from . import pass1_context as _step_context
AIFS_NATIVE_SPATIAL_INTERVAL_DEGREES = getattr(_step_context, 'AIFS_NATIVE_SPATIAL_INTERVAL_DEGREES')
CANDIDATE_DISCOVERY_RECONCILIATION_ASSET_LIMIT = getattr(_step_context, 'CANDIDATE_DISCOVERY_RECONCILIATION_ASSET_LIMIT')
CANDIDATE_DISCOVERY_RECONCILIATION_EVIDENCE_LIMIT = getattr(_step_context, 'CANDIDATE_DISCOVERY_RECONCILIATION_EVIDENCE_LIMIT')

# Original func/step04/pass1_engine.py:711
def _derived_check_scope(diagnostic_id: str, analysis_methods: list[str] | tuple[str, ...] | set[str]) -> str:
    """Return a deterministic fallback scope for legacy/non-compact planning paths."""
    methods = {str(value) for value in analysis_methods}
    if diagnostic_id == "multivariable_composite":
        return "multivariable_alignment"
    if diagnostic_id in {"vertical_cross_section", "time_height_section"}:
        return "vertical_structure"
    if diagnostic_id == "temporal_evolution" or "temporal_evolution" in methods:
        return "temporal_evolution"
    return "single_variable"

# Original func/step04/pass1_engine.py:839
def _normalized_required_signal_components(
    values: Any,
    *,
    variable_role: str,
    diagnostic_id: str,
) -> list[str]:
    """Normalize model-declared required signal components without inferring outcomes."""
    if isinstance(values, list):
        result = list(dict.fromkeys(str(value) for value in values if str(value) in SIGNAL_COMPONENTS))
    else:
        result = []
    if not result:
        raise ValueError(
            "candidate-plan check must explicitly declare required_signal_components "
            f"diagnostic_id={diagnostic_id!r} variable_role={variable_role!r}"
        )
    return result

# Original func/step04/pass1_engine.py:1063
def write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")

# Original func/step04/pass1_engine.py:1067
def report_call_metadata(call_meta: dict[str, Any], output_policy: OutputPolicy) -> dict[str, Any]:
    """Retain operational repair/canonicalization facts without provider detail."""
    result = {
        key: call_meta.get(key, 0)
        for key in (
            "structured_output_fallback_count",
            "source_grounded_retry_attempt_count",
            "repair_attempt_count",
            "http_retry_count",
        )
    }
    canonicalized = call_meta.get("canonicalized_fields", [])
    if output_policy.should_write("provider_metadata"):
        result["canonicalized_fields"] = canonicalized
        result["fixed_field_mismatches"] = call_meta.get("fixed_field_mismatches", [])
    else:
        result["canonicalized_fields"] = [
            {
                key: item[key]
                for key in ("field", "reason", "source_request_id", "added_values")
                if key in item
            }
            for item in canonicalized
            if isinstance(item, dict)
        ]
        reasons = sorted(
            {
                str(item.get("reason", "fixed_field"))
                for item in canonicalized
                if isinstance(item, dict)
            }
        )
        result["canonicalization_summary"] = {
            "count": len(canonicalized),
            "reasons": reasons,
        }
    return result

# Original func/step04/pass1_engine.py:1106
def checkpoint_retry_summary(call_meta: dict[str, Any]) -> dict[str, int]:
    """Keep only non-sensitive retry counters in resumable checkpoints."""
    return {
        key: int(call_meta.get(key, 0))
        for key in (
            "structured_output_fallback_count",
            "source_grounded_retry_attempt_count",
            "repair_attempt_count",
            "http_retry_count",
        )
    }

# Original func/step04/pass1_engine.py:1119
def checkpoint_repair_history(call_meta: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Retain sanitized Step04 regeneration history in resumable checkpoints.

    Provider bodies and free-form plan text stay out of operational checkpoints.
    The retained rows are limited to the candidate/check identity, bounded retry
    accounting, local validation errors, and regeneration outcome.
    """
    keys = (
        "candidate_id",
        "stage",
        "check_key",
        "repair_unit",
        "repair_attempt",
        "repair_attempt_limit",
        "validation_errors",
        "next_validation_errors",
        "result",
    )

    def sanitize(rows: Any) -> list[dict[str, Any]]:
        if not isinstance(rows, list):
            return []
        return [
            {key: row[key] for key in keys if key in row}
            for row in rows
            if isinstance(row, dict)
        ]

    return {
        "atomic_check_regenerations": sanitize(call_meta.get("atomic_repair_history")),
        "candidate_chunk_regenerations": sanitize(call_meta.get("candidate_chunk_repair_history")),
    }

# Original func/step04/pass1_engine.py:1153
def checkpoint_usage_summary(call_meta: dict[str, Any]) -> dict[str, Any]:
    """Retain normalized token accounting without provider bodies or request text."""
    usage = call_meta.get("usage")
    return usage if isinstance(usage, dict) else {"attempts": [], "stage_total": None}

# Original func/step04/pass1_engine.py:1159
def _client_checkpoint_configuration(
    client: Any,
    *,
    stage: str | None = None,
) -> dict[str, Any]:
    """Keep unit-test adapters compatible while production adapters are explicit."""
    if stage is not None:
        scoped = getattr(client, "checkpoint_configuration_for_stage", None)
        if callable(scoped):
            value = scoped(stage)
            return value if isinstance(value, dict) else {}
    value = getattr(client, "checkpoint_configuration", None)
    return value if isinstance(value, dict) else {}

# Original func/step04/pass1_engine.py:1174
def _summarize_usage_attempts(attempts: list[dict[str, Any] | None]) -> dict[str, Any]:
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
    )
    totals: dict[str, int | None] = {}
    for field in fields:
        values = [item.get(field) if isinstance(item, dict) else None for item in attempts]
        totals[field] = (
            sum(values)
            if values and all(isinstance(value, int) for value in values)
            else None
        )
    return {"attempts": attempts, "stage_total": totals}

# Original func/step04/pass1_engine.py:1193
def write_transient_failure_summary(
    *,
    pass1_dir: Path,
    root: Path,
    stage: str,
    exc: Exception,
) -> None:
    """Write a sanitized transient summary for the compatibility engine workspace."""
    safe_stage = re.sub(r"[^A-Za-z0-9_.-]+", "_", stage)
    summary_path = pass1_dir / "failures" / safe_stage / "failure_summary.json"
    write_json(
        summary_path,
        {
            "failed_step": safe_stage,
            "failed_candidate_id": getattr(exc, "candidate_id", None),
            "failed_request_id": None,
            "error_type": type(exc).__name__,
            "sanitized_message": sanitize_failure_text(exc, project_root=str(root)),
            "resume_available": True,
            **(
                {"incomplete_diagnostics": dict(exc.diagnostics)}
                if isinstance(getattr(exc, "diagnostics", None), dict)
                else {}
            ),
        },
    )

# Original func/step04/pass1_engine.py:1221
def write_final_response_artifacts(
    *,
    pass1_dir: Path,
    final_response: dict[str, Any],
    final_raw: str,
    output_policy: OutputPolicy,
) -> dict[str, Path]:
    """Materialize only the final artifacts permitted by the output policy."""
    raw_path = pass1_dir / "ai_candidate_response.raw.txt"
    parsed_path = pass1_dir / "ai_candidate_response.json"
    validated_path = pass1_dir / "ai_candidate_response.validated.json"
    if output_policy.should_write("provider_raw"):
        atomic_write_text(raw_path, final_raw + "\n")
    if output_policy.should_write("intermediate_parsed_json"):
        write_json(parsed_path, final_response)
    write_json(validated_path, final_response)
    return {
        "raw": raw_path,
        "parsed": parsed_path,
        "validated": validated_path,
    }

# Original func/step04/pass1_engine.py:1244
def write_observation_artifacts(
    *,
    observation_path: Path,
    raw_path: Path,
    provider_path: Path,
    observation: dict[str, Any],
    raw: str,
    provider_summary: dict[str, Any],
    call_meta: dict[str, Any],
    elapsed_seconds: float,
    output_policy: OutputPolicy,
) -> None:
    """Persist the resume checkpoint and only the allowed observation diagnostics."""
    if output_policy.should_write("provider_raw"):
        atomic_write_text(raw_path, raw + "\n")
    write_json(observation_path, observation)
    if output_policy.should_write("provider_metadata"):
        write_json(
            provider_path,
            {**provider_summary, **call_meta, "elapsed_seconds": elapsed_seconds},
        )

# Original func/step04/pass1_engine.py:1284
def _safe_identity_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 256 else value[:256] + "...<truncated>"
    return f"<{type(value).__name__}>"

# Original func/step04/pass1_engine.py:1292
def _nested_value(value: dict[str, Any], field: str) -> tuple[bool, Any]:
    current: Any = value
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current

# Original func/step04/pass1_engine.py:1301

# Original func/step04/pass1_engine.py:1381

# Original func/step04/pass1_engine.py:1653
def verify_attachments(root: Path, attachment_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_id = _attachment_by_id(attachment_manifest)
    declared_count = int(attachment_manifest.get("attachment_count", -1))
    if declared_count != len(by_id):
        raise ValueError(f"attachment_count mismatch declared={declared_count} actual={len(by_id)}")
    for asset_id, item in by_id.items():
        file_path = resolve_logical_ref(root, str(item.get("file_path", "")))
        if not file_path.is_file():
            raise FileNotFoundError(f"attachment missing asset_id={asset_id}: {file_path}")
        expected_size = item.get("size_bytes")
        if expected_size is not None and int(expected_size) != file_path.stat().st_size:
            raise ValueError(f"attachment size mismatch asset_id={asset_id}")
        expected_hash = str(item.get("sha256", ""))
        if expected_hash and sha256sum(file_path) != expected_hash:
            raise ValueError(f"attachment sha256 mismatch asset_id={asset_id}")
    return by_id

# Original func/step04/pass1_engine.py:2099
def build_compact_observation_recovery_schema(
    schema: dict[str, Any],
    *,
    batch: dict[str, Any],
) -> dict[str, Any]:
    """Constrain a length-recovery response without changing the canonical schema."""
    compact = copy.deepcopy(schema)
    expected_asset_ids = [str(item) for item in batch["attachment_ids"]]
    properties = compact["properties"]
    observations = properties["attachment_observations"]
    observations["minItems"] = len(expected_asset_ids)
    observations["maxItems"] = len(expected_asset_ids)
    item_properties = observations["items"]["properties"]
    item_properties["asset_id"]["enum"] = expected_asset_ids
    item_properties["observed_signals"]["minItems"] = 1
    item_properties["observed_signals"]["maxItems"] = 4
    item_properties["observed_signals"]["items"]["maxLength"] = 160
    item_properties["spatial_pattern"]["maxLength"] = 220
    item_properties["temporal_evolution"]["maxLength"] = 220
    item_properties["limitations"]["maxItems"] = 2
    item_properties["limitations"]["items"]["maxLength"] = 160
    direct_signal = item_properties.get("direct_precipitation_signal", {}).get("properties", {})
    if direct_signal:
        direct_signal["significant_forecast_hours"]["maxItems"] = 4
        direct_signal["rationale"]["maxLength"] = 160
    properties["cross_product_synthesis"]["maxItems"] = 3
    properties["cross_product_synthesis"]["items"]["maxLength"] = 180
    properties["batch_uncertainties"]["maxItems"] = 3
    properties["batch_uncertainties"]["items"]["maxLength"] = 160
    return compact

# Original func/step04/pass1_engine.py:2131
def build_provider_observation_schema(
    schema: dict[str, Any],
    *,
    batch: dict[str, Any],
    include_batch_synthesis: bool = True,
) -> dict[str, Any]:
    """Limit VLM output to observations; immutable manifest fields are injected locally."""
    provider_schema = build_compact_observation_recovery_schema(schema, batch=batch)
    properties = provider_schema["properties"]
    required = provider_schema["required"]
    for field in (
        "package_id",
        "batch_id",
        "analysis_tier",
        "group_id",
        "observed_attachment_count",
    ):
        properties.pop(field, None)
        if field in required:
            required.remove(field)
    if not include_batch_synthesis:
        for field in ("cross_product_synthesis", "batch_uncertainties"):
            properties.pop(field, None)
            if field in required:
                required.remove(field)
    observation_item = properties["attachment_observations"]["items"]
    item_properties = observation_item["properties"]
    item_required = observation_item["required"]
    for field in ("asset_id", "product_id", "forecast_hours"):
        item_properties.pop(field, None)
        if field in item_required:
            item_required.remove(field)
    return provider_schema

# Original func/step04/pass1_engine.py:2191
def _compact_provider_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    choice = None
    choices = envelope.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice = choices[0]
    return {
        "id": envelope.get("id"),
        "model": envelope.get("model"),
        "created": envelope.get("created"),
        "usage": envelope.get("usage"),
        "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
    }

# Original func/step04/pass1_engine.py:3053
def _compact_candidate_chunk_repair_required(validation_errors: list[str]) -> bool:
    """Identify contracts that cannot be executed within any single Check."""
    return any(
        "CANDIDATE_QUANTITATIVE_SCOPE_UNAVAILABLE" in str(error)
        for error in validation_errors
    )

# Original func/step04/pass1_engine.py:3086
def _compact_check_repair_schema(
    transport_schema: dict[str, Any],
    target_keys: list[str],
    *,
    check_scope_constraints: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build an atomic-repair schema at the provider transport boundary.

    OpenAI/Codex candidate-plan transport uses ``verification_requests``.  The
    normalized in-process representation uses ``diagnostic_checks`` after
    ``_derive_diagnostic_check_from_verification_request``.  Atomic repair must
    therefore regenerate the transport object and only then translate it back to
    the normalized Check representation.  Legacy schemas that already expose
    ``diagnostic_checks`` remain supported.
    """
    properties = transport_schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError("candidate-plan transport schema has no properties")

    transport_field: str
    item_schema_source: Any
    if isinstance(properties.get("verification_requests"), dict):
        transport_field = "verification_requests"
        item_schema_source = properties[transport_field].get("items")
    elif isinstance(properties.get("diagnostic_checks"), dict):
        transport_field = "diagnostic_checks"
        item_schema_source = properties[transport_field].get("items")
    else:
        raise ValueError(
            "candidate-plan transport schema has neither verification_requests nor diagnostic_checks item schema"
        )
    if not isinstance(item_schema_source, dict):
        raise ValueError(
            f"candidate-plan transport schema has no {transport_field} item schema"
        )

    constraints = check_scope_constraints or {}
    variants: list[dict[str, Any]] = []
    for check_key in target_keys:
        variant = copy.deepcopy(item_schema_source)
        variant_properties = variant.get("properties")
        if not isinstance(variant_properties, dict):
            raise ValueError(
                f"candidate-plan {transport_field} item schema has no properties"
            )
        variant_properties["check_key"] = {"type": "string", "enum": [check_key]}
        # ``check_scope`` is Python-owned for OpenAI/Codex transport requests and
        # is derived from verification_mode.  Apply a direct constraint only to
        # legacy schemas that actually expose the field.
        if check_key in constraints and "check_scope" in variant_properties:
            variant_properties["check_scope"] = {
                "type": "string",
                "enum": [constraints[check_key]],
            }
        variants.append(variant)

    item_schema: dict[str, Any] = (
        variants[0] if len(variants) == 1 else {"anyOf": variants}
    )
    return {
        "title": "AIFS Pass1 Candidate Plan Atomic Regeneration",
        "type": "object",
        "additionalProperties": False,
        "required": [transport_field],
        "properties": {
            transport_field: {
                "type": "array",
                "minItems": len(target_keys),
                "maxItems": len(target_keys),
                "items": item_schema,
            }
        },
    }

# Original func/step04/pass1_engine.py:3161
def _compact_continuation_gap_repair_schema(
    transport_schema: dict[str, Any],
) -> dict[str, Any]:
    """Return a constrained whole-chunk schema for spatial/temporal gap repair.

    A continuation that leaves ``spatial_evolution``/``temporal_evolution``
    uncovered has already demonstrated that a free-form regeneration prompt is
    not sufficiently binding.  This schema keeps the meteorological choice with
    the model (component, times, hypothesis, expected signal) while constraining
    the repair unit to an executable ``spatial_evolution`` request.

    The response remains a normal candidate-plan transport chunk and is passed
    through the ordinary semantic validator afterwards.
    """
    schema = copy.deepcopy(transport_schema)
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("candidate-plan transport schema has no properties")
    requests = properties.get("verification_requests")
    if not isinstance(requests, dict):
        raise ValueError(
            "candidate-plan transport schema has no verification_requests for continuation gap repair"
        )
    item = requests.get("items")
    if not isinstance(item, dict):
        raise ValueError(
            "candidate-plan verification_requests schema has no item definition for continuation gap repair"
        )
    item_properties = item.get("properties")
    if not isinstance(item_properties, dict):
        raise ValueError(
            "candidate-plan verification request schema has no properties for continuation gap repair"
        )

    # At least one request must be emitted and every emitted request in this
    # specialized repair must close the spatial/temporal evolution gap.  Do not
    # let unrelated valid Checks consume the bounded chunk-regeneration budget.
    requests["minItems"] = 1
    requests["maxItems"] = min(int(requests.get("maxItems", 10)), 3)
    item_properties["verification_mode"] = {
        "type": "string",
        "enum": ["spatial_evolution"],
    }

    signal_components = item_properties.get("signal_components")
    if isinstance(signal_components, dict):
        signal_components["minItems"] = 1
        signal_components["maxItems"] = 1

    # A spatial-evolution request must contain an explicit proposition rather
    # than relying on coverage-role labels alone.  The model still chooses which
    # of these meteorological states is appropriate for the candidate.
    claims = item_properties.get("atomic_claims")
    if isinstance(claims, dict):
        claims["minItems"] = 1
        claim_item = claims.get("items")
        if isinstance(claim_item, dict):
            claim_properties = claim_item.get("properties")
            if isinstance(claim_properties, dict) and isinstance(
                claim_properties.get("expected_state"), dict
            ):
                claim_properties["expected_state"] = {
                    "type": "string",
                    "enum": [
                        "move",
                        "persist",
                        "split",
                        "merge",
                        "expand",
                        "contract",
                        "reorganize",
                    ],
                }

    title = str(schema.get("title") or "AIFS Pass1 Candidate Plan")
    schema["title"] = f"{title} Continuation Spatial Evolution Gap Repair"
    return schema

# Original func/step04/pass1_engine.py:3240
def _continuation_gap_requires_spatial_repair(
    validation_errors: list[str],
) -> bool:
    joined = "\n".join(str(error) for error in validation_errors)
    return (
        "COMPACT_PLAN_CONTINUATION_NO_PROGRESS" in joined
        and "spatial_evolution" in joined
    )

# Original func/step04/pass1_engine.py:3267
def _merge_compact_check_regeneration(
    original: dict[str, Any],
    regenerated: dict[str, Any],
    target_keys: list[str],
) -> dict[str, Any]:
    """Replace complete targeted Checks and preserve every other plan unit.

    Provider transport repairs may return ``verification_requests`` while the
    normalized plan stores ``diagnostic_checks``.  Convert each repaired request
    through the same deterministic adapter used for the initial candidate plan;
    never field-patch a normalized Check.
    """
    if isinstance(regenerated.get("verification_requests"), list):
        transport_rows = _compact_plan_rows(regenerated, "verification_requests")
        replacement_rows = [
            _derive_diagnostic_check_from_verification_request(row)
            for row in transport_rows
        ]
    else:
        replacement_rows = _compact_plan_rows(regenerated, "diagnostic_checks")
    replacements = {str(row.get("check_key", "")): row for row in replacement_rows}
    if set(replacements) != set(target_keys) or len(replacement_rows) != len(target_keys):
        raise ValueError(
            "atomic diagnostic-check regeneration must return exactly the requested check_key values "
            f"expected={sorted(target_keys)} actual={sorted(replacements)}"
        )
    merged = copy.deepcopy(original)
    output_rows: list[dict[str, Any]] = []
    found: set[str] = set()
    for row in _compact_plan_rows(merged, "diagnostic_checks"):
        key = str(row.get("check_key", ""))
        if key in replacements:
            output_rows.append(copy.deepcopy(replacements[key]))
            found.add(key)
        else:
            output_rows.append(row)
    missing = set(target_keys) - found
    if missing:
        raise ValueError(f"atomic diagnostic-check regeneration target not found in source chunk: {sorted(missing)}")
    merged["diagnostic_checks"] = output_rows
    return merged

# Original func/step04/pass1_engine.py:3309
def call_and_validate_json(
    *,
    client: Pass1ProviderClient,
    model: str,
    messages: list[dict[str, Any]],
    schema: dict[str, Any],
    schema_name: str,
    structured_output_mode: str,
    enable_thinking: bool,
    max_tokens: int,
    temperature: float,
    top_p: float,
    repair_attempts: int,
    semantic_validator,
    normalizer=None,
    fixed_field_expectations: dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    source_retry_attempts: int = 0,
    source_retry_after_repair_reserve: int = 0,
    source_retry_context: dict[str, Any] | None = None,
    source_retry_schema: dict[str, Any] | None = None,
    source_retry_schema_name: str | None = None,
    source_retry_before_repair_on_parse_error: bool = False,
    repair_context_messages: list[dict[str, Any]] | None = None,
    compact_length_recovery: dict[str, Any] | None = None,
    semantic_unit_repair: str | None = None,
    repair_identity: dict[str, Any] | None = None,
    nonrepairable_error_markers: tuple[str, ...] = (),
    chunk_repair_from_original_context: bool = False,
    chunk_repair_previous_chunks: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]:
    fixed_field_mismatches: list[dict[str, Any]] = []
    atomic_repair_history: list[dict[str, Any]] = []

    def parse_normalize_validate(raw_text: str, *, stage: str) -> tuple[dict[str, Any], list[str]]:
        return _step04_parse_normalize_validate(
            raw_text, stage=stage,
            fixed_field_expectations=fixed_field_expectations,
            normalizer=normalizer,
            semantic_validator=semantic_validator,
            fixed_field_mismatches=fixed_field_mismatches,
        )

    def is_json_parse_error(validation_errors: list[str]) -> bool:
        return _step04_is_json_parse_error(validation_errors)

    def retry_with_original_images(*, retry_index: int, stage_prefix: str) -> None:
        nonlocal result, raw, envelope_summary, parsed, errors, source_retry_count, http_retry_count, usage_attempts
        logging.warning(
            "원본 이미지 기반 재시도 단계=%s 시도=%s 오류=%s",
            stage_prefix,
            retry_index,
            errors[:10],
        )
        retry_result = _step04_provider_complete(client,
            model=model,
            messages=build_source_grounded_retry_messages(
                messages,
                validation_errors=errors,
                retry_context=source_retry_context or {},
            ),
            response_schema=source_retry_schema or schema,
            schema_name=source_retry_schema_name or schema_name,
            structured_output_mode=structured_output_mode,
            enable_thinking=False,
            max_tokens=max_tokens,
            temperature=0.0,
            top_p=1.0,
        )
        result = retry_result
        source_retry_count += 1
        http_retry_count += int(getattr(retry_result, "http_retry_count", 0))
        usage_attempts.extend(
            getattr(retry_result, "usage_attempts", ())
            or (_compact_provider_envelope(retry_result.response_envelope).get("usage"),)
        )
        raw = retry_result.content
        envelope_summary = _compact_provider_envelope(retry_result.response_envelope)
        parsed, errors = parse_normalize_validate(
            raw,
            stage=f"{stage_prefix}_{retry_index}",
        )

    result = _step04_provider_complete(client,
        model=model,
        messages=messages,
        response_schema=schema,
        schema_name=schema_name,
        structured_output_mode=structured_output_mode,
        enable_thinking=enable_thinking,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
    )
    raw = result.content
    envelope_summary = _compact_provider_envelope(result.response_envelope)
    usage_attempts: list[dict[str, Any] | None] = list(
        getattr(result, "usage_attempts", ()) or (envelope_summary.get("usage"),)
    )
    repair_count = 0
    http_retry_count = int(getattr(result, "http_retry_count", 0))
    parsed, errors = parse_normalize_validate(raw, stage="initial")

    source_retry_count = 0
    compact_recovery_count = 0
    source_retry_after_repair_reserve = min(
        max(0, int(source_retry_after_repair_reserve)),
        source_retry_attempts,
    )
    source_retry_before_repair_attempts = (
        source_retry_attempts - source_retry_after_repair_reserve
    )
    if source_retry_context is not None and (
        not is_json_parse_error(errors) or source_retry_before_repair_on_parse_error
    ):
        for source_retry_index in range(source_retry_before_repair_attempts):
            if not errors:
                break
            retry_with_original_images(
                retry_index=source_retry_index + 1,
                stage_prefix="source_grounded_retry",
            )

    # Multiple executable quantitative claims require *additional* verification
    # requests and potentially updated hypothesis references. A full-Check
    # repair is intentionally one-to-one (same check_key), so it cannot comply
    # with the validator's split instruction. Escalate this structural case to
    # whole-chunk regeneration instead of burning three futile atomic retries.
    # A compact plan can contain independent Check failures.  Each Check gets
    # its own bounded atomic-regeneration budget; repairs for CHK_A must not
    # consume the budget available to CHK_B.
    chunk_repair_attempts = 0
    check_repair_attempts: dict[str, int] = {}
    check_last_error_signature: dict[str, str] = {}
    chunk_last_error_signature: str | None = None
    chunk_last_state_signature: str | None = None
    candidate_chunk_repair_history: list[dict[str, Any]] = []
    repair_iteration = 0
    while errors:
        joined_errors = "\n".join(str(error) for error in errors)
        matched_nonrepairable = [
            marker for marker in nonrepairable_error_markers if marker in joined_errors
        ]
        if matched_nonrepairable:
            errors = [
                "STEP04_INTERNAL_EXECUTION_MAPPING_ERROR "
                f"markers={matched_nonrepairable}; Python-owned execution contract failed after deterministic intent mapping",
                *errors,
            ]
            break

        # A Check-only repair cannot create or enlarge the chunk-level
        # context_domain.  Skip futile atomic retries for that structural
        # failure, and regenerate the whole chunk from immutable context.
        quantitative_request_split_required = any(
            "QUANTITATIVE_ATOMICITY_INVALID" in str(error) for error in errors
        )
        candidate_chunk_repair_required = _compact_candidate_chunk_repair_required(errors)
        spatial_context_creation_required = (
            "SPATIAL_ANALYSIS_GRID_INSUFFICIENT" in joined_errors
            and "Provide a genuine context_domain" in joined_errors
        )
        atomic_check_targets = (
            _compact_check_keys_from_validation_errors(errors, parsed_plan=parsed)
            if semantic_unit_repair == "diagnostic_check"
            and not spatial_context_creation_required
            and not quantitative_request_split_required
            and not candidate_chunk_repair_required
            else []
        )
        if atomic_check_targets:
            check_error_signatures = {
                check_key: " | ".join(
                    sorted(
                        str(error)
                        for error in errors
                        if check_key in _compact_check_keys_from_validation_errors(
                            [str(error)], parsed_plan=parsed
                        )
                    )
                )
                for check_key in atomic_check_targets
            }
            # Most identical repair failures indicate true non-progress and
            # should stop immediately.  One narrow structural contract is
            # allowed to consume the remaining bounded repair budget: when
            # forecast_hours collapses to fewer than two unique times Python
            # cannot invent the missing comparison hour, so the model gets the
            # normal full-Check regeneration budget with an explicit repair
            # rule instead of being stopped after the first identical retry.
            _bounded_same_signature_marker = (
                "forecast_hours must be unique chronological forecast hours"
            )
            repeated_error_targets = [
                check_key
                for check_key, signature in check_error_signatures.items()
                if (
                    signature
                    and check_last_error_signature.get(check_key) == signature
                    and not (
                        _bounded_same_signature_marker in signature
                        and check_repair_attempts.get(check_key, 0) < repair_attempts
                    )
                )
            ]
            if repeated_error_targets:
                errors = [
                    *errors,
                    "STEP04_ATOMIC_REPAIR_NONPROGRESS "
                    f"check_keys={sorted(repeated_error_targets)}",
                ]
                break
            exhausted_targets = [
                check_key
                for check_key in atomic_check_targets
                if check_repair_attempts.get(check_key, 0) >= repair_attempts
            ]
            if exhausted_targets:
                errors = [
                    *errors,
                    "STEP04_ATOMIC_REPAIR_BUDGET_EXHAUSTED "
                    f"check_keys={sorted(exhausted_targets)} max_attempts={repair_attempts}",
                ]
                break
            # Regenerate complete Check objects only.  Do not ask the model to patch
            # individual fields, and do not accept changes to unrelated plan units.
            repair_schema = _compact_check_repair_schema(
                schema,
                atomic_check_targets,
                check_scope_constraints=_compact_check_scope_constraints_from_validation_errors(errors),
            )
            current_checks = {
                str(row.get("check_key", "")): row
                for row in _compact_plan_rows(parsed, "diagnostic_checks")
            }
            missing_targets = [key for key in atomic_check_targets if key not in current_checks]
            if missing_targets:
                # If validation named a Check that cannot be resolved to the current
                # normalized chunk, fall back to whole-chunk regeneration below.
                atomic_check_targets = []
            else:
                repair_iteration += 1
                target_attempts = {
                    check_key: check_repair_attempts.get(check_key, 0) + 1
                    for check_key in atomic_check_targets
                }
                check_repair_attempts.update(target_attempts)
                check_last_error_signature.update(check_error_signatures)
                phase_keys = [
                    str(row.get("phase_key", ""))
                    for row in _compact_plan_rows(parsed, "analysis_phases")
                ]
                phase_hours_by_key = {
                    str(row.get("phase_key", "")): sorted(_compact_phase_hours(row))
                    for row in _compact_plan_rows(parsed, "analysis_phases")
                }
                history_rows = []
                for check_key in atomic_check_targets:
                    check = current_checks[check_key]
                    history_row = {
                        **(repair_identity or {}),
                        "check_key": check_key,
                        "repair_unit": "full_check",
                        "repair_attempt": target_attempts[check_key],
                        "repair_attempt_limit": repair_attempts,
                        "validation_errors": list(errors),
                        "diagnostic_id": check.get("diagnostic_id"),
                        "check_scope": check.get("check_scope"),
                        "analysis_methods": list(check.get("analysis_methods", [])),
                        "required_signal_components": list(check.get("required_signal_components", [])),
                        "forecast_hours": sorted({
                            hour
                            for phase_key in check.get("phase_keys", [])
                            for hour in phase_hours_by_key.get(str(phase_key), [])
                        }),
                    }
                    atomic_repair_history.append(history_row)
                    history_rows.append(history_row)
                repair_transport_field = (
                    "verification_requests"
                    if "verification_requests" in repair_schema.get("properties", {})
                    else "diagnostic_checks"
                )
                repair_prompt = {
                    "task": (
                        "검증에 실패한 verification request를 필드 수정이 아니라 요청 전체 단위로 처음부터 다시 생성하십시오."
                        if repair_transport_field == "verification_requests"
                        else "검증에 실패한 diagnostic Check를 필드 수정이 아니라 Check 전체 단위로 처음부터 다시 생성하십시오."
                    ),
                    "validation_errors": errors,
                    "regenerate_check_keys": atomic_check_targets,
                    "allowed_phase_keys": phase_keys,
                    "rules": [
                        f"{repair_transport_field} 배열만 반환하십시오.",
                        "각 regenerate_check_keys에 대해 정확히 하나의 완전한 객체를 반환하십시오.",
                        "check_key는 입력값을 정확히 유지하십시오.",
                        (
                            "기존 verification request의 일부 필드만 고치지 마십시오. 기존 객체는 폐기된 참고자료이며 signal_components, verification_mode, domain_scope, spatial_relation_scope, phase_keys, representative_spatial_hours, expected_signal, decision_relevance, purpose, priority, evidence_role, coverage_roles, atomic_claims 및 단면 관련 필드를 하나의 일관된 요청으로 함께 다시 결정하십시오."
                            if repair_transport_field == "verification_requests"
                            else "기존 Check의 일부 필드만 고치지 마십시오. 기존 객체는 폐기된 참고자료이며 diagnostic_id, variable_role, required_signal_components, check_scope, phase_keys, analysis_methods, expected_signal, decision_relevance, purpose, priority, evidence_role, coverage_roles 및 단면 관련 필드를 하나의 일관된 Check로 함께 다시 결정하십시오."
                        ),
                        "원래 후보와 가설의 목적은 앞선 대화 컨텍스트에서 다시 확인하고, 현재 active catalog가 지원하는 진단만 선택하십시오.",
                        "phase_keys는 allowed_phase_keys 중에서만 선택하십시오. 새로운 phase_key를 만들지 마십시오.",
                        "지원되지 않는 product, feature, diagnostic 또는 분석방법을 만들어 내지 마십시오.",
                        "사람이 읽는 모든 설명 문자열은 한국어로 작성하십시오.",
                        *_repair_rules_for_validation_errors(errors),
                    ],
                }
                logging.warning(
                    "STEP04_ATOMIC_REPAIR unit=full_check checks=%s attempts=%s/%s errors=%s",
                    atomic_check_targets,
                    target_attempts,
                    repair_attempts,
                    errors[:10],
                )
                repair_messages = [
                    {
                        "role": "system",
                        "content": (
                            "엄격한 기계 처리 파이프라인의 Step04 검증 요청을 복구합니다. "
                            "필드 패치가 아니라 지정된 검증 객체 전체를 다시 생성하고 JSON만 반환하십시오."
                        ),
                    },
                ]
                if repair_context_messages is not None:
                    repair_messages.extend(repair_context_messages)
                repair_messages.append(
                    {"role": "user", "content": json.dumps(repair_prompt, ensure_ascii=False)}
                )
                repair_result = _step04_provider_complete(client,
                    model=model,
                    messages=repair_messages,
                    response_schema=repair_schema,
                    schema_name=f"{schema_name}DiagnosticCheckRepair",
                    structured_output_mode=structured_output_mode,
                    enable_thinking=False,
                    max_tokens=max_tokens,
                    temperature=0.0,
                    top_p=1.0,
                )
                result = repair_result
                repair_count += 1
                http_retry_count += int(getattr(repair_result, "http_retry_count", 0))
                usage_attempts.extend(
                    getattr(repair_result, "usage_attempts", ())
                    or (_compact_provider_envelope(repair_result.response_envelope).get("usage"),)
                )
                raw = repair_result.content
                envelope_summary = _compact_provider_envelope(repair_result.response_envelope)
                try:
                    replacement_value = extract_json_object(raw)
                    schema_errors = _validate_jsonschema(replacement_value, repair_schema)
                    if schema_errors:
                        raise ValueError(" | ".join(schema_errors[:12]))
                    candidate_value = _merge_compact_check_regeneration(
                        parsed, replacement_value, atomic_check_targets
                    )
                    candidate_errors = semantic_validator(candidate_value)
                    parsed, errors = candidate_value, candidate_errors
                    for history_row in history_rows:
                        history_row["result"] = "PASS" if not candidate_errors else "FAIL"
                        history_row["next_validation_errors"] = list(candidate_errors)
                except Exception as exc:  # noqa: BLE001
                    errors = [str(exc)]
                    for history_row in history_rows:
                        history_row["result"] = "FAIL"
                        history_row["next_validation_errors"] = list(errors)
                continue

        # Errors that cannot be localized to one diagnostic Check (for example a
        # missing first-chunk precipitation Check or a phase/hypothesis contract)
        # are regenerated at the complete chunk level from the original candidate
        # context. A discarded provider response or Check snapshot is never sent
        # back to the model.
        chunk_error_signature = " | ".join(sorted(str(error) for error in errors))
        chunk_state_signature = canonical_hash(
            {
                "validation_errors": sorted(str(error) for error in errors),
                "normalized_chunk": parsed,
            }
        )
        if chunk_last_state_signature == chunk_state_signature:
            errors = [
                *errors,
                "STEP04_CANDIDATE_CHUNK_REPAIR_NONPROGRESS",
            ]
            break
        if chunk_repair_attempts >= repair_attempts:
            break
        chunk_repair_attempts += 1
        chunk_last_error_signature = chunk_error_signature
        chunk_last_state_signature = chunk_state_signature
        repair_iteration += 1
        chunk_history = {
            **(repair_identity or {}),
            "repair_unit": "candidate_plan_chunk",
            "repair_attempt": chunk_repair_attempts,
            "repair_attempt_limit": repair_attempts,
            "validation_errors": list(errors),
        }
        candidate_chunk_repair_history.append(chunk_history)
        discovery_scope_requires_fresh_regeneration = any(
            marker in str(error)
            for error in errors
            for marker in (
                "CANDIDATE_OPERATIONAL_SCOPE_INVALID",
                "SYSTEM_DIRECT_PRECIPITATION_COVERAGE_REQUIRED",
            )
        )
        regenerate_chunk_from_original = (
            semantic_unit_repair == "diagnostic_check"
            or chunk_repair_from_original_context
            or discovery_scope_requires_fresh_regeneration
        )
        continuation_spatial_gap_repair = _continuation_gap_requires_spatial_repair(errors)
        chunk_response_schema = (
            _compact_continuation_gap_repair_schema(schema)
            if continuation_spatial_gap_repair
            else schema
        )
        chunk_schema_name = (
            f"{schema_name}ContinuationSpatialEvolutionGapRepair"
            if continuation_spatial_gap_repair
            else schema_name
        )
        repair_prompt = {
            "task": (
                "이전 provider 응답은 사용하지 말고, 원래 후보 컨텍스트와 검증 오류를 기준으로 "
                "현재 candidate-plan chunk 전체를 일관되게 다시 생성하십시오."
                if regenerate_chunk_from_original
                else "이전 응답을 지원된 분석 내용만 유지하여 유효한 JSON으로 복구하십시오."
            ),
            "validation_errors": errors,
            **(
                {}
                if regenerate_chunk_from_original
                else {"previous_response": raw}
            ),
            "response_schema": schema,
            "rules": [
                "JSON만 반환하십시오.",
                "지원되지 않는 asset_id, product_id, forecast_hour, feature_id, candidate_id를 추가하지 마십시오.",
                *(
                    [
                        "이전 응답의 개별 필드를 patch하지 마십시오. 현재 chunk를 의미적으로 완결된 하나의 계획 단위로 다시 생성하십시오.",
                        "verification_request를 다시 만들 때는 signal_components, verification_mode, domain_scope, spatial_relation_scope, phase_keys, expected_signal, decision_relevance, purpose, priority, evidence_role, coverage_roles와 단면 관련 필드를 하나의 일관된 요청으로 함께 결정하십시오.",
                    ]
                    if regenerate_chunk_from_original
                    else ["유효한 관찰과 분석은 유지하고 형식 및 계약 오류만 수정하십시오."]
                ),
                "JSON 키·식별자·enum 값은 계약 범위 안에서 사용하고 사람이 읽는 설명 문자열은 모두 한국어로 작성하십시오.",
                *_repair_rules_for_validation_errors(errors),
            ],
        }
        if quantitative_request_split_required:
            # Supply bounded structural facts, never a previous provider JSON
            # response. The source-grounded chunk regeneration still owns all
            # meteorological intent and remains under the ordinary validator.
            split_targets = _compact_check_keys_from_validation_errors(
                errors, parsed_plan=parsed
            )
            failed_checks = {
                str(item.get("check_key") or ""): item
                for item in _compact_plan_rows(parsed, "diagnostic_checks")
            } if isinstance(parsed.get("diagnostic_checks"), list) else {}
            repair_prompt["quantitative_split_context"] = [
                {
                    "check_key": key,
                    "expected_signal": str(failed_checks[key].get("expected_signal") or ""),
                    "claims": [
                        {
                            "claim_statement": str(claim.get("claim_statement") or ""),
                            "expected_state": str(claim.get("expected_state") or ""),
                            "scope_quantifier": str(claim.get("scope_quantifier") or ""),
                            "has_quantitative_scope": claim.get("quantitative_scope") is not None,
                        }
                        for claim in failed_checks[key].get("atomic_claims", [])
                        if isinstance(claim, dict)
                    ][:4],
                }
                for key in split_targets
                if key in failed_checks
            ]
            logging.warning(
                "STEP04_QUANTITATIVE_ATOMICITY_SPLIT route=candidate_chunk "
                "check_keys=%s attempt=%s/%s",
                split_targets, chunk_repair_attempts, repair_attempts,
            )
        phase_reference_failure_context = _compact_phase_reference_failure_context(
            parsed,
            errors,
            previous_chunks=chunk_repair_previous_chunks,
        )
        if phase_reference_failure_context is not None:
            repair_prompt["phase_reference_failure_context"] = phase_reference_failure_context
            repair_prompt["rules"].append(
                "phase_reference_failure_context는 실패 원인을 설명하는 구조 요약일 뿐입니다. "
                "이전 응답을 patch하거나 그대로 복원하지 말고 원래 후보 컨텍스트에서 현재 chunk 전체를 다시 생성하십시오."
            )
        check_reference_failure_context = _compact_check_reference_failure_context(
            parsed,
            errors,
            previous_chunks=chunk_repair_previous_chunks,
        )
        if check_reference_failure_context is not None:
            repair_prompt["check_reference_failure_context"] = check_reference_failure_context
            repair_prompt["rules"].append(
                "check_reference_failure_context는 유효한 Check 참조 그래프를 알려주는 구조 요약입니다. "
                "이전 응답을 patch하지 말고 원래 후보 컨텍스트에서 현재 chunk 전체를 다시 생성하되, "
                "hypothesis가 이 구조에 존재하지 않는 이전 Check key를 추정해 참조하지 않도록 하십시오."
            )
        if continuation_spatial_gap_repair:
            existing_phases = _compact_existing_phase_context(chunk_repair_previous_chunks)
            repair_prompt["continuation_gap_contract"] = {
                "required_verification_mode": "spatial_evolution",
                "required_signal_component_count_per_request": 1,
                "required_minimum_distinct_forecast_hours": 2,
                "existing_phases": existing_phases,
                "allowed_spatial_states": [
                    "move", "persist", "split", "merge", "expand", "contract", "reorganize"
                ],
            }
            repair_prompt["rules"].extend(
                [
                    "이번 재생성은 temporal_evolution과 spatial_evolution의 미충족을 해소하기 위한 전용 복구입니다. verification_requests에는 verification_mode=spatial_evolution 요청을 최소 1개 반드시 포함하십시오.",
                    "각 spatial_evolution 요청은 signal_components를 정확히 1개만 사용하고, 해당 기상요소의 여러 시각 공간장을 실제로 비교해야 합니다.",
                    "phase_keys가 가리키는 기존/신규 phase의 forecast_hours 합집합에는 서로 다른 실제 예측시각이 최소 2개 있어야 합니다. continuation_gap_contract.existing_phases를 우선 재사용하십시오.",
                    "기존 phase만으로 두 시각 이상을 확보할 수 있으면 analysis_phases를 새로 만들지 마십시오. 부족할 때만 원래 입력의 available_forecast_hours 범위에서 필요한 phase를 추가하십시오.",
                    "atomic_claims를 최소 1개 작성하고 expected_state는 move/persist/split/merge/expand/contract/reorganize 중 실제 후보에 적합한 상태를 선택하십시오. coverage_roles 문자열만 추가해서는 안 됩니다.",
                    "spatial_evolution에서는 representative_spatial_hours=[]로 두십시오. 정량 강도 변화나 precipitation_amount를 이 복구 요청에 섞지 마십시오.",
                    "기상학적 결론은 강제로 긍정하지 마십시오. 이동이 없다면 persist, 구조 변화가 있다면 해당 spatial state를 선택하여 검증 가능한 가설로 작성하십시오.",
                ]
            )
        if source_retry_context is not None:
            repair_prompt["required_attachment_count"] = source_retry_context.get("required_attachment_count")
            repair_prompt["required_asset_ids"] = source_retry_context.get("required_asset_ids")
            repair_prompt["rules"].append(
                "원본 이미지 없이 누락된 asset 관찰을 추정하거나 만들어 내지 마십시오."
            )
        if continuation_spatial_gap_repair:
            logging.warning(
                "STEP04_CONTINUATION_GAP_REPAIR mode=spatial_evolution attempt=%s/%s existing_phase_count=%s",
                chunk_repair_attempts,
                repair_attempts,
                len(_compact_existing_phase_context(chunk_repair_previous_chunks)),
            )
        logging.warning(
            "STEP04_ATOMIC_REPAIR unit=candidate_chunk attempt=%s/%s errors=%s",
            chunk_repair_attempts,
            repair_attempts,
            errors[:10],
        )
        repair_messages = [
            {
                "role": "system",
                "content": (
                    "엄격한 기계 처리 파이프라인의 JSON을 재생성합니다. "
                    "부분 필드 패치를 하지 말고 요청된 의미 단위 전체를 다시 생성하여 JSON만 반환하십시오."
                ),
            },
        ]
        if repair_context_messages is not None:
            repair_messages.extend(repair_context_messages)
        repair_messages.append(
            {"role": "user", "content": json.dumps(repair_prompt, ensure_ascii=False)}
        )
        repair_result = _step04_provider_complete(client,
            model=model,
            messages=repair_messages,
            response_schema=chunk_response_schema,
            schema_name=chunk_schema_name,
            structured_output_mode=structured_output_mode,
            enable_thinking=False,
            max_tokens=max_tokens,
            temperature=0.0,
            top_p=1.0,
        )
        result = repair_result
        repair_count += 1
        http_retry_count += int(getattr(repair_result, "http_retry_count", 0))
        usage_attempts.extend(
            getattr(repair_result, "usage_attempts", ())
            or (_compact_provider_envelope(repair_result.response_envelope).get("usage"),)
        )
        raw = repair_result.content
        envelope_summary = _compact_provider_envelope(repair_result.response_envelope)
        parsed, errors = parse_normalize_validate(raw, stage=f"semantic_unit_regeneration_{repair_iteration}")
        chunk_history["result"] = "PASS" if not errors else "FAIL"
        chunk_history["next_validation_errors"] = list(errors)

    if errors and source_retry_context is not None:
        for source_retry_index in range(source_retry_count, source_retry_attempts):
            retry_with_original_images(
                retry_index=source_retry_index + 1,
                stage_prefix="source_grounded_after_repair",
            )
            if not errors:
                break

    finish_reason = _compact_provider_envelope(result.response_envelope).get("finish_reason")
    if errors and compact_length_recovery is not None and finish_reason == "length":
        logging.warning(
            "길이 제한 JSON 복구 시도=1 max_tokens=%s 오류=%s",
            compact_length_recovery["max_tokens"],
            errors[:10],
        )
        compact_result = _step04_provider_complete(client,
            model=model,
            messages=compact_length_recovery["messages"],
            response_schema=compact_length_recovery["schema"],
            schema_name=compact_length_recovery["schema_name"],
            structured_output_mode=structured_output_mode,
            enable_thinking=False,
            max_tokens=compact_length_recovery["max_tokens"],
            temperature=0.0,
            top_p=1.0,
        )
        result = compact_result
        compact_recovery_count = 1
        http_retry_count += int(getattr(compact_result, "http_retry_count", 0))
        usage_attempts.extend(
            getattr(compact_result, "usage_attempts", ())
            or (_compact_provider_envelope(compact_result.response_envelope).get("usage"),)
        )
        raw = compact_result.content
        envelope_summary = _compact_provider_envelope(compact_result.response_envelope)
        parsed, errors = parse_normalize_validate(raw, stage="compact_length_recovery_1")

    if errors:
        error = ResponseValidationError("JSON 검증 실패: " + " | ".join(errors[:30]))
        # Retain bounded repair evidence even when the candidate cannot be
        # checkpointed.  This makes a repeated validation failure auditable
        # without storing raw provider text or accepting an invalid plan.
        error.diagnostics = {
            "semantic_unit_repair": semantic_unit_repair,
            "repair_attempt_count": repair_count,
            "repair_attempts_by_check": check_repair_attempts,
            "check_last_error_signature": check_last_error_signature,
            "candidate_chunk_repair_attempt_count": chunk_repair_attempts,
            "chunk_last_state_signature": chunk_last_state_signature,
            "candidate_chunk_repair_history": candidate_chunk_repair_history,
            "candidate_chunk_last_error_signature": chunk_last_error_signature,
            "atomic_repair_history": atomic_repair_history,
            "final_validation_errors": list(errors),
        }
        raise error
    call_meta = {
        "structured_output_used": result.structured_output_used,
        "structured_output_fallback_count": int(
            getattr(result, "structured_output_fallback_count", 0)
        ),
        "provider_extensions_used": result.provider_extensions_used,
        "source_grounded_retry_attempt_count": source_retry_count,
        "compact_length_recovery_attempt_count": compact_recovery_count,
        "repair_attempt_count": repair_count,
        "repair_attempts_by_check": check_repair_attempts,
        "candidate_chunk_repair_attempt_count": chunk_repair_attempts,
        "candidate_chunk_repair_history": candidate_chunk_repair_history,
        "atomic_repair_history": atomic_repair_history,
        "http_retry_count": http_retry_count,
        "usage": _summarize_usage_attempts(usage_attempts),
        "fixed_field_mismatches": fixed_field_mismatches,
        "canonicalized_fields": [
            {
                "stage": item["stage"],
                "field": mismatch["field"],
                "provider_value": mismatch["provider_value"],
                "canonical_value": mismatch["canonical_value"],
            }
            for item in fixed_field_mismatches
            for mismatch in item["mismatches"]
        ],
    }
    return parsed, raw, envelope_summary, call_meta

# Original func/step04/pass1_engine.py:3993
def create_compact_candidate_plan(
    *,
    package_id: str,
    candidate: dict[str, Any],
    candidate_evidence: dict[str, Any],
    group_summaries: list[dict[str, Any]],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    feature_catalog: dict[str, Any],
    operational_region_catalog: dict[str, Any],
    related_precursor_opportunities: list[dict[str, Any]],
    canonical_schema: dict[str, Any],
    transport_schema: dict[str, Any],
    final_schema: dict[str, Any],
    prompt_template: str,
    prompt_sha256: str,
    client: Pass1ProviderClient,
    model: str,
    structured_output_mode: str,
    max_tokens: int,
    temperature: float,
    top_p: float,
    repair_attempts: int,
    continuation_attempts: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, int], dict[str, str]]:
    """Collect compact OpenAI chunks, then expand them without storing raw text."""
    candidate_id = str(candidate["candidate_id"])
    chunks: list[dict[str, Any]] = []
    call_metas: list[dict[str, Any]] = []
    payload_rows: list[dict[str, int]] = []
    provider_summary: dict[str, Any] = {}
    continuation_count = 0
    no_progress_count = 0
    while True:
        chunk_index = len(chunks) + 1
        continuation_context = (
            _compact_plan_context(chunks, candidate=candidate) if chunks else None
        )
        messages, input_value = build_candidate_plan_messages(
            prompt_template=prompt_template,
            package_id=package_id,
            candidate=candidate,
            candidate_evidence=candidate_evidence,
            group_summaries=group_summaries,
            attachment_manifest=attachment_manifest,
            support_catalog=support_catalog,
            feature_catalog=feature_catalog,
            operational_region_catalog=operational_region_catalog,
            related_precursor_opportunities=related_precursor_opportunities,
            compact_transport=True,
            plan_chunk_index=chunk_index,
            continuation_context=continuation_context,
        )
        payload_row = split_payload_diagnostics(
            model=model,
            messages=messages,
            input_value=input_value,
            response_schema=transport_schema,
            schema_name="AIFSPass1CandidatePlan",
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            enable_thinking=False,
        )
        payload_rows.append(payload_row)
        ensure_split_payload_budget(
            f"compact candidate plan {candidate_id} chunk={chunk_index}", payload_row
        )
        try:
            chunk, _raw, provider_summary, call_meta = call_and_validate_json(
                client=client,
                model=model,
                messages=messages,
                schema=transport_schema,
                schema_name="AIFSPass1CandidatePlan",
                structured_output_mode=structured_output_mode,
                enable_thinking=False,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                repair_attempts=repair_attempts,
                semantic_validator=lambda value, _chunk_index=chunk_index, _chunks=chunks: validate_compact_candidate_plan_response(
                    value,
                    schema=None,
                    candidate=candidate,
                    attachment_manifest=attachment_manifest,
                    support_catalog=support_catalog,
                    operational_region_catalog=operational_region_catalog,
                    expected_chunk_index=_chunk_index,
                    previous_chunks=_chunks,
                ),
                normalizer=lambda value, _chunks=chunks, _chunk_index=chunk_index: _normalize_ai_verification_chunk_for_validation(
                    value,
                    _chunks,
                    chunk_index=_chunk_index,
                    transport_schema=transport_schema,
                    candidate=candidate,
                    attachment_manifest=attachment_manifest,
                ),
                repair_context_messages=messages,
                semantic_unit_repair="diagnostic_check",
                repair_identity={"candidate_id": candidate_id, "stage": "step04"},
                nonrepairable_error_markers=(
                    "vertical_wind_shear is spatial-only",
                    "temporal diagnostic/method must declare check_scope=temporal_evolution",
                    "multivariable_composite must declare check_scope=multivariable_alignment",
                    "section diagnostic must declare check_scope=vertical_structure",
                    "multivariable_alignment requires multivariable_composite and at least two signal components",
                    "vertical_structure requires a section diagnostic",
                    "has invalid required_signal_components",
                    "CHECK_EXECUTION_COMPONENT_UNAVAILABLE",
                ),
                chunk_repair_from_original_context=True,
                chunk_repair_previous_chunks=list(chunks),
            )
        except ProviderIncompleteError as exc:
            diagnostics = dict(exc.diagnostics)
            diagnostics.update(
                {
                    "stage": "candidate_plan",
                    "candidate_id": candidate_id,
                    "prompt_sha256": prompt_sha256,
                    "transport_schema_sha256": canonical_hash(transport_schema),
                    "canonical_schema_sha256": canonical_hash(canonical_schema),
                    "input_sha256": canonical_hash(input_value),
                }
            )
            raise ProviderIncompleteError(str(exc), diagnostics=diagnostics) from exc
        previous_chunks = list(chunks)
        chunk = _normalize_compact_continuation_chunk(chunk, previous_chunks)
        chunks.append(chunk)
        call_metas.append(call_meta)
        (
            coverage,
            missing_roles,
            model_is_complete,
            model_remaining_roles,
        ) = normalize_compact_plan_completion_state(
            chunks,
            candidate=candidate,
        )
        if (
            model_is_complete != chunk["is_complete"]
            or model_remaining_roles != chunk["remaining_roles"]
        ):
            logging.info(
                "step04 candidate plan continuation state canonicalized "
                "candidate_id=%s model_is_complete=%s model_remaining_role_count=%s "
                "actual_remaining_role_count=%s",
                candidate_id,
                model_is_complete,
                len(model_remaining_roles),
                len(chunk["remaining_roles"]),
            )
        if not missing_roles:
            break
        if previous_chunks and not compact_candidate_plan_made_progress(
            previous_chunks,
            chunks,
            candidate=candidate,
        ):
            no_progress_count += 1
            if no_progress_count >= continuation_attempts:
                existing_contracts = [
                    {
                        "check_key": str(check.get("check_key") or ""),
                        "verification_mode": str(
                            check.get("verification_mode")
                            or _verification_mode_from_diagnostic_check(check)
                        ),
                        "coverage_roles": _effective_compact_check_coverage_roles(check),
                    }
                    for existing_chunk in chunks
                    for check in _compact_plan_rows(existing_chunk, "diagnostic_checks")
                ]
                raise ValueError(
                    "candidate plan continuation made no progress "
                    f"candidate_id={candidate_id} remaining_roles={missing_roles} "
                    f"existing_contracts={existing_contracts}"
                )
        else:
            no_progress_count = 0
        continuation_count += 1

    expanded = expand_compact_candidate_plan(
        chunks,
        package_id=package_id,
        candidate=candidate,
        attachment_manifest=attachment_manifest,
        support_catalog=support_catalog,
    )
    # Compact-plan coverage treats the compulsory direct-precipitation Check as
    # SYSTEM-owned, not AI-owned. Insert that Check before validating the
    # expanded canonical plan; otherwise the completeness gate can pass while
    # validate_final_response rejects the plan for the missing SYSTEM request.
    # The caller's later ensure call is idempotent and still post-validates.
    expanded, _ = ensure_system_direct_precipitation_anchor(
        expanded,
        candidate=candidate,
        support_catalog=support_catalog,
    )
    final_errors = validate_candidate_plan_response(
        expanded,
        schema=canonical_schema,
        final_schema=final_schema,
        package_id=package_id,
        candidate=candidate,
        candidate_evidence=candidate_evidence,
        attachment_manifest=attachment_manifest,
        support_catalog=support_catalog,
        feature_catalog=feature_catalog,
        operational_region_catalog=operational_region_catalog,
        require_system_direct_precipitation_anchor=True,
    )
    if final_errors:
        raise ValueError("compact candidate plan expansion failed: " + " | ".join(final_errors[:30]))
    usage_attempts = [
        attempt
        for call_meta in call_metas
        for attempt in call_meta.get("usage", {}).get("attempts", [])
    ]
    aggregate_meta = {
        "structured_output_used": True,
        "structured_output_fallback_count": sum(
            int(item.get("structured_output_fallback_count", 0)) for item in call_metas
        ),
        "provider_extensions_used": False,
        "source_grounded_retry_attempt_count": 0,
        "compact_length_recovery_attempt_count": 0,
        "repair_attempt_count": sum(
            int(item.get("repair_attempt_count", 0)) for item in call_metas
        ),
        "repair_attempts_by_check": {
            check_key: attempts
            for item in call_metas
            for check_key, attempts in item.get("repair_attempts_by_check", {}).items()
        },
        "candidate_chunk_repair_attempt_count": sum(
            int(item.get("candidate_chunk_repair_attempt_count", 0)) for item in call_metas
        ),
        "candidate_chunk_repair_history": [
            row
            for item in call_metas
            for row in item.get("candidate_chunk_repair_history", [])
        ],
        "atomic_repair_history": [
            row
            for item in call_metas
            for row in item.get("atomic_repair_history", [])
        ],
        "http_retry_count": sum(int(item.get("http_retry_count", 0)) for item in call_metas),
        "usage": _summarize_usage_attempts(usage_attempts),
        "fixed_field_mismatches": [],
        "canonicalized_fields": [],
        "compact_plan_chunk_count": len(chunks),
        "continuation_attempt_count": continuation_count,
        "coverage": coverage,
    }
    aggregate_payload = {
        "http_json_utf8_bytes": sum(item["http_json_utf8_bytes"] for item in payload_rows),
        "input_utf8_bytes": sum(item["input_utf8_bytes"] for item in payload_rows),
        "response_schema_utf8_bytes": max(
            (item["response_schema_utf8_bytes"] for item in payload_rows), default=0
        ),
        "chunk_count": len(payload_rows),
    }
    return expanded, provider_summary, aggregate_meta, aggregate_payload, coverage

# Original func/step04/pass1_engine.py:4262
def _single_attachment_batch(
    batch: dict[str, Any],
    attachment: dict[str, Any],
) -> dict[str, Any]:
    value = dict(batch)
    value["attachment_ids"] = [str(attachment["asset_id"])]
    value["attachment_count"] = 1
    value["source_panel_count"] = len(attachment.get("source_panels", []))
    return value

# Original func/step04/pass1_engine.py:4273
def _merge_single_image_observations(
    *,
    package_id: str,
    batch: dict[str, Any],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for observation in observations:
        items = observation.get("attachment_observations")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise ResponseValidationError(
                "single-image fallback did not return exactly one attachment observation"
            )
        rows.append(items[0])
    return {
        "package_id": package_id,
        "batch_id": batch["batch_id"],
        "analysis_tier": batch.get("analysis_tier"),
        "group_id": batch.get("group_id"),
        "observed_attachment_count": len(rows),
        "attachment_observations": rows,
        "cross_product_synthesis": [],
        "batch_uncertainties": [],
    }

# Original func/step04/pass1_engine.py:4299
def run_single_image_observation_fallback(
    *,
    client: Pass1ProviderClient,
    model: str,
    package_id: str,
    batch: dict[str, Any],
    attachments: list[dict[str, Any]],
    root: Path,
    prompt_template: str,
    canonical_schema: dict[str, Any],
    structured_output_mode: str,
    max_tokens: int,
    temperature: float,
    top_p: float,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Use bounded single-image calls only after a complete batch contract failure."""
    started = time.monotonic()
    single_observations: list[dict[str, Any]] = []
    call_metadata: list[dict[str, Any]] = []
    attachment_by_id = {str(item["asset_id"]): item for item in attachments}
    logging.warning(
        "single-image observation fallback start batch_id=%s source_asset_count=%s",
        batch["batch_id"],
        len(attachments),
    )
    for index, attachment in enumerate(attachments, start=1):
        single_batch = _single_attachment_batch(batch, attachment)
        messages = build_batch_messages(
            package_id=package_id,
            batch=single_batch,
            attachments=[attachment],
            root=root,
            prompt_template=prompt_template,
        )
        provider_schema = build_provider_observation_schema(
            canonical_schema,
            batch=single_batch,
            include_batch_synthesis=False,
        )
        logging.info(
            "single-image observation start batch_id=%s index=%s/%s",
            batch["batch_id"],
            index,
            len(attachments),
        )
        observation, _, _, call_meta = call_and_validate_json(
            client=client,
            model=model,
            messages=messages,
            schema=provider_schema,
            schema_name="AIFSPass1SingleImageObservation",
            structured_output_mode=structured_output_mode,
            enable_thinking=False,
            max_tokens=min(max_tokens, COMPACT_OBSERVATION_RECOVERY_MAX_TOKENS),
            temperature=temperature,
            top_p=top_p,
            repair_attempts=1,
            normalizer=lambda value, _batch=single_batch, _attachment=attachment: normalize_batch_observation_contract(
                value,
                package_id=package_id,
                batch=_batch,
                attachments_in_payload_order=[_attachment],
                include_batch_synthesis=False,
            ),
            semantic_validator=lambda value, _batch=single_batch: validate_batch_observation(
                value,
                schema=canonical_schema,
                package_id=package_id,
                batch=_batch,
                attachment_by_id=attachment_by_id,
            ),
            source_retry_attempts=1,
            source_retry_context={
                "required_attachment_count": 1,
                "required_asset_ids": [str(attachment["asset_id"])],
            },
            source_retry_schema=provider_schema,
            source_retry_schema_name="AIFSPass1SingleImageObservation",
            source_retry_before_repair_on_parse_error=True,
        )
        single_observations.append(observation)
        call_metadata.append(call_meta)
    merged = _merge_single_image_observations(
        package_id=package_id,
        batch=batch,
        observations=single_observations,
    )
    errors = validate_batch_observation(
        merged,
        schema=canonical_schema,
        package_id=package_id,
        batch=batch,
        attachment_by_id=attachment_by_id,
    )
    if errors:
        raise ResponseValidationError(
            "single-image fallback validation failed: " + " | ".join(errors[:30])
        )
    aggregated = {
        "structured_output_used": all(
            bool(item.get("structured_output_used")) for item in call_metadata
        ),
        "structured_output_fallback_count": sum(
            int(item.get("structured_output_fallback_count", 0)) for item in call_metadata
        ),
        "provider_extensions_used": [],
        "source_grounded_retry_attempt_count": sum(
            int(item.get("source_grounded_retry_attempt_count", 0)) for item in call_metadata
        ),
        "compact_length_recovery_attempt_count": sum(
            int(item.get("compact_length_recovery_attempt_count", 0)) for item in call_metadata
        ),
        "repair_attempt_count": sum(
            int(item.get("repair_attempt_count", 0)) for item in call_metadata
        ),
        "http_retry_count": sum(
            int(item.get("http_retry_count", 0)) for item in call_metadata
        ),
        "fixed_field_mismatches": [],
        "canonicalized_fields": [],
        "single_image_fallback_asset_count": len(attachments),
    }
    logging.info(
        "single-image observation fallback completed batch_id=%s elapsed=%.3fs",
        batch["batch_id"],
        time.monotonic() - started,
    )
    return merged, {}, aggregated

# Original func/step04/pass1_engine.py:5299
def _required_precipitation_coverage_keys(audit: dict[str, Any]) -> set[tuple[int, str]]:
    """SYSTEM-owned keys; never use AI text as the coverage denominator."""
    return {
        (int(row["forecast_hour"]), str(row["region_id"]))
        for row in _required_uncovered_system_direct_precipitation_signals(audit)
    }

# Original func/step04/pass1_engine.py:5307
def _direct_precipitation_reconciliation_progress(
    before_audit: dict[str, Any],
    after_audit: dict[str, Any],
) -> dict[str, Any]:
    """Reject backward/no-op proposals before updating the working discovery."""
    before = _required_precipitation_coverage_keys(before_audit)
    after = _required_precipitation_coverage_keys(after_audit)
    # Coverage can only be satisfied by new links, never by deleting input signals.
    before_all = {
        (int(row["forecast_hour"]), str(row["region_id"]))
        for row in before_audit.get("signals", [])
        if isinstance(row, dict) and row.get("candidate_generation_required") is True
    }
    after_all = {
        (int(row["forecast_hour"]), str(row["region_id"]))
        for row in after_audit.get("signals", [])
        if isinstance(row, dict) and row.get("candidate_generation_required") is True
    }
    if before_all != after_all:
        raise ValueError("COVERAGE_RECONCILIATION_SIGNAL_LEDGER_CHANGED")
    regained = after - before
    improvement = before - after
    return {
        "before": len(before),
        "after": len(after),
        "newly_covered": [f"F{h:03d}:{r}" for h, r in sorted(improvement)],
        "regressed": [f"F{h:03d}:{r}" for h, r in sorted(regained)],
        "accept": bool(improvement) and not regained,
    }

# Original func/step04/pass1_engine.py:8201
def _stage_provider_configuration_equivalent(
    actual_configuration: Any,
    expected_configuration: Any,
    *,
    stage: str,
) -> bool:
    """Allow an older full configuration only when this stage is unchanged."""
    if actual_configuration == expected_configuration:
        return True
    if not isinstance(actual_configuration, dict) or not isinstance(expected_configuration, dict):
        return False
    shared_fields = (
        "provider",
        "api_family",
        "endpoint_id",
        "image_detail",
        "structured_output_mode",
    )
    if any(actual_configuration.get(field) != expected_configuration.get(field) for field in shared_fields):
        return False
    if expected_configuration.get("api_family") != "responses":
        return False
    reasoning_stage = {
        "batch_observation": "observation",
        "group_summary": "group_summary",
        "candidate_discovery": "candidate_discovery",
        "candidate_plan": "candidate_plan",
    }[stage]
    actual_reasoning = actual_configuration.get("reasoning_efforts")
    expected_reasoning = expected_configuration.get("reasoning_efforts")
    actual_hashes = actual_configuration.get("transport_schema_sha256")
    expected_hashes = expected_configuration.get("transport_schema_sha256")
    return (
        isinstance(actual_reasoning, dict)
        and isinstance(expected_reasoning, dict)
        and actual_reasoning.get(reasoning_stage) == expected_reasoning.get(reasoning_stage)
        and isinstance(actual_hashes, dict)
        and isinstance(expected_hashes, dict)
        and actual_hashes.get(stage) == expected_hashes.get(stage)
    )

# Original func/step04/pass1_engine.py:8243
def _checkpoint_fingerprint_matches(
    checkpoint: dict[str, Any],
    expected_checkpoint: dict[str, Any],
    *,
    provider_stage: str | None = None,
) -> bool:
    if checkpoint.get("fingerprint") == expected_checkpoint.get("fingerprint"):
        return True
    if provider_stage is not None and _stage_provider_configuration_equivalent(
        checkpoint.get("provider_configuration"),
        expected_checkpoint.get("provider_configuration"),
        stage=provider_stage,
    ):
        legacy_expected = {
            key: value for key, value in expected_checkpoint.items() if key != "fingerprint"
        }
        legacy_expected["provider_configuration"] = checkpoint.get("provider_configuration")
        if checkpoint.get("fingerprint") == canonical_hash(legacy_expected):
            logging.info(
                "step04 checkpoint reused compatibility=stage_scoped_configuration stage=%s",
                provider_stage,
            )
            return True
    logging.info("step04 checkpoint invalidated reason=provider_or_fingerprint_mismatch")
    return False

# Original func/step04/pass1_engine.py:8270
def _load_existing_group_summary(
    *,
    summary_path: Path,
    checkpoint_path: Path,
    expected_checkpoint: dict[str, Any],
    schema: dict[str, Any],
    package_id: str,
    context: dict[str, Any],
) -> dict[str, Any] | None:
    if not summary_path.is_file() or not checkpoint_path.is_file():
        return None
    try:
        checkpoint = load_json(checkpoint_path)
        if not _checkpoint_fingerprint_matches(
            checkpoint,
            expected_checkpoint,
            provider_stage="group_summary",
        ):
            return None
        summary = load_json(summary_path)
        if checkpoint.get("summary_sha256") != sha256sum(summary_path):
            return None
        errors = validate_group_summary(
            summary,
            schema=schema,
            package_id=package_id,
            context=context,
        )
        return summary if not errors else None
    except Exception:  # noqa: BLE001
        return None

# Original func/step04/pass1_engine.py:8604
def _candidate_plan_transport_schema_for_operational_scope(
    schema: dict[str, Any],
    *,
    candidate: dict[str, Any],
    operational_region_catalog: dict[str, Any],
) -> dict[str, Any]:
    """Constrain compact provider output to modes executable for this candidate.

    This is an execution-capability constraint only; it does not choose a
    meteorological conclusion.  For a source-corridor/precursor candidate with
    no operational overlap, the provider can still select spatial/vertical
    verification, but cannot emit a mode that deterministically expands to
    regional statistics and would be rejected downstream.
    """
    scoped = copy.deepcopy(schema)
    overlap = _candidate_operational_region_overlap(
        candidate=candidate,
        operational_region_catalog=operational_region_catalog,
    )
    allowed = list(overlap["allowed_verification_modes"])
    try:
        mode_schema = (
            scoped["properties"]["verification_requests"]["items"]
            ["properties"]["verification_mode"]
        )
    except (KeyError, TypeError):
        return scoped
    if isinstance(mode_schema, dict):
        mode_schema["enum"] = allowed

    # Quantitative claims are executable only against candidate-overlapping
    # operational regions.  Constrain the transport contract so the provider
    # cannot select a valid catalog region that is outside this candidate, and
    # force quantitative_scope=null when there is no regional target at all.
    try:
        quantitative_scope_schema = (
            scoped["properties"]["verification_requests"]["items"]["properties"]
            ["atomic_claims"]["items"]["properties"]["quantitative_scope"]
        )
    except (KeyError, TypeError):
        return scoped
    selectable_region_ids = [
        str(item.get("region_id"))
        for item in overlap.get("selectable_regions", [])
        if isinstance(item, dict) and str(item.get("region_id") or "")
    ]
    if not overlap.get("regional_verification_available"):
        scoped["properties"]["verification_requests"]["items"]["properties"][
            "atomic_claims"
        ]["items"]["properties"]["quantitative_scope"] = {"type": "null"}
    elif isinstance(quantitative_scope_schema, dict):
        for option in quantitative_scope_schema.get("anyOf", []):
            if not isinstance(option, dict) or option.get("type") != "object":
                continue
            region_items = (
                option.get("properties", {}).get("region_ids", {}).get("items")
            )
            if isinstance(region_items, dict):
                region_items["enum"] = selectable_region_ids
    return scoped

# Original func/step04/pass1_engine.py:9792
def _candidate_evidence_for(
    discovery_response: dict[str, Any],
    candidate_id: str,
) -> dict[str, Any]:
    matches = [
        item
        for item in discovery_response.get("candidate_evidence", [])
        if isinstance(item, dict) and str(item.get("candidate_id")) == candidate_id
    ]
    if len(matches) != 1:
        raise Pass1Error(
            f"candidate evidence cardinality mismatch candidate_id={candidate_id} count={len(matches)}"
        )
    return matches[0]

# Original func/step04/pass1_engine.py:9808
def _related_precursor_opportunities(
    discovery_response: dict[str, Any], candidate_id: str
) -> list[dict[str, Any]]:
    """Return immutable Step04 opportunity provenance relevant to one candidate."""
    return [
        item
        for item in discovery_response.get("precursor_opportunities", [])
        if isinstance(item, dict)
        and (
            candidate_id in item.get("related_direct_candidate_ids", [])
            or item.get("created_candidate_id") == candidate_id
        )
    ]

# Original func/step04/pass1_engine.py:10115
def _candidate_discovery_payload_component_bytes(
    input_value: dict[str, Any],
) -> dict[str, int]:
    keys = (
        "group_summaries",
        "asset_index",
        "supported_spatial_products",
        "precipitation_peak_guidance",
        "system_direct_precipitation_coverage_guidance",
        "candidate_operational_scope",
        "precursor_candidate_contract",
        "response_compactness",
    )
    return {
        f"{key}_utf8_bytes": json_utf8_bytes(input_value.get(key))
        for key in keys
        if key in input_value
    }

# Original func/step04/pass1_engine.py:11371
def _compact_check_combinations(rows: list[dict[str, Any]]) -> set[tuple[str, str, str, str]]:
    """Return execution-meaningful diagnostic combinations.

    A compact check is unique by diagnostic, variable role, selected domain scope,
    and analysis phase.  Actual BBOX coordinates are candidate-plan fields rather
    than per-check values.
    """
    combinations: set[tuple[str, str, str, str]] = set()
    for row in rows:
        diagnostic_id = str(row.get("diagnostic_id", ""))
        variable_role = str(row.get("variable_role", ""))
        domain_scope = str(row.get("domain_scope", ""))
        phases = row.get("phase_keys", [])
        if not isinstance(phases, list):
            raise ValueError("compact diagnostic check phase_keys must be an array")
        for phase_key in phases:
            combinations.add(
                (diagnostic_id, variable_role, domain_scope, str(phase_key))
            )
    return combinations

# Original func/step04/pass1_engine.py:13405
def _limit_canonical_hypotheses(
    hypotheses: list[dict[str, Any]],
    *,
    candidate_number: str,
) -> list[dict[str, Any]]:
    """Select the bounded canonical hypotheses with the broadest check coverage."""
    if len(hypotheses) <= CANONICAL_PLAN_MAX_HYPOTHESES:
        return hypotheses

    remaining = list(enumerate(hypotheses))
    selected: list[tuple[int, dict[str, Any]]] = []
    covered_check_ids: set[str] = set()
    while remaining and len(selected) < CANONICAL_PLAN_MAX_HYPOTHESES:
        def score(item: tuple[int, dict[str, Any]]) -> tuple[int, int]:
            index, hypothesis = item
            referenced = {
                str(value)
                for field in ("supporting_check_ids", "opposing_check_ids")
                for value in hypothesis.get(field, [])
            }
            return len(referenced - covered_check_ids), -index

        chosen = max(remaining, key=score)
        remaining.remove(chosen)
        selected.append(chosen)
        covered_check_ids.update(
            str(value)
            for field in ("supporting_check_ids", "opposing_check_ids")
            for value in chosen[1].get(field, [])
        )

    limited: list[dict[str, Any]] = []
    for output_index, (_source_index, hypothesis) in enumerate(sorted(selected), start=1):
        value = copy.deepcopy(hypothesis)
        value["hypothesis_id"] = f"H_CAND{candidate_number}_{output_index:02d}"
        limited.append(value)
    return limited

# Original func/step04/pass1_engine.py:13444
def _limit_canonical_questions(questions: list[str]) -> list[str]:
    """Keep the provider's relevance order while removing exact question duplicates."""
    seen: set[str] = set()
    unique: list[str] = []
    for question in questions:
        value = str(question).strip()
        key = re.sub(r"\s+", " ", value).casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique[:CANONICAL_PLAN_MAX_FORECASTER_QUESTIONS]

# Original func/step04/pass1_engine.py:14351
def compact_candidate_plan_made_progress(
    previous_chunks: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    *,
    candidate: dict[str, Any],
) -> bool:
    """Return whether a continuation reduced required coverage gaps.

    Continuation exists to complete candidate-plan coverage. Adding a novel
    phase or Check that does not cover any previously missing required role is
    not progress toward completion and therefore consumes the no-progress
    safety budget. This keeps the loop bounded even after removing the old
    total-continuation cap.
    """
    if len(chunks) <= len(previous_chunks):
        return False
    before_coverage = evaluate_compact_candidate_plan_coverage(
        previous_chunks,
        candidate=candidate,
    )
    after_coverage = evaluate_compact_candidate_plan_coverage(chunks, candidate=candidate)
    before_missing = {
        role for role, status in before_coverage.items() if status == "missing_but_relevant"
    }
    after_missing = {
        role for role, status in after_coverage.items() if status == "missing_but_relevant"
    }
    return bool(before_missing - after_missing)

# Original func/step04/pass1_engine.py:14586
def _deduplicate_compact_phases(
    phase_rows: list[dict[str, Any]],
    check_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge only exact continuation phase restatements.

    Distinct forecast-hour windows remain separate even when they share the same
    phase type.
    """
    retained: list[dict[str, Any]] = []
    canonical_by_signature: dict[tuple[str, tuple[int, ...]], str] = {}
    aliases: dict[str, str] = {}
    for row in phase_rows:
        local_key = str(row.get("phase_key", "")).strip()
        if not local_key:
            retained.append(row)
            continue
        signature = _compact_phase_signature(row)
        canonical_key = canonical_by_signature.get(signature)
        if canonical_key is None:
            canonical_by_signature[signature] = local_key
            aliases[local_key] = local_key
            retained.append(row)
        else:
            aliases[local_key] = canonical_key
    for row in check_rows:
        row["phase_keys"] = list(
            dict.fromkeys(
                aliases.get(str(key), str(key))
                for key in row.get("phase_keys", [])
            )
        )
        claims = row.get("atomic_claims")
        if isinstance(claims, list):
            for claim in claims:
                if not isinstance(claim, dict):
                    continue
                claim["phase_keys"] = list(
                    dict.fromkeys(
                        aliases.get(str(key), str(key))
                        for key in claim.get("phase_keys", [])
                    )
                )
    return retained

# Original func/step04/pass1_engine.py:15711
def expand_compact_candidate_plan(
    compact_chunks: list[dict[str, Any]],
    *,
    package_id: str,
    candidate: dict[str, Any],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
) -> dict[str, Any]:
    """Expand AI-selected candidate/context domains into canonical Step05 requests."""
    if not compact_chunks:
        raise ValueError("compact candidate plan has no chunks")
    candidate_id = str(candidate.get("candidate_id"))
    match = re.fullmatch(r"CAND_(\d{3})", candidate_id)
    if match is None:
        raise ValueError(f"candidate_id is not canonical: {candidate_id}")
    number = match.group(1)
    phase_rows: list[dict[str, Any]] = []
    check_rows: list[dict[str, Any]] = []
    hypothesis_rows: list[dict[str, Any]] = []
    questions: list[str] = []
    objective: str | None = None
    context_bbox: dict[str, float] | None = None
    context_reason: str | None = None
    for chunk in compact_chunks:
        chunk_objective = str(chunk.get("analysis_objective", "")).strip()
        if not objective:
            objective = chunk_objective
        elif chunk_objective != objective:
            logging.info(
                "step04 compact candidate plan continuation objective ignored candidate_id=%s",
                candidate_id,
            )
        chunk_context = _normalized_bbox_for_validation(chunk.get("context_domain"))
        chunk_reason_raw = chunk.get("context_domain_reason")
        chunk_reason = str(chunk_reason_raw).strip() if chunk_reason_raw is not None else None
        if context_bbox is None and chunk_context is not None:
            context_bbox = chunk_context
            context_reason = chunk_reason
        elif context_bbox != chunk_context or context_reason != chunk_reason:
            raise ValueError("compact candidate plan continuation changed context_domain")
        phase_rows.extend(_compact_plan_rows(chunk, "analysis_phases"))
        check_rows.extend(_compact_plan_rows(chunk, "diagnostic_checks"))
        hypothesis_rows.extend(_compact_plan_rows(chunk, "hypotheses"))
        questions.extend(str(item) for item in chunk.get("forecaster_questions", []))
    questions = list(dict.fromkeys(question.strip() for question in questions if question.strip()))
    hypothesis_rows = _drop_hypotheses_without_supporting_checks(
        hypothesis_rows,
        candidate_id=candidate_id,
        field_suffix="keys",
        require_one=True,
    )
    phase_rows = _deduplicate_compact_phases(phase_rows, check_rows)
    check_rows, hypothesis_rows = _canonicalize_semantic_duplicate_compact_plan(
        check_rows,
        hypothesis_rows,
        phase_rows,
        candidate_id=candidate_id,
    )
    _compact_local_keys(phase_rows, "phase_key", label="merged analysis_phases")
    _compact_local_keys(check_rows, "check_key", label="merged diagnostic_checks")
    _compact_local_keys(hypothesis_rows, "hypothesis_key", label="merged hypotheses")
    _compact_check_combinations(check_rows)

    candidate_bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
    if candidate_bbox is None:
        raise ValueError("compact candidate plan candidate_area bbox is invalid")
    if context_bbox is not None:
        if not _bbox_contains(context_bbox, candidate_bbox):
            raise ValueError("context_domain must contain candidate_area")
        available_domain = _available_context_domain(support_catalog)
        if not _bbox_contains(available_domain, context_bbox):
            raise ValueError(
                f"context_domain is outside available context domain: available={available_domain}"
            )
        if not context_reason:
            raise ValueError("context_domain requires context_domain_reason")

    has_regional = any(
        bool({str(item) for item in row.get("analysis_methods", [])} & REGIONAL_ANALYSIS_METHODS)
        for row in check_rows
    )
    targets: list[dict[str, Any]] = []
    target_id_by_scope: dict[str, str] = {}
    candidate_target_id = f"AREA_CAND{number}_01"
    target_id_by_scope["candidate"] = candidate_target_id
    targets.append({
        "target_id": candidate_target_id,
        "target_type": "candidate_domain",
        "resolution_method": "explicit_bbox",
        "requested_domain": candidate_bbox,
        "region_ids": [],
        "purpose": "후보 위험 핵심영역의 공간장과 정량 신호를 검증합니다.",
    })
    if context_bbox is not None:
        context_target_id = f"AREA_CAND{number}_{len(targets)+1:02d}"
        target_id_by_scope["context"] = context_target_id
        targets.append({
            "target_id": context_target_id,
            "target_type": "context_domain",
            "resolution_method": "explicit_bbox",
            "requested_domain": context_bbox,
            "region_ids": [],
            "purpose": context_reason,
        })
    if has_regional:
        operational_target_id = f"AREA_CAND{number}_{len(targets)+1:02d}"
        target_id_by_scope["operational_regions"] = operational_target_id
        targets.append({
            "target_id": operational_target_id,
            "target_type": "operational_regions",
            "resolution_method": "candidate_area_overlap",
            "requested_domain": None,
            "region_ids": [],
            "purpose": "후보 위험영역과 겹치는 운영지역의 통계·순위·최대값·시간변화를 검증합니다.",
        })

    available_phase_hours = set(
        _available_candidate_forecast_hours(candidate, attachment_manifest)
    )
    if not available_phase_hours:
        raise ValueError("compact candidate plan cannot derive available forecast hours")
    phase_ids: dict[str, str] = {}
    phases: list[dict[str, Any]] = []
    for index, row in enumerate(phase_rows, start=1):
        key = str(row["phase_key"])
        phase_type = str(row.get("phase_type"))
        hours = list(_compact_phase_hours(row))
        invalid_hours = sorted(set(hours) - available_phase_hours)
        if invalid_hours:
            raise ValueError(
                "compact candidate plan phase uses unavailable forecast hours "
                f"phase_key={key} invalid={invalid_hours}"
            )
        phase_ids[key] = f"PHASE_CAND{number}_{index:02d}"
        phases.append({
            "phase_id": phase_ids[key],
            "phase_type": phase_type,
            "forecast_hours": hours,
            "purpose": row.get("purpose"),
        })

    check_ids: dict[str, str] = {}
    product_ids_by_check_id: dict[str, str] = {}
    checks: list[dict[str, Any]] = []
    for index, row in enumerate(check_rows, start=1):
        local_key = str(row["check_key"])
        check_id = f"CHK_CAND{number}_{index:02d}"
        check_ids[local_key] = check_id
        role = str(row.get("variable_role"))
        diagnostic_id = str(row.get("diagnostic_id"))
        methods = _canonical_analysis_methods_for_diagnostic(
            diagnostic_id,
            row.get("analysis_methods", []),
        )
        row["analysis_methods"] = methods
        check_scope = str(row.get("check_scope") or _derived_check_scope(diagnostic_id, methods))
        required_signal_components = _normalized_required_signal_components(
            row.get("required_signal_components"),
            variable_role=role,
            diagnostic_id=diagnostic_id,
        )
        _validate_check_scope_contract(
            check_key=local_key,
            diagnostic_id=diagnostic_id,
            analysis_methods=methods,
            check_scope=check_scope,
            required_signal_components=required_signal_components,
        )
        product_id = _preferred_product_id_for_check(
            diagnostic_id, role, required_signal_components, support_catalog, candidate
        )
        product_ids_by_check_id[check_id] = product_id
        product_signal_components = _product_signal_components_from_support(
            product_id, support_catalog
        )
        if product_signal_components and not set(required_signal_components).issubset(product_signal_components):
            raise ValueError(
                f"check_key={local_key} required_signal_components={required_signal_components} "
                f"cannot be provided by product_id={product_id!r}; available={sorted(product_signal_components)}"
            )
        scope = str(row.get("domain_scope"))
        if scope == "context" and "context" not in target_id_by_scope:
            raise ValueError(f"check_key={local_key} requests context without context_domain")
        if diagnostic_id in CANDIDATE_SCOPE_ONLY_DIAGNOSTIC_IDS and scope != "candidate":
            raise ValueError(
                f"check_key={local_key} diagnostic_id={diagnostic_id} must use candidate domain"
            )
        target_values: list[str] = []
        if set(methods) & SPATIAL_ANALYSIS_METHODS:
            target_values.append(target_id_by_scope[scope])
        quantitative_feature_ids = (
            []
            if diagnostic_id in SPATIAL_ONLY_DIAGNOSTIC_IDS
            else _quantitative_feature_ids_for_check(diagnostic_id, product_id, support_catalog)
        )
        if set(methods) & REGIONAL_ANALYSIS_METHODS:
            if "operational_regions" not in target_id_by_scope:
                raise ValueError("regional analysis method requires operational_regions target")
            if not quantitative_feature_ids:
                raise ValueError(
                    "regional analysis requires quantitative feature mapping "
                    f"product_id={product_id}"
                )
            target_values.append(target_id_by_scope["operational_regions"])
        canonical_phase_ids = [phase_ids[str(key)] for key in row.get("phase_keys", [])]
        intent_contract = _build_check_intent_contract(
            row,
            check_id=check_id,
            phase_ids=canonical_phase_ids,
        )
        intent_hash = canonical_hash(intent_contract)
        if int(intent_contract.get("merged_source_intent_count") or 0) > 1:
            logging.info(
                "STEP04_INTENT_MERGE_AUDIT check_id=%s source_intents=%s",
                check_id,
                intent_contract.get("merged_source_intent_count"),
            )
        atomic_claim_contract = _build_atomic_claim_contract(
            row,
            check_id=check_id,
        )
        atomic_claim_hash = canonical_hash(atomic_claim_contract)
        atomic_claim_validation = _shadow_validate_atomic_claim_contract(
            check_id=check_id,
            row=row,
            contract=atomic_claim_contract,
        )
        for atomic_claim in atomic_claim_contract.get("canonical_claims", []):
            scope_value = atomic_claim.get("quantitative_scope") if isinstance(atomic_claim, dict) else None
            feature_value = scope_value.get("feature_id") if isinstance(scope_value, dict) else None
            if feature_value is not None and feature_value not in quantitative_feature_ids:
                raise ValueError(
                    f"check_key={local_key} atomic quantitative_scope feature_id={feature_value!r} "
                    f"is outside executable quantitative features={quantitative_feature_ids}"
                )
        capability_profile = _capability_profile_for_check(
            support_catalog,
            diagnostic_id=diagnostic_id,
            product_id=product_id,
        )
        capability_validation = _shadow_validate_check_capability(
            check_id=check_id,
            intent_contract=intent_contract,
            capability_profile=capability_profile,
        )
        checks.append({
            "check_id": check_id,
            "diagnostic_id": row.get("diagnostic_id"),
            "variable_role": role,
            "check_scope": check_scope,
            "required_signal_components": required_signal_components,
            "product_id": product_id,
            "feature_ids": quantitative_feature_ids,
            "target_ids": list(dict.fromkeys(target_values)),
            "phase_ids": canonical_phase_ids,
            "analysis_methods": methods,
            "expected_signal": row.get("expected_signal"),
            "decision_relevance": row.get("decision_relevance"),
            "priority": row.get("priority"),
            "cross_section_line": _cross_section_line_signature(row.get("cross_section_line")),
            "cross_section_strategy": _normalized_cross_section_strategy(row.get("cross_section_strategy")),
            "reference_band_axis": _reference_band_axis_signature(row.get("reference_band_axis")),
            "intent_contract": intent_contract,
            "intent_hash": intent_hash,
            "atomic_claim_contract": atomic_claim_contract,
            "atomic_claim_hash": atomic_claim_hash,
            "atomic_claim_validation": atomic_claim_validation,
            "capability_profile": capability_profile,
            "capability_validation": capability_validation,
        })

    hypotheses: list[dict[str, Any]] = []
    for index, row in enumerate(hypothesis_rows, start=1):
        def mapped(field: str) -> list[str]:
            keys = [str(item) for item in row.get(field, [])]
            unknown = sorted(set(keys) - set(check_ids))
            if unknown:
                raise ValueError(f"compact hypothesis references unknown check_keys={unknown}")
            return list(dict.fromkeys(check_ids[key] for key in keys))
        hypotheses.append({
            "hypothesis_id": f"H_CAND{number}_{index:02d}",
            "statement": row.get("statement"),
            "supporting_check_ids": mapped("supporting_check_keys"),
            "opposing_check_ids": mapped("opposing_check_keys"),
        })
    _canonicalize_disjoint_hypothesis_check_references(
        hypotheses,
        candidate_id=candidate_id,
    )
    hypotheses = _drop_hypotheses_without_supporting_checks(
        hypotheses,
        candidate_id=candidate_id,
        field_suffix="ids",
        require_one=True,
    )
    hypotheses = _limit_canonical_hypotheses(hypotheses, candidate_number=number)
    questions = _limit_canonical_questions(questions)

    phase_hours = {str(item["phase_id"]): item["forecast_hours"] for item in phases}
    requests: list[dict[str, Any]] = []
    for row in check_rows:
        check_id = check_ids[str(row["check_key"])]
        product_id = product_ids_by_check_id[check_id]
        request_phase_ids = [phase_ids[str(key)] for key in row.get("phase_keys", [])]
        forecast_hours = sorted({hour for phase_id in request_phase_ids for hour in phase_hours[phase_id]})
        allowed_hours = _allowed_forecast_hours_for_product(product_id, support_catalog)
        if allowed_hours is not None:
            forecast_hours = [hour for hour in forecast_hours if hour in allowed_hours]
        if not forecast_hours:
            raise ValueError(
                f"compact candidate plan has no supported forecast hours check_key={row['check_key']}"
            )
        minimum_hours = _minimum_forecast_hours_for_check(
            str(row.get("diagnostic_id") or ""),
            str(row.get("check_scope") or ""),
        )
        if len(set(forecast_hours)) < minimum_hours:
            raise ValueError(
                "compact candidate plan has insufficient forecast-hour coverage "
                f"check_key={row['check_key']} required={minimum_hours} actual={len(set(forecast_hours))}"
            )
        methods = list(dict.fromkeys(str(item) for item in row.get("analysis_methods", [])))
        spatial_methods = [item for item in methods if item in SPATIAL_ANALYSIS_METHODS]
        regional_methods = [item for item in methods if item in REGIONAL_ANALYSIS_METHODS]
        common = {
            "candidate_id": candidate_id,
            "plan_id": f"PLAN_CAND{number}",
            "diagnostic_id": row.get("diagnostic_id"),
            "forecast_hours": forecast_hours,
            "check_id": check_id,
            "source_check_ids": [check_id],
            "phase_ids": request_phase_ids,
            "purpose": row.get("purpose"),
            "priority": row.get("priority"),
            "cross_section_line": _cross_section_line_signature(row.get("cross_section_line")),
            "cross_section_strategy": _normalized_cross_section_strategy(row.get("cross_section_strategy")),
            "reference_band_axis": _reference_band_axis_signature(row.get("reference_band_axis")),
        }
        if spatial_methods:
            scope = str(row.get("domain_scope"))
            bbox = candidate_bbox if scope == "candidate" else context_bbox
            if bbox is None:
                raise ValueError(f"check_key={row['check_key']} has unresolved spatial domain")
            spatial_forecast_hours = forecast_hours
            if str(row.get("verification_mode") or _verification_mode_from_diagnostic_check(row)) == "temporal_change":
                representative_hours = [
                    int(value)
                    for value in row.get("representative_spatial_hours", [])
                    if isinstance(value, int) and not isinstance(value, bool)
                ]
                spatial_forecast_hours = [hour for hour in representative_hours if hour in set(forecast_hours)]
                if not spatial_forecast_hours:
                    raise ValueError(
                        f"compact temporal_change has no executable representative_spatial_hours check_key={row['check_key']}"
                    )
            requests.append({
                **common,
                "forecast_hours": spatial_forecast_hours,
                "request_id": f"REQ_CAND{number}_{len(requests)+1:03d}",
                "data_type": "spatial_field",
                "product_id": product_id,
                "feature_ids": [],
                "requested_domain": bbox,
                "target_id": target_id_by_scope[scope],
                "analysis_methods": spatial_methods,
            })
        if regional_methods:
            feature_ids = _quantitative_feature_ids_for_check(
                str(row.get("diagnostic_id") or ""),
                product_id,
                support_catalog,
            )
            statistics = ["valid_grid_count", "mean", "min", "max", "p95"]
            if "regional_max_location" in regional_methods:
                statistics.append("max_location")
            requests.append({
                **common,
                "request_id": f"REQ_CAND{number}_{len(requests)+1:03d}",
                "data_type": "quantitative_excerpt",
                "product_id": product_id,
                "feature_ids": feature_ids,
                "requested_domain": None,
                "target_id": target_id_by_scope["operational_regions"],
                "analysis_methods": regional_methods,
                "output_spec": {
                    "statistics": statistics,
                    "ranking_statistic": "p95",
                    "top_n": 20,
                    "include_time_series": "temporal_evolution" in regional_methods,
                    "include_max_location": "regional_max_location" in regional_methods,
                },
            })

    requests = _merge_analysis_data_requests(requests, candidate_number=number)

    plan = {
        "plan_id": f"PLAN_CAND{number}",
        "candidate_id": candidate_id,
        "analysis_objective": objective,
        "hypotheses": hypotheses,
        "target_areas": targets,
        "analysis_phases": phases,
        "variable_checks": checks,
        "forecaster_questions": questions,
    }
    _link_unreferenced_variable_checks(plan)
    _prune_unreferenced_context_phases(plan)
    return {
        "package_id": package_id,
        "candidate_id": candidate_id,
        "candidate_analysis_plan": plan,
        "analysis_data_requests": requests,
    }

# Original func/step04/pass1_engine.py:16158
def ensure_split_payload_budget(stage: str, diagnostics: dict[str, int]) -> None:
    actual = diagnostics["http_json_utf8_bytes"]
    if actual > HIERARCHICAL_FINAL_MAX_BYTES:
        raise Pass1Error(
            f"{stage} payload exceeds hard limit {HIERARCHICAL_FINAL_MAX_BYTES} UTF-8 bytes: "
            + json.dumps(diagnostics, ensure_ascii=True, sort_keys=True)
        )
    if actual > HIERARCHICAL_PAYLOAD_WARN_BYTES:
        logging.warning(
            "%s payload exceeds warning threshold %s UTF-8 bytes "
            "but is within hard limit %s; continuing: %s",
            stage,
            HIERARCHICAL_PAYLOAD_WARN_BYTES,
            HIERARCHICAL_FINAL_MAX_BYTES,
            json.dumps(diagnostics, ensure_ascii=True, sort_keys=True),
        )

# Original func/step04/pass1_engine.py:16180
def _load_checkpointed_json(
    *,
    value_path: Path,
    checkpoint_path: Path,
    expected_checkpoint: dict[str, Any],
    provider_stage: str,
    validator,
) -> dict[str, Any] | None:
    if not value_path.is_file() or not checkpoint_path.is_file():
        return None
    try:
        checkpoint = load_json(checkpoint_path)
        if not _checkpoint_fingerprint_matches(
            checkpoint,
            expected_checkpoint,
            provider_stage=provider_stage,
        ):
            return None
        if checkpoint.get("value_sha256") != sha256sum(value_path):
            return None
        value = load_json(value_path)
        return value if not validator(value) else None
    except Exception:  # noqa: BLE001
        return None

# Original func/step04/pass1_engine.py:16206
def create_or_resume_candidate_discovery(
    *,
    root: Path,
    output_dir: Path,
    package_id: str,
    group_summaries: list[dict[str, Any]],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    overview_data: dict[str, Any],
    schema: dict[str, Any],
    provider_schema: dict[str, Any] | None = None,
    prompt_template: str,
    prompt_sha256: str,
    client: Pass1ProviderClient,
    model: str,
    structured_output_mode: str,
    enable_thinking: bool,
    max_tokens: int,
    temperature: float,
    top_p: float,
    repair_attempts: int,
    resume: bool,
    reuse_as_immutable_input: bool = False,
    output_policy: OutputPolicy | None = None,
    checkpoint_publisher: Callable[[str, str], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
    output_policy = output_policy or OutputPolicy("operational")
    checkpoint_provider_schema = provider_schema or schema
    runtime_provider_schema = build_candidate_discovery_provider_schema(
        checkpoint_provider_schema,
        attachment_manifest=attachment_manifest,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "candidate_discovery.raw.txt"
    value_path = output_dir / "candidate_discovery.validated.json"
    provider_path = output_dir / "candidate_discovery.provider.json"
    checkpoint_path = output_dir / "candidate_discovery.checkpoint.json"
    system_precipitation_audit_path = output_dir / "system_direct_precipitation_coverage_audit.json"
    coverage_reconciliation_path = output_dir / "system_direct_precipitation_coverage_reconciliation.json"

    def persist_system_precipitation_audit(value: dict[str, Any]) -> dict[str, Any]:
        audit = build_system_direct_precipitation_coverage_audit(
            value,
            overview_data=overview_data,
            attachment_manifest=attachment_manifest,
            support_catalog=support_catalog,
        )
        write_json(system_precipitation_audit_path, audit)
        logging.info(
            "step04 SYSTEM direct precipitation coverage audit signals=%s covered=%s "
            "required_uncovered=%s advisory_uncovered=%s",
            audit.get("signal_count", 0),
            audit.get("covered_signal_count", 0),
            audit.get("required_uncovered_count", 0),
            audit.get("advisory_uncovered_count", 0),
        )
        return audit

    checkpoint = _checkpoint_contract(
        {
            "package_id": package_id,
            "group_summaries_fingerprint": canonical_hash(group_summaries),
            "candidate_discovery_input_projection_version": CANDIDATE_DISCOVERY_INPUT_PROJECTION_VERSION,
            "candidate_discovery_group_summaries_projection_fingerprint": canonical_hash(
                _candidate_discovery_group_summary_projection(group_summaries)
            ),
            "attachment_index_fingerprint": canonical_hash(
                _candidate_discovery_asset_index(attachment_manifest)
            ),
            "support_catalog_fingerprint": canonical_hash(
                _minimal_support_catalog(support_catalog).get("spatial_field_products", [])
            ),
            "precipitation_peak_guidance_fingerprint": canonical_hash(
                build_precipitation_peak_guidance(
                    overview_data, minimum_regional_max_mm=_peak_anchor_minimum_mm(support_catalog)
                )
            ),
            "system_direct_precipitation_coverage_audit_version": SYSTEM_DIRECT_PRECIPITATION_COVERAGE_AUDIT_VERSION,
            "direct_precipitation_coverage_reconciliation_version": DIRECT_PRECIPITATION_COVERAGE_RECONCILIATION_VERSION,
            "system_direct_precipitation_coverage_guidance_fingerprint": canonical_hash(
                build_system_direct_precipitation_coverage_guidance(
                    overview_data, support_catalog=support_catalog
                )
            ),
            "candidate_operational_scope_fingerprint": canonical_hash(
                _candidate_discovery_operational_scope(overview_data)
            ),
            "candidate_operational_scope_contract_version": CANDIDATE_DISCOVERY_OPERATIONAL_SCOPE_CONTRACT_VERSION,
            "prompt_sha256": prompt_sha256,
            "schema_fingerprint": canonical_hash(
                {"canonical": schema, "provider": checkpoint_provider_schema}
            ),
            "canonical_schema_sha256": canonical_hash(schema),
            "provider_configuration": _client_checkpoint_configuration(
                client,
                stage="candidate_discovery",
            ),
            "model": model,
            "contract_version": SPLIT_SYNTHESIS_CONTRACT_VERSION,
            "meteorological_qc_version": CANDIDATE_DISCOVERY_METEOROLOGICAL_QC_VERSION,
            "narrative_language": NARRATIVE_LANGUAGE,
        }
    )
    validator = lambda value: validate_candidate_discovery_response(
        value,
        schema=schema,
        package_id=package_id,
        attachment_manifest=attachment_manifest,
        support_catalog=support_catalog,
        group_summaries=group_summaries,
        overview_data=overview_data,
        enforce_system_direct_precipitation_coverage=True,
    )
    discovery_validator_without_system_coverage = lambda value: validate_candidate_discovery_response(
        value,
        schema=schema,
        package_id=package_id,
        attachment_manifest=attachment_manifest,
        support_catalog=support_catalog,
        group_summaries=group_summaries,
        overview_data=overview_data,
        enforce_system_direct_precipitation_coverage=False,
    )
    if reuse_as_immutable_input:
        if not resume:
            raise Pass1Error("candidate plan-only execution requires candidate discovery resume")
        if not value_path.is_file() or not checkpoint_path.is_file():
            raise Pass1Error(
                "candidate plan-only execution requires an existing candidate discovery checkpoint"
            )
        try:
            frozen_checkpoint = load_json(checkpoint_path)
            frozen_value = load_json(value_path)
        except Exception as exc:  # noqa: BLE001
            raise Pass1Error(
                "candidate plan-only execution could not read the candidate discovery checkpoint"
            ) from exc
        if frozen_checkpoint.get("provider_configuration") != _client_checkpoint_configuration(
            client,
            stage="candidate_discovery",
        ):
            raise Pass1Error(
                "candidate plan-only execution requires a candidate discovery checkpoint for the active provider configuration"
            )
        frozen_value, frozen_qc = _apply_candidate_discovery_meteorological_qc(
            frozen_value,
            overview_data=overview_data,
        )
        errors = validator(frozen_value)
        if errors:
            raise Pass1Error(
                "candidate plan-only execution found an invalid candidate discovery checkpoint: "
                + " | ".join(errors[:10])
            )
        system_precipitation_audit = persist_system_precipitation_audit(frozen_value)
        logging.info(
            "step05 candidate discovery reused as immutable input candidates=%s provider_call_count=0",
            len(frozen_value.get("candidates", [])),
        )
        return (
            frozen_value,
            {
                "status": "reused_as_immutable_input",
                "validated_ref": rel(root, value_path),
                "checkpoint_ref": rel(root, checkpoint_path),
                "candidate_count": len(frozen_value.get("candidates", [])),
                "meteorological_qc_candidate_count": len(frozen_qc),
                "system_direct_precipitation_coverage_audit_ref": rel(root, system_precipitation_audit_path),
                "system_direct_precipitation_required_uncovered_count": int(system_precipitation_audit.get("required_uncovered_count", 0)),
                "system_direct_precipitation_advisory_uncovered_count": int(system_precipitation_audit.get("advisory_uncovered_count", 0)),
                "elapsed_seconds": 0.0,
                "provider_call_count": 0,
            },
            {
                "http_json_utf8_bytes": 0,
                "message_utf8_bytes": 0,
                "input_utf8_bytes": 0,
                "schema_utf8_bytes": 0,
            },
        )
    existing = None
    if resume:
        existing = _load_checkpointed_json(
            value_path=value_path,
            checkpoint_path=checkpoint_path,
            expected_checkpoint=checkpoint,
            provider_stage="candidate_discovery",
            validator=validator,
        )
    messages, input_value = build_candidate_discovery_messages(
        prompt_template=prompt_template,
        package_id=package_id,
        group_summaries=group_summaries,
        attachment_manifest=attachment_manifest,
        support_catalog=support_catalog,
        overview_data=overview_data,
    )
    if structured_output_mode == "off":
        messages = add_prompt_only_schema_constraint(messages, runtime_provider_schema)
    diagnostics = split_payload_diagnostics(
        model=model,
        messages=messages,
        input_value=input_value,
        response_schema=runtime_provider_schema,
        schema_name="AIFSPass1CandidateDiscovery",
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        enable_thinking=enable_thinking,
    )
    diagnostics.update(_candidate_discovery_payload_component_bytes(input_value))
    diagnostics["original_group_summaries_utf8_bytes"] = json_utf8_bytes(group_summaries)
    diagnostics["group_summaries_projection_saved_utf8_bytes"] = (
        diagnostics["original_group_summaries_utf8_bytes"]
        - diagnostics.get("group_summaries_utf8_bytes", 0)
    )
    logging.info(
        "step04 candidate discovery payload bytes=%s input=%s group_summaries=%s "
        "asset_index=%s group_projection_saved=%s peak_guidance=%s coverage_guidance=%s "
        "operational_scope=%s schema=%s",
        diagnostics.get("http_json_utf8_bytes"),
        diagnostics.get("input_utf8_bytes"),
        diagnostics.get("group_summaries_utf8_bytes"),
        diagnostics.get("asset_index_utf8_bytes"),
        diagnostics.get("group_summaries_projection_saved_utf8_bytes"),
        diagnostics.get("precipitation_peak_guidance_utf8_bytes"),
        diagnostics.get("system_direct_precipitation_coverage_guidance_utf8_bytes"),
        diagnostics.get("candidate_operational_scope_utf8_bytes"),
        diagnostics.get("response_schema_utf8_bytes"),
    )
    ensure_split_payload_budget("candidate discovery", diagnostics)
    if existing is not None:
        existing, existing_qc = _apply_candidate_discovery_meteorological_qc(
            existing,
            overview_data=overview_data,
        )
        system_precipitation_audit = persist_system_precipitation_audit(existing)
        report = {
            "status": "reused",
            "validated_ref": rel(root, value_path),
            "checkpoint_ref": rel(root, checkpoint_path),
            "candidate_count": len(existing.get("candidates", [])),
            "meteorological_qc_candidate_count": len(existing_qc),
            "system_direct_precipitation_coverage_audit_ref": rel(root, system_precipitation_audit_path),
            "system_direct_precipitation_required_uncovered_count": int(system_precipitation_audit.get("required_uncovered_count", 0)),
            "system_direct_precipitation_advisory_uncovered_count": int(system_precipitation_audit.get("advisory_uncovered_count", 0)),
            "elapsed_seconds": 0.0,
        }
        logging.info(
            "step05 candidate discovery reused candidates=%s http_payload_bytes=%s",
            report["candidate_count"],
            diagnostics["http_json_utf8_bytes"],
        )
        return existing, report, diagnostics

    if coverage_reconciliation_path.is_file():
        coverage_reconciliation_path.unlink()

    logging.info(
        "step05 candidate discovery start group_summaries=%s http_payload_bytes=%s max_tokens=%s",
        len(group_summaries),
        diagnostics["http_json_utf8_bytes"],
        max_tokens,
    )
    started = time.monotonic()
    def run_candidate_discovery_call(
        call_messages: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]:
        return call_and_validate_json(
            client=client,
            model=model,
            messages=call_messages,
            schema=runtime_provider_schema,
            schema_name="AIFSPass1CandidateDiscovery",
            structured_output_mode=structured_output_mode,
            enable_thinking=enable_thinking,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            repair_attempts=repair_attempts,
            normalizer=lambda item: normalize_candidate_discovery_contract(
                item,
                package_id=package_id,
                attachment_count=int(attachment_manifest.get("attachment_count", 0)),
                attachment_manifest=attachment_manifest,
                stage_schema=runtime_provider_schema,
                group_summaries=group_summaries,
                overview_data=overview_data,
                support_catalog=support_catalog,
            ),
            fixed_field_expectations=lambda raw: {
                "package_id": package_id,
                "analysis_coverage.coverage_status": "complete",
                "analysis_coverage.overview_attachment_count": int(
                    attachment_manifest.get("attachment_count", 0)
                ),
                "run_summary.candidate_count": len(raw.get("candidates", []))
                if isinstance(raw.get("candidates"), list)
                else 0,
                "pass2_required": bool(raw.get("candidates"))
                if isinstance(raw.get("candidates"), list)
                else False,
            },
            semantic_validator=lambda result: [
                *discovery_validator_without_system_coverage(result),
                *_candidate_discovery_initial_compact_limit_errors(result),
            ],
            repair_context_messages=call_messages,
        )

    incomplete_retry_count = 0
    initial_incomplete_diagnostics: dict[str, Any] | None = None
    try:
        value, raw, provider, call_meta = run_candidate_discovery_call(messages)
    except ProviderIncompleteError as exc:
        diagnostics_incomplete = dict(exc.diagnostics)
        if str(diagnostics_incomplete.get("incomplete_reason", "")) != "max_output_tokens":
            raise
        initial_incomplete_diagnostics = diagnostics_incomplete
        incomplete_retry_count = 1
        logging.warning(
            "step04 candidate discovery max_output_tokens incomplete; compact retry=1/1 "
            "output_tokens=%s reasoning_tokens=%s diagnosis=%s",
            diagnostics_incomplete.get("output_tokens"),
            diagnostics_incomplete.get("reasoning_tokens"),
            diagnostics_incomplete.get("incomplete_diagnosis"),
        )
        retry_messages = build_candidate_discovery_length_retry_messages(messages)
        try:
            value, raw, provider, call_meta = run_candidate_discovery_call(retry_messages)
        except ProviderIncompleteError as retry_exc:
            retry_diagnostics = dict(retry_exc.diagnostics)
            retry_diagnostics.update(
                {
                    "candidate_discovery_incomplete_retry_attempt_count": 1,
                    "initial_incomplete_reason": diagnostics_incomplete.get("incomplete_reason"),
                    "initial_output_tokens": diagnostics_incomplete.get("output_tokens"),
                    "initial_reasoning_tokens": diagnostics_incomplete.get("reasoning_tokens"),
                    "initial_total_tokens": diagnostics_incomplete.get("total_tokens"),
                    "initial_incomplete_diagnosis": diagnostics_incomplete.get(
                        "incomplete_diagnosis"
                    ),
                }
            )
            raise ProviderIncompleteError(
                str(retry_exc), diagnostics=retry_diagnostics
            ) from retry_exc

    call_meta["candidate_discovery_incomplete_retry_attempt_count"] = incomplete_retry_count
    if initial_incomplete_diagnostics is not None:
        initial_usage = {
            key: initial_incomplete_diagnostics.get(key)
            for key in (
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "total_tokens",
            )
        }
        retry_usage = call_meta.get("usage")
        retry_attempts = (
            list(retry_usage.get("attempts", []))
            if isinstance(retry_usage, dict) and isinstance(retry_usage.get("attempts"), list)
            else []
        )
        call_meta["usage"] = _summarize_usage_attempts([initial_usage, *retry_attempts])
        call_meta["http_retry_count"] = int(call_meta.get("http_retry_count", 0)) + int(
            initial_incomplete_diagnostics.get("retry_count") or 0
        )
        call_meta["candidate_discovery_initial_incomplete"] = {
            key: initial_incomplete_diagnostics.get(key)
            for key in (
                "incomplete_reason",
                "configured_max_output_tokens",
                "output_tokens",
                "reasoning_tokens",
                "total_tokens",
                "visible_output_token_estimate",
                "incomplete_diagnosis",
            )
        }
    value, meteorological_qc = _apply_candidate_discovery_meteorological_qc(
        value,
        overview_data=overview_data,
    )
    post_qc_errors = [
        *discovery_validator_without_system_coverage(value),
        *_candidate_discovery_initial_compact_limit_errors(value),
    ]
    if post_qc_errors:
        raise Pass1Error(
            "candidate discovery meteorological QC attachment produced an invalid canonical response: "
            + " | ".join(post_qc_errors[:10])
        )

    initial_system_precipitation_audit = persist_system_precipitation_audit(value)
    required_uncovered = _required_uncovered_system_direct_precipitation_signals(
        initial_system_precipitation_audit
    )
    call_meta["coverage_reconciliation_attempt_count"] = 0
    if required_uncovered:
        original_count = len(required_uncovered)
        initial_candidate_count = len(value.get("candidates", []))
        working_value = copy.deepcopy(value)
        working_audit = initial_system_precipitation_audit
        history: list[dict[str, Any]] = []
        rejected_in_row = 0
        working_path = output_dir / "candidate_discovery.coverage_working.json"
        if working_path.exists():
            working_path.unlink()  # never reuse a partially covered discovery as a checkpoint
        # A bounded outer loop accepts partial improvements. A rejected proposal
        # never becomes the baseline for the next AI call.
        max_reconciliation_rounds = max(4, min(12, max(1, repair_attempts) * 3))
        for attempt in range(1, max_reconciliation_rounds + 1):
            missing = _required_uncovered_system_direct_precipitation_signals(working_audit)
            if not missing:
                break
            before_reconciliation = copy.deepcopy(working_value)
            worklist_offset = (rejected_in_row * 4) % len(missing)
            reconciliation_messages = build_system_direct_precipitation_coverage_reconciliation_messages(
                base_messages=messages,
                current_response=working_value,
                audit=working_audit,
                attachment_manifest=attachment_manifest,
                worklist_offset=worklist_offset,
                worklist_limit=4,
            )
            # Do not send the last provider response as a fresh original candidate
            # discovery. The current accepted baseline is explicit in the prompt.
            if history and history[-1].get("status") == "rejected":
                reconciliation_messages.append({
                    "role": "user",
                    "content": json.dumps({
                        "previous_attempt": "rejected_no_progress_or_regression",
                        "reason": history[-1].get("reason"),
                        "instruction": "기존 포괄을 유지한 채 누락된 신호를 새로 포괄하는 별도 기상학적 계획을 작성하십시오.",
                    }, ensure_ascii=False),
                })
            reconciliation_input = {
                "current_candidate_count": len(working_value.get("candidates", [])),
                "required_uncovered_count": len(missing),
                "required_uncovered_signals": missing[:12],
            }
            reconciliation_diagnostics = split_payload_diagnostics(
                model=model,
                messages=reconciliation_messages,
                input_value=reconciliation_input,
                response_schema=runtime_provider_schema,
                schema_name="AIFSPass1CandidateDiscovery",
                max_tokens=max_tokens,
                temperature=0.0,
                top_p=1.0,
                enable_thinking=enable_thinking,
            )
            ensure_split_payload_budget(
                "direct precipitation coverage reconciliation", reconciliation_diagnostics
            )

            def reconciliation_validator(repaired: dict[str, Any]) -> list[str]:
                # Partial coverage is accepted only after the SYSTEM ledger
                # confirms monotonic progress; unrelated schema contracts remain.
                return [
                    *discovery_validator_without_system_coverage(repaired),
                    *_candidate_discovery_coverage_reconciliation_preservation_errors(
                        before_reconciliation,
                        repaired,
                        required_uncovered_count=len(missing),
                    ),
                ]

            entry: dict[str, Any] = {
                "attempt": attempt,
                "before_required_uncovered_count": len(missing),
                "candidate_count_before": len(working_value.get("candidates", [])),
                "candidate_capacity_before": _coverage_reconciliation_capacity(working_value),
                "worklist": [
                    {"forecast_hour": x.get("forecast_hour"), "region_id": x.get("region_id")}
                    for x in (sorted(
                        missing,
                        key=lambda x: (-float(x.get("maximum_6h_mm") or 0), int(x["forecast_hour"]), str(x["region_id"])),
                    )[worklist_offset:] + sorted(
                        missing,
                        key=lambda x: (-float(x.get("maximum_6h_mm") or 0), int(x["forecast_hour"]), str(x["region_id"])),
                    )[:worklist_offset])[:4]
                ],
            }
            try:
                proposed, proposed_raw, proposed_provider, proposed_meta = call_and_validate_json(
                    client=client,
                    model=model,
                    messages=reconciliation_messages,
                    schema=runtime_provider_schema,
                    schema_name="AIFSPass1CandidateDiscovery",
                    structured_output_mode=structured_output_mode,
                    enable_thinking=enable_thinking,
                    max_tokens=max_tokens,
                    temperature=0.0,
                    top_p=1.0,
                    repair_attempts=min(1, repair_attempts),
                    normalizer=lambda item: _canonicalize_candidate_discovery_coverage_reconciliation_preservation(
                        before_reconciliation,
                        normalize_candidate_discovery_contract(
                            item,
                            package_id=package_id,
                            attachment_count=int(attachment_manifest.get("attachment_count", 0)),
                            attachment_manifest=attachment_manifest,
                            stage_schema=runtime_provider_schema,
                            group_summaries=group_summaries,
                            overview_data=overview_data,
                            support_catalog=support_catalog,
                        ),
                        attachment_manifest=attachment_manifest,
                    ),
                    fixed_field_expectations=lambda item: {
                        "package_id": package_id,
                        "analysis_coverage.coverage_status": "complete",
                        "analysis_coverage.overview_attachment_count": int(attachment_manifest.get("attachment_count", 0)),
                        "run_summary.candidate_count": len(item.get("candidates", []))
                        if isinstance(item.get("candidates"), list) else 0,
                        "pass2_required": bool(item.get("candidates"))
                        if isinstance(item.get("candidates"), list) else False,
                    },
                    semantic_validator=reconciliation_validator,
                    repair_context_messages=reconciliation_messages,
                )
                # Account for every provider call even if the proposal is rejected.
                for key in ("http_retry_count", "repair_attempt_count", "structured_output_fallback_count"):
                    call_meta[key] = int(call_meta.get(key, 0)) + int(proposed_meta.get(key, 0))
                previous_usage = call_meta.get("usage") if isinstance(call_meta.get("usage"), dict) else {}
                attempt_usage = proposed_meta.get("usage") if isinstance(proposed_meta.get("usage"), dict) else {}
                call_meta["usage"] = _summarize_usage_attempts([
                    *list(previous_usage.get("attempts", [])),
                    *list(attempt_usage.get("attempts", [])),
                ])
                proposed, proposed_qc = _apply_candidate_discovery_meteorological_qc(
                    proposed, overview_data=overview_data
                )
                post_errors = reconciliation_validator(proposed)
                if post_errors:
                    raise Pass1Error("reconciled candidate QC invalid: " + " | ".join(post_errors[:5]))
                proposed_audit = build_system_direct_precipitation_coverage_audit(
                    proposed,
                    overview_data=overview_data,
                    attachment_manifest=attachment_manifest,
                    support_catalog=support_catalog,
                )
                progress = _direct_precipitation_reconciliation_progress(working_audit, proposed_audit)
                entry.update(progress)
                entry["candidate_count_after"] = len(proposed.get("candidates", []))
                if not progress["accept"]:
                    entry["status"] = "rejected"
                    entry["reason"] = "coverage_regressed" if progress["regressed"] else "no_new_required_signal_covered"
                else:
                    entry["status"] = "accepted"
                    working_value, working_audit = proposed, proposed_audit
                    value, raw, provider, meteorological_qc = proposed, proposed_raw, proposed_provider, proposed_qc
                    # Working state is diagnostic only; never a validated checkpoint.
                    write_json(working_path, working_value)
            except Exception as exc:  # noqa: BLE001
                entry["status"] = "rejected"
                entry["reason"] = sanitize_failure_text(str(exc))[:1200]
            rejected_in_row = rejected_in_row + 1 if entry["status"] == "rejected" else 0
            history.append(entry)
            call_meta["coverage_reconciliation_attempt_count"] = len(history)
            write_json(coverage_reconciliation_path, {
                "contract_version": DIRECT_PRECIPITATION_COVERAGE_RECONCILIATION_VERSION,
                "status": "running",
                "initial_required_uncovered_count": original_count,
                "current_required_uncovered_count": int(working_audit["required_uncovered_count"]),
                "candidate_count_before": initial_candidate_count,
                "candidate_count_current": len(working_value.get("candidates", [])),
                "rounds": history,
                "created_at_utc": now_utc(),
            })
            logging.info(
                "STEP04_COVERAGE_INCREMENTAL attempt=%s/%s status=%s uncovered_before=%s uncovered_after=%s new=%s regressed=%s",
                attempt, max_reconciliation_rounds, entry["status"],
                entry["before_required_uncovered_count"], entry.get("after", "invalid"),
                len(entry.get("newly_covered", [])), len(entry.get("regressed", [])),
            )
            if int(working_audit.get("required_uncovered_count", 0)) == 0:
                break
            if rejected_in_row >= 3:
                logging.warning(
                    "STEP04_COVERAGE_INCREMENTAL_NONPROGRESS attempts=%s remaining=%s",
                    rejected_in_row, working_audit["required_uncovered_count"],
                )
                break
        if int(working_audit.get("required_uncovered_count", 0)) != 0:
            # Persist the LAST accepted baseline, not the initial audit.  In
            # operational mode the temporary workspace is removed on failure;
            # the Step04 wrapper publishes these diagnostics separately.
            persist_system_precipitation_audit(working_value)
            remaining_rows = _required_uncovered_system_direct_precipitation_signals(working_audit)
            write_json(coverage_reconciliation_path, {
                "contract_version": DIRECT_PRECIPITATION_COVERAGE_RECONCILIATION_VERSION,
                "status": "failed",
                "initial_required_uncovered_count": original_count,
                "final_required_uncovered_count": int(working_audit["required_uncovered_count"]),
                "remaining_signals": [
                    {"forecast_hour": row["forecast_hour"], "region_id": row["region_id"],
                     "region_role": row.get("region_role"),
                     "maximum_6h_mm": row.get("maximum_6h_mm"),
                     "candidate_match_diagnostics": row.get("candidate_match_diagnostics", [])}
                    for row in remaining_rows
                ],
                "rounds": history,
                "created_at_utc": now_utc(),
            })
            remaining_keys = [
                "F{:03d}:{}".format(int(row["forecast_hour"]), row["region_id"])
                for row in remaining_rows
            ]
            raise Pass1Error(
                "SYSTEM_DIRECT_PRECIPITATION_COVERAGE_REQUIRED incremental reconciliation exhausted "
                f"attempts={len(history)} remaining={working_audit['required_uncovered_count']} "
                f"remaining_keys={remaining_keys}; audit={coverage_reconciliation_path}"
            )
        write_json(coverage_reconciliation_path, {
            "contract_version": DIRECT_PRECIPITATION_COVERAGE_RECONCILIATION_VERSION,
            "status": "completed",
            "initial_required_uncovered_count": original_count,
            "final_required_uncovered_count": 0,
            "candidate_count_before": initial_candidate_count,
            "candidate_count_after": len(value.get("candidates", [])),
            "rounds": history,
            "created_at_utc": now_utc(),
        })
        logging.info(
            "STEP04_COVERAGE_INCREMENTAL_OK initial_uncovered=%s rounds=%s candidates=%s",
            original_count, len(history), len(value.get("candidates", [])),
        )

    final_errors = validator(value)
    if final_errors:
        raise Pass1Error(
            "candidate discovery final validation failed: " + " | ".join(final_errors[:10])
        )
    system_precipitation_audit = persist_system_precipitation_audit(value)
    elapsed = round(time.monotonic() - started, 3)
    if output_policy.should_write("provider_raw"):
        atomic_write_text(raw_path, raw + "\n")
    write_json(value_path, value)
    if output_policy.should_write("provider_metadata"):
        write_json(provider_path, {**provider, **call_meta, "elapsed_seconds": elapsed})
    retry_summary = checkpoint_retry_summary(call_meta)
    retry_summary["candidate_discovery_incomplete_retry_attempt_count"] = int(
        call_meta.get("candidate_discovery_incomplete_retry_attempt_count", 0)
    )
    retry_summary["coverage_reconciliation_attempt_count"] = int(
        call_meta.get("coverage_reconciliation_attempt_count", 0)
    )
    write_json(
        checkpoint_path,
        {
            **checkpoint,
            "value_sha256": sha256sum(value_path),
            "retry_summary": retry_summary,
            "usage": checkpoint_usage_summary(call_meta),
            "meteorological_qc": {
                "version": CANDIDATE_DISCOVERY_METEOROLOGICAL_QC_VERSION,
                "candidate_count": len(meteorological_qc),
                "candidate_ids": sorted(meteorological_qc),
            },
            "system_direct_precipitation_coverage_audit": {
                "version": SYSTEM_DIRECT_PRECIPITATION_COVERAGE_AUDIT_VERSION,
                "signal_count": int(system_precipitation_audit.get("signal_count", 0)),
                "required_uncovered_count": int(system_precipitation_audit.get("required_uncovered_count", 0)),
                "advisory_uncovered_count": int(system_precipitation_audit.get("advisory_uncovered_count", 0)),
                "audit_ref": rel(root, system_precipitation_audit_path),
            },
            "system_direct_precipitation_coverage_reconciliation": {
                "version": DIRECT_PRECIPITATION_COVERAGE_RECONCILIATION_VERSION,
                "attempt_count": int(call_meta.get("coverage_reconciliation_attempt_count", 0)),
                **(
                    {"reconciliation_ref": rel(root, coverage_reconciliation_path)}
                    if coverage_reconciliation_path.is_file()
                    else {}
                ),
            },
            "created_at_utc": now_utc(),
        },
    )
    report = {
        "status": "completed",
        "validated_ref": rel(root, value_path),
        "checkpoint_ref": rel(root, checkpoint_path),
        "candidate_count": len(value.get("candidates", [])),
        "meteorological_qc_candidate_count": len(meteorological_qc),
        "system_direct_precipitation_coverage_audit_ref": rel(root, system_precipitation_audit_path),
        "system_direct_precipitation_required_uncovered_count": int(system_precipitation_audit.get("required_uncovered_count", 0)),
        "system_direct_precipitation_advisory_uncovered_count": int(system_precipitation_audit.get("advisory_uncovered_count", 0)),
        "elapsed_seconds": elapsed,
        "candidate_discovery_incomplete_retry_attempt_count": int(
            call_meta.get("candidate_discovery_incomplete_retry_attempt_count", 0)
        ),
        "coverage_reconciliation_attempt_count": int(
            call_meta.get("coverage_reconciliation_attempt_count", 0)
        ),
        **(
            {"system_direct_precipitation_coverage_reconciliation_ref": rel(root, coverage_reconciliation_path)}
            if coverage_reconciliation_path.is_file()
            else {}
        ),
        **report_call_metadata(call_meta, output_policy),
    }
    if output_policy.should_write("provider_metadata"):
        report["provider_ref"] = rel(root, provider_path)
        report["usage"] = call_meta.get("usage")
    logging.info(
        "step05 candidate discovery completed candidates=%s elapsed=%.3fs "
        "http_retry_count=%s json_repair_count=%s structured_output_fallback_count=%s coverage_reconciliation_attempt_count=%s",
        report["candidate_count"],
        elapsed,
        call_meta.get("http_retry_count", 0),
        call_meta.get("repair_attempt_count", 0),
        call_meta.get("structured_output_fallback_count", 0),
        call_meta.get("coverage_reconciliation_attempt_count", 0),
    )
    if checkpoint_publisher is not None:
        checkpoint_publisher("candidate_discovery", "")
    return value, report, diagnostics

# Original func/step04/pass1_engine.py:16932
def _next_system_anchor_check_id(plan: dict[str, Any], *, candidate_number: str) -> str:
    used_numbers: set[int] = set()
    for check in plan.get("variable_checks", []):
        if not isinstance(check, dict):
            continue
        match = re.fullmatch(
            rf"CHK_CAND{candidate_number}_(\d{{2}})",
            str(check.get("check_id") or ""),
        )
        if match is not None:
            used_numbers.add(int(match.group(1)))
    for number in range(1, 100):
        if number not in used_numbers:
            return f"CHK_CAND{candidate_number}_{number:02d}"
    raise ValueError(
        f"CAND_{candidate_number} has no free canonical check ID "
        "for the system direct-precipitation anchor"
    )

# Original func/step04/pass1_engine.py:16952
def _system_direct_precipitation_anchor_text(
    candidate_peak_hours: list[int],
) -> tuple[str, str, str]:
    hour_text = ", ".join(f"F{hour:03d}" for hour in candidate_peak_hours)
    threshold = f"{DIRECT_PRECIPITATION_MINIMUM_MM_6H:g}"
    expected_signal = (
        f"{hour_text} 중 하나 이상의 후보 핵심시각에서 후보영역과 겹치는 운영 예보권역(육상·해상)에 "
        f"6시간 강수량 {threshold} mm 이상인 식별 가능한 모델 강수영역이 나타난다."
        if hour_text
        else f"후보 핵심 시각 중 하나 이상에서 후보영역과 겹치는 운영 예보권역(육상·해상)에 6시간 강수량 {threshold} mm 이상인 식별 가능한 모델 강수영역이 나타난다."
    )
    decision_relevance = (
        "후보 핵심시각의 직접 모델 강수 신호가 후보영역과 겹치는 운영 예보권역의 상세 6시간 강수장에서 "
        f"운영상 최소기준인 6시간 강수량 {threshold} mm 이상으로 실제 확인되는지 독립적으로 판정합니다."
    )
    purpose = (
        "모델 강수 예측 존재 사실을 이동·연속성·조직화·강도 변화와 분리하여 검증합니다. "
        "북한·일본·중국 등 운영 예보권역 밖의 강수는 환경 및 이동 맥락에는 사용할 수 있지만 이 직접강수 존재 판정에는 사용하지 않습니다. "
        f"운영 예보권역에서 6시간 강수량 {threshold} mm 미만인 강수는 모델 강수 예측 확인 근거로 사용하지 않습니다."
    )
    return expected_signal, decision_relevance, purpose

# Original func/step04/pass1_engine.py:16975
def _demote_non_system_direct_precipitation_anchor_flags(
    plan: dict[str, Any],
) -> list[str]:
    """Remove legacy AI-owned anchor flags before adding the system invariant."""
    demoted: list[str] = []
    for check in plan.get("variable_checks", []):
        if not isinstance(check, dict):
            continue
        intent = check.get("intent_contract")
        if (
            not isinstance(intent, dict)
            or intent.get("direct_precipitation_anchor") is not True
        ):
            continue
        if (
            str(intent.get("source_check_key") or "")
            == SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY
        ):
            continue
        intent["direct_precipitation_anchor"] = False
        source_intents = intent.get("source_intents")
        if isinstance(source_intents, list):
            for source_intent in source_intents:
                if isinstance(source_intent, dict):
                    source_intent["direct_precipitation_anchor"] = False
        check["intent_hash"] = canonical_hash(intent)
        demoted.append(str(check.get("check_id") or ""))
    return demoted

# Original func/step04/pass1_engine.py:17005
def ensure_system_direct_precipitation_anchor(
    value: dict[str, Any],
    *,
    candidate: dict[str, Any],
    support_catalog: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Add the mandatory system-owned direct-precipitation presence Check.

    The AI owns candidate discovery and meteorological verification hypotheses.
    Python owns this one invariant for every candidate type: independently verify
    whether a candidate-relevant 6-hour precipitation signal is present at the
    candidate core hours that the AI already selected.  For precursor candidates
    those hours remain precursor-overlap representative hours; the SYSTEM Check
    does not reinterpret them as precipitation peaks.

    This function does not change detection_basis or select a new area, time, peak,
    intensity threshold, motion, continuity, organization, or intensity comparison.
    """
    if str(candidate.get("detection_basis") or "") not in {
        "direct_precipitation",
        "precursor_environment",
        "mixed",
    }:
        return value, None
    if not isinstance(value, dict):
        raise ValueError(
            "candidate plan response must be an object before system anchor insertion"
        )

    normalized = copy.deepcopy(value)
    plan = normalized.get("candidate_analysis_plan")
    requests = normalized.get("analysis_data_requests")
    if not isinstance(plan, dict) or not isinstance(requests, list):
        raise ValueError(
            "candidate plan is missing canonical plan or analysis_data_requests"
        )

    candidate_id = str(
        candidate.get("candidate_id") or normalized.get("candidate_id") or ""
    )
    match = re.fullmatch(r"CAND_(\d{3})", candidate_id)
    if match is None:
        raise ValueError(
            "candidate_id is not canonical for the system direct-precipitation "
            f"anchor: {candidate_id!r}"
        )
    candidate_number = match.group(1)

    demoted = _demote_non_system_direct_precipitation_anchor_flags(plan)
    existing_system_anchors = [
        check
        for check in plan.get("variable_checks", [])
        if isinstance(check, dict)
        and _is_system_direct_precipitation_anchor_check(check)
    ]
    if len(existing_system_anchors) > 1:
        raise ValueError(
            f"{candidate_id} contains duplicate system direct-precipitation "
            f"anchors: {[check.get('check_id') for check in existing_system_anchors]}"
        )
    if len(existing_system_anchors) == 1:
        existing_errors = _system_direct_precipitation_anchor_errors(
            normalized,
            candidate=candidate,
        )
        if existing_errors:
            raise ValueError(" | ".join(existing_errors))
        return normalized, {
            "action": "reused",
            "check_id": existing_system_anchors[0].get("check_id"),
            "demoted_legacy_anchor_check_ids": demoted,
        }

    raw_peak_hours = candidate.get("peak_forecast_hours")
    if not isinstance(raw_peak_hours, list):
        raise ValueError(
            f"{candidate_id} candidate has no peak_forecast_hours"
        )
    candidate_peak_hours = sorted(
        {
            int(hour)
            for hour in raw_peak_hours
            if isinstance(hour, int) and not isinstance(hour, bool)
        }
    )
    if not candidate_peak_hours:
        raise ValueError(
            f"{candidate_id} candidate has empty peak_forecast_hours"
        )
    peak_hour_set = set(candidate_peak_hours)

    phase_rows = [
        item
        for item in plan.get("analysis_phases", [])
        if isinstance(item, dict)
    ]
    selected_peak_phases = [
        phase
        for phase in phase_rows
        if str(phase.get("phase_type") or "") == "peak"
        and peak_hour_set.intersection(
            {
                int(hour)
                for hour in phase.get("forecast_hours", [])
                if isinstance(hour, int) and not isinstance(hour, bool)
            }
        )
    ]
    selected_phase_ids = [
        str(phase.get("phase_id"))
        for phase in selected_peak_phases
        if str(phase.get("phase_id") or "")
    ]
    covered_peak_hours = {
        int(hour)
        for phase in selected_peak_phases
        for hour in phase.get("forecast_hours", [])
        if isinstance(hour, int) and not isinstance(hour, bool)
    }
    missing_peak_hours = sorted(peak_hour_set - covered_peak_hours)
    if not selected_phase_ids or missing_peak_hours:
        raise ValueError(
            f"{candidate_id} system direct-precipitation anchor cannot resolve "
            f"candidate peak phases; missing_peak_forecast_hours={missing_peak_hours}"
        )

    candidate_targets = [
        target
        for target in plan.get("target_areas", [])
        if isinstance(target, dict)
        and str(target.get("target_type") or "") == "candidate_domain"
    ]
    if len(candidate_targets) != 1:
        raise ValueError(
            f"{candidate_id} system direct-precipitation anchor requires "
            "exactly one candidate_domain target"
        )
    candidate_target = candidate_targets[0]
    target_id = str(candidate_target.get("target_id") or "")
    candidate_bbox = _normalized_bbox_for_validation(
        candidate_target.get("requested_domain")
    )
    if not target_id or candidate_bbox is None:
        raise ValueError(
            f"{candidate_id} candidate_domain target is incomplete for system anchor"
        )

    check_id = _next_system_anchor_check_id(
        plan,
        candidate_number=candidate_number,
    )
    diagnostic_id = "detailed_spatial_map"
    variable_role = "hazard_magnitude"
    required_signal_components = ["direct_precipitation"]
    product_id = _preferred_product_id_for_check(
        diagnostic_id,
        variable_role,
        required_signal_components,
        support_catalog,
        candidate,
    )
    if not product_id:
        raise ValueError(
            f"{candidate_id} cannot resolve direct-precipitation spatial product "
            "for system anchor"
        )
    product_components = _product_signal_components_from_support(
        product_id,
        support_catalog,
    )
    if (
        product_components
        and "direct_precipitation" not in set(product_components)
    ):
        raise ValueError(
            f"{candidate_id} system anchor product_id={product_id!r} does not "
            "provide direct_precipitation"
        )

    expected_signal, decision_relevance, purpose = (
        _system_direct_precipitation_anchor_text(candidate_peak_hours)
    )
    system_row = {
        "check_key": SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY,
        "verification_mode": "spatial_distribution",
        "variable_role": variable_role,
        "diagnostic_id": diagnostic_id,
        "domain_scope": "candidate",
        "spatial_relation_scope": "candidate_interior",
        # System-owned source phases use canonical IDs because there is no
        # provider-local phase namespace for this injected Check.
        "phase_keys": list(selected_phase_ids),
        "representative_spatial_hours": [],
        "analysis_methods": ["detailed_map"],
        "expected_signal": expected_signal,
        "decision_relevance": decision_relevance,
        "priority": "required",
        "evidence_role": "supporting",
        "coverage_roles": ["hazard_magnitude", "supporting_evidence"],
        "purpose": purpose,
        "atomic_claims": [
            {
                "claim_key": SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CLAIM_KEY,
                "claim_statement": expected_signal,
                "claim_components": ["direct_precipitation"],
                "phase_keys": list(selected_phase_ids),
                "expected_state": "present",
                "scope_quantifier": "at_least_one",
                "scope_description": "지정된 후보 핵심시각과 후보영역∩운영 예보권역(육상·해상)",
            }
        ],
        "cross_section_line": None,
        "cross_section_strategy": None,
        "reference_band_axis": None,
        "check_scope": "single_variable",
        "required_signal_components": required_signal_components,
        "_system_direct_precipitation_anchor": True,
    }

    intent_contract = _build_check_intent_contract(
        system_row,
        check_id=check_id,
        phase_ids=list(selected_phase_ids),
    )
    atomic_claim_contract = _build_atomic_claim_contract(
        system_row,
        check_id=check_id,
    )
    capability_profile = _capability_profile_for_check(
        support_catalog,
        diagnostic_id=diagnostic_id,
        product_id=product_id,
    )
    system_check = {
        "check_id": check_id,
        "variable_role": variable_role,
        "required_signal_components": required_signal_components,
        "check_scope": "single_variable",
        "diagnostic_id": diagnostic_id,
        "spatial_relation_scope": "candidate_interior",
        "product_id": product_id,
        "feature_ids": [],
        "target_ids": [target_id],
        "phase_ids": list(selected_phase_ids),
        "analysis_methods": ["detailed_map"],
        "expected_signal": expected_signal,
        "decision_relevance": decision_relevance,
        "priority": "required",
        "cross_section_line": None,
        "cross_section_strategy": None,
        "reference_band_axis": None,
        "intent_contract": intent_contract,
        "intent_hash": canonical_hash(intent_contract),
        "atomic_claim_contract": atomic_claim_contract,
        "atomic_claim_hash": canonical_hash(atomic_claim_contract),
        "atomic_claim_validation": _shadow_validate_atomic_claim_contract(
            check_id=check_id,
            row=system_row,
            contract=atomic_claim_contract,
        ),
        "capability_profile": capability_profile,
        "capability_validation": _shadow_validate_check_capability(
            check_id=check_id,
            intent_contract=intent_contract,
            capability_profile=capability_profile,
        ),
    }
    plan.setdefault("variable_checks", []).append(system_check)

    system_request = {
        # Temporary ID. _merge_analysis_data_requests rewrites canonical IDs.
        "request_id": f"REQ_CAND{candidate_number}_999",
        "candidate_id": candidate_id,
        "plan_id": str(plan.get("plan_id") or f"PLAN_CAND{candidate_number}"),
        "diagnostic_id": diagnostic_id,
        "data_type": "spatial_field",
        "product_id": product_id,
        "feature_ids": [],
        "forecast_hours": list(candidate_peak_hours),
        "check_id": check_id,
        "source_check_ids": [check_id],
        "target_id": target_id,
        "phase_ids": list(selected_phase_ids),
        "analysis_methods": ["detailed_map"],
        "purpose": purpose,
        "priority": "required",
        "requested_domain": candidate_bbox,
        "cross_section_line": None,
        "cross_section_strategy": None,
        "reference_band_axis": None,
    }
    request_count_before = len(requests)
    normalized["analysis_data_requests"] = _merge_analysis_data_requests(
        [*requests, system_request],
        candidate_number=candidate_number,
        non_mergeable_check_ids={check_id},
    )
    request_reused = (
        len(normalized["analysis_data_requests"]) == request_count_before
    )

    logging.info(
        "STEP04_SYSTEM_DIRECT_PRECIPITATION_ANCHOR "
        "candidate=%s check_id=%s peak_hours=%s product_id=%s "
        "request_asset_reused=%s demoted_legacy=%s",
        candidate_id,
        check_id,
        candidate_peak_hours,
        product_id,
        request_reused,
        demoted,
    )
    return normalized, {
        "action": "added",
        "check_id": check_id,
        "peak_forecast_hours": candidate_peak_hours,
        "product_id": product_id,
        "request_asset_reused": request_reused,
        "demoted_legacy_anchor_check_ids": demoted,
    }

# Original func/step04/pass1_engine.py:17534
def create_or_resume_candidate_plans(
    *,
    root: Path,
    output_dir: Path,
    package_id: str,
    discovery_response: dict[str, Any],
    group_summaries: list[dict[str, Any]],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    feature_catalog: dict[str, Any],
    operational_region_catalog: dict[str, Any],
    schema: dict[str, Any],
    provider_schema: dict[str, Any] | None = None,
    final_schema: dict[str, Any],
    prompt_template: str,
    prompt_sha256: str,
    client: Pass1ProviderClient,
    model: str,
    structured_output_mode: str,
    enable_thinking: bool,
    max_tokens: int,
    temperature: float,
    top_p: float,
    repair_attempts: int,
    request_gap_seconds: float,
    resume: bool,
    candidate_ids: set[str] | None = None,
    compact_continuation_attempts: int = 4,
    output_policy: OutputPolicy | None = None,
    checkpoint_publisher: Callable[[str, str], None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, int]]]:
    output_policy = output_policy or OutputPolicy("operational")
    provider_schema = provider_schema or schema
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = [item for item in discovery_response.get("candidates", []) if isinstance(item, dict)]
    selected_candidate_ids = set(candidate_ids or ())
    if selected_candidate_ids:
        available_candidate_ids = {str(item.get("candidate_id")) for item in candidates}
        unknown_candidate_ids = sorted(selected_candidate_ids - available_candidate_ids)
        if unknown_candidate_ids:
            raise Pass1Error(f"candidate plan selection references unknown candidate_ids={unknown_candidate_ids}")
        candidates = [
            item for item in candidates if str(item.get("candidate_id")) in selected_candidate_ids
        ]
    use_compact_openai_plan = client.provider.api_family == "responses"
    runtime_provider_schema = provider_schema
    if use_compact_openai_plan:
        runtime_provider_schema = client.transport_schemas.get("candidate_plan")
        if not isinstance(runtime_provider_schema, dict):
            raise Pass1Error("OpenAI compact candidate-plan transport schema is unavailable")
    results: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    diagnostics_rows: list[dict[str, int]] = []
    for index, candidate in enumerate(candidates, start=1):
        candidate_id = str(candidate["candidate_id"])
        candidate_provider_schema = runtime_provider_schema
        candidate_operational_overlap = _candidate_operational_region_overlap(
            candidate=candidate,
            operational_region_catalog=operational_region_catalog,
        )
        if use_compact_openai_plan:
            candidate_provider_schema = _candidate_plan_transport_schema_for_operational_scope(
                candidate_provider_schema,
                candidate=candidate,
                operational_region_catalog=operational_region_catalog,
            )
        if use_compact_openai_plan:
            temporal_feasibility = _candidate_temporal_feasibility_contract(
                candidate, attachment_manifest, support_catalog=support_catalog
            )
            logging.info(
                "STEP04_TIME_FEASIBILITY candidate=%s requested=%s effective=%s added=%s source_assets=%s",
                candidate_id,
                temporal_feasibility["requested_hours"],
                temporal_feasibility["effective_hours"],
                temporal_feasibility["added_hours"],
                temporal_feasibility["source_asset_ids"],
            )
            if not temporal_feasibility["temporal_comparison_available"]:
                raise CandidatePlanError(
                    candidate_id=candidate_id,
                    cause=ValueError(
                        "STEP04_TEMPORAL_EVIDENCE_UNAVAILABLE "
                        f"candidate_id={candidate_id} "
                        f"requested_hours={temporal_feasibility['requested_hours']} "
                        f"effective_hours={temporal_feasibility['effective_hours']}; "
                        "temporal/spatial evolution requires two observed forecast hours "
                        "in a related overview asset; do not invent hours or retry an impossible plan"
                    ),
                )
        logging.info(
            "STEP04_CANDIDATE_PLAN_SCOPE candidate=%s regional_verification_available=%s "
            "selectable_region_ids=%s allowed_verification_modes=%s",
            candidate_id,
            candidate_operational_overlap.get("regional_verification_available"),
            [
                row.get("region_id")
                for row in candidate_operational_overlap.get("selectable_regions", [])
                if isinstance(row, dict)
            ],
            candidate_operational_overlap.get("allowed_verification_modes"),
        )
        evidence = _candidate_evidence_for(discovery_response, candidate_id)
        related_opportunities = _related_precursor_opportunities(discovery_response, candidate_id)
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", candidate_id)
        raw_path = output_dir / f"{safe_id}.plan.raw.txt"
        value_path = output_dir / f"{safe_id}.plan.validated.json"
        provider_path = output_dir / f"{safe_id}.plan.provider.json"
        checkpoint_path = output_dir / f"{safe_id}.plan.checkpoint.json"
        related_summaries = _related_group_summaries(candidate, group_summaries)
        checkpoint_base = {
                "package_id": package_id,
                "candidate_id": candidate_id,
                "candidate_fingerprint": canonical_hash(candidate),
                "evidence_fingerprint": canonical_hash(evidence),
                "related_group_summaries_fingerprint": canonical_hash(related_summaries),
                "related_precursor_opportunities_fingerprint": canonical_hash(related_opportunities),
                "support_catalog_fingerprint": canonical_hash(_minimal_support_catalog(support_catalog)),
                "feature_catalog_fingerprint": canonical_hash(_minimal_feature_index(feature_catalog)),
                "operational_region_catalog_fingerprint": canonical_hash(
                    _minimal_operational_region_index(operational_region_catalog)
                ),
            "prompt_sha256": prompt_sha256,
            "schema_fingerprint": canonical_hash(
                {"canonical": schema, "provider": candidate_provider_schema}
            ),
            "canonical_schema_sha256": canonical_hash(schema),
                "provider_configuration": _client_checkpoint_configuration(
                    client,
                    stage="candidate_plan",
                ),
                "model": model,
                "contract_version": SPLIT_SYNTHESIS_CONTRACT_VERSION,
                "system_direct_precipitation_anchor_execution_contract_version": SYSTEM_DIRECT_PRECIPITATION_ANCHOR_EXECUTION_CONTRACT_VERSION,
                "narrative_language": NARRATIVE_LANGUAGE,
        }
        if use_compact_openai_plan:
            if temporal_feasibility["added_hours"]:
                # Only modified one-hour candidates invalidate an existing
                # candidate-plan checkpoint; unrelated candidates keep theirs.
                checkpoint_base["candidate_temporal_feasibility"] = canonical_hash(
                    temporal_feasibility
                )
            checkpoint_base.update(
                {
                    "compact_candidate_plan_contract_version": COMPACT_CANDIDATE_PLAN_CONTRACT_VERSION,
                    "compact_candidate_plan_normalization_version": COMPACT_CANDIDATE_PLAN_NORMALIZATION_VERSION,
                    "compact_candidate_plan_expander_version": COMPACT_CANDIDATE_PLAN_EXPANDER_VERSION,
                    "compact_candidate_plan_coverage_version": COMPACT_CANDIDATE_PLAN_COVERAGE_VERSION,
                    "max_output_tokens": int(max_tokens),
                    "continuation_attempt_limit": int(compact_continuation_attempts),
                }
            )
        checkpoint = _checkpoint_contract(checkpoint_base)
        pre_anchor_validator = lambda value, _candidate=candidate, _evidence=evidence: validate_candidate_plan_response(
            value,
            schema=schema,
            final_schema=final_schema,
            package_id=package_id,
            candidate=_candidate,
            candidate_evidence=_evidence,
            attachment_manifest=attachment_manifest,
            support_catalog=support_catalog,
            feature_catalog=feature_catalog,
            operational_region_catalog=operational_region_catalog,
            require_system_direct_precipitation_anchor=False,
        )
        post_anchor_validator = lambda value, _candidate=candidate, _evidence=evidence: validate_candidate_plan_response(
            value,
            schema=schema,
            final_schema=final_schema,
            package_id=package_id,
            candidate=_candidate,
            candidate_evidence=_evidence,
            attachment_manifest=attachment_manifest,
            support_catalog=support_catalog,
            feature_catalog=feature_catalog,
            operational_region_catalog=operational_region_catalog,
            require_system_direct_precipitation_anchor=True,
        )
        existing = None
        if resume:
            existing = _load_checkpointed_json(
                value_path=value_path,
                checkpoint_path=checkpoint_path,
                expected_checkpoint=checkpoint,
                provider_stage="candidate_plan",
                validator=post_anchor_validator,
            )
        messages, input_value = build_candidate_plan_messages(
            prompt_template=prompt_template,
            package_id=package_id,
            candidate=candidate,
            candidate_evidence=evidence,
            group_summaries=group_summaries,
            attachment_manifest=attachment_manifest,
            support_catalog=support_catalog,
            feature_catalog=feature_catalog,
            operational_region_catalog=operational_region_catalog,
            related_precursor_opportunities=related_opportunities,
            compact_transport=use_compact_openai_plan,
        )
        if structured_output_mode == "off":
            messages = add_prompt_only_schema_constraint(messages, candidate_provider_schema)
        diagnostics = split_payload_diagnostics(
            model=model,
            messages=messages,
            input_value=input_value,
            response_schema=candidate_provider_schema,
            schema_name="AIFSPass1CandidatePlan",
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            enable_thinking=enable_thinking,
        )
        ensure_split_payload_budget(f"candidate plan {candidate_id}", diagnostics)
        diagnostics_rows.append({"candidate_id": candidate_id, **diagnostics})
        if existing is not None:
            results.append(existing)
            reports.append(
                {
                    "candidate_id": candidate_id,
                    "status": "reused",
                    "validated_ref": rel(root, value_path),
                    "checkpoint_ref": rel(root, checkpoint_path),
                    "request_count": len(existing.get("analysis_data_requests", [])),
                    "elapsed_seconds": 0.0,
                }
            )
            logging.info(
                "step05 candidate plan reused candidate_id=%s requests=%s",
                candidate_id,
                len(existing.get("analysis_data_requests", [])),
            )
            continue

        logging.info(
            "step05 candidate plan start index=%s/%s candidate_id=%s related_groups=%s "
            "http_payload_bytes=%s max_tokens=%s",
            index,
            len(candidates),
            candidate_id,
            len(related_summaries),
            diagnostics["http_json_utf8_bytes"],
            max_tokens,
        )
        started = time.monotonic()
        canonicalization_events: list[dict[str, Any]] = []
        try:
            if use_compact_openai_plan:
                value, provider, call_meta, diagnostics, coverage = create_compact_candidate_plan(
                    package_id=package_id,
                    candidate=candidate,
                    candidate_evidence=evidence,
                    group_summaries=group_summaries,
                    attachment_manifest=attachment_manifest,
                    support_catalog=support_catalog,
                    feature_catalog=feature_catalog,
                    operational_region_catalog=operational_region_catalog,
                    related_precursor_opportunities=related_opportunities,
                    canonical_schema=schema,
                    transport_schema=candidate_provider_schema,
                    final_schema=final_schema,
                    prompt_template=prompt_template,
                    prompt_sha256=prompt_sha256,
                    client=client,
                    model=model,
                    structured_output_mode=structured_output_mode,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    repair_attempts=repair_attempts,
                    continuation_attempts=compact_continuation_attempts,
                )
                raw = ""
                call_meta["coverage"] = coverage
                diagnostics_rows[-1] = {"candidate_id": candidate_id, **diagnostics}
            else:
                value, raw, provider, call_meta = call_and_validate_json(
                    client=client,
                    model=model,
                    messages=messages,
                    schema=provider_schema,
                    schema_name="AIFSPass1CandidatePlan",
                    structured_output_mode=structured_output_mode,
                    enable_thinking=enable_thinking,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    repair_attempts=repair_attempts,
                    normalizer=lambda item, _candidate_id=candidate_id: normalize_candidate_plan_contract(
                        item,
                        package_id=package_id,
                        candidate_id=_candidate_id,
                        stage_schema=provider_schema,
                        support_catalog=support_catalog,
                        canonicalization_events=canonicalization_events,
                    ),
                    fixed_field_expectations={
                        "package_id": package_id,
                        "candidate_id": candidate_id,
                    },
                    semantic_validator=pre_anchor_validator,
                    repair_context_messages=messages,
                )
        except Exception as exc:  # Preserve the checkpoint-safe candidate identity in the failure summary.
            raise CandidatePlanError(candidate_id=candidate_id, cause=exc) from exc

        try:
            value, system_anchor_event = ensure_system_direct_precipitation_anchor(
                value,
                candidate=candidate,
                support_catalog=support_catalog,
            )
            system_anchor_errors = _system_direct_precipitation_anchor_errors(
                value,
                candidate=candidate,
            )
            if system_anchor_errors:
                raise ValueError(" | ".join(system_anchor_errors))
            post_anchor_validation_errors = post_anchor_validator(value)
            if post_anchor_validation_errors:
                raise ValueError(
                    "system direct-precipitation anchor post-validation failed: "
                    + " | ".join(post_anchor_validation_errors[:20])
                )
            if system_anchor_event is not None:
                call_meta["system_direct_precipitation_anchor"] = system_anchor_event
        except Exception as exc:
            raise CandidatePlanError(candidate_id=candidate_id, cause=exc) from exc

        if canonicalization_events:
            call_meta["canonicalized_fields"] = [
                *call_meta.get("canonicalized_fields", []),
                *canonicalization_events,
            ]
        elapsed = round(time.monotonic() - started, 3)
        if output_policy.should_write("provider_raw") and not use_compact_openai_plan:
            atomic_write_text(raw_path, raw + "\n")
        write_json(value_path, value)
        if output_policy.should_write("provider_metadata"):
            write_json(provider_path, {**provider, **call_meta, "elapsed_seconds": elapsed})
        write_json(
            checkpoint_path,
            {
                **checkpoint,
                "value_sha256": sha256sum(value_path),
                "retry_summary": checkpoint_retry_summary(call_meta),
                "repair_history": checkpoint_repair_history(call_meta),
                "usage": checkpoint_usage_summary(call_meta),
                **(
                    {
                        "compact_candidate_plan": {
                            "chunk_count": int(call_meta.get("compact_plan_chunk_count", 0)),
                            "continuation_attempt_count": int(call_meta.get("continuation_attempt_count", 0)),
                            "coverage": call_meta.get("coverage", {}),
                        }
                    }
                    if use_compact_openai_plan
                    else {}
                ),
                "created_at_utc": now_utc(),
            },
        )
        results.append(value)
        reports.append(
            {
                "candidate_id": candidate_id,
                "status": "completed",
                "validated_ref": rel(root, value_path),
                "checkpoint_ref": rel(root, checkpoint_path),
                "request_count": len(value.get("analysis_data_requests", [])),
                "elapsed_seconds": elapsed,
                **report_call_metadata(call_meta, output_policy),
            }
        )
        if output_policy.should_write("provider_metadata"):
            reports[-1]["provider_ref"] = rel(root, provider_path)
            reports[-1]["usage"] = provider.get("usage")
        if use_compact_openai_plan:
            reports[-1]["compact_plan_chunk_count"] = int(
                call_meta.get("compact_plan_chunk_count", 0)
            )
            reports[-1]["continuation_attempt_count"] = int(
                call_meta.get("continuation_attempt_count", 0)
            )
            reports[-1]["coverage"] = call_meta.get("coverage", {})
        logging.info(
            "step05 candidate plan completed candidate_id=%s requests=%s elapsed=%.3fs "
            "http_retry_count=%s json_repair_count=%s structured_output_fallback_count=%s "
            "compact_chunk_count=%s continuation_count=%s",
            candidate_id,
            len(value.get("analysis_data_requests", [])),
            elapsed,
            call_meta.get("http_retry_count", 0),
            call_meta.get("repair_attempt_count", 0),
            call_meta.get("structured_output_fallback_count", 0),
            call_meta.get("compact_plan_chunk_count", 0),
            call_meta.get("continuation_attempt_count", 0),
        )
        if checkpoint_publisher is not None:
            checkpoint_publisher("candidate_plan", candidate_id)
        if request_gap_seconds > 0 and index < len(candidates):
            time.sleep(request_gap_seconds)
    return results, reports, diagnostics_rows

# Original func/step04/pass1_engine.py:17941
def merge_split_pass1_response(
    discovery_response: dict[str, Any],
    candidate_plan_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Deterministically combine validated discovery and per-candidate plans."""
    candidates = [item for item in discovery_response.get("candidates", []) if isinstance(item, dict)]
    result_by_candidate: dict[str, dict[str, Any]] = {}
    for result in candidate_plan_results:
        candidate_id = str(result.get("candidate_id"))
        if candidate_id in result_by_candidate:
            raise Pass1Error(f"duplicate candidate plan result: {candidate_id}")
        result_by_candidate[candidate_id] = result
    expected_ids = [str(candidate.get("candidate_id")) for candidate in candidates]
    missing = [candidate_id for candidate_id in expected_ids if candidate_id not in result_by_candidate]
    extra = sorted(set(result_by_candidate) - set(expected_ids))
    if missing or extra:
        raise Pass1Error(f"candidate plan coverage mismatch missing={missing} extra={extra}")

    plans: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    for candidate_id in expected_ids:
        result = result_by_candidate[candidate_id]
        plan = result.get("candidate_analysis_plan")
        if not isinstance(plan, dict):
            raise Pass1Error(f"candidate plan result is invalid: {candidate_id}")
        plans.append(plan)
        candidate_requests = result.get("analysis_data_requests", [])
        if not isinstance(candidate_requests, list):
            raise Pass1Error(f"candidate requests are invalid: {candidate_id}")
        requests.extend(candidate_requests)

    return {
        "package_id": discovery_response.get("package_id"),
        "analysis_coverage": discovery_response.get("analysis_coverage"),
        "run_summary": discovery_response.get("run_summary"),
        "candidates": discovery_response.get("candidates", []),
        "candidate_evidence": discovery_response.get("candidate_evidence", []),
        **(
            {"precursor_opportunities": discovery_response["precursor_opportunities"]}
            if "precursor_opportunities" in discovery_response
            else {}
        ),
        "candidate_analysis_plans": plans,
        "analysis_data_requests": requests,
        "global_uncertainties": discovery_response.get("global_uncertainties", []),
        "precursor_candidate_assessment": discovery_response.get(
            "precursor_candidate_assessment"
        ),
        "pass2_required": discovery_response.get("pass2_required", False),
    }

# Original func/step04/pass1_engine.py:18087
def create_or_resume_group_summaries(
    *,
    root: Path,
    group_summary_dir: Path,
    contexts: list[dict[str, Any]],
    batches: list[dict[str, Any]],
    package_id: str,
    schema: dict[str, Any],
    prompt_template: str,
    prompt_sha256: str,
    schema_sha256: str,
    client: Pass1ProviderClient,
    model: str,
    structured_output_mode: str,
    enable_thinking: bool,
    max_tokens_group_summary: int,
    temperature: float,
    top_p: float,
    repair_attempts: int,
    compression_attempts: int,
    resume: bool,
    overwrite_group_summaries: bool,
    output_policy: OutputPolicy | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output_policy = output_policy or OutputPolicy("operational")
    if overwrite_group_summaries and group_summary_dir.exists():
        shutil.rmtree(group_summary_dir)
    group_summary_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    for context in contexts:
        summary_id = str(context["summary_id"])
        summary_path = group_summary_dir / f"{summary_id}.summary.json"
        raw_path = group_summary_dir / f"{summary_id}.raw.txt"
        provider_path = group_summary_dir / f"{summary_id}.provider.json"
        checkpoint_path = group_summary_dir / f"{summary_id}.checkpoint.json"
        checkpoint_contract = build_group_checkpoint_contract(
            package_id=package_id,
            context=context,
            batches=batches,
            prompt_sha256=prompt_sha256,
            schema_sha256=schema_sha256,
            model=model,
            provider_configuration=_client_checkpoint_configuration(
                client,
                stage="group_summary",
            ),
        )
        messages = build_group_summary_messages(
            prompt_template=prompt_template,
            package_id=package_id,
            context=context,
        )
        http_payload_bytes = json_utf8_bytes(
            build_http_payload(
                model=model,
                messages=messages,
                response_schema=schema,
                schema_name="AIFSPass1GroupSummary",
                structured_output=structured_output_mode != "off",
                enable_thinking=enable_thinking,
                extensions=True,
                max_tokens=max_tokens_group_summary,
                temperature=temperature,
                top_p=top_p,
            )
        )
        source_observation_count = len(context["source_observations"])
        source_asset_count = len(context["source_asset_ids"])
        existing = None
        if resume and not overwrite_group_summaries:
            existing = _load_existing_group_summary(
                summary_path=summary_path,
                checkpoint_path=checkpoint_path,
                expected_checkpoint=checkpoint_contract,
                schema=schema,
                package_id=package_id,
                context=context,
            )
        if existing is not None:
            logging.info(
                "step05 group summary reused tier=%s group_id=%s source_observations=%s "
                "source_assets=%s http_payload_bytes=%s max_tokens_group_summary=%s "
                "elapsed_seconds=0.000 http_retry_count=0 json_repair_count=0 compaction_count=0",
                context["analysis_tier"],
                context["group_id"],
                source_observation_count,
                source_asset_count,
                http_payload_bytes,
                max_tokens_group_summary,
            )
            summaries.append(existing)
            reports.append(
                {
                    "summary_id": summary_id,
                    "status": "reused",
                    "summary_ref": rel(root, summary_path),
                    "checkpoint_ref": rel(root, checkpoint_path),
                    "summary_utf8_bytes": json_utf8_bytes(existing),
                }
            )
            continue

        if source_observation_count == 1:
            started = time.monotonic()
            summary = build_deterministic_group_summary(
                package_id=package_id,
                context=context,
            )
            errors = validate_group_summary(
                summary,
                schema=schema,
                package_id=package_id,
                context=context,
                enforce_byte_budget=False,
            )
            if errors:
                raise Pass1Error(
                    f"deterministic group summary validation failed summary_id={summary_id}: "
                    + " | ".join(errors[:30])
                )
            if json_utf8_bytes(summary) <= GROUP_SUMMARY_MAX_BYTES:
                elapsed = round(time.monotonic() - started, 3)
                if output_policy.should_write("provider_raw"):
                    atomic_write_text(raw_path, json.dumps(summary, ensure_ascii=False) + "\n")
                write_json(summary_path, summary)
                checkpoint = {
                    **checkpoint_contract,
                    "max_tokens_group_summary": max_tokens_group_summary,
                    "summary_sha256": sha256sum(summary_path),
                    "summary_utf8_bytes": json_utf8_bytes(summary),
                    "generation_mode": "deterministic_single_observation",
                    "fixed_field_mismatches": [],
                    "created_at_utc": now_utc(),
                }
                write_json(checkpoint_path, checkpoint)
                if output_policy.should_write("provider_metadata"):
                    write_json(
                        provider_path,
                        {
                            "model": model,
                            "generation_mode": "deterministic_single_observation",
                            "provider_call_count": 0,
                            "http_retry_count": 0,
                            "repair_attempt_count": 0,
                            "compression_attempt_count": 0,
                            "elapsed_seconds": elapsed,
                        },
                    )
                logging.info(
                    "step05 group summary completed deterministically tier=%s group_id=%s "
                    "source_observations=%s source_assets=%s summary_utf8_bytes=%s "
                    "elapsed_seconds=%.3f provider_call_count=0",
                    context["analysis_tier"],
                    context["group_id"],
                    source_observation_count,
                    source_asset_count,
                    json_utf8_bytes(summary),
                    elapsed,
                )
                summaries.append(summary)
                reports.append(
                    {
                        "summary_id": summary_id,
                        "status": "completed",
                        "generation_mode": "deterministic_single_observation",
                        "summary_ref": rel(root, summary_path),
                        "checkpoint_ref": rel(root, checkpoint_path),
                        "summary_utf8_bytes": json_utf8_bytes(summary),
                        "elapsed_seconds": elapsed,
                        "compression_attempt_count": 0,
                    }
                )
                continue
            logging.warning(
                "deterministic group summary exceeds byte budget; falling back to provider compaction "
                "summary_id=%s summary_utf8_bytes=%s hard_max=%s",
                summary_id,
                json_utf8_bytes(summary),
                GROUP_SUMMARY_MAX_BYTES,
            )

        logging.info(
            "step05 group summary start tier=%s group_id=%s source_observations=%s "
            "source_assets=%s http_payload_bytes=%s max_tokens_group_summary=%s",
            context["analysis_tier"],
            context["group_id"],
            source_observation_count,
            source_asset_count,
            http_payload_bytes,
            max_tokens_group_summary,
        )
        started = time.monotonic()
        summary, raw, provider_summary, call_meta = call_and_validate_json(
            client=client,
            model=model,
            messages=messages,
            schema=schema,
            schema_name="AIFSPass1GroupSummary",
            structured_output_mode=structured_output_mode,
            enable_thinking=enable_thinking,
            max_tokens=max_tokens_group_summary,
            temperature=temperature,
            top_p=top_p,
            repair_attempts=repair_attempts,
            normalizer=lambda value, _context=context: normalize_group_summary_contract(
                value,
                package_id=package_id,
                context=_context,
            ),
            fixed_field_expectations={
                "schema_version": GROUP_SUMMARY_SCHEMA_VERSION,
                "package_id": package_id,
                "summary_id": summary_id,
                "analysis_tier": context["analysis_tier"],
                "group_id": context["group_id"],
            },
            semantic_validator=lambda value, _context=context: validate_group_summary(
                value,
                schema=schema,
                package_id=package_id,
                context=_context,
                enforce_byte_budget=False,
            ),
        )
        initial_call_meta = call_meta
        compression_reports: list[dict[str, Any]] = []
        for attempt in range(compression_attempts):
            if json_utf8_bytes(summary) <= GROUP_SUMMARY_MAX_BYTES:
                break
            compressed, compressed_raw, compressed_provider, compressed_meta = call_and_validate_json(
                client=client,
                model=model,
                messages=build_group_summary_compaction_messages(summary=summary, context=context),
                schema=schema,
                schema_name="AIFSPass1GroupSummary",
                structured_output_mode=structured_output_mode,
                enable_thinking=False,
                max_tokens=max_tokens_group_summary,
                temperature=0.0,
                top_p=1.0,
                repair_attempts=repair_attempts,
                normalizer=lambda value, _context=context: normalize_group_summary_contract(
                    value,
                    package_id=package_id,
                    context=_context,
                ),
                fixed_field_expectations={
                    "schema_version": GROUP_SUMMARY_SCHEMA_VERSION,
                    "package_id": package_id,
                    "summary_id": summary_id,
                    "analysis_tier": context["analysis_tier"],
                    "group_id": context["group_id"],
                },
                semantic_validator=lambda value, _context=context: validate_group_summary(
                    value,
                    schema=schema,
                    package_id=package_id,
                    context=_context,
                    enforce_byte_budget=False,
                ),
            )
            summary, raw, provider_summary, call_meta = (
                compressed,
                compressed_raw,
                compressed_provider,
                compressed_meta,
            )
            compression_reports.append(
                {
                    "attempt": attempt + 1,
                    "summary_utf8_bytes": json_utf8_bytes(summary),
                    "call_meta": compressed_meta,
                }
            )
        errors = validate_group_summary(
            summary,
            schema=schema,
            package_id=package_id,
            context=context,
            enforce_byte_budget=True,
        )
        if errors:
            raise Pass1Error(
                f"group summary validation failed summary_id={summary_id}: "
                + " | ".join(errors[:30])
            )
        elapsed = round(time.monotonic() - started, 3)
        if output_policy.should_write("provider_raw"):
            atomic_write_text(raw_path, raw + "\n")
        write_json(summary_path, summary)
        checkpoint = {
            **checkpoint_contract,
            "max_tokens_group_summary": max_tokens_group_summary,
            "summary_sha256": sha256sum(summary_path),
            "summary_utf8_bytes": json_utf8_bytes(summary),
            "fixed_field_mismatches": [
                *initial_call_meta.get("fixed_field_mismatches", []),
                *[
                    mismatch
                    for report in compression_reports
                    for mismatch in report["call_meta"].get("fixed_field_mismatches", [])
                ],
            ],
            "created_at_utc": now_utc(),
        }
        write_json(checkpoint_path, checkpoint)
        if output_policy.should_write("provider_metadata"):
            write_json(
                provider_path,
                {
                    **provider_summary,
                    **call_meta,
                    "initial_call_meta": initial_call_meta,
                    "elapsed_seconds": elapsed,
                    "compression_attempt_count": len(compression_reports),
                    "compression_reports": compression_reports,
                },
            )
        http_retry_count = int(initial_call_meta.get("http_retry_count", 0)) + sum(
            int(report["call_meta"].get("http_retry_count", 0))
            for report in compression_reports
        )
        json_repair_count = int(initial_call_meta.get("repair_attempt_count", 0)) + sum(
            int(report["call_meta"].get("repair_attempt_count", 0))
            for report in compression_reports
        )
        logging.info(
            "step05 group summary completed tier=%s group_id=%s source_observations=%s "
            "source_assets=%s http_payload_bytes=%s max_tokens_group_summary=%s "
            "elapsed_seconds=%.3f http_retry_count=%s json_repair_count=%s compaction_count=%s",
            context["analysis_tier"],
            context["group_id"],
            source_observation_count,
            source_asset_count,
            http_payload_bytes,
            max_tokens_group_summary,
            elapsed,
            http_retry_count,
            json_repair_count,
            len(compression_reports),
        )
        usage = provider_summary.get("usage")
        finish_reason = provider_summary.get("finish_reason")
        if isinstance(usage, dict) and finish_reason is not None:
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            logging.info(
                "step05 group summary provider usage tier=%s group_id=%s prompt_tokens=%s "
                "completion_tokens=%s finish_reason=%s",
                context["analysis_tier"],
                context["group_id"],
                prompt_tokens,
                completion_tokens,
                finish_reason,
            )
        if finish_reason == "length":
            logging.warning(
                "step05 group summary finish_reason=length tier=%s group_id=%s; output may be truncated",
                context["analysis_tier"],
                context["group_id"],
            )
        summaries.append(summary)
        reports.append(
            {
                "summary_id": summary_id,
                "status": "completed",
                "summary_ref": rel(root, summary_path),
                "checkpoint_ref": rel(root, checkpoint_path),
                "summary_utf8_bytes": json_utf8_bytes(summary),
                "elapsed_seconds": elapsed,
                "compression_attempt_count": len(compression_reports),
            }
        )
    return summaries, reports

# Original func/step04/pass1_engine.py:18526
def _load_existing_batch_observation(
    path: Path,
    *,
    schema: dict[str, Any],
    package_id: str,
    batch: dict[str, Any],
    attachment_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = load_json(path)
        errors = validate_batch_observation(
            value,
            schema=schema,
            package_id=package_id,
            batch=batch,
            attachment_by_id=attachment_by_id,
        )
        return value if not errors else None
    except Exception:  # noqa: BLE001
        return None

# Original func/step04/pass1_engine.py:18550
def step05_overwrite_paths(pass1_dir: Path, *, synthesis_mode: str) -> list[Path]:
    paths = [
        pass1_dir / "ai_candidate_response.raw.txt",
        pass1_dir / "ai_candidate_response.json",
        pass1_dir / "ai_candidate_response.validated.json",
        pass1_dir / "ai_candidate_response.validation_report.json",
    ]
    if synthesis_mode == "direct":
        return [pass1_dir / "observations", *paths]
    if synthesis_mode == "hierarchical":
        return [pass1_dir / "candidate_discovery", pass1_dir / "candidate_plans", *paths]
    raise ValueError(f"unsupported synthesis mode: {synthesis_mode}")

# Original func/step04/pass1_engine.py:18564
def _openai_transport_schema_paths(root: Path) -> dict[str, Path]:
    return {
        "batch_observation": runtime_path(root, "prompts", "schemas", "openai", "ai_pass1_batch_observation.transport.schema.json"),
        "group_summary": runtime_path(root, "prompts", "schemas", "openai", "ai_pass1_group_summary.transport.schema.json"),
        "candidate_discovery": runtime_path(root, "prompts", "schemas", "openai", "ai_pass1_candidate_discovery.transport.schema.json"),
        "candidate_plan": runtime_path(root, "prompts", "schemas", "openai", "ai_pass1_candidate_plan.transport.schema.json"),
        "candidate_response": runtime_path(root, "prompts", "schemas", "openai", "ai_discovery_candidate_response.transport.schema.json"),
    }

# Original func/step04/pass1_engine.py:18574
def main(*, checkpoint_publisher: Callable[[str, str], None] | None = None, preloaded_env_load: ProjectEnvLoad | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run Pass 1 with staged image observation and text-only synthesis."
    )
    parser.add_argument("--init", required=True, help="Initialization time YYYYMMDDHH")
    parser.add_argument("--root", default=".")
    parser.add_argument("--provider", default=None, choices=["openai", "codex_cli", "openai_compatible"])
    parser.add_argument("--base-url", default=None, help="Base URL for compatible HTTP providers.")
    parser.add_argument("--openai-base-url", default=None, help="Explicit non-default OpenAI Responses API base URL; official OpenAI uses https://api.openai.com/v1.")
    parser.add_argument("--model", default=None, help="Deprecated for AIFSHeavyRain: model selection is controlled by envi/api_keys.env only.")
    parser.add_argument("--api-key-env", default=None, help="Provider API key environment variable. Default is provider-specific.")
    parser.add_argument(
        "--env-file",
        default=None,
        help="Dotenv path. Default: <root>/envi/api_keys.env. Existing OS environment variables take precedence.",
    )
    parser.add_argument(
        "--no-env-file",
        action="store_true",
        help="Do not load a dotenv file; use OS environment variables only.",
    )
    parser.add_argument("--env-preloaded", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--max-images-per-request", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--retry-backoff-seconds", type=float, default=5.0)
    parser.add_argument("--request-gap-seconds", type=float, default=1.0)
    parser.add_argument("--max-tokens-observation", type=int, default=8000)
    parser.add_argument(
        "--max-tokens-group-summary",
        type=int,
        default=3200,
        help="Maximum output tokens for provider-based group summary fallback and repair.",
    )
    parser.add_argument(
        "--max-tokens-candidate-discovery",
        type=int,
        default=16000,
        help=(
            "Maximum combined reasoning and output-token ceiling for hierarchical candidate "
            "discovery after bounded schema output. Default: 16000."
        ),
    )
    parser.add_argument(
        "--max-tokens-candidate-plan",
        type=int,
        default=None,
        help=(
            "Maximum output-token ceiling for one candidate-plan request. "
            "OpenAI Responses compact chunks default to 8000; legacy providers retain 32000."
        ),
    )
    parser.add_argument(
        "--candidate-plan-continuation-attempts",
        type=int,
        default=4,
        help="Maximum safety limit for no-progress candidate-plan continuation calls. Default: 4.",
    )
    parser.add_argument(
        "--max-tokens-synthesis",
        type=int,
        default=20000,
        help="Legacy direct-mode final synthesis token limit; hierarchical mode does not use it.",
    )
    parser.add_argument(
        "--synthesis-mode",
        choices=SYNTHESIS_MODES,
        default="hierarchical",
        help="hierarchical (default) uses 8 validated group summaries; direct keeps the 26-observation synthesis path.",
    )
    parser.add_argument(
        "--group-summary-compression-attempts",
        type=int,
        default=1,
        help="Text-only compaction attempts after a group summary exceeds 8 KiB.",
    )
    parser.add_argument("--temperature-observation", type=float, default=0.0)
    parser.add_argument("--temperature-synthesis", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument(
        "--structured-output",
        choices=["auto", "on", "off"],
        default="auto",
        help="auto retries once without response_format after provider rejection or 503/504 gateway failure",
    )
    parser.add_argument("--enable-thinking", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--enable-synthesis-thinking",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable model reasoning for candidate discovery and per-candidate planning. Default: disabled.",
    )
    parser.add_argument("--image-detail", choices=sorted(IMAGE_DETAILS), default=None, help="OpenAI Responses image detail. Ignored by Chat Completions providers.")
    parser.add_argument("--reasoning-effort-observation", choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"], default=None)
    parser.add_argument("--reasoning-effort-group-summary", choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"], default=None)
    parser.add_argument("--reasoning-effort-candidate-discovery", choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"], default=None)
    parser.add_argument("--reasoning-effort-candidate-plan", choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"], default=None)
    parser.add_argument(
        "--multimodal-retry-attempts",
        type=int,
        default=2,
        help=(
            "Number of source-grounded retries that resend the original images "
            "before text-only JSON repair. Default: 2."
        ),
    )
    parser.add_argument("--repair-attempts", type=int, default=1)
    parser.add_argument(
        "--candidate-id",
        default=None,
        help="Run only one already-discovered candidate plan; requires --resume and does not run final merge.",
    )
    parser.add_argument(
        "--stop-after",
        choices=["group_summary", "candidate_discovery"],
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--overwrite-group-summaries",
        action="store_true",
        help="Remove only Step05 hierarchical group-summary checkpoints before rebuilding them.",
    )
    parser.add_argument("--dry-run", action="store_true")
    add_output_mode_argument(parser)
    args = parser.parse_args()

    if not re.fullmatch(r"\d{10}", args.init):
        print("step05 실패: --init은 YYYYMMDDHH 형식이어야 합니다.", file=sys.stderr)
        return 2
    if not 1 <= args.max_images_per_request <= 4:
        print("step05 실패: --max-images-per-request는 1~4 범위여야 합니다.", file=sys.stderr)
        return 2
    if (
        args.max_retries < 0
        or args.multimodal_retry_attempts < 0
        or args.repair_attempts < 0
        or args.group_summary_compression_attempts < 0
        or args.candidate_plan_continuation_attempts < 0
    ):
        print("step05 실패: retry/repair 횟수는 0 이상이어야 합니다.", file=sys.stderr)
        return 2

    if args.no_env_file and args.env_file:
        print("step05 실패: --env-file과 --no-env-file은 함께 사용할 수 없습니다.", file=sys.stderr)
        return 2
    if args.overwrite_group_summaries and args.synthesis_mode != "hierarchical":
        print("step05 failed: --overwrite-group-summaries requires --synthesis-mode hierarchical", file=sys.stderr)
        return 2
    if args.candidate_id is not None and (not args.resume or args.overwrite):
        print("step05 failed: --candidate-id requires --resume and cannot be combined with --overwrite", file=sys.stderr)
        return 2
    if args.stop_after is not None and args.synthesis_mode != "hierarchical":
        print("step05 failed: --stop-after requires --synthesis-mode hierarchical", file=sys.stderr)
        return 2

    root = Path(args.root).resolve()
    try:
        output_policy = resolve_output_mode(args.output_mode)
    except ValueError as exc:
        print(f"step05 failed: {exc}", file=sys.stderr)
        return 2
    try:
        if preloaded_env_load is not None:
            if args.env_file or args.no_env_file or args.env_preloaded:
                raise ValueError(
                    "in-process Step04 engine received both preloaded dotenv provenance and dotenv CLI controls"
                )
            env_load = preloaded_env_load
        else:
            env_load = load_project_env(
                root,
                env_file=args.env_file,
                override=False,
                enabled=not args.no_env_file and not args.env_preloaded,
            )
        env_file = env_load.path or runtime_path(root, "envi", "api_keys.env")
        loaded_env_names: list[str] = list(env_load.loaded_keys)
    except Exception as exc:  # noqa: BLE001
        print(f"step05 실패: .env 로드 오류: {exc}", file=sys.stderr)
        return 2

    try:
        if args.model is not None:
            raise ProviderConfigurationError(
                "model selection is env-file-only; edit AIFS_MODEL_STEP04 in envi/api_keys.env and do not pass --model"
            )
        settings = resolve_ai_settings(
            cli_provider=args.provider,
            cli_model=None,
            cli_image_detail=args.image_detail,
            cli_reasoning_efforts={
                "observation": args.reasoning_effort_observation,
                "group_summary": args.reasoning_effort_group_summary,
                "candidate_discovery": args.reasoning_effort_candidate_discovery,
                "candidate_plan": args.reasoning_effort_candidate_plan,
            },
            reasoning_defaults={
            "observation": "none",
            "group_summary": "none",
            "candidate_discovery": "low",
            "candidate_plan": "none",
            },
            env_load=env_load,
        )
        if settings.model.source != "env_file":
            raise ProviderConfigurationError(
                "model must come from envi/api_keys.env only; "
                f"resolved source={settings.model.source!r}. Configure AIFS_MODEL_STEP04 in the project env file and remove process-environment model overrides."
            )
    except ProviderConfigurationError as exc:
        print(f"step05 configuration failed: {exc}", file=sys.stderr)
        return 2
    args.provider = settings.provider.value
    args.model = settings.model.value
    args.image_detail = settings.image_detail.value
    args.reasoning_effort_observation = settings.reasoning_efforts["observation"].value
    args.reasoning_effort_group_summary = settings.reasoning_efforts["group_summary"].value
    args.reasoning_effort_candidate_discovery = settings.reasoning_efforts["candidate_discovery"].value
    args.reasoning_effort_candidate_plan = settings.reasoning_efforts["candidate_plan"].value
    if args.max_tokens_candidate_plan is None:
        args.max_tokens_candidate_plan = 8000 if args.provider in {"openai", "codex_cli"} else 32000
    defaults = provider_defaults(args.provider)
    api_key_env = args.api_key_env or defaults["api_key_env"]

    setup_logging(root, args.init)
    if args.env_preloaded:
        logging.info("step05 dotenv preloaded by wrapper")
    elif args.no_env_file:
        logging.info("step05 dotenv loading disabled")
    elif env_file.is_file():
        logging.info("pass1 dotenv 로드 적용변수수=%s", len(loaded_env_names))
    else:
        logging.info("step04 dotenv 파일을 찾지 못해 운영체제 환경 변수만 사용합니다.")

    logging.info(
        "step05 provider configuration provider=%s provider_source=%s model=%s model_source=%s "
        "image_detail=%s image_detail_source=%s api_key_configured=%s",
        args.provider,
        settings.provider.source,
        args.model,
        settings.model.source,
        args.image_detail,
        settings.image_detail.source,
        bool(os.getenv(api_key_env, "").strip()),
    )
    logging.info(
        "step05 reasoning configuration observation=%s(%s) group_summary=%s(%s) "
        "candidate_discovery=%s(%s) candidate_plan=%s(%s)",
        args.reasoning_effort_observation,
        settings.reasoning_efforts["observation"].source,
        args.reasoning_effort_group_summary,
        settings.reasoning_efforts["group_summary"].source,
        args.reasoning_effort_candidate_discovery,
        settings.reasoning_efforts["candidate_discovery"].source,
        args.reasoning_effort_candidate_plan,
        settings.reasoning_efforts["candidate_plan"].source,
    )

    pass1_dir = runtime_path(root, "daio", args.init, "ai_pass1")
    input_dir = pass1_dir / "input"
    observation_dir = pass1_dir / "observations"
    group_summary_dir = pass1_dir / "group_summaries"
    candidate_discovery_dir = pass1_dir / "candidate_discovery"
    candidate_plan_dir = pass1_dir / "candidate_plans"
    prompt_dir = runtime_path(root, "daio", args.init, "prompts")
    required_paths = {
        "input_manifest": input_dir / "ai_discovery_input_manifest.json",
        "input_validation": input_dir / "ai_discovery_input_validation_report.json",
        "compact_input": input_dir / "ai_discovery_input.compact.json",
        "attachment_manifest": input_dir / "ai_discovery_attachment_manifest.json",
        "batch_plan": input_dir / "ai_discovery_batch_plan.json",
        "support_catalog": input_dir / "ai_pass1_supported_request_catalog.json",
        "final_schema": input_dir / "ai_discovery_candidate_response.schema.json",
        "batch_schema": input_dir / "ai_pass1_batch_observation.schema.json",
        "batch_prompt": prompt_dir / "ai_pass1_batch_observation_prompt.ko.md",
        "final_prompt": prompt_dir / "ai_discovery_candidate_prompt.ko.md",
        "feature_catalog": runtime_path(root, "daio", args.init, "feature_package", "discovery_feature_catalog.json"),
        "operational_region_catalog": runtime_path(root, "daio", args.init, "feature_package", "operational_region_catalog.json"),
    }
    hierarchical_paths = {
        "group_summary_prompt": runtime_path(root, "prompts", "templates", "ai_pass1_group_summary_prompt.ko.md"),
        "candidate_discovery_prompt": runtime_path(root, "prompts", "templates", "ai_pass1_candidate_discovery_prompt.ko.md"),
        "candidate_plan_prompt": runtime_path(root, "prompts", "templates", "ai_pass1_candidate_plan_prompt.ko.md"),
        "group_summary_schema": runtime_path(root, "prompts", "schemas", "ai_pass1_group_summary.schema.json"),
        "candidate_discovery_schema": runtime_path(root, "prompts", "schemas", "ai_pass1_candidate_discovery.schema.json"),
        "candidate_plan_schema": runtime_path(root, "prompts", "schemas", "ai_pass1_candidate_plan.schema.json"),
    }
    if args.synthesis_mode == "hierarchical" and not required_paths["batch_prompt"].is_file():
        legacy_batch_prompt = runtime_path(root, "prompts", "templates", "ai_pass1_batch_observation_prompt.ko.md")
        if legacy_batch_prompt.is_file():
            required_paths["batch_prompt"] = legacy_batch_prompt

    failure_stage = "preflight"
    client: Pass1ProviderClient | None = None
    try:
        required_for_mode = {
            name: path
            for name, path in required_paths.items()
            if args.synthesis_mode == "direct" or name != "final_prompt"
        }
        missing = [
            rel(root, path) if path.is_absolute() and root in path.parents else str(path)
            for path in required_for_mode.values()
            if not path.is_file()
        ]
        if missing:
            raise FileNotFoundError(
                "Step04 staged Pass1 package is incomplete. Re-run Step04 after this update. Missing: "
                + ", ".join(missing)
            )
        if args.synthesis_mode == "hierarchical":
            hierarchical_missing = [
                rel(root, path) if path.is_absolute() and root in path.parents else str(path)
                for path in hierarchical_paths.values()
                if not path.is_file()
            ]
            if hierarchical_missing:
                raise FileNotFoundError(
                    "Step05 hierarchical execution contract is incomplete. Missing: "
                    + ", ".join(hierarchical_missing)
                )
        transport_paths = _openai_transport_schema_paths(root)
        if args.provider in {"openai", "codex_cli"}:
            missing_transport = [rel(root, path) for path in transport_paths.values() if not path.is_file()]
            if missing_transport:
                raise FileNotFoundError(
                    "OpenAI Responses transport schemas are incomplete. Missing: "
                    + ", ".join(missing_transport)
                )


        loaded = {name: load_json(path) for name, path in required_paths.items() if path.suffix == ".json"}
        input_manifest = loaded["input_manifest"]
        input_validation = loaded["input_validation"]
        compact_input = loaded["compact_input"]
        attachment_manifest = loaded["attachment_manifest"]
        batch_plan = loaded["batch_plan"]
        support_catalog = loaded["support_catalog"]
        final_schema = loaded["final_schema"]
        batch_schema = loaded["batch_schema"]
        feature_catalog = loaded["feature_catalog"]
        operational_region_catalog = loaded["operational_region_catalog"]
        group_summary_schema = (
            load_json(hierarchical_paths["group_summary_schema"])
            if args.synthesis_mode == "hierarchical"
            else None
        )
        candidate_discovery_provider_schema = (
            load_json(hierarchical_paths["candidate_discovery_schema"])
            if args.synthesis_mode == "hierarchical"
            else None
        )
        candidate_plan_provider_schema = (
            load_json(hierarchical_paths["candidate_plan_schema"])
            if args.synthesis_mode == "hierarchical"
            else None
        )
        package_id = str(input_manifest.get("package_id", ""))
        if not package_id:
            raise ValueError("Step04 package_id is missing")
        overview_data_ref = str(input_manifest.get("overview_data_ref", "")).strip()
        if not overview_data_ref:
            raise ValueError("Step04 input manifest overview_data_ref is missing")
        overview_data_path = resolve_logical_ref(root, overview_data_ref)
        if not overview_data_path.is_file():
            raise FileNotFoundError(f"Step02 overview_data is missing: {overview_data_ref}")
        expected_overview_sha = str(input_manifest.get("overview_data_sha256", "")).strip()
        if expected_overview_sha and sha256sum(overview_data_path) != expected_overview_sha:
            raise ValueError("Step02 overview_data hash does not match Step03 input manifest")
        overview_data = load_json(overview_data_path)
        if str(overview_data.get("init", "")) != args.init:
            raise ValueError("Step02 overview_data init does not match --init")
        precipitation_peak_guidance = build_precipitation_peak_guidance(
            overview_data, minimum_regional_max_mm=_peak_anchor_minimum_mm(support_catalog)
        )
        if not precipitation_peak_guidance.get("operational_timeline"):
            raise ValueError("Step02 overview_data has no operational 6-hour precipitation timeline")
        logging.info(
            "step04 quantitative precipitation peak guidance primary_peak_hours=%s regional_anchor_count=%s",
            precipitation_peak_guidance.get("primary_peak_hours", []),
            len(precipitation_peak_guidance.get("regional_peak_anchors", [])),
        )
        for name, value in {
            "compact_input": compact_input,
            "attachment_manifest": attachment_manifest,
            "batch_plan": batch_plan,
            "support_catalog": support_catalog,
        }.items():
            if value.get("package_id") != package_id:
                raise ValueError(f"{name} package_id does not match input manifest")
        if input_validation.get("coverage_status") != "complete" or input_validation.get("errors"):
            raise ValueError("Step04 input validation did not pass")

        attachment_by_id = verify_attachments(root, attachment_manifest)
        batches = normalize_batch_plan(
            batch_plan,
            attachment_manifest,
            max_images_per_request=args.max_images_per_request,
        )
        expected_batch_count = int(batch_plan.get("batch_count", len(batches)))
        planned_max_images = int(batch_plan.get("max_images_per_request", args.max_images_per_request))
        if (
            str(batch_plan.get("execution_mode", "")).startswith("staged")
            and args.max_images_per_request == planned_max_images
            and expected_batch_count != len(batches)
        ):
            raise ValueError(
                f"batch_plan batch_count mismatch declared={expected_batch_count} actual={len(batches)}"
            )

        logging.info(
            "step05 preflight package_id=%s attachments=%s batches=%s max_images=%s",
            package_id,
            len(attachment_by_id),
            len(batches),
            args.max_images_per_request,
        )
        if args.dry_run:
            print("AIFSHeavyRain Step04 Pass1 dry-run")
            print(f"  package_id: {package_id}")
            print(f"  provider: {args.provider}")
            print(f"  model: {args.model}")
            print(f"  overview_attachment_count: {len(attachment_by_id)}")
            print(f"  image_observation_batch_count: {len(batches)}")
            print(f"  synthesis_mode: {args.synthesis_mode}")
            print(
                "  quantitative_precipitation_peak_hours: "
                + ",".join(str(value) for value in precipitation_peak_guidance.get("primary_peak_hours", []))
            )
            for batch in batches:
                print(
                    f"  {batch['batch_id']}: {len(batch['attachment_ids'])} images "
                    f"tier={batch.get('analysis_tier')} group={batch.get('group_id')}"
                )
            return 0

        model = args.model
        reasoning_efforts = {
            "observation": args.reasoning_effort_observation,
            "group_summary": args.reasoning_effort_group_summary,
            "candidate_discovery": args.reasoning_effort_candidate_discovery,
            "candidate_plan": args.reasoning_effort_candidate_plan,
        }
        if args.provider in {"openai", "codex_cli"}:
            validate_openai_capabilities(
                model=model,
                require_images=True,
                require_structured_outputs=True,
                image_detail=args.image_detail,
                reasoning_efforts=reasoning_efforts.values(),
            )
        provider_client = build_provider(
            provider=args.provider,
            base_url=args.base_url,
            openai_base_url=args.openai_base_url,
            api_key_env=api_key_env,
            timeout_seconds=args.timeout_seconds,
            max_retries=args.max_retries,
            retry_backoff_seconds=args.retry_backoff_seconds,
        )
        client = Pass1ProviderClient(
            provider=provider_client,
            image_detail=args.image_detail,
            reasoning_efforts=reasoning_efforts,
            transport_schemas={key: load_json(path) for key, path in transport_paths.items()} if args.provider in {"openai", "codex_cli"} else {},
        )

        final_validated_path = pass1_dir / "ai_candidate_response.validated.json"
        replace_final_outputs = args.overwrite or args.overwrite_group_summaries
        if final_validated_path.exists() and not replace_final_outputs:
            raise FileExistsError("step05 validated output exists. Use --overwrite to run again.")
        if replace_final_outputs:
            for path in step05_overwrite_paths(pass1_dir, synthesis_mode=args.synthesis_mode):
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
        observation_dir.mkdir(parents=True, exist_ok=True)

        batch_prompt_template = required_paths["batch_prompt"].read_text(encoding="utf-8")
        final_prompt = (
            required_paths["final_prompt"].read_text(encoding="utf-8")
            if args.synthesis_mode == "direct"
            else ""
        )
        batch_observations: list[dict[str, Any]] = []
        batch_reports: list[dict[str, Any]] = []
        for batch_index, batch in enumerate(batches, start=1):
            failure_stage = "observation"
            batch_id = str(batch["batch_id"])
            safe_batch_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", batch_id)
            observation_path = observation_dir / f"{safe_batch_id}.observation.json"
            raw_path = observation_dir / f"{safe_batch_id}.raw.txt"
            provider_path = observation_dir / f"{safe_batch_id}.provider.json"
            existing = None
            if args.resume and not args.overwrite and observation_path.exists():
                existing = _load_existing_batch_observation(
                    observation_path,
                    schema=batch_schema,
                    package_id=package_id,
                    batch=batch,
                    attachment_by_id=attachment_by_id,
                )
                if args.synthesis_mode == "hierarchical" and existing is None:
                    raise Pass1Error(
                        "hierarchical resume found an invalid observation checkpoint; "
                        f"batch_id={batch_id} state=invalid path={rel(root, observation_path)}"
                    )
            if existing is not None:
                logging.info("step05 batch resume batch_id=%s", batch_id)
                batch_observations.append(existing)
                batch_reports.append(
                    {
                        "batch_id": batch_id,
                        "status": "reused",
                        "attachment_count": len(batch["attachment_ids"]),
                        "observation_ref": rel(root, observation_path),
                    }
                )
                continue

            attachments = [attachment_by_id[asset_id] for asset_id in batch["attachment_ids"]]
            messages = build_batch_messages(
                package_id=package_id,
                batch=batch,
                attachments=attachments,
                root=root,
                prompt_template=batch_prompt_template,
            )
            logging.info(
                "step05 image batch start index=%s/%s batch_id=%s images=%s",
                batch_index,
                len(batches),
                batch_id,
                len(attachments),
            )
            started = time.monotonic()
            provider_observation_schema = build_provider_observation_schema(
                batch_schema,
                batch=batch,
            )
            try:
                observation, raw, provider_summary, call_meta = call_and_validate_json(
                    client=client,
                    model=model,
                    messages=messages,
                    schema=provider_observation_schema,
                    schema_name="AIFSPass1BatchObservation",
                    structured_output_mode=args.structured_output,
                    enable_thinking=args.enable_thinking,
                    max_tokens=min(
                        args.max_tokens_observation,
                        COMPACT_OBSERVATION_RECOVERY_MAX_TOKENS,
                    ),
                    temperature=args.temperature_observation,
                    top_p=args.top_p,
                    repair_attempts=args.repair_attempts,
                    normalizer=lambda value, _batch=batch, _attachments=attachments: normalize_batch_observation_contract(
                        value,
                        package_id=package_id,
                        batch=_batch,
                        attachments_in_payload_order=_attachments,
                    ),
                    semantic_validator=lambda value, _batch=batch: validate_batch_observation(
                        value,
                        schema=batch_schema,
                        package_id=package_id,
                        batch=_batch,
                        attachment_by_id=attachment_by_id,
                    ),
                    source_retry_attempts=args.multimodal_retry_attempts,
                    source_retry_after_repair_reserve=1,
                    source_retry_context={
                        "required_attachment_count": len(attachments),
                        "required_asset_ids": [
                            str(item["asset_id"]) for item in attachments
                        ],
                    },
                    source_retry_schema=provider_observation_schema,
                    source_retry_schema_name="AIFSPass1BatchObservation",
                    source_retry_before_repair_on_parse_error=True,
                    compact_length_recovery={
                        "messages": build_compact_observation_recovery_messages(
                            messages,
                            batch=batch,
                        ),
                        "schema": provider_observation_schema,
                        "schema_name": "AIFSPass1BatchObservationCompactRecovery",
                        "max_tokens": min(
                            args.max_tokens_observation,
                            COMPACT_OBSERVATION_RECOVERY_MAX_TOKENS,
                        ),
                    },
                )
            except ResponseValidationError as exc:
                logging.warning(
                    "single-image observation fallback triggered batch_id=%s reason=%s",
                    batch_id,
                    str(exc),
                )
                observation, provider_summary, call_meta = run_single_image_observation_fallback(
                    client=client,
                    model=model,
                    package_id=package_id,
                    batch=batch,
                    attachments=attachments,
                    root=root,
                    prompt_template=batch_prompt_template,
                    canonical_schema=batch_schema,
                    structured_output_mode=args.structured_output,
                    max_tokens=args.max_tokens_observation,
                    temperature=args.temperature_observation,
                    top_p=args.top_p,
                )
                raw = ""
            elapsed = round(time.monotonic() - started, 3)
            write_observation_artifacts(
                observation_path=observation_path,
                raw_path=raw_path,
                provider_path=provider_path,
                observation=observation,
                raw=raw,
                provider_summary=provider_summary,
                call_meta=call_meta,
                elapsed_seconds=elapsed,
                output_policy=output_policy,
            )
            if checkpoint_publisher is not None:
                checkpoint_publisher("observation", batch_id)
            batch_observations.append(observation)
            batch_reports.append(
                {
                    "batch_id": batch_id,
                    "status": "completed",
                    "attachment_count": len(attachments),
                    "elapsed_seconds": elapsed,
                    "observation_ref": rel(root, observation_path),
                    **report_call_metadata(call_meta, output_policy),
                }
            )
            if "single_image_fallback_asset_count" in call_meta:
                batch_reports[-1]["single_image_fallback_asset_count"] = int(
                    call_meta["single_image_fallback_asset_count"]
                )
            if output_policy.should_write("provider_metadata"):
                batch_reports[-1]["provider_ref"] = rel(root, provider_path)
                batch_reports[-1]["usage"] = provider_summary.get("usage")
            logging.info("step05 image batch completed batch_id=%s elapsed=%.3fs", batch_id, elapsed)
            if args.request_gap_seconds > 0 and batch_index < len(batches):
                time.sleep(args.request_gap_seconds)

        group_summary_reports: list[dict[str, Any]] = []
        candidate_discovery_report: dict[str, Any] | None = None
        candidate_plan_reports: list[dict[str, Any]] = []
        hierarchical_diagnostics: dict[str, Any] | None = None
        synthesis_started = time.monotonic()
        if args.synthesis_mode == "direct":
            failure_stage = "direct_synthesis"
            synthesis_messages = build_synthesis_messages(
                final_prompt=final_prompt,
                package_manifest=input_manifest,
                compact_input=compact_input,
                attachment_manifest=attachment_manifest,
                support_catalog=support_catalog,
                feature_catalog=feature_catalog,
                operational_region_catalog=operational_region_catalog,
                batch_observations=batch_observations,
            )
            logging.info("step05 direct final synthesis start batches=%s", len(batch_observations))
            final_response, final_raw, final_provider_summary, final_call_meta = call_and_validate_json(
                client=client,
                model=model,
                messages=synthesis_messages,
                schema=final_schema,
                schema_name="AIFSPass1CandidateResponse",
                structured_output_mode=args.structured_output,
                enable_thinking=args.enable_thinking,
                max_tokens=args.max_tokens_synthesis,
                temperature=args.temperature_synthesis,
                top_p=args.top_p,
                repair_attempts=args.repair_attempts,
                normalizer=lambda value: normalize_final_response_contract(
                    value,
                    package_id=package_id,
                ),
                fixed_field_expectations={"package_id": package_id},
                semantic_validator=lambda value: validate_final_response(
                    value,
                    schema=final_schema,
                    package_id=package_id,
                    attachment_manifest=attachment_manifest,
                    support_catalog=support_catalog,
                    feature_catalog=feature_catalog,
                    operational_region_catalog=operational_region_catalog,
                ),
            )
        else:
            if group_summary_schema is None:
                raise RuntimeError("hierarchical group summary schema is unavailable")
            contexts = build_group_contexts(
                batch_observations=batch_observations,
                batches=batches,
                attachment_by_id=attachment_by_id,
            )
            if len(contexts) != 8:
                actual = [context["summary_id"] for context in contexts]
                raise Pass1Error(f"hierarchical synthesis requires exactly 8 groups, actual={actual}")
            group_prompt_path = hierarchical_paths["group_summary_prompt"]
            failure_stage = "group_summary"
            group_summaries, group_summary_reports = create_or_resume_group_summaries(
                root=root,
                group_summary_dir=group_summary_dir,
                contexts=contexts,
                batches=batches,
                package_id=package_id,
                schema=group_summary_schema,
                prompt_template=group_prompt_path.read_text(encoding="utf-8"),
                prompt_sha256=sha256sum(group_prompt_path),
                schema_sha256=sha256sum(hierarchical_paths["group_summary_schema"]),
                client=client,
                model=model,
                structured_output_mode=args.structured_output,
                enable_thinking=args.enable_thinking,
                max_tokens_group_summary=args.max_tokens_group_summary,
                temperature=args.temperature_synthesis,
                top_p=args.top_p,
                repair_attempts=args.repair_attempts,
                compression_attempts=args.group_summary_compression_attempts,
                resume=args.resume,
                overwrite_group_summaries=args.overwrite_group_summaries,
                output_policy=output_policy,
            )
            if args.stop_after == "group_summary":
                logging.info(
                    "step04 audit stop-after=group_summary completed summaries=%s",
                    len(group_summaries),
                )
                return 0
            discovery_schema = build_candidate_discovery_schema(final_schema)
            candidate_plan_schema = build_candidate_plan_schema(final_schema)
            if candidate_discovery_provider_schema is None or candidate_plan_provider_schema is None:
                raise RuntimeError("hierarchical stage provider schemas are unavailable")
            discovery_prompt_path = hierarchical_paths["candidate_discovery_prompt"]
            plan_prompt_path = hierarchical_paths["candidate_plan_prompt"]
            failure_stage = "candidate_discovery"
            discovery_response, candidate_discovery_report, discovery_diagnostics = (
                create_or_resume_candidate_discovery(
                    root=root,
                    output_dir=candidate_discovery_dir,
                    package_id=package_id,
                    group_summaries=group_summaries,
                    attachment_manifest=attachment_manifest,
                    support_catalog=support_catalog,
                    overview_data=overview_data,
                    schema=discovery_schema,
                    provider_schema=candidate_discovery_provider_schema,
                    prompt_template=discovery_prompt_path.read_text(encoding="utf-8"),
                    prompt_sha256=sha256sum(discovery_prompt_path),
                    client=client,
                    model=model,
                    structured_output_mode=args.structured_output,
                    enable_thinking=args.enable_synthesis_thinking,
                    max_tokens=args.max_tokens_candidate_discovery,
                    temperature=args.temperature_synthesis,
                    top_p=args.top_p,
                    repair_attempts=args.repair_attempts,
                    resume=args.resume and not args.overwrite,
                    reuse_as_immutable_input=args.candidate_id is not None,
                    output_policy=output_policy,
                    checkpoint_publisher=checkpoint_publisher,
                )
            )
            if args.stop_after == "candidate_discovery":
                logging.info(
                    "step04 audit stop-after=candidate_discovery completed candidates=%s",
                    len(discovery_response.get("candidates", [])),
                )
                return 0
            failure_stage = "candidate_plan"
            candidate_plan_results, candidate_plan_reports, candidate_plan_diagnostics = (
                create_or_resume_candidate_plans(
                    root=root,
                    output_dir=candidate_plan_dir,
                    package_id=package_id,
                    discovery_response=discovery_response,
                    group_summaries=group_summaries,
                    attachment_manifest=attachment_manifest,
                    support_catalog=support_catalog,
                    feature_catalog=feature_catalog,
                    operational_region_catalog=operational_region_catalog,
                    schema=candidate_plan_schema,
                    provider_schema=candidate_plan_provider_schema,
                    final_schema=final_schema,
                    prompt_template=plan_prompt_path.read_text(encoding="utf-8"),
                    prompt_sha256=sha256sum(plan_prompt_path),
                    client=client,
                    model=model,
                    structured_output_mode=args.structured_output,
                    enable_thinking=args.enable_synthesis_thinking,
                    max_tokens=args.max_tokens_candidate_plan,
                    temperature=args.temperature_synthesis,
                    top_p=args.top_p,
                    repair_attempts=args.repair_attempts,
                    request_gap_seconds=args.request_gap_seconds,
                    resume=args.resume and not args.overwrite,
                    candidate_ids={args.candidate_id} if args.candidate_id else None,
                    compact_continuation_attempts=args.candidate_plan_continuation_attempts,
                    output_policy=output_policy,
                    checkpoint_publisher=checkpoint_publisher,
                )
            )
            if args.candidate_id:
                logging.info(
                    "step05 candidate plan-only completed candidate_id=%s compact_chunks=%s continuations=%s",
                    args.candidate_id,
                    candidate_plan_reports[0].get("compact_plan_chunk_count", 0)
                    if candidate_plan_reports else 0,
                    candidate_plan_reports[0].get("continuation_attempt_count", 0)
                    if candidate_plan_reports else 0,
                )
                return 0
            final_response = merge_split_pass1_response(
                discovery_response,
                candidate_plan_results,
            )
            final_errors = validate_final_response(
                final_response,
                schema=final_schema,
                package_id=package_id,
                attachment_manifest=attachment_manifest,
                support_catalog=support_catalog,
                feature_catalog=feature_catalog,
                operational_region_catalog=operational_region_catalog,
                require_system_direct_precipitation_anchor=True,
            )
            if final_errors:
                raise Pass1Error(
                    "deterministic final merge validation failed: "
                    + " | ".join(final_errors[:30])
                )
            final_raw = json.dumps(final_response, ensure_ascii=False, indent=2)
            final_provider_summary = {}
            final_call_meta = {
                "merge_mode": "deterministic_python",
                "final_merge_provider_call_count": 0,
                "candidate_discovery_status": candidate_discovery_report.get("status"),
                "candidate_plan_count": len(candidate_plan_results),
            }
            hierarchical_diagnostics = {
                "candidate_discovery": discovery_diagnostics,
                "candidate_plans": candidate_plan_diagnostics,
                "final_merge_provider_call_count": 0,
            }
            logging.info(
                "step05 deterministic final merge completed candidates=%s plans=%s requests=%s "
                "provider_call_count=0",
                len(final_response.get("candidates", [])),
                len(final_response.get("candidate_analysis_plans", [])),
                len(final_response.get("analysis_data_requests", [])),
            )
        synthesis_elapsed = round(time.monotonic() - synthesis_started, 3)
        raw_final_path = pass1_dir / "ai_candidate_response.raw.txt"
        parsed_final_path = pass1_dir / "ai_candidate_response.json"
        validation_report_path = pass1_dir / "ai_candidate_response.validation_report.json"
        final_artifacts = write_final_response_artifacts(
            pass1_dir=pass1_dir,
            final_response=final_response,
            final_raw=final_raw,
            output_policy=output_policy,
        )
        raw_final_path = final_artifacts["raw"]
        parsed_final_path = final_artifacts["parsed"]
        final_validated_path = final_artifacts["validated"]

        validation_report = {
            "validation_status": "pass",
            "step": "step05",
            "script": "scripts/05_run_ai_pass1.py",
            "created_at_utc": now_utc(),
            "init": args.init,
            "package_id": package_id,
            "provider": args.provider,
            "model": model,
            "execution_mode": (
                "staged_image_observation_then_text_synthesis"
                if args.synthesis_mode == "direct"
                else (
                    "staged_image_observation_then_group_summary_then_candidate_discovery_"
                    "then_per_candidate_planning_then_deterministic_merge"
                )
            ),
            "synthesis_mode": args.synthesis_mode,
            "overview_attachment_count": len(attachment_by_id),
            "image_observation_batch_count": len(batch_observations),
            "max_images_per_request": args.max_images_per_request,
            "candidate_count": len(final_response.get("candidates", [])),
            "analysis_plan_count": len(final_response.get("candidate_analysis_plans", [])),
            "analysis_data_request_count": len(final_response.get("analysis_data_requests", [])),
            "batch_reports": batch_reports,
            "group_summary_reports": group_summary_reports,
            "candidate_discovery_report": candidate_discovery_report,
            "candidate_plan_reports": candidate_plan_reports,
            "synthesis": {
                "elapsed_seconds": synthesis_elapsed,
                **final_call_meta,
            },
            "output_refs": {
                "validated_response": rel(root, final_validated_path),
            },
            **output_policy.manifest_fields(
                [
                    "validated_response",
                    "validation_summary",
                    "provider_raw",
                    "intermediate_parsed_json",
                    "provider_metadata",
                    "payload_diagnostics",
                    "execution_snapshot",
                ]
            ),
            "checks": {
                "step04_input_validation_passed": True,
                "attachment_hashes_verified": True,
                "all_attachments_observed_once": True,
                "batch_observation_schema_valid": True,
                "candidate_discovery_schema_valid": True,
                "candidate_plan_schema_valid": True,
                "deterministic_final_merge_used": args.synthesis_mode == "hierarchical",
                "final_response_schema_valid": True,
                "final_response_reference_integrity_valid": True,
            },
        }
        if output_policy.should_write("provider_raw"):
            validation_report["output_refs"]["raw_response"] = rel(root, raw_final_path)
        if output_policy.should_write("intermediate_parsed_json"):
            validation_report["output_refs"]["parsed_response"] = rel(root, parsed_final_path)
        if output_policy.should_write("payload_diagnostics"):
            validation_report["hierarchical_payload_diagnostics"] = hierarchical_diagnostics
        if output_policy.should_write("provider_metadata"):
            validation_report["endpoint_id"] = client.provider.endpoint_id
            validation_report["synthesis"]["usage"] = final_provider_summary.get("usage")
            validation_report["synthesis"]["finish_reason"] = final_provider_summary.get("finish_reason")
        write_json(validation_report_path, validation_report)
        logging.info(
            "step05 completed package_id=%s candidates=%s requests=%s batches=%s synthesis_elapsed=%.3fs",
            package_id,
            len(final_response.get("candidates", [])),
            len(final_response.get("analysis_data_requests", [])),
            len(batch_observations),
            synthesis_elapsed,
        )
    except Exception as exc:  # noqa: BLE001
        logging.exception("step05 failed")
        write_transient_failure_summary(
            pass1_dir=pass1_dir,
            root=root,
            stage=failure_stage,
            exc=exc,
        )
        try:
            print(f"step04 실패: {exc}", file=sys.stderr)
        except OSError:
            logging.error("step05 실패 메시지를 표준 오류에 기록하지 못했습니다.")
        return 1

    print("AIFSHeavyRain step04 Pass 1")
    print(f"  package_id: {package_id}")
    print(f"  provider: {args.provider}")
    print(f"  model: {model}")
    print(f"  overview_attachment_count: {len(attachment_by_id)}")
    print(f"  image_observation_batch_count: {len(batch_observations)}")
    print(f"  candidate_count: {len(final_response.get('candidates', []))}")
    print(f"  analysis_data_request_count: {len(final_response.get('analysis_data_requests', []))}")
    print(f"  validated_response: {rel(root, final_validated_path)}")
    return 0
