"""Grounded, post-operational LLM Wiki v1 explanation for Analysis History."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
from typing import Any

from func.shared.ai_provider import AIProvider, build_openai_transport_schema, normalize_openai_transport_json
from func.step10.analysis_history import compare_history_snapshots, history_init_dir, list_history_snapshots
from func.shared.pass2_common import (
    Pass2PipelineError,
    load_json_object,
    load_schema,
    template_text_and_hash,
    validate_schema,
    write_validated_atomic,
)
from func.shared.pipeline_common import canonical_hash, now_utc, sha256sum


WIKI_RESPONSE_SCHEMA = "llm_wiki_explanation_response.schema.json"
WIKI_RESULT_SCHEMA = "llm_wiki_result.schema.json"
WIKI_PROMPT = "llm_wiki_explanation_prompt.ko.md"
WIKI_RESULT_VERSION = "llm-wiki-result-v1"
_ALLOWED_EVIDENCE_REFS = frozenset({
    "comparability",
    "mandatory_direct_signal",
    "step04.precursor",
    "step04.candidates",
    "step04_provenance.before",
    "step04_provenance.after",
    "step07",
    "step08",
    "step09",
    "step10",
})


def wiki_dir(root: Path, init: str) -> Path:
    return history_init_dir(root, init) / "wiki"


def _snapshot(root: Path, init: str, analysis_id: str) -> dict[str, Any]:
    for snapshot in list_history_snapshots(root, init):
        if snapshot.get("analysis_id") == analysis_id:
            return snapshot
    raise Pass2PipelineError("LLM_WIKI_SNAPSHOT_NOT_FOUND", f"History snapshot not found: {analysis_id}.")


def _archived_optional(root: Path, init: str, snapshot: dict[str, Any], role: str) -> dict[str, Any] | None:
    record = next(
        (
            item for item in snapshot.get("source_files", [])
            if isinstance(item, dict) and item.get("role") == role
        ),
        None,
    )
    if not isinstance(record, dict):
        return None
    path = history_init_dir(root, init) / str(snapshot["analysis_id"]) / str(record.get("archived_name", ""))
    if not path.is_file():
        return None
    return load_json_object(path, code="LLM_WIKI_GROUNDING_INVALID")


def _provenance_grounding(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"availability": "legacy_unavailable"}
    return {
        "availability": "available",
        "mandatory_direct_signal_audit": deepcopy(value.get("mandatory_direct_signal_audit")),
        "group_summaries": deepcopy(value.get("group_summaries", [])),
        "stage_provenance": deepcopy(value.get("stage_provenance", {})),
    }


def build_wiki_grounding(
    root: Path,
    init: str,
    *,
    before_id: str | None = None,
    after_id: str | None = None,
) -> dict[str, Any]:
    comparison = compare_history_snapshots(root, init, before_id=before_id, after_id=after_id)
    before = _snapshot(root, init, str(comparison["before_analysis_id"]))
    after = _snapshot(root, init, str(comparison["after_analysis_id"]))
    before_prov = _archived_optional(root, init, before, "step04_analysis_provenance")
    after_prov = _archived_optional(root, init, after, "step04_analysis_provenance")
    allowed_refs = sorted(_ALLOWED_EVIDENCE_REFS - ({"step04_provenance.before"} if before_prov is None else set()) - ({"step04_provenance.after"} if after_prov is None else set()))
    return {
        "comparison": comparison,
        "step04_provenance": {
            "before": _provenance_grounding(before_prov),
            "after": _provenance_grounding(after_prov),
        },
        "allowed_evidence_refs": allowed_refs,
    }


def _messages(prompt_text: str, grounding: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": prompt_text},
        {"role": "user", "content": json.dumps(grounding, ensure_ascii=False, separators=(",", ":"))},
    ]


def _provider_call(
    provider: AIProvider,
    root: Path,
    config: dict[str, str],
    messages: list[dict[str, Any]],
) -> tuple[dict[str, Any], Any]:
    canonical = load_schema(root, WIKI_RESPONSE_SCHEMA)
    schema = build_openai_transport_schema(canonical) if provider.api_family == "responses" else canonical
    response = provider.complete(
        messages=messages,
        model=config["model"],
        max_tokens=7000,
        schema=schema,
        schema_name="AIFSHeavyRainLLMWikiV1",
        structured_output_mode="auto",
        image_detail=config.get("image_detail", "original"),
        reasoning_effort=config["reasoning_effort"],
    )
    content = normalize_openai_transport_json(response.content, canonical) if provider.api_family == "responses" else response.content
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise Pass2PipelineError("LLM_WIKI_PROVIDER_RESULT_INVALID", "LLM Wiki provider response is not JSON.") from exc
    if not isinstance(value, dict):
        raise Pass2PipelineError("LLM_WIKI_PROVIDER_RESULT_INVALID", "LLM Wiki provider response root must be an object.")
    validate_schema(root, WIKI_RESPONSE_SCHEMA, value, code="LLM_WIKI_PROVIDER_RESULT_INVALID")
    return value, response


def _all_evidence_refs(explanation: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for field in (
        "maintained_judgments",
        "changed_judgments",
        "evidence_changes",
        "contradictions_and_uncertainties",
        "forecaster_attention",
    ):
        for item in explanation.get(field, []):
            if isinstance(item, dict):
                refs.extend(str(value) for value in item.get("evidence_refs", []) if isinstance(value, str))
    return refs


def validate_wiki_explanation(grounding: dict[str, Any], explanation: dict[str, Any]) -> None:
    allowed = set(grounding.get("allowed_evidence_refs", []))
    refs = _all_evidence_refs(explanation)
    unknown = sorted(set(refs) - allowed)
    if unknown:
        raise Pass2PipelineError("LLM_WIKI_EVIDENCE_REF_INVALID", f"LLM Wiki used unavailable evidence refs: {unknown}.")
    for field in (
        "maintained_judgments",
        "changed_judgments",
        "evidence_changes",
        "contradictions_and_uncertainties",
        "forecaster_attention",
    ):
        rows = explanation.get(field, [])
        topics = [str(item.get("topic")) for item in rows if isinstance(item, dict)]
        if len(topics) != len(set(topics)):
            raise Pass2PipelineError("LLM_WIKI_DUPLICATE_TOPIC", f"LLM Wiki {field} contains duplicate topics.")
    comparison = grounding.get("comparison", {})
    comparability = comparison.get("comparability", {}).get("status") if isinstance(comparison, dict) else None
    provenance = grounding.get("step04_provenance", {})
    before_available = isinstance(provenance.get("before"), dict) and provenance["before"].get("availability") == "available"
    after_available = isinstance(provenance.get("after"), dict) and provenance["after"].get("availability") == "available"
    if (comparability != "same_condition" or not before_available or not after_available) and not explanation.get("limitations"):
        raise Pass2PipelineError(
            "LLM_WIKI_LIMITATION_REQUIRED",
            "LLM Wiki must state limitations when comparison conditions or Step04 provenance are incomplete.",
        )


def _wiki_identity(
    init: str,
    grounding_sha256: str,
    *,
    provider: str,
    model: str,
    reasoning_effort: str,
    prompt_sha256: str,
    response_schema_sha256: str,
) -> str:
    digest = canonical_hash({
        "grounding_sha256": grounding_sha256,
        "provider": provider,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "prompt_sha256": prompt_sha256,
        "response_schema_sha256": response_schema_sha256,
    })
    return f"WIKI_{init}_{digest[:12].upper()}"


def _result_path(root: Path, init: str, wiki_id: str) -> Path:
    return wiki_dir(root, init) / f"{wiki_id}.json"


def list_wiki_results(root: Path, init: str) -> list[dict[str, Any]]:
    directory = wiki_dir(root, init)
    if not directory.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for path in sorted(directory.glob("WIKI_*.json")):
        try:
            value = load_json_object(path, code="LLM_WIKI_RESULT_INVALID")
            validate_schema(root, WIKI_RESULT_SCHEMA, value, code="LLM_WIKI_RESULT_INVALID")
        except Pass2PipelineError:
            continue
        if value.get("init") == init:
            results.append(value)
    return sorted(results, key=lambda item: (str(item.get("generated_at_utc", "")), str(item.get("wiki_id", ""))))


def load_latest_wiki_for_current_comparison(root: Path, init: str) -> dict[str, Any] | None:
    snapshots = list_history_snapshots(root, init)
    if len(snapshots) < 2:
        return None
    before_id = str(snapshots[-2]["analysis_id"])
    after_id = str(snapshots[-1]["analysis_id"])
    matches = [
        item for item in list_wiki_results(root, init)
        if item.get("before_analysis_id") == before_id and item.get("after_analysis_id") == after_id
    ]
    return matches[-1] if matches else None


def dry_run_summary(
    root: Path,
    init: str,
    *,
    before_id: str | None = None,
    after_id: str | None = None,
) -> dict[str, Any]:
    grounding = build_wiki_grounding(root, init, before_id=before_id, after_id=after_id)
    encoded = json.dumps(grounding, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    comparison = grounding["comparison"]
    return {
        "init": init,
        "before_analysis_id": comparison["before_analysis_id"],
        "after_analysis_id": comparison["after_analysis_id"],
        "comparability": comparison["comparability"]["status"],
        "grounding_bytes": len(encoded),
        "before_step04_provenance": grounding["step04_provenance"]["before"]["availability"],
        "after_step04_provenance": grounding["step04_provenance"]["after"]["availability"],
        "expected_api_calls": 1,
    }


def generate_llm_wiki(
    *,
    root: Path,
    init: str,
    config: dict[str, str],
    provider: AIProvider,
    before_id: str | None = None,
    after_id: str | None = None,
) -> tuple[dict[str, Any], str]:
    grounding = build_wiki_grounding(root, init, before_id=before_id, after_id=after_id)
    prompt_text, prompt_sha256 = template_text_and_hash(root, WIKI_PROMPT)
    response_schema_sha256 = sha256sum(root / "prompts" / "schemas" / WIKI_RESPONSE_SCHEMA)
    grounding_sha256 = canonical_hash(grounding)
    comparison = grounding["comparison"]
    wiki_id = _wiki_identity(
        init,
        grounding_sha256,
        provider=config["provider"],
        model=config["model"],
        reasoning_effort=config["reasoning_effort"],
        prompt_sha256=prompt_sha256,
        response_schema_sha256=response_schema_sha256,
    )
    output_path = _result_path(root, init, wiki_id)
    if output_path.is_file():
        existing = load_json_object(output_path, code="LLM_WIKI_RESULT_INVALID")
        validate_schema(root, WIKI_RESULT_SCHEMA, existing, code="LLM_WIKI_RESULT_INVALID")
        if existing.get("grounding_sha256") != grounding_sha256:
            raise Pass2PipelineError("LLM_WIKI_IDENTITY_CONFLICT", f"Existing {wiki_id} has a different grounding hash.")
        return existing, "reused"

    explanation, response = _provider_call(provider, root, config, _messages(prompt_text, grounding))
    validate_wiki_explanation(grounding, explanation)
    result = {
        "schema_version": WIKI_RESULT_VERSION,
        "wiki_id": wiki_id,
        "init": init,
        "before_analysis_id": comparison["before_analysis_id"],
        "after_analysis_id": comparison["after_analysis_id"],
        "comparability_status": comparison["comparability"]["status"],
        "grounding_sha256": grounding_sha256,
        "generated_at_utc": now_utc(),
        "explanation": explanation,
        "provenance": {
            "provider": config["provider"],
            "model": config["model"],
            "reasoning_effort": config["reasoning_effort"],
            "prompt_sha256": prompt_sha256,
            "response_schema_sha256": response_schema_sha256,
            "comparison_schema_version": comparison["comparison_schema_version"],
            "response_id": response.response_id,
            "request_id": response.request_id,
            "retry_count": int(response.retry_count),
            "usage": response.usage,
        },
    }
    validate_schema(root, WIKI_RESULT_SCHEMA, result, code="LLM_WIKI_RESULT_INVALID")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_validated_atomic(root, output_path, result, schema_name=WIKI_RESULT_SCHEMA)
    return result, "created"
