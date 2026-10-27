"""Deterministic Analysis History v1 registration, comparison, and rendering.

Analysis History is a post-operational utility. It archives validated Step03/04/
07/08/09/10 JSON sources after Step10 completes, compares snapshots from the
same init without creating persistent Candidate identities, and embeds a collapsed
History comparison into the existing Step10 forecaster index when prior snapshots exist.
It does not call an AI provider and does not modify Step01--09 analysis results.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Iterable

from func.shared.pass2_common import Pass2PipelineError, load_json_object, validate_schema
from func.shared.pipeline_common import now_utc, sha256sum, write_json
from func.shared.runtime_paths import logical_ref, resolve_logical_ref, runtime_path
from func.step10.forecaster_product import step10_output_dir, validate_published_product
from func.step07.checks import MINORITY_REPORT_SCHEMA, step07_minority_report_path
from func.step04.analysis_provenance import PROVENANCE_FILENAME, PROVENANCE_SCHEMA_NAME


HISTORY_SCHEMA_NAME = "analysis_history_snapshot.schema.json"
HISTORY_SCHEMA_VERSION = "analysis-history-snapshot-v1"
HISTORY_VIEW_VERSION = "analysis-history-view-v1"
_VALID_PRODUCT_STATUSES = frozenset({"published", "no_surface_candidates"})
_ANALYSIS_ID_SAFE_TIME = re.compile(r"[^0-9TZ]")

_SOURCE_SPECS: tuple[tuple[str, str, str | None], ...] = (
    ("step03_pass1_request", "step03_pass1_request.json", None),
    ("step04_pass1_result", "step04_pass1_result.json", "ai_discovery_candidate_response.schema.json"),
    ("step07_pass2_check_results", "step07_pass2_check_results.json", "pass2_check_results.schema.json"),
    ("step08_pass2_result", "step08_pass2_result.json", "pass2_result.schema.json"),
    ("step09_operational_result", "step09_operational_result.json", "operational_result.schema.json"),
    ("step10_forecaster_product", "step10_forecaster_product.json", "forecaster_product.schema.json"),
)

_OPTIONAL_SOURCE_SPECS: tuple[tuple[str, str, str | None], ...] = (
    ("step04_analysis_provenance", PROVENANCE_FILENAME, PROVENANCE_SCHEMA_NAME),
    ("step07_llm_wiki_minority_report", "step07_llm_wiki_minority_report.json", MINORITY_REPORT_SCHEMA),
)

# Step10 is an archived publication snapshot, not part of meteorological analysis identity.
_ANALYSIS_STATE_ROLES = (
    "step03_pass1_request",
    "step04_pass1_result",
    "step07_pass2_check_results",
    "step07_llm_wiki_minority_report",
    "step08_pass2_result",
    "step09_operational_result",
)


def history_root(root: Path, init: str) -> Path:
    """Return the long-term History directory inside the permanent Step10 package."""
    return step10_output_dir(root, init) / "history"


def history_init_dir(root: Path, init: str) -> Path:
    """Backward-compatible alias for the per-init History directory."""
    return history_root(root, init)


def _legacy_history_init_dir(root: Path, init: str) -> Path:
    return root / "history" / init


def _migrate_legacy_history(root: Path, init: str) -> None:
    """Move snapshots from the earlier root/history layout into daou/<init>/history."""
    legacy = _legacy_history_init_dir(root, init)
    if not legacy.is_dir():
        return
    target = history_init_dir(root, init)
    target.mkdir(parents=True, exist_ok=True)
    for child in list(legacy.iterdir()):
        if child.name == "index.html" and child.is_file():
            child.unlink()
            continue
        if not child.is_dir() or not child.name.startswith("AN_"):
            continue
        destination = target / child.name
        if destination.exists():
            source_snapshot = child / "snapshot.json"
            destination_snapshot = destination / "snapshot.json"
            if not source_snapshot.is_file() or not destination_snapshot.is_file() or sha256sum(source_snapshot) != sha256sum(destination_snapshot):
                raise Pass2PipelineError("HISTORY_MIGRATION_CONFLICT", f"Legacy History snapshot conflicts with permanent package: {child.name}.")
            shutil.rmtree(child)
        else:
            os.replace(child, destination)
    try:
        legacy.rmdir()
    except OSError:
        pass
    try:
        legacy.parent.rmdir()
    except OSError:
        pass


def _step04_output_dir(root: Path, init: str) -> Path:
    """Resolve the completed Step04 provider output without assuming OpenAI."""
    status_path = runtime_path(root, "daio", init, "run_status.json")
    if status_path.is_file():
        try:
            status = load_json_object(status_path, code="HISTORY_OPTIONAL_SOURCE_INVALID")
            summary = status.get("steps", {}).get("step04", {}).get("summary", {})
            output_ref = summary.get("output_dir") if isinstance(summary, dict) else None
            if isinstance(output_ref, str) and output_ref.strip():
                candidate = resolve_logical_ref(root, output_ref)
                if (candidate / "pass1_result.json").is_file():
                    return candidate
        except (Pass2PipelineError, OSError, ValueError):
            pass

    step04_root = runtime_path(root, "daio", init, "step04")
    candidates = sorted(
        path.parent
        for path in step04_root.glob("*/pass1_result.json")
        if path.is_file()
    ) if step04_root.is_dir() else []
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise Pass2PipelineError("HISTORY_SOURCE_MISSING", "Unable to resolve completed Step04 provider output.")
    raise Pass2PipelineError(
        "HISTORY_SOURCE_AMBIGUOUS",
        "Multiple Step04 provider outputs exist and run_status.json does not identify the completed output_dir.",
    )


def _source_paths(root: Path, init: str) -> dict[str, Path]:
    step04_output = _step04_output_dir(root, init)
    return {
        "step03_pass1_request": runtime_path(root, "daio", init, "step03", "pass1_request.json"),
        "step04_pass1_result": step04_output / "pass1_result.json",
        "step04_analysis_provenance": step04_output / PROVENANCE_FILENAME,
        "step07_pass2_check_results": runtime_path(root, "daio", init, "step07", "pass2_check_results.json"),
        "step07_llm_wiki_minority_report": step07_minority_report_path(root, init),
        "step08_pass2_result": runtime_path(root, "daio", init, "step08", "pass2_result.json"),
        "step09_operational_result": runtime_path(root, "daio", init, "step09", "operational_result.json"),
        "step10_forecaster_product": step10_output_dir(root, init) / "forecaster_product.json",
    }


def _schema_version(value: dict[str, Any]) -> str | None:
    raw = value.get("schema_version")
    return raw if isinstance(raw, str) and raw else None


def _validate_step03(value: dict[str, Any], init: str) -> None:
    required = {
        "schema_version": str,
        "package_id": str,
        "init": str,
        "overview_data_sha256": str,
        "attachment_fingerprint": str,
        "prompt_hashes": dict,
    }
    for key, expected_type in required.items():
        if not isinstance(value.get(key), expected_type):
            raise Pass2PipelineError("HISTORY_SOURCE_INVALID", f"Step03 {key} is missing or invalid.")
    if value["init"] != init:
        raise Pass2PipelineError("HISTORY_SOURCE_INVALID", "Step03 init does not match History init.")


def _validate_sources(root: Path, init: str) -> dict[str, dict[str, Any]]:
    paths = _source_paths(root, init)
    values: dict[str, dict[str, Any]] = {}
    for role, _archive_name, schema_name in _SOURCE_SPECS:
        path = paths[role]
        if not path.is_file():
            raise Pass2PipelineError("HISTORY_SOURCE_MISSING", f"Required History source is missing: {role}.")
        value = load_json_object(path, code="HISTORY_SOURCE_INVALID")
        if role == "step03_pass1_request":
            _validate_step03(value, init)
        elif schema_name is not None:
            validate_schema(root, schema_name, value, code="HISTORY_SOURCE_INVALID")
        values[role] = value

    for role, _archive_name, schema_name in _OPTIONAL_SOURCE_SPECS:
        path = paths[role]
        if not path.is_file():
            continue
        value = load_json_object(path, code="HISTORY_OPTIONAL_SOURCE_INVALID")
        if schema_name is not None:
            validate_schema(root, schema_name, value, code="HISTORY_OPTIONAL_SOURCE_INVALID")
        if value.get("init") != init:
            raise Pass2PipelineError("HISTORY_OPTIONAL_SOURCE_INVALID", f"{role} init does not match History init.")
        values[role] = value

    step03 = values["step03_pass1_request"]
    step04 = values["step04_pass1_result"]
    for role in ("step07_pass2_check_results", "step08_pass2_result", "step09_operational_result", "step10_forecaster_product"):
        if values[role].get("init") != init:
            raise Pass2PipelineError("HISTORY_SOURCE_INVALID", f"{role} init does not match History init.")
    if step04.get("package_id") != step03.get("package_id"):
        raise Pass2PipelineError("HISTORY_SOURCE_INVALID", "Step03 package_id and Step04 package_id do not match.")
    step04_provenance = values.get("step04_analysis_provenance")
    if isinstance(step04_provenance, dict):
        if step04_provenance.get("package_id") != step03.get("package_id"):
            raise Pass2PipelineError("HISTORY_OPTIONAL_SOURCE_INVALID", "Step04 provenance package_id does not match Step03.")
        if step04_provenance.get("pass1_result_sha256") != sha256sum(paths["step04_pass1_result"]):
            raise Pass2PipelineError("HISTORY_OPTIONAL_SOURCE_INVALID", "Step04 provenance does not match current pass1_result.json.")

    # Step10 validation is intentionally performed from the permanent package itself.
    product = validate_published_product(root, step10_output_dir(root, init))
    if product.get("product_status") not in _VALID_PRODUCT_STATUSES:
        raise Pass2PipelineError(
            "HISTORY_REGISTRATION_GATE_FAILED",
            f"Step10 product_status is not a normal completed product: {product.get('product_status')!r}.",
        )

    # Prevent archiving a stale mixture of Step04/07/08/09 relative to Step10 provenance.
    expected = {
        item.get("role"): item.get("sha256")
        for item in product.get("provenance", {}).get("source_files", [])
        if isinstance(item, dict)
    }
    provenance_role_map = {
        "step04_pass1_result": "step04_pass1_result",
        "step07_check_results": "step07_pass2_check_results",
        "step07_minority_report": "step07_llm_wiki_minority_report",
        "step08_pass2_result": "step08_pass2_result",
        "step09_operational_result": "step09_operational_result",
    }
    for step10_role, history_role in provenance_role_map.items():
        expected_hash = expected.get(step10_role)
        if not isinstance(expected_hash, str) or expected_hash != sha256sum(paths[history_role]):
            raise Pass2PipelineError(
                "HISTORY_SOURCE_STALE",
                f"Step10 provenance does not match the current {history_role} source.",
            )
    return values


def _unique_dicts(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        cleaned = {key: row[key] for key in sorted(row) if row.get(key) is not None}
        key = json.dumps(cleaned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if key not in seen:
            seen.add(key)
            output.append(cleaned)
    return output


def _step04_execution_identity(root: Path, init: str, provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read Step04 execution settings, preferring the archived provenance contract."""
    if isinstance(provenance, dict):
        stage_rows: dict[str, Any] = {}
        missing_reasoning: list[str] = []
        stages = provenance.get("stage_provenance")
        if isinstance(stages, dict):
            for stage in ("observation", "group_summary", "candidate_discovery", "candidate_plan"):
                value = stages.get(stage)
                configurations = value.get("configurations") if isinstance(value, dict) else None
                configurations = configurations if isinstance(configurations, list) else []
                stage_rows[stage] = {"configurations": configurations}
                if not any(isinstance(row, dict) and row.get("reasoning_effort") is not None for row in configurations):
                    missing_reasoning.append(stage)
        return {
            "provenance_scope": "complete" if not missing_reasoning else "partial",
            "provenance_source": "step04_analysis_provenance",
            "stages": stage_rows,
            "missing_reasoning_stages": missing_reasoning,
        }

    identity: dict[str, Any] = {}
    status_path = runtime_path(root, "daio", init, "run_status.json")
    if status_path.is_file():
        try:
            status = load_json_object(status_path, code="HISTORY_OPTIONAL_SOURCE_INVALID")
            summary = status.get("steps", {}).get("step04", {}).get("summary", {})
            if isinstance(summary, dict):
                for key in ("provider", "model", "mode"):
                    if summary.get(key) is not None:
                        identity[key] = summary.get(key)
        except Pass2PipelineError:
            pass
    checkpoint = _step04_output_dir(root, init) / "_checkpoint" / "candidate_discovery.json"
    if checkpoint.is_file():
        try:
            value = load_json_object(checkpoint, code="HISTORY_OPTIONAL_SOURCE_INVALID")
            for src_key, dst_key in (
                ("prompt_sha256", "candidate_discovery_prompt_sha256"),
                ("schema_sha256", "candidate_discovery_schema_sha256"),
                ("model", "model"),
                ("provider", "provider"),
            ):
                if value.get(src_key) is not None:
                    identity[dst_key] = value.get(src_key)
            config = value.get("provider_configuration")
            if isinstance(config, dict):
                for key in ("image_detail", "structured_output_mode", "transport_schema_sha256"):
                    if config.get(key) is not None:
                        identity[key] = config.get(key)
                efforts = config.get("reasoning_efforts")
                if isinstance(efforts, dict):
                    identity["reasoning_efforts"] = efforts
        except Pass2PipelineError:
            pass
    efforts = identity.get("reasoning_efforts") if isinstance(identity.get("reasoning_efforts"), dict) else {}
    expected_reasoning_stages = {"observation", "group_summary", "candidate_discovery", "candidate_plan"}
    known_reasoning_stages = {str(key) for key in efforts}
    missing_reasoning_stages = sorted(expected_reasoning_stages - known_reasoning_stages)
    identity["provenance_scope"] = "complete" if not missing_reasoning_stages else "partial"
    identity["missing_reasoning_stages"] = missing_reasoning_stages
    return identity


