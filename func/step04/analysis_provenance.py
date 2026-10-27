"""Deterministic Step04 analysis provenance artifact.

The artifact preserves the validated Group Summary inputs actually used by
Candidate Discovery plus the minimum execution metadata needed for later
Analysis History / LLM Wiki comparison. It does not alter pass1_result.json,
does not call an AI provider, and is non-blocking for the operational Step04
result when invoked by the runner.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from func.shared.pass2_common import Pass2PipelineError, load_json_object, validate_schema
from func.shared.pipeline_common import now_utc, sha256sum, write_json


PROVENANCE_SCHEMA_NAME = "step04_analysis_provenance.schema.json"
PROVENANCE_SCHEMA_VERSION = "step04-analysis-provenance-v1"
PROVENANCE_FILENAME = "step04_analysis_provenance.json"


def _sorted_unique(values: Iterable[str]) -> list[str]:
    return sorted({str(value) for value in values if str(value)})


def _nullable_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _reasoning_effort(configuration: dict[str, Any], stage: str) -> str | None:
    efforts = configuration.get("reasoning_efforts")
    if not isinstance(efforts, dict):
        return None
    return _nullable_string(efforts.get(stage))


def _transport_schema_hash(configuration: dict[str, Any], stage: str) -> str | None:
    hashes = configuration.get("transport_schema_sha256")
    if not isinstance(hashes, dict):
        return None
    key = "batch_observation" if stage == "observation" else stage
    return _nullable_string(hashes.get(key))


def _stage_configuration(
    checkpoint: dict[str, Any],
    *,
    stage: str,
    prompt_key: str,
    schema_key: str,
) -> dict[str, Any]:
    configuration = checkpoint.get("provider_configuration")
    if not isinstance(configuration, dict):
        configuration = {}
    provider = checkpoint.get("provider") or configuration.get("provider")
    return {
        "provider": _nullable_string(provider),
        "model": _nullable_string(checkpoint.get("model")),
        "reasoning_effort": _reasoning_effort(configuration, stage),
        "image_detail": _nullable_string(configuration.get("image_detail")),
        "api_family": _nullable_string(configuration.get("api_family")),
        "endpoint_id": _nullable_string(configuration.get("endpoint_id")),
        "structured_output_mode": _nullable_string(configuration.get("structured_output_mode")),
        "prompt_sha256": _nullable_string(checkpoint.get(prompt_key)),
        "schema_sha256": _nullable_string(checkpoint.get(schema_key)),
        "transport_schema_sha256": _transport_schema_hash(configuration, stage),
    }


def _unique_configurations(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        key = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def _retry_summary(checkpoints: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "http_retry_count",
        "repair_attempt_count",
        "structured_output_fallback_count",
        "source_grounded_retry_attempt_count",
    )
    rows = [item.get("retry_summary") for item in checkpoints]
    available = [row for row in rows if isinstance(row, dict)]
    if not available:
        return {"availability": "unavailable", **{key: None for key in keys}}
    totals = {key: 0 for key in keys}
    for row in available:
        for key in keys:
            raw = row.get(key)
            if raw is None:
                continue
            try:
                totals[key] += max(0, int(raw))
            except (TypeError, ValueError):
                continue
    status = "available" if len(available) == len(checkpoints) else "partial"
    return {"availability": status, **totals}


def _stage_record(
    checkpoints: list[dict[str, Any]],
    *,
    stage: str,
    prompt_key: str,
    schema_key: str,
    fingerprint_keys: tuple[str, ...],
) -> dict[str, Any]:
    fingerprints: list[str] = []
    for checkpoint in checkpoints:
        for key in fingerprint_keys:
            value = checkpoint.get(key)
            if isinstance(value, str) and value:
                fingerprints.append(value)
                break
    configurations = _unique_configurations(
        _stage_configuration(checkpoint, stage=stage, prompt_key=prompt_key, schema_key=schema_key)
        for checkpoint in checkpoints
    )
    return {
        "checkpoint_count": len(checkpoints),
        "configurations": configurations,
        "input_fingerprints": _sorted_unique(fingerprints),
        "retry_summary": _retry_summary(checkpoints),
    }


def _load_json_files(paths: Iterable[Path]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for path in sorted(paths):
        if path.is_file():
            values.append(load_json_object(path, code="STEP04_PROVENANCE_SOURCE_INVALID"))
    return values


def _load_group_summaries(source: Path, package_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    directory = source / "group_summaries"
    summaries = _load_json_files(directory.glob("*.summary.json"))
    checkpoints = _load_json_files(directory.glob("*.checkpoint.json"))
    if not summaries:
        raise Pass2PipelineError(
            "STEP04_PROVENANCE_SOURCE_MISSING",
            "Validated Group Summary sources are unavailable for Step04 provenance.",
        )
    if len(summaries) != len(checkpoints):
        raise Pass2PipelineError(
            "STEP04_PROVENANCE_SOURCE_INCOMPLETE",
            f"Group Summary/checkpoint count mismatch: summaries={len(summaries)} checkpoints={len(checkpoints)}.",
        )
    summary_ids = [str(item.get("summary_id", "")) for item in summaries]
    if len(set(summary_ids)) != len(summary_ids) or any(not item for item in summary_ids):
        raise Pass2PipelineError("STEP04_PROVENANCE_SOURCE_INVALID", "Group Summary IDs are missing or duplicated.")
    if any(item.get("package_id") != package_id for item in summaries):
        raise Pass2PipelineError("STEP04_PROVENANCE_SOURCE_INVALID", "Group Summary package_id does not match Step03.")
    checkpoint_ids = {str(item.get("summary_id", "")) for item in checkpoints}
    if checkpoint_ids != set(summary_ids):
        raise Pass2PipelineError("STEP04_PROVENANCE_SOURCE_INVALID", "Group Summary checkpoint IDs do not match summaries.")
    return sorted(summaries, key=lambda item: str(item["summary_id"])), sorted(
        checkpoints, key=lambda item: str(item.get("summary_id", ""))
    )


def _candidate_evidence_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("candidate_id")): item
        for item in result.get("candidate_evidence", [])
        if isinstance(item, dict) and item.get("candidate_id")
    }


def _mandatory_direct_signal_audit(
    *,
    group_summaries: list[dict[str, Any]],
    request: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    attachments = {
        str(item.get("asset_id")): item
        for item in request.get("attachments", [])
        if isinstance(item, dict) and item.get("asset_id")
    }
    candidates = [item for item in result.get("candidates", []) if isinstance(item, dict)]
    evidence_by_candidate = _candidate_evidence_map(result)
    anchors: list[dict[str, Any]] = []

    for summary in group_summaries:
        audit = summary.get("direct_precipitation_audit")
        if not isinstance(audit, dict) or audit.get("status") != "present":
            continue
        significant_hours = sorted({int(value) for value in audit.get("significant_forecast_hours", [])})
        for asset_id in sorted({str(value) for value in audit.get("source_asset_ids", []) if str(value)}):
            source = attachments.get(asset_id)
            if not isinstance(source, dict) or str(source.get("product_id")) != "QL_PRECIP_6H":
                source_hours: set[int] = set()
                source_valid = False
            else:
                source_hours = {int(value) for value in source.get("forecast_hours", [])}
                source_valid = True
            for hour in significant_hours:
                if source_valid and hour not in source_hours:
                    continue
                covered_candidates: list[str] = []
                for candidate in candidates:
                    candidate_id = str(candidate.get("candidate_id", ""))
                    if not candidate_id:
                        continue
                    related_assets = {str(value) for value in candidate.get("related_overview_asset_ids", [])}
                    if asset_id not in related_assets:
                        continue
                    window = candidate.get("time_window") if isinstance(candidate.get("time_window"), dict) else {}
                    try:
                        start = int(window.get("forecast_hour_start"))
                        end = int(window.get("forecast_hour_end"))
                    except (TypeError, ValueError):
                        continue
                    if not (start <= hour <= end):
                        continue
                    evidence_hours = {
                        int(value)
                        for item in evidence_by_candidate.get(candidate_id, {}).get("evidence_items", [])
                        if isinstance(item, dict) and str(item.get("asset_id")) == asset_id
                        for value in item.get("forecast_hours", [])
                    }
                    if hour in evidence_hours:
                        covered_candidates.append(candidate_id)
                anchors.append({
                    "summary_id": str(summary.get("summary_id")),
                    "asset_id": asset_id,
                    "forecast_hour": hour,
                    "source_valid": source_valid,
                    "covered": bool(covered_candidates),
                    "covered_candidate_ids": sorted(set(covered_candidates)),
                })

    covered = sum(1 for item in anchors if item["covered"])
    missing = len(anchors) - covered
    return {
        "policy": "group_summary_present_direct_precipitation_requires_candidate_evidence_coverage",
        "required_anchor_count": len(anchors),
        "covered_anchor_count": covered,
        "missing_anchor_count": missing,
        "all_required_anchors_covered": missing == 0,
        "anchors": anchors,
    }


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_step04_analysis_provenance(
    *,
    root: Path,
    init: str,
    request: dict[str, Any],
    result: dict[str, Any],
    source: Path,
    output: Path,
) -> dict[str, Any]:
    """Build and validate the Step04 provenance document without writing it."""
    package_id = str(request.get("package_id", ""))
    if not package_id or result.get("package_id") != package_id:
        raise Pass2PipelineError("STEP04_PROVENANCE_SOURCE_INVALID", "Step03/Step04 package_id mismatch.")

    group_summaries, group_checkpoints = _load_group_summaries(source, package_id)
    observation_checkpoints = _load_json_files((output / "_checkpoint" / "observations").glob("*.json"))
    discovery_checkpoints = _load_json_files([output / "_checkpoint" / "candidate_discovery.json"])
    plan_checkpoints = _load_json_files((output / "_checkpoint" / "candidate_plans").glob("*.json"))

    if not observation_checkpoints or not discovery_checkpoints:
        raise Pass2PipelineError("STEP04_PROVENANCE_SOURCE_INCOMPLETE", "Observation or Candidate Discovery checkpoints are unavailable.")

    stages = {
        "observation": _stage_record(
            observation_checkpoints,
            stage="observation",
            prompt_key="prompt_sha256",
            schema_key="schema_sha256",
            fingerprint_keys=("input_fingerprint", "actual_ai_input_hash"),
        ),
        "group_summary": _stage_record(
            group_checkpoints,
            stage="group_summary",
            prompt_key="group_prompt_sha256",
            schema_key="group_schema_sha256",
            fingerprint_keys=("source_observation_fingerprint", "fingerprint"),
        ),
        "candidate_discovery": _stage_record(
            discovery_checkpoints,
            stage="candidate_discovery",
            prompt_key="prompt_sha256",
            schema_key="schema_sha256",
            fingerprint_keys=("input_fingerprint", "actual_ai_input_hash"),
        ),
        "candidate_plan": _stage_record(
            plan_checkpoints,
            stage="candidate_plan",
            prompt_key="prompt_sha256",
            schema_key="schema_sha256",
            fingerprint_keys=("input_fingerprint", "actual_ai_input_hash"),
        ),
    }

    document = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "init": init,
        "package_id": package_id,
        "generated_at_utc": now_utc(),
        "pass1_result_sha256": sha256sum(output / "pass1_result.json"),
        "group_summary_count": len(group_summaries),
        "group_summaries": group_summaries,
        "mandatory_direct_signal_audit": _mandatory_direct_signal_audit(
            group_summaries=group_summaries,
            request=request,
            result=result,
        ),
        "stage_provenance": stages,
        "fingerprints": {
            "group_summaries_sha256": _sha256_json(group_summaries),
            "observation_input_fingerprints_sha256": _sha256_json(stages["observation"]["input_fingerprints"]),
            "group_summary_input_fingerprints_sha256": _sha256_json(stages["group_summary"]["input_fingerprints"]),
            "candidate_discovery_input_fingerprint": (
                stages["candidate_discovery"]["input_fingerprints"][0]
                if stages["candidate_discovery"]["input_fingerprints"]
                else None
            ),
            "candidate_plan_input_fingerprints_sha256": _sha256_json(stages["candidate_plan"]["input_fingerprints"]),
        },
    }
    validate_schema(root, PROVENANCE_SCHEMA_NAME, document, code="STEP04_PROVENANCE_SCHEMA_INVALID")
    return document


def write_step04_analysis_provenance(
    *,
    root: Path,
    init: str,
    request: dict[str, Any],
    result: dict[str, Any],
    source: Path,
    output: Path,
) -> Path:
    """Write the validated deterministic provenance artifact and return its path."""
    document = build_step04_analysis_provenance(
        root=root,
        init=init,
        request=request,
        result=result,
        source=source,
        output=output,
    )
    path = output / PROVENANCE_FILENAME
    write_json(path, document)
    if sha256sum(output / "pass1_result.json") != document["pass1_result_sha256"]:
        path.unlink(missing_ok=True)
        raise Pass2PipelineError("STEP04_PROVENANCE_SOURCE_CHANGED", "pass1_result changed while provenance was being written.")
    return path
