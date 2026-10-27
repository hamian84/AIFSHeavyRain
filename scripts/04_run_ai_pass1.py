"""재사용 가능한 관찰·후보 탐색·계획 checkpoint와 Python 병합으로 Pass1을 실행합니다."""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from func.step04 import pass1_engine as engine
from func.step04.analysis_provenance import PROVENANCE_FILENAME, write_step04_analysis_provenance
from func.shared.ai_provider import (
    IMAGE_DETAILS,
    ProviderConfigurationError,
    normalized_endpoint_id,
    provider_defaults,
    resolve_ai_settings,
    resolve_base_url,
    validate_openai_capabilities,
)
from func.shared.env_loader import ProjectEnvLoad, load_project_env
from func.shared.runtime_paths import configured_project_root, resolve_logical_ref, runtime_dir, runtime_path
from func.shared.pipeline_common import (
    add_output_mode_argument, canonical_hash, load_json, rel, require_new_output,
    resolve_output_mode, sanitize_failure_text, setup_pipeline_logging, sha256sum,
    step_dir, step_workspace, update_run_status, validate_init, write_failure_summary, write_json,
)


_PROVIDER_OUTPUT_NAMES = frozenset({"codex_cli", "openai", "openai_compatible"})


def _provider_output_dir(root: Path, init: str, provider: str) -> Path:
    """Keep provider-specific results and resume checkpoints isolated."""
    if provider not in _PROVIDER_OUTPUT_NAMES:
        raise ValueError(f"unsupported Step04 provider output name: {provider}")
    return step_dir(root, init, "step04") / provider


def _validate_request_contract_files(root: Path, request: dict[str, Any]) -> None:
    """Ensure Step03 recorded the exact prompt/schema files used by Step04.

    A Step03 package is immutable with respect to its prompt and schema contract.
    If any referenced file has changed since the package was built, Step03 must be
    regenerated so package_id and downstream resume checkpoints are invalidated
    deterministically.
    """
    refs = request.get("prompt_refs")
    hashes = request.get("prompt_hashes")
    if not isinstance(refs, dict) or not isinstance(hashes, dict):
        raise RuntimeError(
            "Step03 Pass1 요청 패키지에 prompt_refs/prompt_hashes가 없습니다. "
            "Step03을 --overwrite로 다시 실행하십시오."
        )

    mismatches: list[str] = []
    for key, raw_ref in refs.items():
        if not isinstance(key, str) or not isinstance(raw_ref, str) or not raw_ref.strip():
            mismatches.append(str(key))
            continue
        expected = hashes.get(key)
        if not isinstance(expected, str) or not expected:
            mismatches.append(key)
            continue

        try:
            path = resolve_logical_ref(root, raw_ref)
        except ValueError:
            raise RuntimeError(
                f"Step03 Pass1 요청 패키지에 프로젝트 외부 계약 경로가 있습니다: {raw_ref}"
            ) from None
        if not path.is_file() or sha256sum(path) != expected:
            mismatches.append(key)

    extra_hash_keys = sorted(set(hashes) - set(refs))
    mismatches.extend(extra_hash_keys)
    if mismatches:
        names = ", ".join(sorted(set(mismatches)))
        raise RuntimeError(
            "Step03 Pass1 요청 패키지가 현재 프롬프트/스키마와 일치하지 않습니다. "
            "Step03을 --overwrite로 다시 실행하십시오. "
            f"불일치 항목: {names}"
        )