def _step07_execution_identity(step07: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in step07.get("results", []):
        if not isinstance(result, dict):
            continue
        meta = result.get("provider_metadata")
        if isinstance(meta, dict):
            row = {
                key: meta.get(key)
                for key in ("provider", "model", "reasoning_effort", "image_detail", "prompt_sha256", "schema_sha256")
            }
            consensus = result.get("ensemble_consensus") if isinstance(result.get("ensemble_consensus"), dict) else {}
            row["ensemble_contract_version"] = consensus.get("contract_version")
            row["ensemble_member_count"] = consensus.get("member_count")
            rows.append(row)
    return _unique_dicts(rows)


def _step08_execution_identity(step08: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in step08.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        meta = candidate.get("provenance")
        if isinstance(meta, dict):
            rows.append({
                "provider": meta.get("provider"),
                "model": meta.get("model"),
                "reasoning_effort": meta.get("reasoning_effort"),
                "image_detail": meta.get("image_detail"),
                "prompt_sha256": meta.get("synthesis_prompt_sha256"),
                "schema_sha256": meta.get("synthesis_schema_sha256"),
            })
    return _unique_dicts(rows)


def _execution_identity(root: Path, init: str, values: dict[str, dict[str, Any]]) -> dict[str, Any]:
    step03 = values["step03_pass1_request"]
    step10 = values["step10_forecaster_product"]
    provenance = step10.get("provenance") if isinstance(step10.get("provenance"), dict) else {}
    return {
        "step03": {
            "package_id": step03.get("package_id"),
            "overview_data_sha256": step03.get("overview_data_sha256"),
            "attachment_fingerprint": step03.get("attachment_fingerprint"),
            "prompt_hashes": step03.get("prompt_hashes", {}),
        },
        "step04": _step04_execution_identity(root, init, values.get("step04_analysis_provenance")),
        "step07": _step07_execution_identity(values["step07_pass2_check_results"]),
        "step08": _step08_execution_identity(values["step08_pass2_result"]),
        "step10": {
            "generator_version": provenance.get("generator_version"),
            "schema_sha256": provenance.get("schema_sha256"),
            "template_sha256": provenance.get("template_sha256"),
        },
    }


def _safe_analysis_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        parsed = parsed.astimezone(timezone.utc)
        return parsed.strftime("%Y%m%dT%H%M%SZ")
    except ValueError:
        cleaned = _ANALYSIS_ID_SAFE_TIME.sub("", value.upper())
        return cleaned[:16] or "UNKNOWN"


def _digest_hashes(source_hashes: dict[str, str]) -> str:
    payload = json.dumps(source_hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _analysis_state_hashes(source_hashes: dict[str, str]) -> dict[str, str]:
    return {role: source_hashes[role] for role in _ANALYSIS_STATE_ROLES if role in source_hashes}


def _analysis_state_digest(source_hashes: dict[str, str]) -> str:
    hashes = _analysis_state_hashes(source_hashes)
    if set(hashes) != set(_ANALYSIS_STATE_ROLES):
        missing = sorted(set(_ANALYSIS_STATE_ROLES) - set(hashes))
        raise Pass2PipelineError("HISTORY_SOURCE_INVALID", f"Analysis-state hashes are incomplete: {', '.join(missing)}.")
    return _digest_hashes(hashes)


def _analysis_id(init: str, generated_at_utc: str, analysis_state_sha256: str) -> str:
    return f"AN_{init}_{_safe_analysis_time(generated_at_utc)}_{analysis_state_sha256[:12].upper()}"


def _snapshot_analysis_state_digest(snapshot: dict[str, Any]) -> str | None:
    explicit = snapshot.get("analysis_state_sha256")
    if isinstance(explicit, str) and re.fullmatch(r"[0-9a-f]{64}", explicit):
        return explicit
    hashes = {
        str(item.get("role")): str(item.get("sha256"))
        for item in snapshot.get("source_files", [])
        if isinstance(item, dict) and item.get("role") in _ANALYSIS_STATE_ROLES and isinstance(item.get("sha256"), str)
    }
    if set(hashes) != set(_ANALYSIS_STATE_ROLES):
        return None
    return _digest_hashes(hashes)


def _source_records(root: Path, init: str, values: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    paths = _source_paths(root, init)
    records: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    for role, archive_name, _schema_name in (*_SOURCE_SPECS, *_OPTIONAL_SOURCE_SPECS):
        path = paths[role]
        if role not in values or not path.is_file():
            continue
        digest = sha256sum(path)
        hashes[role] = digest
        records.append({
            "role": role,
            "archived_name": archive_name,
            "source_ref": logical_ref(root, path),
            "source_schema_version": _schema_version(values[role]),
            "sha256": digest,
        })
    return records, hashes


def _snapshot_matches_sources(snapshot: dict[str, Any], source_hashes: dict[str, str]) -> bool:
    archived = {
        item.get("role"): item.get("sha256")
        for item in snapshot.get("source_files", [])
        if isinstance(item, dict)
    }
    return archived == source_hashes


def _write_snapshot_atomic(root: Path, target: Path, snapshot: dict[str, Any]) -> None:
    validate_schema(root, HISTORY_SCHEMA_NAME, snapshot, code="HISTORY_SNAPSHOT_SCHEMA_INVALID")
    write_json(target / "snapshot.json", snapshot)


def register_analysis_history(root: Path, init: str) -> tuple[dict[str, Any], str]:
    """Register a completed Step10 analysis as an immutable History source bundle.

    Returns ``(snapshot, status)`` where status is ``created`` or ``reused``.
    """
    _migrate_legacy_history(root, init)
    values = _validate_sources(root, init)
    source_records, source_hashes = _source_records(root, init, values)
    product = values["step10_forecaster_product"]
    generated_at = product.get("generated_at_utc")
    if not isinstance(generated_at, str) or not generated_at:
        raise Pass2PipelineError("HISTORY_SOURCE_INVALID", "Step10 generated_at_utc is missing.")

    analysis_state_sha256 = _analysis_state_digest(source_hashes)
    # A Step10-only re-render is publication work, not a new meteorological analysis.
    # Reuse any immutable snapshot whose Step03/04/07/08/09 state is identical.
    for existing in list_history_snapshots(root, init):
        if _snapshot_analysis_state_digest(existing) == analysis_state_sha256:
            return existing, "reused"

    source_digest = _digest_hashes(source_hashes)
    analysis_id = _analysis_id(init, generated_at, analysis_state_sha256)
    target = history_init_dir(root, init) / analysis_id

    snapshot = {
        "history_schema_version": HISTORY_SCHEMA_VERSION,
        "analysis_id": analysis_id,
        "init": init,
        "analysis_generated_at_utc": generated_at,
        "registered_at_utc": now_utc(),
        "analysis_state_sha256": analysis_state_sha256,
        "source_digest_sha256": source_digest,
        "product_status": product.get("product_status"),
        "summary": {
            "candidate_count": product.get("summary", {}).get("candidate_count"),
            "surfaced_candidate_count": product.get("summary", {}).get("surfaced_candidate_count"),
            "non_surfaced_candidate_count": product.get("summary", {}).get("non_surfaced_candidate_count"),
        },
        "source_files": source_records,
        "execution_identity": _execution_identity(root, init, values),
        "registration_status": "complete",
    }

    if target.exists():
        snapshot_path = target / "snapshot.json"
        if not snapshot_path.is_file():
            raise Pass2PipelineError("HISTORY_IMMUTABILITY_CONFLICT", f"History directory exists without snapshot.json: {analysis_id}.")
        existing = load_json_object(snapshot_path, code="HISTORY_SNAPSHOT_INVALID")
        validate_schema(root, HISTORY_SCHEMA_NAME, existing, code="HISTORY_SNAPSHOT_SCHEMA_INVALID")
        if not _snapshot_matches_sources(existing, source_hashes):
            raise Pass2PipelineError("HISTORY_IMMUTABILITY_CONFLICT", f"Existing History snapshot has different source hashes: {analysis_id}.")
        _validate_archived_bundle(root, target, existing)
        return existing, "reused"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(exist_ok=False)
    try:
        paths = _source_paths(root, init)
        for record in source_records:
            shutil.copy2(paths[record["role"]], target / record["archived_name"])
        _write_snapshot_atomic(root, target, snapshot)
        _validate_archived_bundle(root, target, snapshot)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return snapshot, "created"


def _validate_archived_bundle(root: Path, snapshot_dir: Path, snapshot: dict[str, Any]) -> None:
    validate_schema(root, HISTORY_SCHEMA_NAME, snapshot, code="HISTORY_SNAPSHOT_SCHEMA_INVALID")
    for record in snapshot.get("source_files", []):
        if not isinstance(record, dict):
            raise Pass2PipelineError("HISTORY_SNAPSHOT_INVALID", "History source record is invalid.")
        path = snapshot_dir / str(record.get("archived_name", ""))
        if not path.is_file() or sha256sum(path) != record.get("sha256"):
            raise Pass2PipelineError("HISTORY_ARCHIVE_HASH_INVALID", f"Archived source is missing or changed: {path.name}.")
        load_json_object(path, code="HISTORY_ARCHIVE_INVALID")


def _snapshot_sort_key(snapshot: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(snapshot.get("analysis_generated_at_utc", "")),
        str(snapshot.get("registered_at_utc", "")),
        str(snapshot.get("analysis_id", "")),
    )


def list_history_snapshots(root: Path, init: str) -> list[dict[str, Any]]:
    directory = history_init_dir(root, init)
    if not directory.is_dir():
        return []
    snapshots: list[dict[str, Any]] = []
    for child in directory.iterdir():
        if not child.is_dir() or not child.name.startswith("AN_"):
            continue
        path = child / "snapshot.json"
        if not path.is_file():
            continue
        try:
            snapshot = load_json_object(path, code="HISTORY_SNAPSHOT_INVALID")
            validate_schema(root, HISTORY_SCHEMA_NAME, snapshot, code="HISTORY_SNAPSHOT_SCHEMA_INVALID")
            _validate_archived_bundle(root, child, snapshot)
        except Pass2PipelineError:
            continue
        snapshots.append(snapshot)
    return sorted(snapshots, key=_snapshot_sort_key)


def _snapshot_by_id(root: Path, init: str, analysis_id: str) -> dict[str, Any]:
    path = history_init_dir(root, init) / analysis_id / "snapshot.json"
    if not path.is_file():
        raise Pass2PipelineError("HISTORY_SNAPSHOT_NOT_FOUND", f"History snapshot not found: {analysis_id}.")
    snapshot = load_json_object(path, code="HISTORY_SNAPSHOT_INVALID")
    validate_schema(root, HISTORY_SCHEMA_NAME, snapshot, code="HISTORY_SNAPSHOT_SCHEMA_INVALID")
    _validate_archived_bundle(root, path.parent, snapshot)
    return snapshot


def _load_archived_role(root: Path, snapshot: dict[str, Any], role: str) -> dict[str, Any]:
    records = {
        item.get("role"): item
        for item in snapshot.get("source_files", [])
        if isinstance(item, dict)
    }
    record = records.get(role)
    if not isinstance(record, dict):
        raise Pass2PipelineError("HISTORY_SNAPSHOT_INVALID", f"Snapshot omits source role: {role}.")
    path = history_init_dir(root, str(snapshot["init"])) / str(snapshot["analysis_id"]) / str(record["archived_name"])
    return load_json_object(path, code="HISTORY_ARCHIVE_INVALID")


def _load_archived_role_optional(root: Path, snapshot: dict[str, Any], role: str) -> dict[str, Any] | None:
    records = {
        item.get("role"): item
        for item in snapshot.get("source_files", [])
        if isinstance(item, dict)
    }
    record = records.get(role)
    if not isinstance(record, dict):
        return None
    path = history_init_dir(root, str(snapshot["init"])) / str(snapshot["analysis_id"]) / str(record["archived_name"])
    return load_json_object(path, code="HISTORY_ARCHIVE_INVALID")


def _mandatory_direct_summary(provenance: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(provenance, dict):
        return {"availability": "legacy_unavailable"}
    audit = provenance.get("mandatory_direct_signal_audit")
    if not isinstance(audit, dict):
        return {"availability": "legacy_unavailable"}
    return {
        "availability": "available",
        "required_anchor_count": audit.get("required_anchor_count"),
        "covered_anchor_count": audit.get("covered_anchor_count"),
        "missing_anchor_count": audit.get("missing_anchor_count"),
        "all_required_anchors_covered": audit.get("all_required_anchors_covered"),
        "anchors": audit.get("anchors", []),
    }


def _mandatory_direct_comparison(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    before_summary = _mandatory_direct_summary(before)
    after_summary = _mandatory_direct_summary(after)
    available_count = sum(
        1 for item in (before_summary, after_summary) if item.get("availability") == "available"
    )
    if available_count == 0:
        return {
            "availability": "not_available_in_archived_operational_sources",
            "reason": (
                "Legacy snapshots do not archive Step04 Group Summary direct-precipitation anchors. "
                "History does not infer mandatory anchors from Candidate output or repeatability artifacts."
            ),
        }
    return {
        "availability": "available" if available_count == 2 else "partial",
        "before": before_summary,
        "after": after_summary,
    }


def _schema_version_map(snapshot: dict[str, Any]) -> dict[str, Any]:
    # Comparability concerns the analysis pipeline, not Step10 presentation schema.
    return {
        str(item.get("role")): item.get("source_schema_version")
        for item in snapshot.get("source_files", [])
        if isinstance(item, dict) and item.get("role") in _ANALYSIS_STATE_ROLES
    }


def _comparability(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    reasons: list[dict[str, Any]] = []
    if left.get("init") != right.get("init"):
        return {"status": "not_comparable", "reasons": [{"field": "init", "left": left.get("init"), "right": right.get("init")}]}

    left_exec = left.get("execution_identity", {}) if isinstance(left.get("execution_identity"), dict) else {}
    right_exec = right.get("execution_identity", {}) if isinstance(right.get("execution_identity"), dict) else {}

    def changed(field: str, lval: Any, rval: Any, category: str) -> None:
        if lval != rval:
            reasons.append({"field": field, "category": category, "left": lval, "right": rval})

    l3 = left_exec.get("step03", {}) if isinstance(left_exec.get("step03"), dict) else {}
    r3 = right_exec.get("step03", {}) if isinstance(right_exec.get("step03"), dict) else {}
    changed("step03.overview_data_sha256", l3.get("overview_data_sha256"), r3.get("overview_data_sha256"), "input")
    changed("step03.attachment_fingerprint", l3.get("attachment_fingerprint"), r3.get("attachment_fingerprint"), "input")
    changed("step03.prompt_hashes", l3.get("prompt_hashes"), r3.get("prompt_hashes"), "contract")
    changed("step04.execution_identity", left_exec.get("step04"), right_exec.get("step04"), "ai_configuration")
    changed("step07.execution_identity", left_exec.get("step07"), right_exec.get("step07"), "ai_configuration")
    changed("step08.execution_identity", left_exec.get("step08"), right_exec.get("step08"), "ai_configuration")
    changed("source_schema_versions", _schema_version_map(left), _schema_version_map(right), "schema")

    left_step04 = left_exec.get("step04") if isinstance(left_exec.get("step04"), dict) else {}
    right_step04 = right_exec.get("step04") if isinstance(right_exec.get("step04"), dict) else {}
    if left_step04.get("provenance_scope") != "complete" or right_step04.get("provenance_scope") != "complete":
        reasons.append({
            "field": "step04.provenance_scope",
            "category": "provenance_gap",
            "left": left_step04.get("provenance_scope", "unavailable"),
            "right": right_step04.get("provenance_scope", "unavailable"),
        })

    schema_changed = any(item.get("category") == "schema" for item in reasons)
    provenance_gap = any(item.get("category") == "provenance_gap" for item in reasons)
    condition_changed = any(item.get("category") in {"input", "contract", "ai_configuration"} for item in reasons)
    if schema_changed or provenance_gap:
        status = "partially_comparable"
    elif condition_changed:
        status = "condition_changed"
    else:
        status = "same_condition"
    return {"status": status, "reasons": reasons}


def _counter(values: Iterable[Any]) -> dict[str, int]:
    counts = Counter(str(value) for value in values if value is not None)
    return dict(sorted(counts.items()))


def _precursor_summary(step04: dict[str, Any]) -> dict[str, Any]:
    opportunities = [item for item in step04.get("precursor_opportunities", []) if isinstance(item, dict)]
    if not isinstance(step04.get("precursor_opportunities"), list):
        return {"availability": "legacy_unavailable", "opportunity_count": None}
    return {
        "availability": "available",
        "opportunity_count": len(opportunities),
        "qualification": _counter(item.get("qualification") for item in opportunities),
        "relation_to_direct": _counter(item.get("relation_to_direct") for item in opportunities),
        "candidate_creation": _counter(item.get("candidate_creation") for item in opportunities),
        "support_categories": _counter(category for item in opportunities for category in item.get("support_categories", []) if isinstance(category, str)),
        "representative_forecast_hours": sorted({hour for item in opportunities for hour in item.get("representative_forecast_hours", []) if isinstance(hour, int) and not isinstance(hour, bool)}),
    }


def _candidate_rows(step04: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in step04.get("candidates", []):
        if not isinstance(item, dict):
            continue
        rows.append({
            "candidate_id": item.get("candidate_id"),
            "candidate_name": item.get("candidate_name"),
            "time_window": item.get("time_window"),
            "candidate_area": item.get("candidate_area"),
            "peak_forecast_hours": item.get("peak_forecast_hours"),
            "detection_basis": item.get("detection_basis"),
            "precipitation_signal_status": item.get("precipitation_signal_status"),
            "confidence": item.get("confidence"),
        })
    return rows


def _candidate_summary(step04: dict[str, Any]) -> dict[str, Any]:
    rows = _candidate_rows(step04)
    return {
        "candidate_count": len(rows),
        "detection_basis": _counter(row.get("detection_basis") for row in rows),
        "precipitation_signal_status": _counter(row.get("precipitation_signal_status") for row in rows),
        "candidates": rows,
    }


def _step07_summary(step07: dict[str, Any]) -> dict[str, Any]:
    results = [item for item in step07.get("results", []) if isinstance(item, dict)]
    return {
        "check_count": len(results),
        "assessment": _counter(item.get("assessment") for item in results),
        "evidence_status": _counter(item.get("evidence_status") for item in results),
        "evidence_complete": _counter(str(bool(item.get("evidence_complete"))).lower() for item in results),
    }


def _step08_summary(step08: dict[str, Any]) -> dict[str, Any]:
    candidates = [item for item in step08.get("candidates", []) if isinstance(item, dict)]
    rows = []
    for item in candidates:
        signal = item.get("signal_assessment") if isinstance(item.get("signal_assessment"), dict) else {}
        rows.append({
            "candidate_id": item.get("candidate_id"),
            "signal_class": signal.get("signal_class"),
            "validated_precursor_evidence_count": len(signal.get("validated_precursor_evidence", [])) if isinstance(signal.get("validated_precursor_evidence"), list) else None,
            "validated_spatiotemporal_alignment": signal.get("validated_spatiotemporal_alignment"),
            "final_assessment": item.get("final_assessment"),
            "uncertainties": item.get("uncertainties", []),
            "forecaster_question_answers": item.get("forecaster_question_answers", []),
        })
    return {
        "candidate_count": len(rows),
        "signal_class": _counter(row.get("signal_class") for row in rows),
        "candidates": rows,
    }


def _step09_summary(step09: dict[str, Any]) -> dict[str, Any]:
    candidates = [item for item in step09.get("candidates", []) if isinstance(item, dict)]
    return {
        "candidate_count": len(candidates),
        "signal_class": _counter(item.get("signal_class") for item in candidates),
        "validation_status": _counter(item.get("validation_status") for item in candidates),
        "should_surface": _counter(str(bool(item.get("should_surface"))).lower() for item in candidates),
        "candidates": [
            {
                "candidate_id": item.get("candidate_id"),
                "signal_class": item.get("signal_class"),
                "validation_status": item.get("validation_status"),
                "should_surface": item.get("should_surface"),
                "validation_reason": item.get("validation_reason"),
            }
            for item in candidates
        ],
    }


def _step10_summary(step10: dict[str, Any]) -> dict[str, Any]:
    summary = step10.get("summary") if isinstance(step10.get("summary"), dict) else {}
    return {
        "product_status": step10.get("product_status"),
        "candidate_count": summary.get("candidate_count"),
        "surfaced_candidate_count": summary.get("surfaced_candidate_count"),
        "non_surfaced_candidate_count": summary.get("non_surfaced_candidate_count"),
        "surfaced_candidate_ids": [item.get("candidate_id") for item in step10.get("surfaced_candidates", []) if isinstance(item, dict)],
    }


def compare_history_snapshots(root: Path, init: str, *, before_id: str | None = None, after_id: str | None = None) -> dict[str, Any]:
    snapshots = list_history_snapshots(root, init)
    if before_id is None and after_id is None:
        if len(snapshots) < 2:
            raise Pass2PipelineError("HISTORY_COMPARISON_UNAVAILABLE", "At least two History snapshots are required for comparison.")
        before, after = snapshots[-2], snapshots[-1]
    else:
        if before_id is None or after_id is None:
            raise Pass2PipelineError("HISTORY_COMPARISON_INVALID", "Both before_id and after_id are required when either is specified.")
        before, after = _snapshot_by_id(root, init, before_id), _snapshot_by_id(root, init, after_id)

    before_sources = {role: _load_archived_role(root, before, role) for role, _name, _schema in _SOURCE_SPECS}
    after_sources = {role: _load_archived_role(root, after, role) for role, _name, _schema in _SOURCE_SPECS}
    before_step04_provenance = _load_archived_role_optional(root, before, "step04_analysis_provenance")
    after_step04_provenance = _load_archived_role_optional(root, after, "step04_analysis_provenance")

    return {
        "comparison_schema_version": "analysis-history-comparison-v1",
        "init": init,
        "before_analysis_id": before["analysis_id"],
        "after_analysis_id": after["analysis_id"],
        "before_generated_at_utc": before["analysis_generated_at_utc"],
        "after_generated_at_utc": after["analysis_generated_at_utc"],
        "comparability": _comparability(before, after),
        "mandatory_direct_signal": _mandatory_direct_comparison(
            before_step04_provenance, after_step04_provenance
        ),
        "step04": {
            "before": _candidate_summary(before_sources["step04_pass1_result"]),
            "after": _candidate_summary(after_sources["step04_pass1_result"]),
            "precursor_before": _precursor_summary(before_sources["step04_pass1_result"]),
            "precursor_after": _precursor_summary(after_sources["step04_pass1_result"]),
        },
        "step07": {
            "before": _step07_summary(before_sources["step07_pass2_check_results"]),
            "after": _step07_summary(after_sources["step07_pass2_check_results"]),
        },
        "step08": {
            "before": _step08_summary(before_sources["step08_pass2_result"]),
            "after": _step08_summary(after_sources["step08_pass2_result"]),
        },
        "step09": {
            "before": _step09_summary(before_sources["step09_operational_result"]),
            "after": _step09_summary(after_sources["step09_operational_result"]),
        },
        "step10": {
            "before": _step10_summary(before_sources["step10_forecaster_product"]),
            "after": _step10_summary(after_sources["step10_forecaster_product"]),
        },
    }


def _counter_text(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "-"
    return ", ".join(f"{escape(str(key))} {escape(str(count))}" for key, count in value.items())


def _candidate_table(rows: Any) -> str:
    if not isinstance(rows, list) or not rows:
        return '<div class="history-empty">후보 없음</div>'
    body = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        window = row.get("time_window") if isinstance(row.get("time_window"), dict) else {}
        area = row.get("candidate_area") if isinstance(row.get("candidate_area"), dict) else {}
        time_text = (
            f"F{int(window.get('forecast_hour_start', 0)):03d}–F{int(window.get('forecast_hour_end', 0)):03d}"
            if isinstance(window.get("forecast_hour_start"), int) and isinstance(window.get("forecast_hour_end"), int)
            else "-"
        )
        area_text = "-"
        if all(isinstance(area.get(key), (int, float)) for key in ("west", "east", "south", "north")):
            area_text = f"{area['west']:.1f}–{area['east']:.1f}E / {area['south']:.1f}–{area['north']:.1f}N"
        body.append(
            "<tr>"
            f"<td>{escape(str(row.get('candidate_id', '-')))}</td>"
            f"<td>{escape(str(row.get('candidate_name', '-')))}</td>"
            f"<td>{escape(time_text)}</td>"
            f"<td>{escape(area_text)}</td>"
            f"<td>{escape(str(row.get('detection_basis', '-')))}</td>"
            "</tr>"
        )
    return (
        '<div class="history-table-wrap"><table class="history-table"><thead><tr>'
        '<th>Local ID</th><th>후보</th><th>시간</th><th>영역</th><th>Detection</th>'
        f"</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )


def _assessment_rows(rows: Any) -> str:
    if not isinstance(rows, list) or not rows:
        return '<div class="history-empty">정보 없음</div>'
    cards = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        uncertainties = row.get("uncertainties") if isinstance(row.get("uncertainties"), list) else []
        uncertainty_html = "".join(f"<li>{escape(str(item))}</li>" for item in uncertainties) or "<li>-</li>"
        cards.append(
            '<article class="history-assessment">'
            f"<div><strong>{escape(str(row.get('candidate_id', '-')))}</strong> · {escape(str(row.get('signal_class', '-')))}</div>"
            f"<p>{escape(str(row.get('final_assessment', '-')))}</p>"
            f"<details><summary>불확실성</summary><ul>{uncertainty_html}</ul></details>"
            "</article>"
        )
    return "".join(cards)


_HISTORY_STYLE_START = "<!-- ANALYSIS_HISTORY_STYLE_START -->"
_HISTORY_STYLE_END = "<!-- ANALYSIS_HISTORY_STYLE_END -->"
_HISTORY_SECTION_START = "<!-- ANALYSIS_HISTORY_SECTION_START -->"
_HISTORY_SECTION_END = "<!-- ANALYSIS_HISTORY_SECTION_END -->"


def _history_section_css() -> str:
    return """
.analysis-history{margin-top:26px;background:#fff;border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);overflow:hidden}
.analysis-history>summary{display:flex;justify-content:space-between;gap:14px;align-items:center;padding:16px 18px;font-weight:850;background:#f8fafb;cursor:pointer}
.analysis-history>summary small{font-size:.74rem;color:var(--muted);font-weight:650}.history-body{padding:18px}.history-note{font-size:.78rem;color:var(--muted);margin:0 0 12px}.history-status{border-left:4px solid #347a45;background:#eef8f0;border-radius:8px;padding:10px 12px;margin-bottom:14px}.history-status.warn{border-left-color:#9a6b00;background:#fff8e6}.history-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:10px 0 16px}.history-card{border:1px solid var(--line);border-radius:10px;padding:13px;background:#fbfcfd;min-width:0}.history-card h4{margin:0 0 8px}.history-metric{display:flex;justify-content:space-between;gap:12px;padding:5px 0;border-bottom:1px solid #edf0f3;font-size:.8rem}.history-metric:last-child{border-bottom:0}.history-subsection{margin-top:18px}.history-subsection>h3{font-size:.95rem;margin:0 0 8px}.history-table-wrap{overflow-x:auto}.history-table{width:100%;border-collapse:collapse;font-size:.75rem}.history-table th,.history-table td{padding:7px 8px;border:1px solid #e1e5ea;text-align:left;vertical-align:top}.history-table th{background:#f1f5f7}.history-assessment{border:1px solid #e1e5ea;border-radius:9px;padding:10px;margin:7px 0;background:#fff;font-size:.8rem}.history-assessment p{margin:.3rem 0}.history-empty{color:var(--muted);font-size:.8rem;padding:6px 0}.history-compare-time{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}.history-compare-time span{font-size:.72rem;padding:4px 7px;border:1px solid var(--line);border-radius:999px;background:#fff}
.history-wiki{margin:16px 0 18px;border:1px solid #cfd8e3;border-radius:11px;background:#f7f9fc;padding:14px}.history-wiki h3{margin:0 0 5px;font-size:.96rem}.history-wiki .wiki-label{display:inline-block;font-size:.67rem;font-weight:800;border:1px solid #aab8c8;border-radius:999px;padding:2px 7px;margin-left:7px;background:#fff;color:#405267}.history-wiki-note{font-size:.75rem;color:var(--muted);margin:0 0 10px}.history-wiki-summary{font-size:.84rem;line-height:1.55;margin:8px 0 12px}.history-wiki-block{border-top:1px solid #dde4ec;padding-top:9px;margin-top:9px}.history-wiki-block h4{font-size:.8rem;margin:0 0 6px}.history-wiki-list{margin:0;padding-left:18px;font-size:.79rem}.history-wiki-list li{margin:5px 0;line-height:1.45}.history-wiki-ref{font-size:.66rem;color:var(--muted);margin-left:5px}.history-wiki-limit{background:#fff8e6;border-radius:7px;padding:8px 10px}
@media(max-width:720px){.history-grid{grid-template-columns:1fr}.analysis-history>summary{align-items:flex-start;flex-direction:column}.history-table{min-width:640px}}
"""


def _history_metric(label: str, value: Any) -> str:
    return f'<div class="history-metric"><span>{escape(label)}</span><b>{escape(str(value))}</b></div>'


def _wiki_list(rows: Any) -> str:
    if not isinstance(rows, list) or not rows:
        return '<div class="history-empty">해당 사항 없음</div>'
    items: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        topic = escape(str(row.get("topic", "-")))
        explanation = escape(str(row.get("explanation", "-")))
        change_type = row.get("change_type")
        prefix = f"[{escape(str(change_type))}] " if change_type else ""
        refs = row.get("evidence_refs") if isinstance(row.get("evidence_refs"), list) else []
        ref_text = ", ".join(escape(str(item)) for item in refs)
        ref_html = f'<span class="history-wiki-ref">근거: {ref_text}</span>' if ref_text else ""
        items.append(f"<li><strong>{prefix}{topic}</strong> — {explanation}{ref_html}</li>")
    return f'<ul class="history-wiki-list">{"".join(items)}</ul>' if items else '<div class="history-empty">해당 사항 없음</div>'


def _render_wiki_section(report: dict[str, Any], wiki_result: dict[str, Any] | None) -> str:
    if not isinstance(wiki_result, dict):
        return ""
    if (
        wiki_result.get("before_analysis_id") != report.get("before_analysis_id")
        or wiki_result.get("after_analysis_id") != report.get("after_analysis_id")
    ):
        raise Pass2PipelineError("LLM_WIKI_COMPARISON_MISMATCH", "LLM Wiki result does not match the History comparison embedded in Step10.")
    explanation = wiki_result.get("explanation")
    if not isinstance(explanation, dict):
        raise Pass2PipelineError("LLM_WIKI_RESULT_INVALID", "LLM Wiki explanation is missing.")
    limitations = explanation.get("limitations") if isinstance(explanation.get("limitations"), list) else []
    limitations_html = "".join(f"<li>{escape(str(item))}</li>" for item in limitations)
    limitation_block = (
        f'<div class="history-wiki-block history-wiki-limit"><h4>해석 한계</h4><ul class="history-wiki-list">{limitations_html}</ul></div>'
        if limitations_html else ""
    )
    return f'''
<section class="history-wiki" aria-label="LLM Wiki AI comparison explanation">
<h3>AI 비교 해설 <span class="wiki-label">LLM Wiki</span></h3>
<p class="history-wiki-note">아래 내용은 Python 기반 deterministic 비교와 보존 provenance를 근거로 AI가 사후 해석한 설명이며, 현재 위험판정이나 Step09 검증결과를 변경하지 않습니다.</p>
<p class="history-wiki-summary">{escape(str(explanation.get('overall_summary', '-')))}</p>
<div class="history-wiki-block"><h4>유지된 판단</h4>{_wiki_list(explanation.get('maintained_judgments'))}</div>
<div class="history-wiki-block"><h4>변경된 판단</h4>{_wiki_list(explanation.get('changed_judgments'))}</div>
<div class="history-wiki-block"><h4>근거 변화</h4>{_wiki_list(explanation.get('evidence_changes'))}</div>
<div class="history-wiki-block"><h4>모순·불확실성</h4>{_wiki_list(explanation.get('contradictions_and_uncertainties'))}</div>
<div class="history-wiki-block"><h4>예보관 확인사항</h4>{_wiki_list(explanation.get('forecaster_attention'))}</div>
{limitation_block}
</section>'''


def _history_display_kst(value: Any) -> str:
    """Format one stored UTC analysis timestamp for forecaster-facing HTML."""
    text = str(value or "").strip()
    if not text:
        return "확인할 수 없음"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")


def _render_history_section(report: dict[str, Any], wiki_explanation: dict[str, Any] | None = None) -> str:
    comp = report["comparability"]
    status = str(comp.get("status"))
    status_class = "history-status" if status == "same_condition" else "history-status warn"
    reasons = comp.get("reasons", []) if isinstance(comp.get("reasons"), list) else []
    reason_text = "차이 없음" if not reasons else "; ".join(str(item.get("field")) for item in reasons if isinstance(item, dict))
    s4b, s4a = report["step04"]["before"], report["step04"]["after"]
    p4b, p4a = report["step04"]["precursor_before"], report["step04"]["precursor_after"]
    mandatory = report.get("mandatory_direct_signal") if isinstance(report.get("mandatory_direct_signal"), dict) else {}
    mandatory_before = mandatory.get("before") if isinstance(mandatory.get("before"), dict) else {}
    mandatory_after = mandatory.get("after") if isinstance(mandatory.get("after"), dict) else {}
    mandatory_section = ""
    if mandatory.get("availability") in {"available", "partial"}:
        mandatory_section = f"""
<div class="history-subsection"><h3>필수 직접강수 신호</h3><div class="history-grid">
<div class="history-card"><h4>이전</h4>{_history_metric('Availability', mandatory_before.get('availability'))}{_history_metric('Required anchors', mandatory_before.get('required_anchor_count'))}{_history_metric('Covered', mandatory_before.get('covered_anchor_count'))}{_history_metric('Missing', mandatory_before.get('missing_anchor_count'))}</div>
<div class="history-card"><h4>현재</h4>{_history_metric('Availability', mandatory_after.get('availability'))}{_history_metric('Required anchors', mandatory_after.get('required_anchor_count'))}{_history_metric('Covered', mandatory_after.get('covered_anchor_count'))}{_history_metric('Missing', mandatory_after.get('missing_anchor_count'))}</div>
</div></div>"""
    s7b, s7a = report["step07"]["before"], report["step07"]["after"]
    s8b, s8a = report["step08"]["before"], report["step08"]["after"]
    s9b, s9a = report["step09"]["before"], report["step09"]["after"]
    s10b, s10a = report["step10"]["before"], report["step10"]["after"]
    return f'''{_HISTORY_SECTION_START}
<details class="analysis-history" id="analysis-history">
<summary><span>이전 분석과 비교</span><small>{escape(_history_display_kst(report['before_generated_at_utc']))} → {escape(_history_display_kst(report['after_generated_at_utc']))}</small></summary>
<div class="history-body">
<p class="history-note">현재 분석이 기본 정보이며, 아래 내용은 완료된 동일 init 분석 간 deterministic 사후 비교입니다. Local Candidate ID는 snapshot 간 영속 identity가 아닙니다.</p>
<div class="{status_class}"><strong>비교조건: {escape(status)}</strong><br><span>{escape(reason_text)}</span></div>
<div class="history-compare-time"><span>이전 {escape(_history_display_kst(report['before_generated_at_utc']))}</span><span>현재 {escape(_history_display_kst(report['after_generated_at_utc']))}</span></div>
{_render_wiki_section(report, wiki_explanation)}
<div class="history-subsection"><h3>운영 결과</h3><div class="history-grid">
<div class="history-card"><h4>이전</h4>{_history_metric('Candidate', s10b.get('candidate_count'))}{_history_metric('Surface', s10b.get('surfaced_candidate_count'))}{_history_metric('Step09 signal class', _counter_text(s9b.get('signal_class')))}</div>
<div class="history-card"><h4>현재</h4>{_history_metric('Candidate', s10a.get('candidate_count'))}{_history_metric('Surface', s10a.get('surfaced_candidate_count'))}{_history_metric('Step09 signal class', _counter_text(s9a.get('signal_class')))}</div>
</div></div>
{mandatory_section}
<div class="history-subsection"><h3>선행환경 판단</h3><div class="history-grid">
<div class="history-card"><h4>이전</h4>{_history_metric('Opportunity', p4b.get('opportunity_count'))}{_history_metric('Qualification', _counter_text(p4b.get('qualification')))}{_history_metric('Relation', _counter_text(p4b.get('relation_to_direct')))}{_history_metric('Creation', _counter_text(p4b.get('candidate_creation')))}</div>
<div class="history-card"><h4>현재</h4>{_history_metric('Opportunity', p4a.get('opportunity_count'))}{_history_metric('Qualification', _counter_text(p4a.get('qualification')))}{_history_metric('Relation', _counter_text(p4a.get('relation_to_direct')))}{_history_metric('Creation', _counter_text(p4a.get('candidate_creation')))}</div>
</div></div>
<div class="history-subsection"><h3>Step07 상세 Evidence 판독</h3><div class="history-grid">
<div class="history-card"><h4>이전</h4>{_history_metric('Checks', s7b.get('check_count'))}{_history_metric('Assessment', _counter_text(s7b.get('assessment')))}{_history_metric('Evidence status', _counter_text(s7b.get('evidence_status')))}</div>
<div class="history-card"><h4>현재</h4>{_history_metric('Checks', s7a.get('check_count'))}{_history_metric('Assessment', _counter_text(s7a.get('assessment')))}{_history_metric('Evidence status', _counter_text(s7a.get('evidence_status')))}</div>
</div></div>
<details class="history-subsection"><summary><strong>Step08 종합판단 상세</strong></summary><div class="history-grid"><div class="history-card"><h4>이전</h4>{_assessment_rows(s8b.get('candidates'))}</div><div class="history-card"><h4>현재</h4>{_assessment_rows(s8a.get('candidates'))}</div></div></details>
<details class="history-subsection"><summary><strong>Candidate 구성 상세</strong></summary><div class="history-grid"><div class="history-card"><h4>이전</h4>{_candidate_table(s4b.get('candidates'))}</div><div class="history-card"><h4>현재</h4>{_candidate_table(s4a.get('candidates'))}</div></div></details>
</div></details>
{_HISTORY_SECTION_END}'''


def _remove_embedded_history(html: str) -> str:
    for start_marker, end_marker in (
        (_HISTORY_STYLE_START, _HISTORY_STYLE_END),
        (_HISTORY_SECTION_START, _HISTORY_SECTION_END),
    ):
        while start_marker in html and end_marker in html:
            start = html.index(start_marker)
            end = html.index(end_marker, start) + len(end_marker)
            html = html[:start] + html[end:]
    return html


def embed_history_comparison(root: Path, init: str, *, wiki_explanation: dict[str, Any] | None = None) -> dict[str, Any]:
    """Embed the latest same-init comparison into the one forecaster-facing index.html.

    With fewer than two snapshots, no History UI is shown. The source snapshots stay
    under ``daou/<init>/history`` and the HTML never reads them at browser runtime.
    """
    _migrate_legacy_history(root, init)
    product_dir = step10_output_dir(root, init)
    index_path = product_dir / "index.html"
    if not index_path.is_file():
        raise Pass2PipelineError("HTML_MISSING", f"Step10 index.html is missing: {index_path}")
    original = index_path.read_text(encoding="utf-8")
    base = _remove_embedded_history(original)
    snapshots = list_history_snapshots(root, init)
    if len(snapshots) < 2:
        updated = base
        embedded = False
        report = None
    else:
        report = compare_history_snapshots(root, init)
        style = f"{_HISTORY_STYLE_START}<style>{_history_section_css()}</style>{_HISTORY_STYLE_END}"
        section = _render_history_section(report, wiki_explanation)
        if "</head>" not in base or "</main>" not in base:
            raise Pass2PipelineError("HTML_HISTORY_INTEGRATION_FAILED", "Step10 HTML has no expected </head> or </main> insertion point.")
        updated = base.replace("</head>", style + "</head>", 1).replace("</main>", section + "</main>", 1)
        embedded = True
    if updated != original:
        temporary = index_path.with_suffix(".html.tmp")
        temporary.write_text(updated, encoding="utf-8")
        os.replace(temporary, index_path)
    try:
        validate_published_product(root, product_dir)
    except Exception:
        if updated != original:
            index_path.write_text(original, encoding="utf-8")
        raise
    return {
        "embedded": embedded,
        "snapshot_count": len(snapshots),
        "comparison_available": len(snapshots) >= 2,
        "comparability": report.get("comparability", {}).get("status") if isinstance(report, dict) else None,
        "llm_wiki_embedded": bool(isinstance(wiki_explanation, dict) and report is not None),
        "index_html": str(index_path),
    }


def history_status_summary(root: Path, init: str) -> dict[str, Any]:
    _migrate_legacy_history(root, init)
    snapshots = list_history_snapshots(root, init)
    return {
        "init": init,
        "snapshot_count": len(snapshots),
        "latest_analysis_id": snapshots[-1]["analysis_id"] if snapshots else None,
        "comparison_available": len(snapshots) >= 2,
        "history_dir": str(history_init_dir(root, init)) if snapshots else None,
        "forecaster_index": str(step10_output_dir(root, init) / "index.html") if snapshots else None,
    }
