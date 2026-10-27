"""Read-only layout table for legacy prompt/schema logical references.

Historical Step03 package fingerprints contain paths such as
``prompts/templates/ai_pass1...``; those references remain stable. New source
files reside in ``prompts/stepNN``. No content duplication or silent fallback.
"""
from __future__ import annotations

from pathlib import Path

LEGACY_PROMPT_PATHS: dict[str, str] = {
    "prompts/schemas/ai_discovery_candidate_response.schema.json": "prompts/step04/candidate_response.schema.json",
    "prompts/schemas/ai_pass1_batch_observation.schema.json": "prompts/step04/batch_observation.schema.json",
    "prompts/schemas/ai_pass1_candidate_discovery.schema.json": "prompts/step04/candidate_discovery.schema.json",
    "prompts/schemas/ai_pass1_candidate_plan.schema.json": "prompts/step04/candidate_plan.schema.json",
    "prompts/schemas/ai_pass1_group_summary.schema.json": "prompts/step04/group_summary.schema.json",
    "prompts/schemas/analysis_history_snapshot.schema.json": "prompts/step10/analysis_history_snapshot.schema.json",
    "prompts/schemas/forecaster_product.schema.json": "prompts/step10/forecaster_product.schema.json",
    "prompts/schemas/llm_wiki_explanation_response.schema.json": "prompts/step10/wiki_explanation_response.schema.json",
    "prompts/schemas/llm_wiki_result.schema.json": "prompts/step10/wiki_result.schema.json",
    "prompts/schemas/openai/ai_discovery_candidate_response.transport.schema.json": "prompts/step04/openai/candidate_response.transport.schema.json",
    "prompts/schemas/openai/ai_pass1_batch_observation.transport.schema.json": "prompts/step04/openai/batch_observation.transport.schema.json",
    "prompts/schemas/openai/ai_pass1_candidate_discovery.transport.schema.json": "prompts/step04/openai/candidate_discovery.transport.schema.json",
    "prompts/schemas/openai/ai_pass1_candidate_plan.transport.schema.json": "prompts/step04/openai/candidate_plan.transport.schema.json",
    "prompts/schemas/openai/ai_pass1_group_summary.transport.schema.json": "prompts/step04/openai/group_summary.transport.schema.json",
    "prompts/schemas/operational_result.schema.json": "prompts/step09/operational_result.schema.json",
    "prompts/schemas/pass2_check_assessment_response.schema.json": "prompts/step07/check_assessment_response.schema.json",
    "prompts/schemas/pass2_check_results.schema.json": "prompts/step07/check_results.schema.json",
    "prompts/schemas/pass2_input_package.schema.json": "prompts/step06/input_package.schema.json",
    "prompts/schemas/pass2_result.schema.json": "prompts/step08/pass2_result.schema.json",
    "prompts/schemas/pass2_synthesis_response.schema.json": "prompts/step08/synthesis_response.schema.json",
    "prompts/schemas/step04_analysis_provenance.schema.json": "prompts/step04/analysis_provenance.schema.json",
    "prompts/schemas/step05_diagnostic_result.schema.json": "prompts/step05/diagnostic_result.schema.json",
    "prompts/schemas/step07_minority_report.schema.json": "prompts/step07/minority_report.schema.json",
    "prompts/templates/ai_discovery_candidate_prompt.ko.md": "prompts/step04/candidate_response.ko.md",
    "prompts/templates/ai_pass1_batch_observation_prompt.ko.md": "prompts/step04/batch_observation.ko.md",
    "prompts/templates/ai_pass1_candidate_discovery_prompt.ko.md": "prompts/step04/candidate_discovery.ko.md",
    "prompts/templates/ai_pass1_candidate_plan_prompt.ko.md": "prompts/step04/candidate_plan.ko.md",
    "prompts/templates/ai_pass1_group_summary_prompt.ko.md": "prompts/step04/group_summary.ko.md",
    "prompts/templates/ai_pass2_candidate_synthesis_prompt.ko.md": "prompts/step08/candidate_synthesis.ko.md",
    "prompts/templates/ai_pass2_check_assessment_prompt.ko.md": "prompts/step07/check_assessment.ko.md",
    "prompts/templates/llm_wiki_explanation_prompt.ko.md": "prompts/step10/wiki_explanation.ko.md"
}

def remap_prompt_parts(parts: tuple[str, ...]) -> tuple[str, ...] | None:
    """Return canonical path components for an old relative prompt path."""
    normalized = Path("prompts", *parts).as_posix()
    mapped = LEGACY_PROMPT_PATHS.get(normalized)
    if mapped is not None:
        return tuple(Path(mapped).parts[1:])
    return None
