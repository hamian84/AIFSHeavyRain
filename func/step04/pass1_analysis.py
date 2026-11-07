"""Step step04 analysis responsibility; original function bodies preserved."""
from __future__ import annotations
from . import pass1_context as _step_context
AIFS_NATIVE_SPATIAL_INTERVAL_DEGREES = getattr(_step_context, 'AIFS_NATIVE_SPATIAL_INTERVAL_DEGREES')
CANDIDATE_DISCOVERY_RECONCILIATION_ASSET_LIMIT = getattr(_step_context, 'CANDIDATE_DISCOVERY_RECONCILIATION_ASSET_LIMIT')
CANDIDATE_DISCOVERY_RECONCILIATION_EVIDENCE_LIMIT = getattr(_step_context, 'CANDIDATE_DISCOVERY_RECONCILIATION_EVIDENCE_LIMIT')

# Original func/step04/pass1_engine.py:1428
def build_http_payload(
    *,
    model: str,
    messages: list[dict[str, Any]],
    response_schema: dict[str, Any] | None,
    schema_name: str,
    structured_output: bool,
    enable_thinking: bool,
    extensions: bool,
    max_tokens: int,
    temperature: float,
    top_p: float,
) -> dict[str, Any]:
    return build_chat_completions_http_payload(
        model=model,
        messages=messages,
        response_schema=response_schema,
        schema_name=schema_name,
        structured_output=structured_output,
        enable_thinking=enable_thinking,
        extensions=extensions,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
    )

# Original func/step04/pass1_engine.py:1455
class Pass1ProviderClient:
    """Compatibility adapter that keeps Pass1 repair flow provider-neutral."""

    _TRANSPORT_STAGE_BY_SCHEMA = {
        "AIFSPass1BatchObservation": "batch_observation",
        "AIFSPass1BatchObservationCompactRecovery": "batch_observation",
        "AIFSPass1SingleImageObservation": "batch_observation",
        "AIFSPass1GroupSummary": "group_summary",
        "AIFSPass1GroupSummaryCompaction": "group_summary",
        "AIFSPass1CandidateDiscovery": "candidate_discovery",
        "AIFSPass1CandidatePlan": "candidate_plan",
        "AIFSPass1CandidateResponse": "candidate_response",
    }

    _REASONING_STAGE_BY_SCHEMA = {
        "AIFSPass1BatchObservation": "observation",
        "AIFSPass1BatchObservationCompactRecovery": "observation",
        "AIFSPass1SingleImageObservation": "observation",
        "AIFSPass1GroupSummary": "group_summary",
        "AIFSPass1GroupSummaryCompaction": "group_summary",
        "AIFSPass1CandidateDiscovery": "candidate_discovery",
        "AIFSPass1CandidatePlan": "candidate_plan",
        "AIFSPass1CandidateResponse": "candidate_discovery",
    }

    def __init__(
        self,
        *,
        provider: AIProvider,
        image_detail: str,
        reasoning_efforts: dict[str, str],
        transport_schemas: dict[str, dict[str, Any]],
    ) -> None:
        self.provider = provider
        self.image_detail = image_detail
        self.reasoning_efforts = dict(reasoning_efforts)
        self.transport_schemas = transport_schemas
        if self.provider.api_family == "responses":
            for stage, schema in sorted(self.transport_schemas.items()):
                try:
                    validate_openai_transport_schema(schema)
                except ProviderConfigurationError as exc:
                    raise ProviderConfigurationError(
                        f"invalid OpenAI transport schema stage={stage}: {exc}"
                    ) from exc
        self.transport_schema_hashes = {
            stage: hashlib.sha256(
                json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            for stage, schema in transport_schemas.items()
        }
        self.last_result: ProviderResult | None = None

    @property
    def checkpoint_configuration(self) -> dict[str, Any]:
        return {
            "provider": self.provider.provider_name,
            "api_family": self.provider.api_family,
            "endpoint_id": self.provider.endpoint_id,
            "image_detail": self.image_detail if self.provider.api_family == "responses" else None,
            "reasoning_efforts": self.reasoning_efforts if self.provider.api_family == "responses" else None,
            "structured_output_mode": "strict" if self.provider.api_family == "responses" else "legacy",
            "transport_schema_sha256": self.transport_schema_hashes if self.provider.api_family == "responses" else None,
        }

    def checkpoint_configuration_for_stage(self, stage: str) -> dict[str, Any]:
        configuration = self.checkpoint_configuration
        if configuration.get("api_family") != "responses":
            return configuration
        reasoning_stage = {
            "batch_observation": "observation",
            "group_summary": "group_summary",
            "candidate_discovery": "candidate_discovery",
            "candidate_plan": "candidate_plan",
        }[stage]
        reasoning_efforts = configuration.get("reasoning_efforts")
        if isinstance(reasoning_efforts, dict):
            configuration["reasoning_efforts"] = {
                reasoning_stage: reasoning_efforts.get(reasoning_stage)
            }
        transport_hashes = configuration.get("transport_schema_sha256")
        if isinstance(transport_hashes, dict):
            configuration["transport_schema_sha256"] = {
                stage: transport_hashes.get(stage)
            }
        return configuration

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        response_schema: dict[str, Any] | None,
        schema_name: str,
        structured_output_mode: str,
        enable_thinking: bool,
        max_tokens: int,
        temperature: float,
        top_p: float,
    ) -> ProviderResult:
        stage = self._TRANSPORT_STAGE_BY_SCHEMA.get(schema_name)
        canonical_schema = response_schema
        dynamic_candidate_plan_transport = schema_name in {
            "AIFSPass1CandidatePlanDiagnosticCheckRepair",
            "AIFSPass1CandidatePlanContinuationSpatialEvolutionGapRepair",
        }
        if self.provider.api_family == "responses":
            if dynamic_candidate_plan_transport:
                if not isinstance(response_schema, dict):
                    raise Pass1Error(
                        f"OpenAI dynamic transport schema is unavailable for stage schema {schema_name}"
                    )
                try:
                    validate_openai_transport_schema(response_schema)
                except ProviderConfigurationError as exc:
                    raise Pass1Error(
                        f"invalid OpenAI dynamic transport schema for stage schema {schema_name}: {exc}"
                    ) from exc
            else:
                if stage is None or stage not in self.transport_schemas:
                    raise Pass1Error(f"OpenAI transport schema is unavailable for stage schema {schema_name}")
                response_schema = self.transport_schemas[stage]
        reasoning_stage = (
            "candidate_plan"
            if dynamic_candidate_plan_transport
            else self._REASONING_STAGE_BY_SCHEMA.get(schema_name, "candidate_discovery")
        )
        reasoning_effort = self.reasoning_efforts.get(reasoning_stage)
        try:
            response: ProviderResponse = self.provider.complete(
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                schema=response_schema,
                schema_name=schema_name,
                structured_output_mode=structured_output_mode,
                enable_thinking=enable_thinking,
                temperature=temperature,
                top_p=top_p,
                image_detail=self.image_detail,
                reasoning_effort=reasoning_effort,
            )
        except SharedProviderIncompleteError as exc:
            raise ProviderIncompleteError(str(exc), diagnostics=exc.diagnostics) from exc
        except SharedProviderHTTPError as exc:
            raise ProviderHTTPError(exc.status, "", message=str(exc)) from exc
        except SharedProviderError as exc:
            raise Pass1Error(str(exc)) from exc
        content = response.content
        if self.provider.api_family == "responses" and canonical_schema is not None:
            # ``response_schema`` is transport schema here; canonicalization needs the
            # original caller schema saved before its OpenAI substitution.
            content = normalize_openai_transport_json(content, canonical_schema)
        result = ProviderResult(
            content=content,
            response_envelope=response.compact_envelope(),
            structured_output_used=response.structured_output_used,
            provider_extensions_used=response.provider_extensions_used,
            http_retry_count=response.retry_count,
            structured_output_fallback_count=response.structured_output_fallback_count,
            usage_attempts=response.attempt_usage,
        )
        if response.finish_reason == "length":
            logging.warning(
                "provider response reached output length limit provider=%s api_family=%s schema=%s",
                self.provider.provider_name,
                self.provider.api_family,
                schema_name,
            )
        self.last_result = result
        return result

# Original func/step04/pass1_engine.py:1628
def image_data_url(path: Path) -> str:
    mime = SUPPORTED_IMAGE_SUFFIXES.get(path.suffix.lower())
    if mime is None:
        raise ValueError(f"unsupported image format: {path}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"

# Original func/step04/pass1_engine.py:1754
def _batch_attachment_descriptor(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "asset_id": item.get("asset_id"),
            "product_id": item.get("product_id"),
            "analysis_tier": item.get("analysis_tier"),
            "group_id": item.get("group_id"),
            "forecast_hours": item.get("forecast_hours"),
            "valid_times_utc": item.get("valid_times_utc"),
            "source_panels": item.get("source_panels"),
            "render_domain_id": item.get("render_domain_id"),
            "render_domain_bbox": item.get("render_domain_bbox"),
            "unit_label": item.get("unit_label"),
        }
        for item in items
    ]