def _checkpoint(*, stage: str, result: dict[str, Any], input_fingerprint: str, prompt_sha256: str, schema_sha256: str, provider: str, model: str, provider_configuration: dict[str, Any] | None = None, retry_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    provider_configuration = provider_configuration or {}
    return {
        "schema_version": "1.0.0", "stage": stage, "status": "completed", "completed": True,
        "input_fingerprint": input_fingerprint, "prompt_sha256": prompt_sha256,
        "schema_sha256": schema_sha256, "provider": provider, "model": model,
        "generation_parameters": {"stage": stage, "provider_configuration": provider_configuration},
        "provider_configuration": provider_configuration,
        "actual_ai_input_hash": input_fingerprint,
        "candidate_id": result.get("candidate_id") if isinstance(result, dict) else None,
        "retry_summary": retry_summary or {"http_retry_count": 0, "repair_attempt_count": 0},
        "result": result,
    }


def _transport_schema_hashes(root: Path, provider: str) -> dict[str, str] | None:
    if provider not in {"openai", "codex_cli"}:
        return None
    names = {
        "batch_observation": "ai_pass1_batch_observation.transport.schema.json",
        "group_summary": "ai_pass1_group_summary.transport.schema.json",
        "candidate_discovery": "ai_pass1_candidate_discovery.transport.schema.json",
        "candidate_plan": "ai_pass1_candidate_plan.transport.schema.json",
        "candidate_response": "ai_discovery_candidate_response.transport.schema.json",
    }
    missing = [name for name in names.values() if not runtime_path(root, "prompts", "schemas", "openai", name).is_file()]
    if missing:
        raise FileNotFoundError("OpenAI transport schema is missing: " + ", ".join(missing))
    return {stage: sha256sum(runtime_path(root, "prompts", "schemas", "openai", name)) for stage, name in names.items()}


def _provider_configuration(root: Path, args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    model = str(args.model)
    base_url = resolve_base_url(
        provider=args.provider,
        cli_base_url=args.base_url,
        cli_openai_base_url=args.openai_base_url,
    )
    api_family = "responses" if args.provider in {"openai", "codex_cli"} else "chat_completions"
    endpoint_id = (
        "codex_cli"
        if args.provider == "codex_cli"
        else normalized_endpoint_id(
            base_url,
            official=args.provider == "openai" and base_url == "https://api.openai.com/v1",
        )
    )
    return model, {
        "provider": args.provider,
        "api_family": api_family,
        "endpoint_id": endpoint_id,
        "image_detail": args.image_detail if api_family == "responses" else None,
        "reasoning_efforts": {
            "observation": args.reasoning_effort_observation,
            "group_summary": args.reasoning_effort_group_summary,
            "candidate_discovery": args.reasoning_effort_candidate_discovery,
            "candidate_plan": args.reasoning_effort_candidate_plan,
        } if api_family == "responses" else None,
        "structured_output_mode": "strict" if api_family == "responses" else "legacy",
        "transport_schema_sha256": _transport_schema_hashes(root, args.provider),
    }


def _stage_provider_configuration(
    provider_configuration: dict[str, Any],
    stage: str,
) -> dict[str, Any]:
    """Keep only the provider settings that can affect one checkpoint stage."""
    configuration = copy.deepcopy(provider_configuration)
    if configuration.get("api_family") != "responses":
        return configuration
    reasoning_stage = {
        "observation": "observation",
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
    transport_stage = "batch_observation" if stage == "observation" else stage
    if isinstance(transport_hashes, dict):
        configuration["transport_schema_sha256"] = {
            transport_stage: transport_hashes.get(transport_stage)
        }
    return configuration


def _stage_provider_configuration_matches(
    actual_configuration: Any,
    expected_configuration: dict[str, Any],
    *,
    stage: str,
) -> bool:
    """Accept legacy full-stage configuration only when this stage is equivalent."""
    if actual_configuration == expected_configuration:
        return True
    if not isinstance(actual_configuration, dict):
        return False
    expected = _stage_provider_configuration(expected_configuration, stage)
    if actual_configuration == expected:
        return True
    shared_fields = (
        "provider",
        "api_family",
        "endpoint_id",
        "image_detail",
        "structured_output_mode",
    )
    if any(actual_configuration.get(field) != expected.get(field) for field in shared_fields):
        return False
    if expected.get("api_family") != "responses":
        return False
    reasoning_stage = {
        "observation": "observation",
        "group_summary": "group_summary",
        "candidate_discovery": "candidate_discovery",
        "candidate_plan": "candidate_plan",
    }[stage]
    transport_stage = "batch_observation" if stage == "observation" else stage
    actual_reasoning = actual_configuration.get("reasoning_efforts")
    expected_reasoning = expected.get("reasoning_efforts")
    actual_hashes = actual_configuration.get("transport_schema_sha256")
    expected_hashes = expected.get("transport_schema_sha256")
    return (
        isinstance(actual_reasoning, dict)
        and isinstance(expected_reasoning, dict)
        and actual_reasoning.get(reasoning_stage) == expected_reasoning.get(reasoning_stage)
        and isinstance(actual_hashes, dict)
        and isinstance(expected_hashes, dict)
        and actual_hashes.get(transport_stage) == expected_hashes.get(transport_stage)
    )


def _resolve_runtime_settings(root: Path, args: argparse.Namespace) -> tuple[ProjectEnvLoad, Any]:
    env_load = load_project_env(
        root,
        env_file=args.env_file,
        override=False,
        enabled=not args.no_env_file,
    )
    if args.model is not None:
        raise ProviderConfigurationError(
            "Step04 model selection is env-file-only; edit AIFS_MODEL_STEP04 in envi/api_keys.env and do not pass --model"
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
    args.provider = settings.provider.value
    args.model = settings.model.value
    args.image_detail = settings.image_detail.value
    args.reasoning_effort_observation = settings.reasoning_efforts["observation"].value
    args.reasoning_effort_group_summary = settings.reasoning_efforts["group_summary"].value
    args.reasoning_effort_candidate_discovery = settings.reasoning_efforts["candidate_discovery"].value
    args.reasoning_effort_candidate_plan = settings.reasoning_efforts["candidate_plan"].value
    if settings.model.source != "env_file":
        raise ProviderConfigurationError(
            "Step04 model must come from envi/api_keys.env only; "
            f"resolved source={settings.model.source!r}. Configure AIFS_MODEL_STEP04 in the project env file and remove process-environment model overrides."
        )
    return env_load, settings


def _observation_input_fingerprint(batch: dict[str, Any], provider_configuration: dict[str, Any] | None) -> str:
    return canonical_hash(batch) if provider_configuration is None else canonical_hash({"batch": batch, "provider_configuration": provider_configuration})


def _checkpoint_provider_configuration_matches(
    checkpoint: dict[str, Any],
    *,
    expected_configuration: dict[str, Any],
    expected_model: str,
    stage: str,
) -> bool:
    if _stage_provider_configuration_matches(
        checkpoint.get("provider_configuration"),
        expected_configuration,
        stage=stage,
    ):
        return True
    logging.info("step04 checkpoint invalidated: provider configuration mismatch stage=%s", stage)
    return False


def _response_with_package(result: dict[str, Any], package_id: str) -> dict[str, Any]:
    value = copy.deepcopy(result)
    value["package_id"] = package_id
    return value


def _print_completion_summary(
    *,
    root: Path,
    output: Path,
    result: dict[str, Any],
) -> None:
    print("AIFSHeavyRain Step04 Pass1")
    print(f"  package_id: {result.get('package_id')}")
    candidates = [
        item for item in result.get("candidates", []) if isinstance(item, dict)
    ]
    precursor_count = sum(
        1 for item in candidates if str(item.get("detection_basis")) == "precursor_environment"
    )
    precursor_assessment = result.get("precursor_candidate_assessment")
    precursor_status = (
        precursor_assessment.get("assessment_status")
        if isinstance(precursor_assessment, dict)
        else None
    )
    print(f"  candidate_count: {len(candidates)}")
    print(f"  precursor_candidate_count: {precursor_count}")
    print(f"  precursor_assessment_status: {precursor_status}")
    print(f"  analysis_data_request_count: {len(result.get('analysis_data_requests', []))}")
    print(f"  validated_response: {rel(root, output / 'pass1_result.json')}")
    provenance_path = output / PROVENANCE_FILENAME
    print(f"  analysis_provenance: {rel(root, provenance_path) if provenance_path.is_file() else 'unavailable'}")


def _completed_result_checkpoint_matches(
    *,
    output: Path,
    package_id: str,
    provider_configuration: dict[str, Any],
    model: str,
    prompt_sha256: str,
    schema_sha256: str,
) -> bool:
    """Reuse a completed result only when the active discovery contract still matches."""
    checkpoint_path = output / "_checkpoint" / "candidate_discovery.json"
    if not checkpoint_path.is_file():
        logging.info("step04 completed result checkpoint missing; full result will not be reused")
        return False
    checkpoint = load_json(checkpoint_path)
    result = checkpoint.get("result")
    engine_checkpoint = checkpoint.get("engine_resume_checkpoint")
    return (
        checkpoint.get("stage") == "candidate_discovery"
        and checkpoint.get("completed") is True
        and checkpoint.get("prompt_sha256") == prompt_sha256
        and checkpoint.get("schema_sha256") == schema_sha256
        and isinstance(result, dict)
        and result.get("package_id") == package_id
        and isinstance(engine_checkpoint, dict)
        and engine_checkpoint.get("contract_version")
        == engine.SPLIT_SYNTHESIS_CONTRACT_VERSION
        and _checkpoint_provider_configuration_matches(
            checkpoint,
            expected_configuration=provider_configuration,
            expected_model=model,
            stage="candidate_discovery",
        )
    )


def _completed_system_anchor_contract_matches(
    *,
    output: Path,
    result: dict[str, Any],
) -> bool:
    """Reject completed Step04 results produced under an obsolete Anchor contract."""
    errors = engine.system_direct_precipitation_anchor_final_errors(result)
    if errors:
        logging.info(
            "step04 completed result invalidated by SYSTEM Anchor invariant: %s",
            " | ".join(errors[:8]),
        )
        return False
    for candidate in result.get("candidates", []):
        if not isinstance(candidate, dict) or str(candidate.get("detection_basis") or "") not in {
            "direct_precipitation",
            "mixed",
        }:
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        checkpoint_path = output / "_checkpoint" / "candidate_plans" / f"{candidate_id}.json"
        if not checkpoint_path.is_file():
            logging.info(
                "step04 completed result invalidated: candidate-plan checkpoint missing candidate=%s",
                candidate_id,
            )
            return False
        checkpoint = load_json(checkpoint_path)
        engine_checkpoint = checkpoint.get("engine_resume_checkpoint")
        if (
            not isinstance(engine_checkpoint, dict)
            or engine_checkpoint.get("system_direct_precipitation_anchor_execution_contract_version")
            != engine.SYSTEM_DIRECT_PRECIPITATION_ANCHOR_EXECUTION_CONTRACT_VERSION
        ):
            logging.info(
                "step04 completed result invalidated: SYSTEM Anchor execution contract mismatch candidate=%s",
                candidate_id,
            )
            return False
    return True


def _safe_batch_id(batch_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", batch_id)


def _engine_retry_summary(engine_checkpoint: dict[str, Any]) -> dict[str, int]:
    """Read retry counters while accepting checkpoints created before they existed."""
    raw = engine_checkpoint.get("retry_summary")
    if not isinstance(raw, dict):
        raw = {}
    result: dict[str, int] = {}
    for key in (
        "structured_output_fallback_count",
        "source_grounded_retry_attempt_count",
        "repair_attempt_count",
        "http_retry_count",
    ):
        try:
            result[key] = max(0, int(raw.get(key, 0)))
        except (TypeError, ValueError):
            result[key] = 0
    return result


def _observation_checkpoint_matches(
    checkpoint: dict[str, Any],
    *,
    request: dict[str, Any],
    batch: dict[str, Any],
    provider_configuration: dict[str, Any] | None = None,
    model: str | None = None,
) -> bool:
    prompts = request["prompt_hashes"]
    result = checkpoint.get("result")
    basic_match = (
        checkpoint.get("stage") == "observation"
        and checkpoint.get("status") == "completed"
        and checkpoint.get("completed") is True
        and checkpoint.get("prompt_sha256") == prompts["batch_observation"]
        and checkpoint.get("schema_sha256") == prompts["batch_schema"]
        and isinstance(result, dict)
        and result.get("batch_id") == batch["batch_id"]
    )
    if not basic_match:
        return False
    if provider_configuration is None:
        return checkpoint.get("input_fingerprint") == canonical_hash(batch)
    expected_configuration = _stage_provider_configuration(
        provider_configuration,
        "observation",
    )
    current_fingerprint = _observation_input_fingerprint(batch, expected_configuration)
    if checkpoint.get("provider_configuration") == expected_configuration:
        return checkpoint.get("input_fingerprint") == current_fingerprint
    if _stage_provider_configuration_matches(
        checkpoint.get("provider_configuration"),
        provider_configuration,
        stage="observation",
    ):
        legacy_fingerprint = _observation_input_fingerprint(
            batch,
            checkpoint.get("provider_configuration"),
        )
        return checkpoint.get("input_fingerprint") == legacy_fingerprint
    logging.info("step04 checkpoint invalidated: provider configuration mismatch batch_id=%s", batch["batch_id"])
    return False


def _persist_completed_observations(
    *,
    request: dict[str, Any],
    source: Path,
    output: Path,
    provider: str,
    model: str,
    provider_configuration: dict[str, Any] | None = None,
) -> int:
    """Copy only engine-validated observation checkpoints into the active Step04 output."""
    prompts = request["prompt_hashes"]
    batches = {str(item["batch_id"]): item for item in request["batch_plan"]["batches"]}
    count = 0
    stage_configuration = (
        _stage_provider_configuration(provider_configuration, "observation")
        if provider_configuration is not None
        else None
    )
    for source_path in sorted((source / "observations").glob("*.observation.json")):
        observation = load_json(source_path)
        batch_id = str(observation.get("batch_id"))
        batch = batches.get(batch_id)
        if batch is None or observation.get("package_id") != request["package_id"]:
            continue
        write_json(
            output / "_checkpoint" / "observations" / f"{batch_id}.json",
            _checkpoint(
                stage="observation",
                result=_response_with_package(observation, str(request["package_id"])),
                input_fingerprint=_observation_input_fingerprint(batch, stage_configuration),
                prompt_sha256=prompts["batch_observation"],
                schema_sha256=prompts["batch_schema"],
                provider=provider,
                model=model,
                provider_configuration=stage_configuration,
            ),
        )
        count += 1
    return count


def _valid_engine_resume_checkpoint(
    *,
    value: dict[str, Any],
    engine_checkpoint: dict[str, Any],
    package_id: str,
    candidate_id: str | None = None,
) -> bool:
    """Accept only complete, non-secret engine checkpoints for transient resume seeding."""
    if value.get("package_id") != package_id:
        return False
    if candidate_id is not None and value.get("candidate_id") != candidate_id:
        return False
    if (
        candidate_id is not None
        and engine_checkpoint.get("system_direct_precipitation_anchor_execution_contract_version")
        != engine.SYSTEM_DIRECT_PRECIPITATION_ANCHOR_EXECUTION_CONTRACT_VERSION
    ):
        return False
    return (
        engine_checkpoint.get("package_id") == package_id
        and isinstance(engine_checkpoint.get("fingerprint"), str)
        and bool(engine_checkpoint["fingerprint"])
    )


def _persist_completed_split_checkpoints(
    *,
    request: dict[str, Any],
    source: Path,
    output: Path,
    provider: str,
    model: str,
    provider_configuration: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Preserve validated split-stage state before a transient engine workspace is removed."""
    package_id = str(request["package_id"])
    prompts = request["prompt_hashes"]
    persisted = {"candidate_discovery": 0, "candidate_plans": 0}
    discovery_configuration = (
        _stage_provider_configuration(provider_configuration, "candidate_discovery")
        if provider_configuration is not None
        else None
    )
    plan_configuration = (
        _stage_provider_configuration(provider_configuration, "candidate_plan")
        if provider_configuration is not None
        else None
    )

    discovery_dir = source / "candidate_discovery"
    discovery_value_path = discovery_dir / "candidate_discovery.validated.json"
    discovery_engine_checkpoint_path = discovery_dir / "candidate_discovery.checkpoint.json"
    if discovery_value_path.is_file() and discovery_engine_checkpoint_path.is_file():
        discovery = load_json(discovery_value_path)
        engine_checkpoint = load_json(discovery_engine_checkpoint_path)
        if _valid_engine_resume_checkpoint(
            value=discovery,
            engine_checkpoint=engine_checkpoint,
            package_id=package_id,
        ):
            write_json(
                output / "_checkpoint" / "candidate_discovery.json",
                {
                    **_checkpoint(
                        stage="candidate_discovery",
                        result=_response_with_package(discovery, package_id),
                        input_fingerprint=str(engine_checkpoint["fingerprint"]),
                        prompt_sha256=prompts["candidate_discovery"],
                        schema_sha256=prompts["candidate_discovery_schema"],
                        provider=provider,
                        model=model,
                        provider_configuration=discovery_configuration,
                        retry_summary=_engine_retry_summary(engine_checkpoint),
                    ),
                    "engine_resume_checkpoint": engine_checkpoint,
                },
            )
            persisted["candidate_discovery"] = 1

    for value_path in sorted((source / "candidate_plans").glob("*.plan.validated.json")):
        candidate_id = value_path.name.removesuffix(".plan.validated.json")
        engine_checkpoint_path = value_path.with_name(f"{candidate_id}.plan.checkpoint.json")
        if not engine_checkpoint_path.is_file():
            continue
        value = load_json(value_path)
        engine_checkpoint = load_json(engine_checkpoint_path)
        if not _valid_engine_resume_checkpoint(
            value=value,
            engine_checkpoint=engine_checkpoint,
            package_id=package_id,
            candidate_id=candidate_id,
        ):
            continue
        write_json(
            output / "_checkpoint" / "candidate_plans" / f"{_safe_batch_id(candidate_id)}.json",
            {
                **_checkpoint(
                    stage="candidate_plan",
                    result=_response_with_package(value, package_id),
                    input_fingerprint=str(engine_checkpoint["fingerprint"]),
                    prompt_sha256=prompts["candidate_plan"],
                    schema_sha256=prompts["candidate_plan_schema"],
                    provider=provider,
                    model=model,
                    provider_configuration=plan_configuration,
                    retry_summary=_engine_retry_summary(engine_checkpoint),
                ),
                "engine_resume_checkpoint": engine_checkpoint,
            },
        )
        persisted["candidate_plans"] += 1
    return persisted


def _promote_incremental_checkpoint(
    *,
    stage: str,
    stage_id: str,
    root: Path,
    request: dict[str, Any],
    source: Path,
    output: Path,
    provider: str,
    model: str,
    provider_configuration: dict[str, Any] | None = None,
) -> None:
    """Promote only validated transient state without exposing provider artifacts."""
    try:
        if stage == "observation":
            count = _persist_completed_observations(
                request=request,
                source=source,
                output=output,
                provider=provider,
                model=model,
                provider_configuration=provider_configuration,
            )
            logging.info(
                "step04 checkpoint promoted stage=observation batch_id=%s completed_observations=%s",
                stage_id,
                count,
            )
            return
        if stage in {"candidate_discovery", "candidate_plan"}:
            persisted = _persist_completed_split_checkpoints(
                request=request,
                source=source,
                output=output,
                provider=provider,
                model=model,
                provider_configuration=provider_configuration,
            )
            logging.info(
                "step04 checkpoint promoted stage=%s stage_id=%s candidate_discovery=%s candidate_plans=%s",
                stage,
                stage_id or "-",
                persisted["candidate_discovery"],
                persisted["candidate_plans"],
            )
            return
        raise ValueError(f"unsupported Step04 checkpoint stage: {stage}")
    except Exception as exc:  # noqa: BLE001 - final synchronization still runs on engine exit
        logging.warning(
            "step04 incremental checkpoint promotion failed stage=%s stage_id=%s error=%s",
            stage,
            stage_id or "-",
            sanitize_failure_text(exc, root=root),
        )


def _seed_completed_observations(
    *,
    request: dict[str, Any],
    output: Path,
    destination: Path,
    provider_configuration: dict[str, Any] | None = None,
    model: str | None = None,
) -> int:
    """Seed valid active checkpoints into the transient engine workspace for --resume."""
    count = 0
    for batch in request["batch_plan"]["batches"]:
        batch_id = str(batch["batch_id"])
        checkpoint_path = output / "_checkpoint" / "observations" / f"{batch_id}.json"
        if not checkpoint_path.is_file():
            continue
        try:
            checkpoint = load_json(checkpoint_path)
        except Exception:  # An invalid checkpoint must be regenerated by the engine.
            continue
        if not _observation_checkpoint_matches(
            checkpoint,
            request=request,
            batch=batch,
            provider_configuration=provider_configuration,
            model=model,
        ):
            continue
        result = dict(checkpoint["result"])
        previous_package_id = str(result.get("package_id", ""))
        result["package_id"] = str(request["package_id"])
        if previous_package_id and previous_package_id != result["package_id"]:
            logging.info(
                "step04 observation checkpoint repackaged batch_id=%s old_package_id=%s new_package_id=%s",
                batch_id,
                previous_package_id,
                result["package_id"],
            )
        write_json(destination / f"{_safe_batch_id(batch_id)}.observation.json", result)
        count += 1
    return count


def _seed_completed_split_checkpoints(
    *,
    request: dict[str, Any],
    output: Path,
    destination: Path,
    provider_configuration: dict[str, Any] | None = None,
    model: str | None = None,
) -> dict[str, int]:
    """Seed persisted split checkpoints; the engine performs the final fingerprint validation."""
    package_id = str(request["package_id"])
    seeded = {"candidate_discovery": 0, "candidate_plans": 0}
    discovery_path = output / "_checkpoint" / "candidate_discovery.json"
    if discovery_path.is_file():
        try:
            checkpoint = load_json(discovery_path)
            discovery = checkpoint.get("result")
            engine_checkpoint = checkpoint.get("engine_resume_checkpoint")
            if (
                checkpoint.get("stage") == "candidate_discovery"
                and checkpoint.get("completed") is True
                and (
                    provider_configuration is None
                    or _checkpoint_provider_configuration_matches(
                        checkpoint,
                        expected_configuration=provider_configuration,
                        expected_model=model or "",
                        stage="candidate_discovery",
                    )
                )
                and isinstance(discovery, dict)
                and isinstance(engine_checkpoint, dict)
                and _valid_engine_resume_checkpoint(
                    value=discovery,
                    engine_checkpoint=engine_checkpoint,
                    package_id=package_id,
                )
            ):
                target = destination / "candidate_discovery"
                write_json(target / "candidate_discovery.validated.json", discovery)
                write_json(target / "candidate_discovery.checkpoint.json", engine_checkpoint)
                seeded["candidate_discovery"] = 1
        except Exception:  # noqa: BLE001
            pass

    for checkpoint_path in sorted((output / "_checkpoint" / "candidate_plans").glob("*.json")):
        try:
            checkpoint = load_json(checkpoint_path)
            value = checkpoint.get("result")
            engine_checkpoint = checkpoint.get("engine_resume_checkpoint")
            candidate_id = str(value.get("candidate_id")) if isinstance(value, dict) else ""
            if (
                checkpoint.get("stage") != "candidate_plan"
                or checkpoint.get("completed") is not True
                or (
                    provider_configuration is not None
                    and not _checkpoint_provider_configuration_matches(
                        checkpoint,
                        expected_configuration=provider_configuration,
                        expected_model=model or "",
                        stage="candidate_plan",
                    )
                )
                or not isinstance(value, dict)
                or not isinstance(engine_checkpoint, dict)
                or not _valid_engine_resume_checkpoint(
                    value=value,
                    engine_checkpoint=engine_checkpoint,
                    package_id=package_id,
                    candidate_id=candidate_id,
                )
            ):
                continue
            safe_id = _safe_batch_id(candidate_id)
            target = destination / "candidate_plans"
            write_json(target / f"{safe_id}.plan.validated.json", value)
            write_json(target / f"{safe_id}.plan.checkpoint.json", engine_checkpoint)
            seeded["candidate_plans"] += 1
        except Exception:  # noqa: BLE001
            continue
    return seeded


def _close_temporary_file_handlers(temp_root: Path) -> None:
    """Release only log files owned by the transient engine workspace on Windows."""
    root = temp_root.resolve()
    loggers = [logging.getLogger()]
    loggers.extend(
        item
        for item in logging.Logger.manager.loggerDict.values()
        if isinstance(item, logging.Logger)
    )
    for logger in loggers:
        for handler in list(logger.handlers):
            if not isinstance(handler, logging.FileHandler):
                continue
            try:
                Path(handler.baseFilename).resolve().relative_to(root)
            except ValueError:
                continue
            logger.removeHandler(handler)
            handler.close()


class _TemporaryEngineLogFilter(logging.Filter):
    """Expose compatibility-engine logs without persisting secrets or local paths."""

    def __init__(self, *, root: Path) -> None:
        super().__init__()
        self._root = root.resolve()

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - logging must not prevent cleanup on a provider failure
            message = str(record.msg)
        ratios: list[str] = []

        def preserve_ratio(match: re.Match[str]) -> str:
            ratios.append(match.group(0))
            return f"AIFSLOGRATIO{len(ratios) - 1}TOKEN"

        protected = re.sub(r"\b\d+/\d+\b", preserve_ratio, message.replace("step05", "step04"))
        sanitized = sanitize_failure_text(protected, root=self._root)
        for index, ratio in enumerate(ratios):
            sanitized = sanitized.replace(f"AIFSLOGRATIO{index}TOKEN", ratio)
        record.msg = sanitized
        record.args = ()
        if record.exc_info:
            record.exc_info = None
            record.exc_text = None
        return True


def _build_engine_argv(*, root: Path, temp_root: Path, init: str, args: argparse.Namespace) -> list[str]:
    """Translate the public Step04 controls to the isolated legacy engine invocation."""
    provider = args.provider or "openai"
    image_detail = args.image_detail or "original"
    reasoning_efforts = {
        "observation": args.reasoning_effort_observation or "none",
        "group_summary": args.reasoning_effort_group_summary or "none",
        "candidate_discovery": args.reasoning_effort_candidate_discovery or "low",
        "candidate_plan": args.reasoning_effort_candidate_plan or "low",
    }
    argv = [
        "04_run_ai_pass1.py", "--init", init, "--root", str(temp_root), "--provider", provider,
        "--synthesis-mode", "hierarchical", "--max-images-per-request", "4", "--max-retries", str(args.max_retries),
        "--multimodal-retry-attempts", str(args.multimodal_retry_attempts),
        "--retry-backoff-seconds", str(args.retry_backoff_seconds),
        "--request-gap-seconds", str(args.request_gap_seconds),
        "--repair-attempts", str(args.repair_attempts),
        "--max-tokens-observation", str(args.max_tokens_observation),
        "--max-tokens-candidate-discovery", str(args.max_tokens_candidate_discovery),
        "--max-tokens-candidate-plan", str(args.max_tokens_candidate_plan), "--no-enable-thinking",
        "--candidate-plan-continuation-attempts", str(args.candidate_plan_continuation_attempts),
        "--image-detail", image_detail,
        "--reasoning-effort-observation", reasoning_efforts["observation"],
        "--reasoning-effort-group-summary", reasoning_efforts["group_summary"],
        "--reasoning-effort-candidate-discovery", reasoning_efforts["candidate_discovery"],
        "--reasoning-effort-candidate-plan", reasoning_efforts["candidate_plan"],
        "--output-mode", getattr(args, "output_mode", "operational"),
    ]
    if args.base_url:
        argv.extend(["--base-url", args.base_url])
    if args.openai_base_url:
        argv.extend(["--openai-base-url", args.openai_base_url])
    if args.api_key_env:
        argv.extend(["--api-key-env", args.api_key_env])
    if args.no_env_file:
        argv.append("--no-env-file")
    else:
        # The wrapper resolves the project dotenv once and passes its provenance
        # directly to the in-process compatibility engine. Do not encode the
        # resolved model back into argv; model selection remains env-file-only.
        pass
    if args.candidate_id:
        argv.extend(["--candidate-id", args.candidate_id])
    return argv


def _stage_from_legacy(*, root: Path, init: str, request: dict[str, Any], source: Path, output: Path, provider: str, model: str, provider_configuration: dict[str, Any]) -> dict[str, Any]:
    """원문 제공자 산출물을 읽지 않고 검증된 기존 checkpoint를 변환합니다."""

    package_id = str(request["package_id"])
    prompts = request["prompt_hashes"]
    observations = sorted((source / "observations").glob("*.observation.json"))
    batches = {str(item["batch_id"]): item for item in request["batch_plan"]["batches"]}
    if len(observations) != len(batches):
        raise RuntimeError(f"기존 observation checkpoint 수가 일치하지 않습니다: expected={len(batches)} actual={len(observations)}")
    for path in observations:
        value = load_json(path)
        batch_id = str(value.get("batch_id"))
        batch = batches.get(batch_id)
        if batch is None:
            raise RuntimeError(f"기존 observation이 알 수 없는 batch를 참조합니다: {batch_id}")
        write_json(
            output / "_checkpoint" / "observations" / f"{batch_id}.json",
            _checkpoint(
                stage="observation", result=_response_with_package(value, package_id),
                input_fingerprint=_observation_input_fingerprint(batch, _stage_provider_configuration(provider_configuration, "observation")), prompt_sha256=prompts["batch_observation"],
                schema_sha256=prompts["batch_schema"], provider=provider, model=model,
                provider_configuration=_stage_provider_configuration(provider_configuration, "observation"),
            ),
        )
    discovery = _response_with_package(load_json(source / "candidate_discovery" / "candidate_discovery.validated.json"), package_id)
    discovery_engine_checkpoint_path = source / "candidate_discovery" / "candidate_discovery.checkpoint.json"
    discovery_engine_checkpoint = (
        load_json(discovery_engine_checkpoint_path)
        if discovery_engine_checkpoint_path.is_file()
        else None
    )
    discovery_input_fingerprint = canonical_hash(discovery.get("candidates", []))
    discovery_retry_summary = None
    if isinstance(discovery_engine_checkpoint, dict) and _valid_engine_resume_checkpoint(
        value=discovery,
        engine_checkpoint=discovery_engine_checkpoint,
        package_id=package_id,
    ):
        discovery_input_fingerprint = str(discovery_engine_checkpoint["fingerprint"])
        discovery_retry_summary = _engine_retry_summary(discovery_engine_checkpoint)
    write_json(
        output / "_checkpoint" / "candidate_discovery.json",
        {
            **_checkpoint(
                stage="candidate_discovery",
                result=discovery,
                input_fingerprint=discovery_input_fingerprint,
                prompt_sha256=prompts["candidate_discovery"],
                schema_sha256=prompts["candidate_discovery_schema"],
                provider=provider,
                model=model,
                provider_configuration=_stage_provider_configuration(provider_configuration, "candidate_discovery"),
                retry_summary=discovery_retry_summary,
            ),
            **(
                {"engine_resume_checkpoint": discovery_engine_checkpoint}
                if discovery_retry_summary is not None
                else {}
            ),
        },
    )
    plans = sorted((source / "candidate_plans").glob("*.plan.validated.json"))
    if len(plans) != len(discovery.get("candidates", [])):
        raise RuntimeError("기존 candidate-plan 범위가 완전하지 않습니다.")
    for path in plans:
        value = _response_with_package(load_json(path), package_id)
        candidate_id = str(value["candidate_id"])
        engine_checkpoint_path = path.with_name(f"{candidate_id}.plan.checkpoint.json")
        engine_checkpoint = (
            load_json(engine_checkpoint_path)
            if engine_checkpoint_path.is_file()
            else None
        )
        input_fingerprint = canonical_hash({"candidate_id": candidate_id, "discovery": discovery})
        retry_summary = None
        if isinstance(engine_checkpoint, dict) and _valid_engine_resume_checkpoint(
            value=value,
            engine_checkpoint=engine_checkpoint,
            package_id=package_id,
            candidate_id=candidate_id,
        ):
            input_fingerprint = str(engine_checkpoint["fingerprint"])
            retry_summary = _engine_retry_summary(engine_checkpoint)
        write_json(
            output / "_checkpoint" / "candidate_plans" / f"{candidate_id}.json",
            {
                **_checkpoint(
                    stage="candidate_plan",
                    result=value,
                    input_fingerprint=input_fingerprint,
                    prompt_sha256=prompts["candidate_plan"],
                    schema_sha256=prompts["candidate_plan_schema"],
                    provider=provider,
                    model=model,
                    provider_configuration=_stage_provider_configuration(provider_configuration, "candidate_plan"),
                    retry_summary=retry_summary,
                ),
                **(
                    {"engine_resume_checkpoint": engine_checkpoint}
                    if retry_summary is not None
                    else {}
                ),
            },
        )
    result = _response_with_package(load_json(source / "ai_candidate_response.validated.json"), package_id)
    write_json(output / "pass1_result.json", result)
    (output / PROVENANCE_FILENAME).unlink(missing_ok=True)
    try:
        provenance_path = write_step04_analysis_provenance(
            root=root,
            init=init,
            request=request,
            result=result,
            source=source,
            output=output,
        )
        logging.info("step04 analysis provenance written path=%s", rel(root, provenance_path))
    except Exception as exc:  # noqa: BLE001 - provenance must not block the operational Step04 result
        logging.warning(
            "step04 analysis provenance unavailable error=%s",
            sanitize_failure_text(exc, root=root),
        )
    return result


def _prepare_engine_workspace(
    root: Path,
    temp_root: Path,
    init: str,
    request: dict[str, Any],
    output: Path,
    resume: bool,
    provider_configuration: dict[str, Any],
    model: str,
) -> int:
    """폐기된 엔진에 필요한 임시 입력 계약만 구성합니다."""

    (temp_root / "prompts").mkdir(parents=True, exist_ok=True)
    shutil.copytree(runtime_dir(root, "prompts"), temp_root / "prompts", dirs_exist_ok=True)
    input_dir = temp_root / "daio" / init / "ai_pass1" / "input"
    prompt_dir = temp_root / "daio" / init / "prompts"
    feature_dir = temp_root / "daio" / init / "feature_package"
    input_dir.mkdir(parents=True, exist_ok=True)
    prompt_dir.mkdir(parents=True, exist_ok=True)
    feature_dir.mkdir(parents=True, exist_ok=True)
    observation_dir = temp_root / "daio" / init / "ai_pass1" / "observations"
    observation_dir.mkdir(parents=True, exist_ok=True)
    seeded_count = (
        _seed_completed_observations(
            request=request,
            output=output,
            destination=observation_dir,
            provider_configuration=provider_configuration,
            model=model,
        )
        if resume
        else 0
    )
    pass1_dir = temp_root / "daio" / init / "ai_pass1"
    if resume:
        _seed_completed_split_checkpoints(
            request=request,
            output=output,
            destination=pass1_dir,
            provider_configuration=provider_configuration,
            model=model,
        )
    overview_data_ref = str(request.get("overview_data_ref", "")).strip()
    if not overview_data_ref:
        raise ValueError("Step03 pass1_request overview_data_ref is missing")
    overview_data_source = resolve_logical_ref(root, overview_data_ref)
    if not overview_data_source.is_file():
        raise FileNotFoundError(f"Step02 overview_data is missing: {overview_data_ref}")
    overview_data_sha256 = sha256sum(overview_data_source)
    expected_overview_sha256 = str(request.get("overview_data_sha256", "")).strip()
    if expected_overview_sha256 and overview_data_sha256 != expected_overview_sha256:
        raise ValueError("Step02 overview_data hash does not match Step03 pass1_request")
    overview_data_target = temp_root / "daio" / init / "step02" / "overview_data.json"
    overview_data_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(overview_data_source, overview_data_target)

    attachments = copy.deepcopy(request["attachments"])
    for item in attachments:
        source = resolve_logical_ref(root, str(item["file_path"]))
        target = temp_root / "overview" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        item["file_path"] = rel(temp_root, target)
    attachment_manifest = {"package_id": request["package_id"], "attachment_count": len(attachments), "attachments": attachments}
    batch_plan = copy.deepcopy(request["batch_plan"])
    support_catalog = copy.deepcopy(request["support_catalog"])
    compact = {"package_id": request["package_id"], "init": init, "attachment_count": len(attachments)}
    engine_input_manifest = {
        "package_id": request["package_id"],
        "init": init,
        "overview_data_ref": rel(temp_root, overview_data_target),
        "overview_data_sha256": overview_data_sha256,
    }
    for name, value in {
        "ai_discovery_input_manifest.json": engine_input_manifest,
        "ai_discovery_input_validation_report.json": {"coverage_status": "complete", "errors": []},
        "ai_discovery_input.compact.json": compact,
        "ai_discovery_attachment_manifest.json": attachment_manifest,
        "ai_discovery_batch_plan.json": batch_plan,
        "ai_pass1_supported_request_catalog.json": support_catalog,
    }.items():
        write_json(input_dir / name, value)
    refs = request["prompt_refs"]
    for destination, source in {
        "ai_discovery_candidate_response.schema.json": refs["final_schema"],
        "ai_pass1_batch_observation.schema.json": refs["batch_schema"],
    }.items():
        shutil.copy2(resolve_logical_ref(root, source), input_dir / destination)
    for destination, source in {
        "ai_pass1_batch_observation_prompt.ko.md": refs["batch_observation"],
        "ai_discovery_candidate_prompt.ko.md": refs["candidate_discovery"],
    }.items():
        shutil.copy2(resolve_logical_ref(root, source), prompt_dir / destination)
    write_json(feature_dir / "discovery_feature_catalog.json", request["feature_catalog"])
    write_json(feature_dir / "operational_region_catalog.json", request["operational_region_catalog"])
    return seeded_count


def _engine_failure_message(*, source: Path, root: Path, exit_code: int) -> tuple[str, dict[str, Any] | None]:
    """Promote the compatibility engine's sanitized failure stage to Step04."""
    failures = source / "failures"
    summaries = sorted(failures.glob("*/failure_summary.json")) if failures.is_dir() else []
    for path in summaries:
        try:
            summary = load_json(path)
        except Exception:  # noqa: BLE001 - keep the exit-code fallback when the scratch summary is unreadable
            continue
        stage = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(summary.get("failed_step", "provider")))
        message = sanitize_failure_text(str(summary.get("sanitized_message", "")), root=root)
        diagnostics = summary.get("incomplete_diagnostics")
        safe_diagnostics = diagnostics if isinstance(diagnostics, dict) else None
        if message:
            return f"Pass1 provider stage={stage} failed: {message}", safe_diagnostics
        return f"Pass1 provider stage={stage} failed with exit code {exit_code}", safe_diagnostics
    return f"Pass1 provider execution failed with exit code {exit_code}", None


def _persist_coverage_failure_diagnostics(*, source: Path, output: Path) -> None:
    """Preserve the SYSTEM-owned final coverage audit when the scratch workspace is removed.

    Never publish AI raw output or partially validated candidate-discovery JSON.
    Diagnostics live in daio/<init>/step04/codex_cli/_diagnostics only on failure.
    """
    coverage_dir = source / "candidate_discovery"
    audit_path = coverage_dir / "system_direct_precipitation_coverage_audit.json"
    reconciliation_path = coverage_dir / "system_direct_precipitation_coverage_reconciliation.json"
    if not reconciliation_path.is_file():
        return
    try:
        reconciliation = load_json(reconciliation_path)
        audit = load_json(audit_path) if audit_path.is_file() else {}
        if not isinstance(reconciliation, dict) or not isinstance(audit, dict):
            return
        diagnostic = {
            "contract_version": reconciliation.get("contract_version"),
            "status": reconciliation.get("status"),
            "initial_required_uncovered_count": reconciliation.get("initial_required_uncovered_count"),
            "final_required_uncovered_count": reconciliation.get("final_required_uncovered_count"),
            "candidate_count_before": reconciliation.get("candidate_count_before"),
            "candidate_count_current": reconciliation.get("candidate_count_current"),
            "rounds": reconciliation.get("rounds", []),
            "remaining_signals": reconciliation.get("remaining_signals", []),
            "final_audit_counts": {
                key: audit.get(key) for key in (
                    "signal_count", "covered_signal_count", "required_uncovered_count",
                    "advisory_uncovered_count", "candidate_reference_threshold_6h_mm",
                )
            },
            "final_uncovered_diagnostics": [
                {key: row.get(key) for key in (
                    "forecast_hour", "region_id", "region_role", "maximum_6h_mm",
                    "candidate_match_diagnostics",
                )}
                for row in audit.get("signals", [])
                if isinstance(row, dict)
                and row.get("candidate_generation_required") is True
                and row.get("coverage_status") == "uncovered"
            ],
        }
        write_json(output / "_diagnostics" / "step04_coverage_failure.json", diagnostic)
    except (ValueError, TypeError, OSError, KeyError) as exc:
        logging.warning("step04 coverage diagnostic persistence failed: %s", type(exc).__name__)


def _failed_candidate_id(exc: Exception) -> str | None:
    match = re.search(r"\bcandidate_id=(CAND_[0-9]{3})\b", str(exc))
    return match.group(1) if match else None


def _run_engine(*, root: Path, init: str, request: dict[str, Any], args: argparse.Namespace, output: Path, model: str, provider_configuration: dict[str, Any], env_load: ProjectEnvLoad) -> dict[str, Any]:
    with step_workspace(
        root=root,
        init=init,
        step="step04",
        output_mode=getattr(args, "output_mode", "operational"),
    ) as temp_root:
        _prepare_engine_workspace(
            root,
            temp_root,
            init,
            request,
            output,
            args.resume,
            provider_configuration,
            model,
        )
        argv = _build_engine_argv(root=root, temp_root=temp_root, init=init, args=args)
        old_argv = sys.argv
        code: int | None = None
        failure_message: str | None = None
        failure_diagnostics: dict[str, Any] | None = None
        engine_log_filter = _TemporaryEngineLogFilter(root=root)
        root_logger = logging.getLogger()
        old_codex_diagnostic_dir = os.environ.get("AIFS_CODEX_DIAGNOSTIC_DIR")

        def checkpoint_publisher(stage: str, stage_id: str) -> None:
            _promote_incremental_checkpoint(
                stage=stage,
                stage_id=stage_id,
                root=root,
                request=request,
                source=temp_root / "daio" / init / "ai_pass1",
                output=output,
                provider=args.provider,
                model=model,
                provider_configuration=provider_configuration,
            )

        try:
            sys.argv = argv
            if args.provider == "codex_cli":
                os.environ["AIFS_CODEX_DIAGNOSTIC_DIR"] = str(output / "_diagnostics")
            root_logger.addFilter(engine_log_filter)
            with contextlib.redirect_stdout(io.StringIO()):
                code = engine.main(checkpoint_publisher=checkpoint_publisher, preloaded_env_load=env_load)
        finally:
            root_logger.removeFilter(engine_log_filter)
            sys.argv = old_argv
            if old_codex_diagnostic_dir is None:
                os.environ.pop("AIFS_CODEX_DIAGNOSTIC_DIR", None)
            else:
                os.environ["AIFS_CODEX_DIAGNOSTIC_DIR"] = old_codex_diagnostic_dir
            _persist_completed_observations(
                request=request,
                source=temp_root / "daio" / init / "ai_pass1",
                output=output,
                provider=args.provider,
                model=model,
                provider_configuration=provider_configuration,
            )
            _persist_completed_split_checkpoints(
                request=request,
                source=temp_root / "daio" / init / "ai_pass1",
                output=output,
                provider=args.provider,
                model=model,
                provider_configuration=provider_configuration,
            )
            if code:
                _persist_coverage_failure_diagnostics(
                    source=temp_root / "daio" / init / "ai_pass1",
                    output=output,
                )
                failure_message, failure_diagnostics = _engine_failure_message(
                    source=temp_root / "daio" / init / "ai_pass1",
                    root=root,
                    exit_code=code,
                )
            _close_temporary_file_handlers(temp_root)
        if code:
            error = RuntimeError(failure_message or f"Pass1 provider execution failed with exit code {code}")
            if failure_diagnostics is not None:
                error.diagnostics = failure_diagnostics
            raise error
        if args.candidate_id:
            checkpoint_path = output / "_checkpoint" / "candidate_plans" / f"{_safe_batch_id(args.candidate_id)}.json"
            if not checkpoint_path.is_file():
                raise RuntimeError(
                    f"candidate plan-only completed without a promoted checkpoint candidate_id={args.candidate_id}"
                )
            checkpoint = load_json(checkpoint_path)
            value = checkpoint.get("result")
            if not isinstance(value, dict):
                raise RuntimeError(
                    f"candidate plan-only checkpoint is invalid candidate_id={args.candidate_id}"
                )
            return {
                "package_id": request["package_id"],
                "candidate_id": args.candidate_id,
                "candidate_plan_only": True,
                "analysis_data_requests": value.get("analysis_data_requests", []),
            }
        return _stage_from_legacy(
            root=root,
            init=init,
            request=request,
            source=temp_root / "daio" / init / "ai_pass1",
            output=output,
            provider=args.provider,
            model=model,
            provider_configuration=provider_configuration,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="재사용 가능한 checkpoint로 Step04 Pass1 분석을 실행합니다.")
    parser.add_argument("--init", required=True)
    parser.add_argument("--root", default=str(configured_project_root(PROJECT_ROOT)))
    parser.add_argument("--provider", choices=["openai", "codex_cli", "openai_compatible"], default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--openai-base-url", default=None, help="Explicit non-default OpenAI Responses API base URL.")
    parser.add_argument("--model", default=None)
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--env-file", default=None, help="Path to dotenv configuration. Default: envi/api_keys.env.")
    parser.add_argument("--no-env-file", action="store_true")
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Provider execution retry count. Default: 2.",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=15.0,
        help="Initial retry backoff seconds; the engine uses exponential backoff with jitter. Default: 15.",
    )
    parser.add_argument(
        "--request-gap-seconds",
        type=float,
        default=3.0,
        help="Delay between completed image batches and candidate-plan requests. Default: 3.",
    )
    parser.add_argument(
        "--repair-attempts",
        type=int,
        default=3,
        help="Maximum JSON repair attempts per provider stage. Default: 3.",
    )
    parser.add_argument(
        "--multimodal-retry-attempts",
        type=int,
        default=4,
        help="Maximum original-image retries for each Step04 observation batch. Default: 4.",
    )
    parser.add_argument("--max-tokens-observation", type=int, default=5000)
    parser.add_argument(
        "--max-tokens-candidate-discovery",
        type=int,
        default=16000,
        help=(
            "Maximum combined reasoning and output-token ceiling for bounded hierarchical candidate "
            "discovery. Default: 16000."
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
    parser.add_argument("--image-detail", choices=sorted(IMAGE_DETAILS), default=None)
    parser.add_argument("--reasoning-effort-observation", choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"], default=None)
    parser.add_argument("--reasoning-effort-group-summary", choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"], default=None)
    parser.add_argument("--reasoning-effort-candidate-discovery", choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"], default=None)
    parser.add_argument("--reasoning-effort-candidate-plan", choices=["none", "minimal", "low", "medium", "high", "xhigh", "max"], default=None)
    parser.add_argument(
        "--candidate-id",
        default=None,
        help="Run only one existing candidate plan from a reusable discovery checkpoint; no final merge is written.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate Step04 configuration and input without a provider call or output write.")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--overwrite", action="store_true")
    add_output_mode_argument(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    output: Path | None = None
    args.output_mode = resolve_output_mode(args.output_mode)
    logger = setup_pipeline_logging(root, args.init, "step04")
    try:
        validate_init(args.init)
        if args.no_env_file and args.env_file:
            raise ValueError("--env-file and --no-env-file cannot be used together")
        if (
            args.max_retries < 0
            or args.retry_backoff_seconds < 0
            or args.request_gap_seconds < 0
            or args.repair_attempts < 0
            or args.multimodal_retry_attempts < 0
            or args.candidate_plan_continuation_attempts < 0
        ):
            raise ValueError("재시도 횟수와 대기 시간은 0 이상이어야 합니다.")
        env_load, settings = _resolve_runtime_settings(root, args)
        if args.max_tokens_candidate_plan is None:
            args.max_tokens_candidate_plan = 8000 if args.provider in {"openai", "codex_cli"} else 32000
        if args.provider in {"openai", "codex_cli"}:
            validate_openai_capabilities(
                model=args.model,
                require_images=True,
                require_structured_outputs=True,
                image_detail=args.image_detail,
                reasoning_efforts=[
                    args.reasoning_effort_observation,
                    args.reasoning_effort_group_summary,
                    args.reasoning_effort_candidate_discovery,
                    args.reasoning_effort_candidate_plan,
                ],
            )
        defaults = provider_defaults(args.provider)
        api_key_env = args.api_key_env or defaults["api_key_env"]
        if env_load.path is not None:
            logger.info(
                "step04 dotenv loaded path=%s applied_variable_count=%s",
                env_load.relative_path(root),
                len(env_load.loaded_keys),
            )
        elif args.no_env_file:
            logger.info("step04 dotenv loading disabled")
        else:
            logger.info("step04 dotenv file not found; process environment and defaults only")
        logger.info(
            "step04 provider configuration provider=%s provider_source=%s model=%s model_source=%s "
            "image_detail=%s image_detail_source=%s api_key_configured=%s",
            args.provider,
            settings.provider.source,
            args.model,
            settings.model.source,
            args.image_detail,
            settings.image_detail.source,
            bool(os.getenv(api_key_env, "").strip()),
        )
        logger.info(
            "step04 reasoning configuration observation=%s(%s) group_summary=%s(%s) "
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
        request = load_json(step_dir(root, args.init, "step03") / "pass1_request.json")
        _validate_request_contract_files(root, request)
        model, provider_configuration = _provider_configuration(root, args)
        output = _provider_output_dir(root, args.init, args.provider)
        logger.info("step04 provider output directory=%s", rel(root, output))
        if args.candidate_id:
            if not args.resume or args.overwrite:
                raise ValueError("--candidate-id requires --resume and cannot be combined with --overwrite")
            discovery_checkpoint_path = output / "_checkpoint" / "candidate_discovery.json"
            if not discovery_checkpoint_path.is_file():
                raise RuntimeError("--candidate-id requires an existing candidate discovery checkpoint")
            discovery_checkpoint = load_json(discovery_checkpoint_path)
            discovery = discovery_checkpoint.get("result")
            if (
                discovery_checkpoint.get("stage") != "candidate_discovery"
                or discovery_checkpoint.get("completed") is not True
                or not isinstance(discovery, dict)
                or not _checkpoint_provider_configuration_matches(
                    discovery_checkpoint,
                    expected_configuration=provider_configuration,
                    expected_model=model,
                    stage="candidate_discovery",
                )
                or args.candidate_id not in {
                    str(item.get("candidate_id"))
                    for item in discovery.get("candidates", [])
                    if isinstance(item, dict)
                }
            ):
                raise RuntimeError(
                    "--candidate-id requires a reusable candidate discovery checkpoint for the selected provider and model"
                )
        if args.dry_run:
            print("AIFSHeavyRain Step04 Pass1 dry-run")
            print(f"  package_id: {request.get('package_id')}")
            print(f"  provider: {args.provider}")
            print(f"  model: {model}")
            print(f"  image_detail: {args.image_detail}")
            print(f"  provider_output: {rel(root, output)}")
            print(f"  batch_count: {request.get('batch_plan', {}).get('batch_count')}")
            return 0
        result_path = output / "pass1_result.json"
        completed_result_language_errors: list[str] = []
        if args.resume and result_path.is_file() and not args.overwrite and not args.candidate_id:
            result = load_json(result_path)
            completed_result_language_errors = engine.validate_step04_narrative_language(result)
            completed_result_matches = (
                result.get("package_id") == request.get("package_id")
                and _completed_result_checkpoint_matches(
                    output=output,
                    package_id=str(request["package_id"]),
                    provider_configuration=provider_configuration,
                    model=model,
                    prompt_sha256=str(request["prompt_hashes"]["candidate_discovery"]),
                    schema_sha256=str(request["prompt_hashes"]["candidate_discovery_schema"]),
                )
            )
            completed_anchor_contract_matches = _completed_system_anchor_contract_matches(
                output=output,
                result=result,
            )
            if (
                not completed_result_language_errors
                and completed_result_matches
                and completed_anchor_contract_matches
            ):
                logger.info("step04 완료 결과 재개 init=%s 후보수=%s", args.init, len(result.get("candidates", [])))
                _print_completion_summary(root=root, output=output, result=result)
                return 0
            if not completed_result_matches:
                logger.info("step04 completed result invalidated: package or provider configuration mismatch")
            logger.info(
                "step04 완료 결과 한글 서술 계약 재생성 init=%s 오류수=%s",
                args.init,
                len(completed_result_language_errors),
            )
        partial_resume = args.resume and output.exists() and not args.overwrite
        if not partial_resume:
            require_new_output(output, overwrite=args.overwrite)
        if args.overwrite and output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True, exist_ok=True)
        if partial_resume:
            logger.info("step04 불완전 결과 재개 init=%s", args.init)
        update_run_status(root=root, init=args.init, step="step04", status="running", summary={
            "provider": args.provider,
            "model": model,
            "output_dir": rel(root, output),
        })
        result = _run_engine(
            root=root,
            init=args.init,
            request=request,
            args=args,
            output=output,
            model=model,
            provider_configuration=provider_configuration,
            env_load=env_load,
        )
        mode = "provider_execution"
        if args.candidate_id:
            update_run_status(root=root, init=args.init, step="step04", status="running", summary={
                "package_id": result.get("package_id"),
                "candidate_plan_only": args.candidate_id,
                "request_count": len(result.get("analysis_data_requests", [])),
                "final_merge_provider_call_count": 0,
                "mode": mode,
                "provider": args.provider,
                "model": model,
                "output_dir": rel(root, output),
            })
            logger.info(
                "step04 candidate plan-only completed candidate_id=%s requests=%s mode=%s",
                args.candidate_id,
                len(result.get("analysis_data_requests", [])),
                mode,
            )
            return 0
        update_run_status(root=root, init=args.init, step="step04", status="completed", summary={
            "package_id": result.get("package_id"), "candidate_count": len(result.get("candidates", [])),
            "precursor_candidate_count": sum(
                1
                for item in result.get("candidates", [])
                if isinstance(item, dict)
                and str(item.get("detection_basis")) == "precursor_environment"
            ),
            "precursor_assessment_status": (
                result.get("precursor_candidate_assessment", {}).get("assessment_status")
                if isinstance(result.get("precursor_candidate_assessment"), dict)
                else None
            ),
            "request_count": len(result.get("analysis_data_requests", [])), "final_merge_provider_call_count": 0, "mode": mode,
            "provider": args.provider, "model": model, "output_dir": rel(root, output),
            "analysis_provenance_status": "available" if (output / PROVENANCE_FILENAME).is_file() else "unavailable",
        })
        failure_summary = output / "failure_summary.json"
        if failure_summary.is_file():
            failure_summary.unlink()
        logger.info("step04 완료 init=%s 후보수=%s 요청수=%s 모드=%s", args.init, len(result.get("candidates", [])), len(result.get("analysis_data_requests", [])), mode)
        _print_completion_summary(root=root, output=output, result=result)
        return 0
    except Exception as exc:  # noqa: BLE001
        safe_message = sanitize_failure_text(exc, root=root)
        incomplete_diagnostics = (
            dict(exc.diagnostics)
            if isinstance(getattr(exc, "diagnostics", None), dict)
            else None
        )
        update_run_status(root=root, init=args.init, step="step04", status="failed")
        write_failure_summary(
            root=root,
            init=args.init,
            step="step04",
            exc=exc,
            failed_candidate_id=_failed_candidate_id(exc),
            diagnostics=incomplete_diagnostics,
            output_dir=output,
        )
        if incomplete_diagnostics is not None:
            logger.error(
                "step04 incomplete diagnostics stage=%s candidate_id=%s diagnosis=%s "
                "output_tokens=%s reasoning_tokens=%s partial_output_present=%s",
                incomplete_diagnostics.get("stage"),
                incomplete_diagnostics.get("candidate_id"),
                incomplete_diagnostics.get("incomplete_diagnosis"),
                incomplete_diagnostics.get("output_tokens"),
                incomplete_diagnostics.get("reasoning_tokens"),
                incomplete_diagnostics.get("partial_output_present"),
            )
        logger.error("step04 실패 exception_type=%s message=%s", type(exc).__name__, safe_message)
        try:
            print(f"step04 실패: {safe_message}", file=sys.stderr)
        except OSError:
            logger.error("step04 실패 메시지를 표준 오류에 기록하지 못했습니다.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