# Original func/step04/pass1_engine.py:1772
def build_batch_messages(
    *,
    package_id: str,
    batch: dict[str, Any],
    attachments: list[dict[str, Any]],
    root: Path,
    prompt_template: str,
) -> list[dict[str, Any]]:
    descriptor = _batch_attachment_descriptor(attachments)
    content: list[dict[str, Any]] = []
    # Gemma 4 multimodal guidance recommends placing image content before text.
    for item in attachments:
        path = resolve_logical_ref(root, str(item["file_path"]))
        content.append({"type": "image_url", "image_url": {"url": image_data_url(path)}})
    prompt = (
        prompt_template.strip()
        + "\n\n## 현재 배치\n"
        + json.dumps(
            {
                "package_id": package_id,
                "batch_id": batch["batch_id"],
                "analysis_tier": batch.get("analysis_tier"),
                "group_id": batch.get("group_id"),
                "attachment_count": len(attachments),
                "attachments_in_payload_order": descriptor,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n\n## Response contract\n"
        + json.dumps(
            {
                "attachment_observations_order": "same order as images in the payload",
                "system_assigned_fields": [
                    "package_id",
                    "batch_id",
                    "analysis_tier",
                    "group_id",
                    "observed_attachment_count",
                    "asset_id",
                    "product_id",
                    "forecast_hours",
                ],
                "rules": [
                    "이미지 payload 순서대로 이미지마다 관찰 항목을 하나씩 반환합니다.",
                    "응답 스키마가 요구하는 이미지 기반 분석 필드만 반환합니다.",
                    "식별자와 시간 메타데이터는 시스템이 결정하므로 추가하지 않습니다.",
                    "사람이 읽는 모든 관찰·설명 문장은 한국어로 작성합니다.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    content.append({"type": "text", "text": prompt})
    return [
        {
            "role": "system",
            "content": (
                "당신은 AIFS 강수 감시용 개요장을 관찰하는 멀티모달 분석기입니다. "
                "현재 단계에서는 후보를 생성하지 말고, 제공된 원본 asset_id에 연결된 관찰 사실만 JSON으로 기록하십시오. "
                "JSON 키와 기계 식별자는 계약값을 유지하고, 사람이 읽는 모든 설명 문장은 한국어로 작성하십시오."
            ),
        },
        {"role": "user", "content": content},
    ]

# Original func/step04/pass1_engine.py:2061
def build_source_grounded_retry_messages(
    messages: list[dict[str, Any]],
    *,
    validation_errors: list[str],
    retry_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Re-send the original multimodal request with a strict completeness correction.

    The original messages include the source images. This retry is therefore able
    to recover omitted attachment observations, unlike a text-only JSON repair.
    """

    correction = {
        "task": (
            "Re-read every image in the original multimodal request and return a "
            "complete JSON object that satisfies the required attachment coverage."
        ),
        "validation_errors": validation_errors,
        "required_attachment_count": retry_context.get("required_attachment_count"),
        "payload_order": "same order as the original image payload",
        "rules": [
            "The original source images are included earlier in this same request.",
            "Return JSON only. Do not use Markdown or explanatory text.",
            "Return exactly one attachment_observations item for every source image.",
            "Keep attachment_observations in the original image payload order.",
            "Do not output package, batch, asset, product, or forecast metadata fields.",
            "Keep observations grounded only in the corresponding source image.",
        ],
    }
    return [
        *messages,
        {
            "role": "user",
            "content": json.dumps(correction, ensure_ascii=False),
        },
    ]

# Original func/step04/pass1_engine.py:2166
def build_compact_observation_recovery_messages(
    messages: list[dict[str, Any]],
    *,
    batch: dict[str, Any],
) -> list[dict[str, Any]]:
    """Request the same observations with bounded factual prose after a length stop."""
    correction = {
        "task": "직전 응답은 길이 제한으로 JSON을 완성하지 못했습니다. 원본 이미지 4장을 다시 판독해 완전한 JSON만 반환하십시오.",
        "required_asset_ids": [str(item) for item in batch["attachment_ids"]],
        "rules": [
            "각 required_asset_id에 대해 attachment_observations 항목을 정확히 하나씩 작성하십시오.",
            "이미지에서 확인되는 사실만 짧게 작성하고 반복, 서론, 결론, 후보 판단을 넣지 마십시오.",
            "observed_signals는 시간 변화를 대표하는 짧은 문장 1~4개로 제한하십시오.",
            "spatial_pattern과 temporal_evolution은 각각 한 문장으로 작성하십시오.",
            "limitations, cross_product_synthesis, batch_uncertainties는 필요한 경우에만 짧게 작성하십시오.",
            "각 이미지의 direct_precipitation_signal을 반드시 유지하고 QL_PRECIP_6H 이외에는 not_applicable로 작성하십시오.",
            "JSON 외의 설명, Markdown, 들여쓰기용 공백을 출력하지 말고 마지막 } 직후 즉시 종료하십시오.",
        ],
    }
    return [
        *messages,
        {"role": "user", "content": json.dumps(correction, ensure_ascii=False)},
    ]

# Original func/step04/pass1_engine.py:2246
def _compact_phase_reference_failure_context(
    value: dict[str, Any],
    validation_errors: list[str],
    *,
    previous_chunks: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return bounded structural context for compact-plan phase graph failures.

    Whole-chunk regeneration intentionally does not send the discarded provider
    response back to the model. For unreferenced phases or unresolved quantitative
    forecast-hour ownership, the validator can safely expose a compact structural
    summary of declared phases and references. This is diagnostic context only;
    it must never be merged into or patched onto the regenerated plan.
    """
    has_unreferenced_phase_error = any(
        "COMPACT_PLAN_UNREFERENCED_PHASE" in str(error) for error in validation_errors
    )
    has_quantitative_phase_error = any(
        "QUANTITATIVE_SCOPE_PHASE_MISMATCH" in str(error) for error in validation_errors
    )
    has_hour_coverage_error = any(
        "insufficient forecast-hour coverage" in str(error) for error in validation_errors
    )
    if not (has_unreferenced_phase_error or has_quantitative_phase_error or has_hour_coverage_error):
        return None
    if not isinstance(value, dict):
        return None

    chunks = [*(previous_chunks or []), value]
    phases = [
        phase
        for chunk in chunks
        for phase in _compact_plan_rows(chunk, "analysis_phases")
    ]
    checks = [
        check
        for chunk in chunks
        for check in _compact_plan_rows(chunk, "diagnostic_checks")
    ]
    if not phases:
        return None

    def local_key(raw: Any) -> str:
        key = str(raw or "").strip()
        match = re.fullmatch(r"chunk\d+:(.+)", key)
        return match.group(1) if match else key

    declared_keys = {
        str(row.get("phase_key", "")).strip()
        for row in phases
        if str(row.get("phase_key", "")).strip()
    }
    referenced_keys = {
        str(phase_key).strip()
        for check in checks
        for phase_key in check.get("phase_keys", [])
        if str(phase_key).strip()
    }
    unreferenced_keys = _unreferenced_compact_phase_keys(chunks)
    phase_hours_by_key = {
        str(row.get("phase_key", "")).strip(): set(_compact_phase_hours(row))
        for row in phases
        if str(row.get("phase_key", "")).strip()
    }
    quantitative_phase_mismatches: list[dict[str, Any]] = []
    for check in checks:
        request_phase_keys = [
            str(key).strip() for key in check.get("phase_keys", []) if str(key).strip()
        ]
        request_hours = {
            hour
            for phase_key in request_phase_keys
            for hour in phase_hours_by_key.get(phase_key, set())
        }
        for claim in check.get("atomic_claims", []):
            if not isinstance(claim, dict):
                continue
            scope = claim.get("quantitative_scope")
            if not isinstance(scope, dict):
                continue
            raw_hours = scope.get("forecast_hours")
            if not isinstance(raw_hours, list) or any(
                isinstance(hour, bool) or not isinstance(hour, int) or hour < 0
                for hour in raw_hours
            ):
                continue
            quantitative_hours = set(int(hour) for hour in raw_hours)
            claim_phase_keys = [
                str(key).strip() for key in claim.get("phase_keys", []) if str(key).strip()
            ]
            claim_hours = {
                hour
                for phase_key in claim_phase_keys
                for hour in phase_hours_by_key.get(phase_key, set())
            }
            missing_request_hours = sorted(quantitative_hours - request_hours)
            missing_claim_hours = sorted(quantitative_hours - claim_hours)
            if not missing_request_hours and not missing_claim_hours:
                continue
            missing_hours = sorted(set(missing_request_hours) | set(missing_claim_hours))
            quantitative_phase_mismatches.append(
                {
                    "check_key": local_key(check.get("check_key")),
                    "claim_key": str(claim.get("claim_key") or ""),
                    "quantitative_forecast_hours": sorted(quantitative_hours),
                    "request_phase_keys": [local_key(key) for key in request_phase_keys],
                    "claim_phase_keys": [local_key(key) for key in claim_phase_keys],
                    "missing_request_hours": missing_request_hours,
                    "missing_claim_hours": missing_claim_hours,
                    "declared_covering_phase_keys": {
                        str(hour): [
                            local_key(phase_key)
                            for phase_key, phase_hours in phase_hours_by_key.items()
                            if hour in phase_hours
                        ]
                        for hour in missing_hours
                    },
                }
            )

    return {
        "declared_phases": [
            {
                "phase_key": local_key(row.get("phase_key")),
                "normalized_phase_key": str(row.get("phase_key", "")),
                "phase_type": str(row.get("phase_type", "")),
                "forecast_hours": sorted(_compact_phase_hours(row)),
            }
            for row in phases
        ],
        "check_phase_references": [
            {
                "check_key": local_key(check.get("check_key")),
                "phase_keys": [local_key(key) for key in check.get("phase_keys", [])],
                "normalized_phase_keys": [str(key) for key in check.get("phase_keys", [])],
                "verification_mode": _verification_mode_from_diagnostic_check(check),
                "signal_components": list(check.get("required_signal_components", [])),
            }
            for check in checks
        ],
        "unreferenced_phase_keys": [local_key(key) for key in unreferenced_keys],
        "normalized_unreferenced_phase_keys": unreferenced_keys,
        "quantitative_scope_phase_mismatches": quantitative_phase_mismatches,
        "forecast_hour_coverage_failures": [
            {
                "check_key": local_key(check.get("check_key")),
                "phase_keys": [local_key(key) for key in check.get("phase_keys", [])],
                "referenced_hours": sorted({
                    hour for key in check.get("phase_keys", [])
                    for hour in phase_hours_by_key.get(str(key), set())
                }),
                "required_hours": _minimum_forecast_hours_for_check(
                    str(check.get("diagnostic_id") or ""),
                    str(check.get("check_scope") or ""),
                ),
            }
            for check in checks
            if len({
                hour for key in check.get("phase_keys", [])
                for hour in phase_hours_by_key.get(str(key), set())
            }) < _minimum_forecast_hours_for_check(
                str(check.get("diagnostic_id") or ""),
                str(check.get("check_scope") or ""),
            )
        ] if has_hour_coverage_error else [],
        "instruction": (
            "이 구조는 실패 원인을 설명하기 위한 참고정보입니다. 이전 객체를 부분 수정하지 말고 "
            "원래 후보 컨텍스트에서 chunk 전체를 다시 생성하십시오. 미참조 phase가 기상학적으로 필요하면 "
            "의미 있는 verification_request가 실제로 참조하도록 하고, 필요하지 않으면 새 출력에 선언하지 마십시오. "
            "정량 명제가 여러 F시각을 요구하면 각 시각이 실제 analysis phase에 포함되고 verification_request와 "
            "atomic claim이 모두 그 phase를 참조해야 합니다. 스키마 통과만을 위한 무의미한 요청이나 정량 "
            "forecast_hours 임의 삭제는 하지 마십시오."
        ),
    }

# Original func/step04/pass1_engine.py:2422
def _compact_check_reference_failure_context(
    value: dict[str, Any],
    validation_errors: list[str],
    *,
    previous_chunks: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return bounded graph context for an unresolved hypothesis Check reference.

    Candidate-plan chunk regeneration intentionally starts again from the original
    candidate context instead of patching a failed provider response.  A dangling
    hypothesis Check reference is different from a meteorological-content error:
    the repair model needs the *valid graph keys* in order to avoid inventing a
    stale continuation key again.  Expose only structural metadata (keys, modes,
    components, and phase references), never the discarded narrative content.
    """
    if not any(
        "unresolved check reference after normalization" in str(error)
        for error in validation_errors
    ):
        return None
    if not isinstance(value, dict):
        return None

    previous = list(previous_chunks or [])
    previous_checks = [
        check
        for chunk in previous
        for check in _compact_plan_rows(chunk, "diagnostic_checks")
    ]
    current_checks = _compact_plan_rows(value, "diagnostic_checks")
    hypotheses = _compact_plan_rows(value, "hypotheses")

    def local_key(raw: Any) -> str:
        key = str(raw or "").strip()
        match = re.fullmatch(r"chunk\d+:(.+)", key)
        return match.group(1) if match else key

    existing_check_keys = sorted(
        {
            str(check.get("check_key", "")).strip()
            for check in previous_checks
            if str(check.get("check_key", "")).strip()
        }
    )
    normalized_current_check_keys = sorted(
        {
            str(check.get("check_key", "")).strip()
            for check in current_checks
            if str(check.get("check_key", "")).strip()
        }
    )
    valid_normalized_keys = set(existing_check_keys) | set(normalized_current_check_keys)

    hypothesis_rows: list[dict[str, Any]] = []
    unresolved: set[str] = set()
    for hypothesis in hypotheses:
        supporting = [
            str(key).strip()
            for key in hypothesis.get("supporting_check_keys", [])
            if str(key).strip()
        ]
        opposing = [
            str(key).strip()
            for key in hypothesis.get("opposing_check_keys", [])
            if str(key).strip()
        ]
        bad = sorted((set(supporting) | set(opposing)) - valid_normalized_keys)
        unresolved.update(bad)
        hypothesis_rows.append(
            {
                "hypothesis_key": local_key(hypothesis.get("hypothesis_key")),
                "supporting_check_keys": [local_key(key) for key in supporting],
                "opposing_check_keys": [local_key(key) for key in opposing],
                "normalized_supporting_check_keys": supporting,
                "normalized_opposing_check_keys": opposing,
                "unresolved_check_keys": bad,
            }
        )

    def check_summary(check: dict[str, Any], *, include_local_key: bool) -> dict[str, Any]:
        normalized_key = str(check.get("check_key", "")).strip()
        row = {
            "check_key": local_key(normalized_key) if include_local_key else normalized_key,
            "verification_mode": _verification_mode_from_diagnostic_check(check),
            "signal_components": list(check.get("required_signal_components", [])),
            "phase_keys": [local_key(key) for key in check.get("phase_keys", [])],
        }
        if include_local_key:
            row["normalized_check_key"] = normalized_key
        return row

    return {
        "existing_check_keys": existing_check_keys,
        "existing_checks": [
            check_summary(check, include_local_key=False) for check in previous_checks
        ],
        "current_declared_check_keys": [
            local_key(key) for key in normalized_current_check_keys
        ],
        "normalized_current_declared_check_keys": normalized_current_check_keys,
        "current_declared_checks": [
            check_summary(check, include_local_key=True) for check in current_checks
        ],
        "hypothesis_check_references": hypothesis_rows,
        "unresolved_check_keys": sorted(unresolved),
        "instruction": (
            "이 구조는 hypothesis의 Check 참조 오류를 설명하기 위한 참고정보입니다. "
            "이전 chunk를 참조할 때는 existing_check_keys에 실제 존재하는 key만 그대로 사용하십시오. "
            "현재 chunk에서 새 verification_request를 선언하면 hypothesis는 그 요청의 check_key 로컬값을 "
            "정확히 참조하십시오. 번호를 추정해 존재하지 않는 chunkXX:CHKYY key를 만들지 마십시오. "
            "필요한 기상학적 근거에 대응하는 유효 Check가 없다면 의미 있는 verification_request를 현재 "
            "chunk에 새로 구성하고 그 check_key를 참조하십시오. 오류를 숨기기 위해 참조만 삭제하지 마십시오."
        ),
    }

# Original func/step04/pass1_engine.py:3250
def _compact_existing_phase_context(
    chunks: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Return only the prior phase identifiers and actual hours needed for repair."""
    rows: list[dict[str, Any]] = []
    for chunk in chunks or []:
        for phase in _compact_plan_rows(chunk, "analysis_phases"):
            rows.append(
                {
                    "phase_key": str(phase.get("phase_key") or ""),
                    "phase_type": str(phase.get("phase_type") or ""),
                    "forecast_hours": list(_compact_phase_hours(phase)),
                }
            )
    return rows

# Original func/step04/pass1_engine.py:5842
def build_system_direct_precipitation_coverage_reconciliation_messages(
    *,
    base_messages: list[dict[str, Any]],
    current_response: dict[str, Any],
    audit: dict[str, Any],
    attachment_manifest: dict[str, Any],
    worklist_offset: int = 0,
    worklist_limit: int = 4,
) -> list[dict[str, Any]]:
    all_missing = _required_uncovered_system_direct_precipitation_signals(audit)
    # Small work batches avoid enormous candidate-by-candidate error prose.
    # Sort by intensity first; grouping is a repair worklist, not storm identity.
    prioritized = sorted(
        all_missing,
        key=lambda row: (-float(row.get("maximum_6h_mm") or 0), int(row["forecast_hour"]), str(row["region_id"])),
    )
    # Rejected proposals should not repeatedly receive the same oversized worklist.
    # Rotate among unresolved signals; preserve the original SYSTEM signal ledger.
    limit = max(1, min(4, int(worklist_limit)))
    if prioritized:
        offset = int(worklist_offset) % len(prioritized)
        selected = (prioritized[offset:] + prioritized[:offset])[:limit]
    else:
        selected = []
    missing = []
    for row in selected:
        item = {key: row.get(key) for key in (
            "forecast_hour", "region_id", "region_name_ko", "maximum_6h_mm", "max_location",
        )}
        # The validator knows whether an existing candidate failed due to time,
        # space, asset, or evidence. Supply that distinction to the repair model.
        # This is diagnostic metadata, not a change in the acceptance contract.
        candidate_diagnostics = [
            {
                "candidate_id": diag.get("candidate_id"),
                "failure_reasons": diag.get("failure_reasons", []),
                "matching_precipitation_asset_ids": diag.get("matching_precipitation_asset_ids", []),
            }
            for diag in row.get("candidate_match_diagnostics", [])
            if isinstance(diag, dict)
        ]
        item["candidate_diagnostics"] = sorted(
            candidate_diagnostics,
            key=lambda diag: (len(diag["failure_reasons"]), str(diag["candidate_id"])),
        )[:4]
        missing.append(item)
    hours = sorted({int(row["forecast_hour"]) for row in missing})
    precipitation_assets_by_hour = {
        f"F{hour:03d}": _precipitation_asset_ids_for_hour(attachment_manifest, hour)
        for hour in hours
    }
    payload = {
        "task": (
            "현재 candidate discovery의 SYSTEM 직접강수 coverage 누락만 보완하십시오. "
            "후보 탐색을 처음부터 다시 하지 마십시오."
        ),
        "contract_version": DIRECT_PRECIPITATION_COVERAGE_RECONCILIATION_VERSION,
        "current_candidate_discovery": current_response,
        "required_uncovered_signals": missing,
        "total_required_uncovered_signal_count": len(all_missing),
        "worklist_size": len(missing),
        "matching_precipitation_assets_by_hour": precipitation_assets_by_hour,
        "candidate_capacity": _coverage_reconciliation_capacity(current_response),
        "rules": [
            "기존 후보와 candidate_evidence는 삭제하거나 순서를 바꾸지 마십시오.",
            "누락 신호가 기존 후보와 동일한 기상계라는 근거가 있을 때만 해당 후보의 bbox·time_window·관련 강수 asset/evidence를 바깥쪽으로 확장하십시오.",
            "기존 후보의 detection_basis, precipitation_signal_status, precursor 판단, confidence, diagnostic_plan, separation_reason, 기존 근거·제한·불확실성은 변경하거나 삭제하지 마십시오.",
            "기존 후보에서는 candidate_area, time_window, 관련 강수 asset/evidence, 그리고 확장 범위를 설명하기 위한 candidate_name·hypothesis.statement 외의 필드를 수정하지 마십시오.",
            "서로 떨어진 강수군을 coverage만 맞추기 위해 하나의 큰 bbox로 합치지 마십시오. 별도 기상계이면 direct_precipitation 또는 mixed 후보를 새로 추가하십시오.",
            "각 required_uncovered_signal은 해당 forecast_hour의 QL_PRECIP_6H asset을 related_overview_asset_ids와 candidate_evidence에 포함해야 합니다.",
            "각 후보의 candidate_capacity를 확인하십시오. 기존 QL_PRECIP_6H evidence_item이 같은 forecast_hour를 포함할 수 있으면 기존 항목의 forecast_hours만 확장하십시오. 기존 이미지·근거를 중복으로 추가하지 마십시오.",
            "초기 후보당 영상 8개·근거 6개 제한은 유지됩니다. 이 보완 단계에서만 기존 항목 보존을 위해 영상 최대 16개·근거 최대 12개까지 허용됩니다. 신규 precipitation asset이 실제로 필요할 때만 추가하십시오.",
            "보완 후에도 슬롯이 부족하면 기존 후보의 자료를 잘라내지 마십시오. 독립 기상계로 확인되는 경우에만 별도 후보를 검토하고, 독립성이 확인되지 않으면 안전한 미보완 상태를 유지하십시오.",
            "SYSTEM candidate reference threshold를 낮추거나 정량 신호를 삭제하지 마십시오.",
            "5 mm 이상이라는 이유만으로 해당 시각을 peak_forecast_hours에 추가하지 마십시오. 실제 후보 강수의 peak일 때만 추가하십시오.",
            "candidate_name과 hypothesis.statement은 기존 후보의 확장된 시간·공간 범위를 사실대로 표현하기 위해 필요한 경우에만 수정하십시오.",
            "완전한 candidate discovery JSON 객체 하나만 반환하십시오. Python은 허용된 포괄성 변경만 적용합니다.",
            "이번 작업목록에 포함된 신호를 우선 처리하십시오. 다른 포괄 신호를 잃거나 강수계를 억지로 병합하지 마십시오.",
            "candidate_diagnostics의 failure_reasons를 확인하고 공간/시간/영상/근거 중 실제 누락된 항목만 보완하십시오.",
            "10개 후보 개수 상한을 넘기거나, 그 상한을 맞추기 위해 독립적인 강수계를 무리하게 병합하지 마십시오.",
        ],
    }
    return [
        *base_messages,
        {
            "role": "user",
            "content": "## SYSTEM direct precipitation coverage reconciliation\n" + json.dumps(payload, ensure_ascii=False),
        },
    ]

# Original func/step04/pass1_engine.py:7687
def _group_sort_key(tier: str, group_id: str) -> tuple[int, str, str]:
    return ({"temporal_core": 0, "synoptic_context": 1}.get(tier, 99), tier, group_id)

# Original func/step04/pass1_engine.py:7691
def group_summary_id(tier: str, group_id: str) -> str:
    return f"PASS1_SUMMARY_{tier.upper()}_{group_id.upper()}"

# Original func/step04/pass1_engine.py:7695
def _valid_time_range(items: list[dict[str, Any]]) -> dict[str, str | None]:
    values = sorted(
        {
            str(value)
            for item in items
            for value in item.get("valid_times_utc", [])
            if value is not None and str(value)
        }
    )
    return {"start_utc": values[0] if values else None, "end_utc": values[-1] if values else None}

# Original func/step04/pass1_engine.py:7707
def build_group_contexts(
    *,
    batch_observations: list[dict[str, Any]],
    batches: list[dict[str, Any]],
    attachment_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group observations by their validated tier and group fields, never filenames."""
    batches_by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    batch_by_id: dict[str, dict[str, Any]] = {}
    for batch in batches:
        batch_id = str(batch["batch_id"])
        if batch_id in batch_by_id:
            raise ValueError(f"duplicate batch_id in observation plan: {batch_id}")
        batch_by_id[batch_id] = batch
        batches_by_group[(str(batch.get("analysis_tier")), str(batch.get("group_id")))].append(batch)

    observations_by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for observation in batch_observations:
        key = (str(observation.get("analysis_tier")), str(observation.get("group_id")))
        batch_id = str(observation.get("batch_id"))
        batch = batch_by_id.get(batch_id)
        if batch is None:
            raise ValueError(f"observation references batch outside current plan: {batch_id}")
        if key != (str(batch.get("analysis_tier")), str(batch.get("group_id"))):
            raise ValueError(f"observation tier/group does not match current plan: {batch_id}")
        observations_by_group[key].append(observation)

    contexts: list[dict[str, Any]] = []
    for key in sorted(batches_by_group, key=lambda value: _group_sort_key(*value)):
        tier, group_id = key
        planned_batches = sorted(batches_by_group[key], key=lambda item: str(item["batch_id"]))
        observations = sorted(
            observations_by_group.get(key, []), key=lambda item: str(item.get("batch_id"))
        )
        expected_batch_ids = [str(item["batch_id"]) for item in planned_batches]
        actual_batch_ids = [str(item.get("batch_id")) for item in observations]
        if Counter(actual_batch_ids) != Counter(expected_batch_ids):
            raise ValueError(
                f"group observation batch coverage mismatch tier={tier} group={group_id} "
                f"expected={expected_batch_ids} actual={actual_batch_ids}"
            )
        expected_asset_ids = [
            str(asset_id) for batch in planned_batches for asset_id in batch["attachment_ids"]
        ]
        source_items = [attachment_by_id[asset_id] for asset_id in expected_asset_ids]
        source_rows: list[dict[str, Any]] = []
        for observation in observations:
            batch_id = str(observation["batch_id"])
            rows = observation.get("attachment_observations", [])
            if not isinstance(rows, list):
                raise ValueError(f"observation attachment_observations is invalid: {batch_id}")
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError(f"observation row is invalid: {batch_id}")
                source_rows.append({"source_batch_id": batch_id, **row})
        actual_asset_ids = [str(row.get("asset_id")) for row in source_rows]
        if Counter(actual_asset_ids) != Counter(expected_asset_ids):
            raise ValueError(
                f"group observation asset coverage mismatch tier={tier} group={group_id} "
                f"expected={expected_asset_ids} actual={actual_asset_ids}"
            )
        contexts.append(
            {
                "summary_id": group_summary_id(tier, group_id),
                "analysis_tier": tier,
                "group_id": group_id,
                "source_batch_ids": expected_batch_ids,
                "source_asset_ids": expected_asset_ids,
                "source_product_ids": sorted({str(item.get("product_id")) for item in source_items}),
                "forecast_hours": sorted(
                    {int(hour) for item in source_items for hour in item.get("forecast_hours", [])}
                ),
                "valid_time_range": _valid_time_range(source_items),
                "source_observations": observations,
                "source_rows": source_rows,
                "asset_to_batch_id": {str(row["asset_id"]): str(row["source_batch_id"]) for row in source_rows},
                "attachment_by_id": {str(item["asset_id"]): item for item in source_items},
            }
        )
    return contexts

# Original func/step04/pass1_engine.py:7811
def _compact_group_summary_narratives(summary: dict[str, Any]) -> dict[str, Any]:
    """Keep mandatory group coverage while bounding verbose VLM narrative fields.

    A single observation batch already includes every asset in a group.  When
    its free-text descriptions exceed the group-summary transport budget, the
    old flow called the provider again for compaction.  That extra provider
    call is non-deterministic and prevents resume from reproducing the same
    candidate-discovery input.  This projection keeps every source asset,
    product, forecast hour, and one concise form of each narrative field.
    """
    if json_utf8_bytes(summary) <= GROUP_SUMMARY_TARGET_BYTES:
        return summary

    compact = json.loads(json.dumps(summary, ensure_ascii=False))
    compacted_field_count = 0
    rows = compact.get("integrated_observations", [])
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            signals = _unique_nonempty_strings(row.get("observed_signals", []))
            limited_signals = [
                _truncate_utf8_text(value, max_bytes=72)
                for value in signals[:2]
            ]
            compacted_field_count += max(0, len(signals) - len(limited_signals))
            row["observed_signals"] = limited_signals
            for field in ("spatial_pattern", "temporal_evolution"):
                original = str(row.get(field, ""))
                shortened = _truncate_utf8_text(original, max_bytes=112)
                if shortened != original:
                    compacted_field_count += 1
                row[field] = shortened
            limitations = _unique_nonempty_strings(row.get("limitations", []))
            limited_limitations = [
                _truncate_utf8_text(value, max_bytes=72)
                for value in limitations[:1]
            ]
            compacted_field_count += max(0, len(limitations) - len(limited_limitations))
            row["limitations"] = limited_limitations
            direct_signal = row.get("direct_precipitation_signal")
            if isinstance(direct_signal, dict):
                original = str(direct_signal.get("rationale", ""))
                shortened = _truncate_utf8_text(original, max_bytes=96)
                if shortened != original:
                    compacted_field_count += 1
                direct_signal["rationale"] = shortened

    if isinstance(rows, list):
        compact["direct_precipitation_audit"] = _direct_precipitation_audit_from_rows(
            [row for row in rows if isinstance(row, dict)]
        )

    for field, max_items, max_bytes in (
        ("cross_product_relationships", 1, 112),
        ("uncertainty", 1, 96),
    ):
        original_values = _unique_nonempty_strings(compact.get(field, []))
        compact_values = [
            _truncate_utf8_text(value, max_bytes=max_bytes)
            for value in original_values[:max_items]
        ]
        compacted_field_count += max(0, len(original_values) - len(compact_values))
        compact[field] = compact_values

    if compacted_field_count:
        compact["omitted_or_unresolved_signals"] = [
            "그룹 요약 용량 제한으로 반복 또는 장문 설명 일부를 축약했습니다."
        ]
    return compact

# Original func/step04/pass1_engine.py:7883
def build_deterministic_group_summary(
    *,
    package_id: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Build a schema-complete summary when one validated batch already covers the group.

    With the normal eight-batch plan, each tier/group has exactly one validated
    multimodal observation containing every source asset in that group. Asking a
    second model call to reproduce the same deterministic identifiers and rows is
    both redundant and fragile. This builder preserves the validated observation
    content and only performs lossless structural projection.
    """

    integrated_observations: list[dict[str, Any]] = []
    for row in context.get("source_rows", []):
        if not isinstance(row, dict):
            continue
        integrated_observations.append(
            {
                "source_batch_id": str(row.get("source_batch_id", "")),
                "asset_id": str(row.get("asset_id", "")),
                "product_id": str(row.get("product_id", "")),
                "forecast_hours": [int(value) for value in row.get("forecast_hours", [])],
                "observed_signals": [str(value) for value in row.get("observed_signals", [])],
                "spatial_pattern": str(row.get("spatial_pattern", "")),
                "temporal_evolution": str(row.get("temporal_evolution", "")),
                "confidence": str(row.get("confidence", "medium")),
                "limitations": [str(value) for value in row.get("limitations", [])],
                "direct_precipitation_signal": _normalize_direct_precipitation_signal(
                    row.get("direct_precipitation_signal")
                ),
            }
        )

    cross_product_relationships = _unique_nonempty_strings(
        value
        for observation in context.get("source_observations", [])
        if isinstance(observation, dict)
        for value in observation.get("cross_product_synthesis", [])
    )
    uncertainty = _unique_nonempty_strings(
        value
        for observation in context.get("source_observations", [])
        if isinstance(observation, dict)
        for value in observation.get("batch_uncertainties", [])
    )

    summary = {
        "schema_version": GROUP_SUMMARY_SCHEMA_VERSION,
        "package_id": package_id,
        "summary_id": context["summary_id"],
        "analysis_tier": context["analysis_tier"],
        "group_id": context["group_id"],
        "source_batch_ids": list(context["source_batch_ids"]),
        "source_asset_ids": list(context["source_asset_ids"]),
        "source_product_ids": list(context["source_product_ids"]),
        "forecast_hours": list(context["forecast_hours"]),
        "valid_time_range": dict(context["valid_time_range"]),
        "direct_precipitation_audit": _direct_precipitation_audit_from_rows(integrated_observations),
        "integrated_observations": integrated_observations,
        "cross_product_relationships": cross_product_relationships,
        "uncertainty": uncertainty,
        "omitted_or_unresolved_signals": [],
    }
    return _compact_group_summary_narratives(summary)

# Original func/step04/pass1_engine.py:8095
def build_group_summary_messages(
    *,
    prompt_template: str,
    package_id: str,
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    group_input = {
        "schema_version": GROUP_SUMMARY_SCHEMA_VERSION,
        "package_id": package_id,
        "summary_id": context["summary_id"],
        "analysis_tier": context["analysis_tier"],
        "group_id": context["group_id"],
        "required_source_batch_ids": context["source_batch_ids"],
        "required_source_asset_ids": context["source_asset_ids"],
        "required_source_product_ids": context["source_product_ids"],
        "required_forecast_hours": context["forecast_hours"],
        "required_valid_time_range": context["valid_time_range"],
        "source_observations": context["source_observations"],
    }
    return [
        {
            "role": "system",
            "content": (
                "엄격한 그룹 요약 JSON 객체 하나만 반환하고 후보 판단은 생성하지 마십시오. "
                "JSON 키와 식별자는 유지하고 사람이 읽는 설명 문장은 모두 한국어로 작성하십시오."
            ),
        },
        {
            "role": "user",
            "content": prompt_template.strip() + "\n\n## group observation input\n" + json.dumps(group_input, ensure_ascii=False),
        },
    ]

# Original func/step04/pass1_engine.py:8129
def build_group_summary_compaction_messages(
    *,
    summary: dict[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    compact_request = {
        "task": "필수 coverage를 제거하지 말고 이 그룹 요약을 byte 상한 아래로 압축하십시오.",
        "hard_max_utf8_bytes": GROUP_SUMMARY_MAX_BYTES,
        "target_utf8_bytes": GROUP_SUMMARY_TARGET_BYTES,
        "required_contract": {
            "summary_id": context["summary_id"],
            "analysis_tier": context["analysis_tier"],
            "group_id": context["group_id"],
            "source_batch_ids": context["source_batch_ids"],
            "source_asset_ids": context["source_asset_ids"],
            "source_product_ids": context["source_product_ids"],
            "forecast_hours": context["forecast_hours"],
            "valid_time_range": context["valid_time_range"],
        },
        "rules": [
            "Keep every source asset exactly once in integrated_observations.",
            "Keep product, forecast hour, spatial signal, temporal signal, and uncertainty.",
            "Return JSON only. Do not truncate strings or omit required fields.",
            "Keep all human-readable narrative strings in Korean.",
        ],
        "previous_summary": summary,
    }
    return [
        {
            "role": "system",
            "content": "엄격한 JSON 그룹 요약을 압축합니다. 설명 문장은 한국어로 유지하고 JSON만 반환하십시오.",
        },
        {"role": "user", "content": json.dumps(compact_request, ensure_ascii=False)},
    ]

# Original func/step04/pass1_engine.py:8303
def _minimal_asset_index(attachment_manifest: dict[str, Any]) -> dict[str, Any]:
    fields = ("asset_id", "product_id", "analysis_tier", "group_id", "forecast_hours", "valid_times_utc")
    return {
        "asset_count": int(attachment_manifest.get("attachment_count", 0)),
        "assets": [{field: item.get(field) for field in fields} for item in attachment_manifest.get("attachments", [])],
    }

# Original func/step04/pass1_engine.py:8684
def build_candidate_discovery_schema(final_schema: dict[str, Any]) -> dict[str, Any]:
    """Derive the candidate-only contract from the canonical final Pass1 schema."""
    required = [
        "package_id",
        "analysis_coverage",
        "run_summary",
        "candidates",
        "candidate_evidence",
        "global_uncertainties",
        "precursor_candidate_assessment",
        "pass2_required",
    ]
    optional = ["precursor_opportunities"]
    properties = final_schema.get("properties", {})
    defs = final_schema.get("$defs", {})
    missing_properties = [key for key in required if key not in properties]
    required_defs = ("bbox", "timeWindow", "candidate", "candidateEvidence", "precursorOpportunity")
    missing_defs = [key for key in required_defs if key not in defs]
    if missing_properties or missing_defs:
        raise ValueError(
            "final Pass1 schema does not support split candidate discovery: "
            f"missing_properties={missing_properties} missing_defs={missing_defs}"
        )
    needed_defs = {key: defs[key] for key in required_defs}
    return {
        "$schema": final_schema.get("$schema", "https://json-schema.org/draft/2020-12/schema"),
        "title": "AI Pass 1 Candidate Discovery Response",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": {
            key: final_schema["properties"][key]
            for key in [*required, *optional]
            if key in final_schema.get("properties", {})
        },
        "$defs": needed_defs,
    }

# Original func/step04/pass1_engine.py:8723
def build_candidate_discovery_provider_schema(
    schema: dict[str, Any],
    *,
    attachment_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Constrain model-selected discovery references to the active manifest.

    Canonical schemas use local ``$defs`` while the OpenAI transport schema is
    fully inlined. Both forms must receive the same runtime enums.
    """
    constrained = copy.deepcopy(schema)
    attachment_by_id = _attachment_by_id(attachment_manifest)
    asset_ids = list(attachment_by_id)
    forecast_hours = sorted(
        {
            int(hour)
            for attachment in attachment_by_id.values()
            for hour in attachment.get("forecast_hours", [])
        }
    )

    defs = constrained.get("$defs")
    if isinstance(defs, dict) and isinstance(defs.get("candidate"), dict):
        candidate_properties = defs["candidate"]["properties"]
        evidence_properties = defs["candidateEvidence"]["properties"]
    else:
        root_properties = constrained.get("properties", {})
        candidate_properties = (
            root_properties.get("candidates", {}).get("items", {}).get("properties", {})
        )
        evidence_properties = (
            root_properties.get("candidate_evidence", {}).get("items", {}).get("properties", {})
        )
    related_asset_items = (
        candidate_properties.get("related_overview_asset_ids", {}).get("items")
    )
    if not isinstance(related_asset_items, dict):
        raise ValueError("candidate discovery schema is missing related_overview_asset_ids.items")
    related_asset_items["enum"] = asset_ids

    evidence_item_properties = (
        evidence_properties.get("evidence_items", {}).get("items", {}).get("properties", {})
    )
    asset_id_schema = evidence_item_properties.get("asset_id")
    if not isinstance(asset_id_schema, dict):
        raise ValueError("candidate discovery schema is missing evidence asset_id")
    asset_id_schema["enum"] = asset_ids
    forecast_hour_items = (
        evidence_item_properties.get("forecast_hours", {}).get("items")
    )
    if forecast_hours and isinstance(forecast_hour_items, dict):
        forecast_hour_items["enum"] = forecast_hours

    assessment_properties = constrained.get("properties", {}).get(
        "precursor_candidate_assessment", {}
    ).get("properties", {})
    source_asset_items = assessment_properties.get("source_asset_ids", {}).get("items")
    if isinstance(source_asset_items, dict):
        source_asset_items["enum"] = asset_ids
    assessment_hour_items = assessment_properties.get("forecast_hours", {}).get("items")
    if forecast_hours and isinstance(assessment_hour_items, dict):
        assessment_hour_items["enum"] = forecast_hours
    opportunity_schema = constrained.get("properties", {}).get("precursor_opportunities", {})
    if isinstance(opportunity_schema, dict):
        opportunity_items = opportunity_schema.get("items", {})
        if isinstance(opportunity_items, dict) and "$ref" in opportunity_items:
            opportunity_items = (constrained.get("$defs", {}) or {}).get("precursorOpportunity", {})
        opportunity_properties = opportunity_items.get("properties", {}) if isinstance(opportunity_items, dict) else {}
        support_items = opportunity_properties.get("support_asset_ids", {}).get("items")
        if isinstance(support_items, dict):
            support_items["enum"] = asset_ids
        representative_items = opportunity_properties.get("representative_forecast_hours", {}).get("items")
        if forecast_hours and isinstance(representative_items, dict):
            representative_items["enum"] = forecast_hours
    return constrained

# Original func/step04/pass1_engine.py:8799
def build_candidate_plan_schema(final_schema: dict[str, Any]) -> dict[str, Any]:
    """Derive the per-candidate plan contract from the canonical final schema."""
    defs = final_schema.get("$defs", {})
    needed_names = (
        "bbox",
        "crossSectionLine",
        "referenceBandAxis",
        "analysisDataRequest",
        "analysisHypothesis",
        "analysisTargetArea",
        "analysisPhase",
        "variableCheck",
        "candidateAnalysisPlan",
    )
    missing_defs = [key for key in needed_names if key not in defs]
    if missing_defs:
        raise ValueError(
            "final Pass1 schema does not support per-candidate planning: "
            f"missing_defs={missing_defs}. Re-run Step04 with the analysis-plan schema."
        )
    return {
        "$schema": final_schema.get("$schema", "https://json-schema.org/draft/2020-12/schema"),
        "title": "AI Pass 1 Per-Candidate Analysis Plan Response",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "package_id",
            "candidate_id",
            "candidate_analysis_plan",
            "analysis_data_requests",
        ],
        "properties": {
            "package_id": {"type": "string", "minLength": 1},
            "candidate_id": {"type": "string", "pattern": "^CAND_[0-9]{3}$"},
            "candidate_analysis_plan": {"$ref": "#/$defs/candidateAnalysisPlan"},
            "analysis_data_requests": {
                "type": "array",
                "items": {"$ref": "#/$defs/analysisDataRequest"},
                "minItems": 1,
            },
        },
        "$defs": {key: defs[key] for key in needed_names},
    }

# Original func/step04/pass1_engine.py:9966
def _assert_no_private_paths(value: Any, *, label: str) -> None:
    serialized = json.dumps(value, ensure_ascii=False)
    if "source_panels" in serialized or "absolute_path" in serialized:
        raise ValueError(f"{label} must not contain source panel paths")
    if re.search(r"(?:[A-Za-z]:[\\/]|file://|\\\\\\\\)", serialized, flags=re.IGNORECASE):
        raise ValueError(f"{label} must not contain local absolute paths")

# Original func/step04/pass1_engine.py:10135
def build_candidate_discovery_messages(
    *,
    prompt_template: str,
    package_id: str,
    group_summaries: list[dict[str, Any]],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    overview_data: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    discovery_group_summaries = _candidate_discovery_group_summary_projection(group_summaries)
    discovery_input = {
        "package_id": package_id,
        "split_synthesis_contract_version": SPLIT_SYNTHESIS_CONTRACT_VERSION,
        "candidate_discovery_meteorological_qc_version": CANDIDATE_DISCOVERY_METEOROLOGICAL_QC_VERSION,
        "candidate_discovery_input_projection_version": CANDIDATE_DISCOVERY_INPUT_PROJECTION_VERSION,
        "group_summaries": discovery_group_summaries,
        "asset_index": _candidate_discovery_asset_index(attachment_manifest),
        "supported_spatial_products": _minimal_support_catalog(support_catalog).get(
            "spatial_field_products", []
        ),
        "precipitation_peak_guidance": build_precipitation_peak_guidance(
            overview_data, minimum_regional_max_mm=_peak_anchor_minimum_mm(support_catalog)
        ),
        "system_direct_precipitation_coverage_guidance": build_system_direct_precipitation_coverage_guidance(
            overview_data, support_catalog=support_catalog
        ),
        "candidate_operational_scope": _candidate_discovery_operational_scope(overview_data),
        "precursor_candidate_contract": {
            "scan_required": True,
            "minimum_output_when_qualified": 1,
            "output_when_no_qualified_signal": 0,
            "independence_rule": (
                "모델 강수 예측 또는 mixed 후보와 같은 시간·영역·메커니즘이면 별도 precursor 후보로 중복 생성하지 않습니다."
            ),
            "qualification_rules": [
                "모델 강수 예측 상태가 weak, absent 또는 uncertain입니다.",
                "서로 다른 선행환경 범주가 둘 이상이며 수분 범주 외에 역학적 강제력 범주가 하나 이상 포함됩니다.",
                "선행환경 신호가 같은 시간과 후보 핵심영역에서 중첩됩니다.",
                "기존 direct_precipitation 또는 mixed 후보로 설명되지 않는 독립적인 감시 필요가 있습니다.",
            ],
        },
        "response_compactness": {
            "rules": [
                "후보 수나 근거 범위를 줄이지 말고 후보별 중복 설명만 제거합니다.",
                "supporting_reasons, opposing_reasons, uncertainties는 각각 최대 두 개의 짧은 문장으로 작성합니다.",
                "diagnostic_plan은 후보 검증에 필요한 서로 다른 diagnostic_id만 최대 네 개 제시합니다.",
                "candidate_evidence는 후보별 핵심 asset만 인용하고 asset마다 reason 한 문장만 작성합니다.",
                "같은 기상학적 설명을 후보, evidence, uncertainty 필드에 반복하지 않습니다.",
                "각 문자열은 간결한 한국어 한두 문장으로 끝내고 Markdown이나 해설을 추가하지 않습니다.",
                "group_summaries.direct_precipitation_audit.status=present는 원본 영상의 직접강수 관찰 보존용입니다. 운영권역 밖 source corridor 신호를 그 이유만으로 Candidate로 만들지 않습니다.",
                "direct_precipitation/mixed 후보의 candidate_area는 candidate_operational_scope.eligible_forecast_regions 중 하나 이상과 실제로 겹쳐야 하며, 맞추기 위해 bbox를 임의로 확대하지 않습니다.",
                "system_direct_precipitation_coverage_guidance의 candidate reference threshold 이상 정량 신호는 SYSTEM 독립 감사 대상이므로 후보 coverage에서 누락하지 않습니다.",
                "1.0 mm 이상이지만 candidate reference threshold 미만인 SYSTEM 미커버 신호는 기록 대상이며 그 값만으로 후보 생성을 강제하지 않습니다.",
                "precipitation_peak_guidance의 정량 peak는 참고 anchor로 사용하되 최종 peak·merge/split은 AI가 판단하고, overview 그룹 시작시각을 자동 peak로 사용하지 않습니다.",
                "유효하고 독립적인 선행환경 신호가 있으면 precursor_environment 후보를 최소 1개 유지하고, 없으면 생성하지 않습니다.",
            ]
        },
    }
    _assert_no_private_paths(discovery_input, label="candidate discovery input")
    return [
        {
            "role": "system",
            "content": (
                "엄격한 후보 탐색 JSON 객체 하나만 반환하십시오. "
                "분석계획이나 자료요청은 생성하지 마십시오. "
                "최상위에는 candidates, candidate_evidence, global_uncertainties, "
                "precursor_candidate_assessment 네 필드를 반드시 포함하고, "
                "단일 후보도 candidates 배열 안에 넣으십시오. "
                "JSON 키·식별자·enum 값은 계약대로 유지하고, 후보명·가설·근거·목적·불확실성 등 "
                "사람이 읽는 모든 설명 문자열은 한국어로 작성하십시오."
            ),
        },
        {
            "role": "user",
            "content": prompt_template.strip()
            + "\n\n## candidate discovery input\n"
            + json.dumps(discovery_input, ensure_ascii=False),
        },
    ], discovery_input

# Original func/step04/pass1_engine.py:10216
def build_candidate_discovery_length_retry_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Retry candidate discovery once with stricter prose compactness.

    This recovery is intentionally narrow: it reuses the original input and
    response schema, does not reduce candidate/evidence coverage, and is only
    used after the provider reports ``incomplete_reason=max_output_tokens``.
    """
    correction = {
        "task": (
            "직전 candidate discovery 응답이 출력 길이 제한으로 중단되었습니다. "
            "동일한 입력과 동일한 판단 기준으로 처음부터 완전한 JSON 객체 하나를 다시 생성하십시오."
        ),
        "rules": [
            "후보 수, 직접강수 coverage, 정량 peak coverage, precursor 감시 필요성을 줄여 길이를 맞추지 마십시오.",
            "같은 기상학적 설명을 candidates, candidate_evidence, global_uncertainties 사이에 반복하지 마십시오.",
            "supporting_reasons, opposing_reasons, uncertainties는 각 항목을 짧은 한 문장으로 작성하고 꼭 필요한 항목만 유지하십시오.",
            "candidate_evidence.reason과 precursor_candidate_assessment의 설명도 중복 없이 한 문장으로 작성하십시오.",
            "JSON 키·식별자·enum 값과 스키마 계약은 그대로 유지하십시오.",
            "Markdown, 서론, 결론, 부연설명 없이 JSON 객체만 반환하고 마지막 } 직후 종료하십시오.",
        ],
    }
    return [
        *messages,
        {"role": "user", "content": json.dumps(correction, ensure_ascii=False)},
    ]

# Original func/step04/pass1_engine.py:10245
def add_prompt_only_schema_constraint(
    messages: list[dict[str, Any]],
    response_schema: dict[str, Any],
) -> list[dict[str, Any]]:
    """Give prompt-only fallback calls the same explicit stage contract."""
    return [
        *messages,
        {
            "role": "user",
            "content": (
                "JSON만 반환하십시오. 응답은 단계 스키마를 정확히 만족해야 합니다. "
                "legacy 별칭이나 스키마 밖의 필드를 사용하지 마십시오. "
                "사람이 읽는 모든 설명 문자열은 한국어로 작성하십시오.\n\n"
                "## response schema\n"
                + json.dumps(response_schema, ensure_ascii=False)
            ),
        },
    ]

# Original func/step04/pass1_engine.py:10282
def _related_asset_index(
    candidate: dict[str, Any],
    attachment_manifest: dict[str, Any],
) -> dict[str, Any]:
    related = {str(value) for value in candidate.get("related_overview_asset_ids", [])}
    minimal = _minimal_asset_index(attachment_manifest)
    assets = [item for item in minimal["assets"] if str(item.get("asset_id")) in related]
    return {"asset_count": len(assets), "assets": assets}

# Original func/step04/pass1_engine.py:11149
def _catalog_bbox(value: Any) -> dict[str, float] | None:
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

# Original func/step04/pass1_engine.py:11168
def _available_context_domain(
    support_catalog: dict[str, Any],
    feature_catalog: dict[str, Any] | None = None,
) -> dict[str, float]:
    bboxes = [
        bbox
        for item in support_catalog.get("spatial_field_products", [])
        if isinstance(item, dict)
        for bbox in [_catalog_bbox(item.get("default_render_domain_bbox"))]
        if bbox is not None
    ]
    if feature_catalog is not None:
        for field in ("map_domain_bbox", "spatial_grid_bbox"):
            bbox = _catalog_bbox(feature_catalog.get(field))
            if bbox is not None:
                bboxes.append(bbox)
    if not bboxes:
        return {"west": -180.0, "east": 180.0, "south": -90.0, "north": 90.0}
    return {
        "west": min(item["west"] for item in bboxes),
        "east": max(item["east"] for item in bboxes),
        "south": min(item["south"] for item in bboxes),
        "north": max(item["north"] for item in bboxes),
    }

# Original func/step04/pass1_engine.py:11194
def build_candidate_plan_messages(
    *,
    prompt_template: str,
    package_id: str,
    candidate: dict[str, Any],
    candidate_evidence: dict[str, Any],
    group_summaries: list[dict[str, Any]],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    feature_catalog: dict[str, Any],
    operational_region_catalog: dict[str, Any],
    related_precursor_opportunities: list[dict[str, Any]] | None = None,
    compact_transport: bool = False,
    plan_chunk_index: int = 1,
    continuation_context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidate_id = str(candidate.get("candidate_id"))
    candidate_operational_region_overlap = _candidate_operational_region_overlap(
        candidate=candidate,
        operational_region_catalog=operational_region_catalog,
    )
    candidate_available_verification_modes = list(
        candidate_operational_region_overlap["allowed_verification_modes"]
    )
    if compact_transport:
        phase_hours = _compact_candidate_phase_hours(candidate, attachment_manifest)
        compact_input = {
            "package_id": package_id,
            "candidate_id": candidate_id,
            "immutable_candidate": candidate,
            "candidate_evidence": candidate_evidence,
            "related_precursor_opportunities": related_precursor_opportunities or [],
            "related_group_summaries": _related_group_summaries(candidate, group_summaries),
            "related_asset_index": _related_asset_index(candidate, attachment_manifest),
            "available_verification_signals": list(VERIFICATION_SIGNAL_COMPONENTS),
            "available_verification_modes": candidate_available_verification_modes,
            "candidate_operational_region_overlap": candidate_operational_region_overlap,
            "verification_mode_guidance": {
                "spatial_distribution": "한 기상요소의 한 시각 또는 선택 시각 공간분포·위치. 시간에 따른 이동은 spatial_evolution으로 분리",
                "regional_intensity": "한 기상요소의 후보지역 강도와 최대위치 정량 비교. 시간에 따른 중심 이동은 다루지 않음",
                "temporal_change": "한 기상요소의 후보지역 평균 강화·약화·최대시각. 전체 정량 시계열과 AI 선택 대표 공간장을 함께 제공하되 공간 중심 이동은 다루지 않음",
                "spatial_evolution": "한 기상요소의 여러 시각 공간구조·중심 이동·분리·재조직 변화",
                "multivariable_alignment": "강수·수분·하층수렴·중층상승 중 둘 이상 공간 중첩",
                "vertical_structure": "수분·중층상승·바람구조의 지정 단면 연직구조",
                "vertical_time_evolution": "후보영역 평균 연직구조의 시간변화",
                "precipitation_amount": "선택 시각의 6시간 강수량과 후보지역 최대위치. tp_accum_mm는 초기시각부터 해당 예측시각까지의 누적강수량이며, 6시간 누적 강수량이라는 표현은 사용하지 않음. 시간에 따른 중심 이동은 spatial_evolution으로 분리",
            },
            "available_context_domain": _available_context_domain(
                support_catalog, feature_catalog
            ),
            "operational_region_index": _minimal_operational_region_index(
                operational_region_catalog
            ),
            "feature_index": _minimal_feature_index(feature_catalog),
            "coverage_roles": list(COMPACT_COVERAGE_ROLES),
            "available_analysis_phase_types": list(phase_hours),
            "available_forecast_hours": _available_candidate_forecast_hours(
                candidate, attachment_manifest
            ),
            "candidate_peak_forecast_hours": [
                int(value) for value in candidate.get("peak_forecast_hours", [])
            ],
            "candidate_anchor_interpretation": (
                "precursor_overlap"
                if str(candidate.get("detection_basis")) == "precursor_environment"
                else "precipitation_peak"
            ),
            "phase_type_constraint": (
                "Use only listed phase types and explicit available_forecast_hours. "
                "Non-context phases must form chronological, non-overlapping, consecutive-cadence "
                "blocks. Map one contiguous candidate peak group to one peak phase and merge "
                "adjacent phases of the same type."
            ),
            "plan_chunk_index": int(plan_chunk_index),
        }
        # Keep the original model input and checkpoint semantics unchanged for
        # already feasible multi-hour candidates.
        temporal_contract = _candidate_temporal_feasibility_contract(
            candidate, attachment_manifest, support_catalog=support_catalog
        )
        if temporal_contract["added_hours"]:
            compact_input["time_feasibility_contract"] = temporal_contract
        compact_input["verification_mode_guidance"] = {
            key: value
            for key, value in compact_input["verification_mode_guidance"].items()
            if key in candidate_available_verification_modes
        }
        if continuation_context is not None:
            compact_input["continuation_context"] = continuation_context
        _assert_no_private_paths(compact_input, label=f"compact candidate plan input {candidate_id}")
        return [
            {
                "role": "system",
                "content": (
                    "AIFS 후보별 기상학적 검증계획 JSON만 반환하십시오. "
                    "AI의 역할은 언제, 어디에서, 어떤 기상요소를, 왜 추가 확인할지 결정하는 것입니다. "
                    "후보 candidate_area는 변경하지 말고, 필요한 경우 context_domain bbox를 직접 지정하십시오. "
                    "각 analysis_phase에는 실제 forecast_hours를 지정하고 verification_requests에는 "
                    "signal_components, verification_mode, domain_scope, spatial_relation_scope, phase_keys, representative_spatial_hours와 확인 이유를 작성하십시오. "
                    "diagnostic_id, variable_role, check_scope, required_signal_components, analysis_methods, "
                    "product_id, request_id, output_spec은 Python이 결정하므로 출력하지 마십시오. "
                    "단, atomic_claims[].quantitative_scope의 feature_id와 region_ids는 입력의 feature_index와 operational_region_index에서 "
                    "실제 식별자를 선택해 작성하며 새 식별자를 만들지 마십시오. "
                    "candidate_operational_region_overlap.regional_verification_available=false이면 available_verification_modes에 없는 "
                    "regional_intensity, temporal_change, precipitation_amount를 사용하거나 candidate_area를 운영권역 쪽으로 확대하지 마십시오. "
                    "precursor_environment 후보의 핵심 시각은 선행환경 중첩 대표시각으로 해석하십시오. "
                    "모든 후보의 순수 모델 강수 예측 존재 여부는 후보 계획 수용 후 SYSTEM이 별도 필수 Check로 추가하므로, "
                    "존재 여부만을 위한 verification_request를 중복 생성하지 마십시오. 모델 강수 예측 추가 검증은 이동, 연속성, "
                    "조직화, 강도 변화 등 존재 여부와 다른 특성만 다루십시오. 설명 문자열은 모두 간결한 한국어로 작성하십시오."
                ),
            },
            {
                "role": "user",
                "content": prompt_template.strip()
                + "\n\n## compact candidate planning input\n"
                + json.dumps(compact_input, ensure_ascii=False),
            },
        ], compact_input
    plan_input = {
        "package_id": package_id,
        "candidate_id": candidate_id,
        "immutable_candidate": candidate,
        "candidate_evidence": candidate_evidence,
        "related_precursor_opportunities": related_precursor_opportunities or [],
        "related_group_summaries": _related_group_summaries(candidate, group_summaries),
        "related_asset_index": _related_asset_index(candidate, attachment_manifest),
        "supported_analysis_data_requests": _minimal_support_catalog(support_catalog),
        "feature_index": _minimal_feature_index(feature_catalog),
        "operational_region_index": _minimal_operational_region_index(
            operational_region_catalog
        ),
        "candidate_operational_region_overlap": candidate_operational_region_overlap,
        "available_verification_modes": candidate_available_verification_modes,
    }
    _assert_no_private_paths(plan_input, label=f"candidate plan input {candidate_id}")
    return [
        {
            "role": "system",
            "content": (
                "후보별 엄격한 분석계획 JSON 객체 하나만 반환하십시오. "
                "최상위에는 candidate_analysis_plan과 analysis_data_requests만 포함하십시오. "
                "중첩된 hypothesis나 plan 객체 하나를 최상위 응답으로 반환하지 마십시오. "
                "모든 자료요청에는 현행 필드인 data_type, diagnostic_id, product_id, feature_ids, "
                "forecast_hours, requested_domain, cross_section_line, check_key, target_key, phase_keys, "
                "analysis_methods, purpose, priority만 사용하십시오. "
                "request_id, request_type, feature_id, derived_outputs 같은 legacy 필드는 사용하지 마십시오. "
                "explicit_bbox는 필드명이 아니라 resolution_method의 enum 값입니다. "
                "priority에는 required 또는 supplementary만 사용하십시오. "
                "immutable candidate 필드는 변경하지 마십시오. "
                "공간 중첩·공간 구조 변화 검증에 사용할 지도 범위가 AIFS 원본 0.25도 격자 중심 좌표에서 어느 한 축에 격자가 2개 미만이라면, 후보 candidate_area는 고정하고 주변 격자를 포함하는 context_domain을 설정해 해당 verification_request의 domain_scope=context로 지정하십시오. 강수 존재 확인용 작은 핵심영역은 유지하십시오. "
                "JSON 키·식별자·enum 값은 계약대로 유지하고, 분석목적·가설·근거·질문·요청 목적 등 "
                "사람이 읽는 모든 설명 문자열은 한국어로 작성하십시오."
            ),
        },
        {
            "role": "user",
            "content": prompt_template.strip()
            + "\n\n## per-candidate planning input\n"
            + json.dumps(plan_input, ensure_ascii=False),
        },
    ], plan_input

# Original func/step04/pass1_engine.py:13458
def _compact_plan_context(
    chunks: list[dict[str, Any]],
    *,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    phases: list[str] = []
    phase_types: list[str] = []
    existing_phases: list[dict[str, Any]] = []
    checks: list[str] = []
    combinations: list[dict[str, str]] = []
    coverage: dict[str, str] = {}
    context_domain: dict[str, float] | None = None
    context_domain_reason: str | None = None
    phase_aliases = _compact_phase_key_aliases(chunks)
    for chunk in chunks:
        phase_rows = _compact_plan_rows(chunk, "analysis_phases")
        check_rows = _compact_plan_rows(chunk, "diagnostic_checks")
        phases.extend(str(item["phase_key"]) for item in phase_rows)
        phase_types.extend(str(item.get("phase_type")) for item in phase_rows)
        existing_phases.extend(
            {
                "phase_key": str(item["phase_key"]),
                "phase_type": str(item.get("phase_type")),
                "forecast_hours": list(_compact_phase_hours(item)),
            }
            for item in phase_rows
        )
        checks.extend(str(item["check_key"]) for item in check_rows)
        for check in check_rows:
            for phase_key in check.get("phase_keys", []):
                combinations.append(
                    {
                        "signal_components": list(check.get("required_signal_components", [])),
                        "verification_mode": _verification_mode_from_diagnostic_check(check),
                        "domain_scope": str(check.get("domain_scope", "candidate")),
                        "phase_key": phase_aliases.get(str(phase_key), str(phase_key)),
                    }
                )
        raw_context = _normalized_bbox_for_validation(chunk.get("context_domain"))
        if raw_context is not None and context_domain is None:
            context_domain = raw_context
            reason = chunk.get("context_domain_reason")
            context_domain_reason = str(reason).strip() if reason is not None else None
        coverage = evaluate_compact_candidate_plan_coverage(chunks, candidate=candidate)
    remaining_roles = sorted(
        role for role, status in coverage.items() if status == "missing_but_relevant"
    )
    role_guidance = {
        "temporal_evolution": (
            "둘 이상의 예측시각을 실제로 비교하는 새 verification_request를 만드십시오. "
            "강도·평균값 변화는 verification_mode=temporal_change, 공간 중심의 이동·분리·재조직은 "
            "verification_mode=spatial_evolution, 연직 시간변화는 vertical_time_evolution을 사용하십시오. "
            "spatial_evolution과 temporal_evolution이 함께 남아 있으면 둘 이상의 실제 시각을 사용하는 "
            "단일 spatial_evolution 요청으로 두 역할을 함께 충족할 수 있습니다. "
            "단일시각 요청에 coverage_roles 태그만 추가해서는 충족되지 않습니다."
        ),
        "spatial_evolution": (
            "둘 이상의 예측시각 공간장을 비교하는 verification_mode=spatial_evolution 요청을 추가하십시오. "
            "이동·확대·축소·분리·재조직처럼 방향성이 있는 검증 명제를 작성하십시오. "
            "이 요청은 둘 이상의 실제 예측시각을 사용하면 temporal_evolution 역할도 함께 충족합니다."
        ),
        "moisture_supply": "signal_components에 moisture_supply를 포함한 실제 수분공급 검증 요청을 추가하십시오.",
        "low_level_forcing": "signal_components에 low_level_forcing을 포함한 실제 하층수렴 검증 요청을 추가하십시오.",
        "mid_level_forcing": "signal_components에 mid_upper_forcing을 포함한 실제 중층상승 검증 요청을 추가하십시오.",
        "supporting_evidence": "evidence_role=supporting인 실제 지지 검증 요청을 추가하십시오.",
        "opposing_evidence": "evidence_role=opposing인 실제 제한·반대 검증 요청을 추가하십시오.",
    }
    return {
        "existing_phase_keys": phases,
        "existing_phase_types": list(dict.fromkeys(phase_types)),
        "existing_phases": existing_phases,
        "existing_check_keys": checks,
        "existing_verification_domain_phase_combinations": combinations,
        "context_domain": context_domain,
        "context_domain_reason": context_domain_reason,
        "coverage_status": coverage,
        "remaining_roles": remaining_roles,
        "remaining_role_guidance": {
            role: role_guidance.get(
                role,
                "해당 역할을 실제로 검증하는 새로운 verification_request를 추가하십시오."
            )
            for role in remaining_roles
        },
        "instruction": (
            "후속 chunk에서는 remaining_roles를 최우선으로 채우십시오. "
            "coverage_roles 문자열만 추가하지 말고 remaining_role_guidance에 맞는 실제 verification 구조를 만드십시오. "
            "existing_phases의 forecast_hours를 사용해 기존 phase를 재사용할 수 있습니다. "
            "남은 필수 역할이 하나 이상이면 최소 하나의 remaining_role을 실제로 감소시키는 verification_request를 추가해야 하며, "
            "이미 충족된 역할의 요청만 추가하거나 빈 continuation을 반환해서는 안 됩니다."
        ),
    }

# Original func/step04/pass1_engine.py:17992
def build_hierarchical_synthesis_messages(
    *,
    prompt_template: str,
    package_id: str,
    group_summaries: list[dict[str, Any]],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    feature_catalog: dict[str, Any],
    operational_region_catalog: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    synthesis_input = {
        "package_id": package_id,
        "synthesis_contract_version": GROUP_SUMMARY_CONTRACT_VERSION,
        "group_summaries": group_summaries,
        "asset_index": _minimal_asset_index(attachment_manifest),
        "supported_analysis_data_requests": _minimal_support_catalog(support_catalog),
        "feature_index": _minimal_feature_index(feature_catalog),
        "operational_region_index": _minimal_operational_region_index(operational_region_catalog),
    }
    serialized = json.dumps(synthesis_input, ensure_ascii=False)
    if "source_panels" in serialized:
        raise ValueError("hierarchical synthesis input must not contain source_panels")
    if re.search(r"(?:[A-Za-z]:[\\/]|file://|\\\\\\\\)", serialized, flags=re.IGNORECASE):
        raise ValueError("hierarchical synthesis input must not contain local absolute paths")
    return [
        {
            "role": "system",
            "content": (
                "검증된 그룹 요약에서 엄격한 최종 Pass1 후보 JSON 객체 하나만 반환하십시오. "
                "JSON 키·식별자·enum 값은 유지하고 사람이 읽는 설명 문장은 모두 한국어로 작성하십시오."
            ),
        },
        {
            "role": "user",
            "content": prompt_template.strip() + "\n\n## hierarchical synthesis input\n" + serialized,
        },
    ], synthesis_input

# Original func/step04/pass1_engine.py:18464
def _reduced_attachment_manifest(attachment_manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "package_id": attachment_manifest.get("package_id"),
        "attachment_count": attachment_manifest.get("attachment_count"),
        "attachments": [
            {
                "asset_id": item.get("asset_id"),
                "product_id": item.get("product_id"),
                "analysis_tier": item.get("analysis_tier"),
                "group_id": item.get("group_id"),
                "forecast_hours": item.get("forecast_hours"),
                "valid_times_utc": item.get("valid_times_utc"),
                "render_domain_id": item.get("render_domain_id"),
                "render_domain_bbox": item.get("render_domain_bbox"),
                "source_panels": item.get("source_panels"),
            }
            for item in attachment_manifest.get("attachments", [])
        ],
    }

# Original func/step04/pass1_engine.py:18485
def build_synthesis_messages(
    *,
    final_prompt: str,
    package_manifest: dict[str, Any],
    compact_input: dict[str, Any],
    attachment_manifest: dict[str, Any],
    support_catalog: dict[str, Any],
    feature_catalog: dict[str, Any],
    operational_region_catalog: dict[str, Any],
    batch_observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    synthesis_input = {
        "execution_note": (
            "overview 26장은 동일 시간그룹별 다중 이미지 배치에서 먼저 판독되었습니다. "
            "아래 batch_observations는 원본 asset_id와 연결된 검증 완료 관찰기록입니다. "
            "최종 후보 근거는 반드시 원본 overview asset_id를 인용하십시오."
        ),
        "package_manifest": package_manifest,
        "compact_input": compact_input,
        "attachment_manifest_reduced": _reduced_attachment_manifest(attachment_manifest),
        "supported_request_catalog": support_catalog,
        "feature_catalog": feature_catalog,
        "operational_region_catalog": operational_region_catalog,
        "batch_observations": batch_observations,
    }
    return [
        {
            "role": "system",
            "content": (
                "당신은 AIFS 강수 감시후보 Pass 1 최종 종합기입니다. "
                "검증된 배치 관찰만 종합하고, 제공되지 않은 자료를 보았다고 주장하지 마십시오. "
                "최종 출력은 JSON만 반환하십시오."
            ),
        },
        {
            "role": "user",
            "content": final_prompt.strip() + "\n\n## 검증된 다단계 관찰 입력\n" + json.dumps(synthesis_input, ensure_ascii=False),
        },
    ]

# Provider transport for the Step04 workflow; unchanged client.complete behavior.
def _step04_provider_complete(client, **kwargs):
    return client.complete(**kwargs)
