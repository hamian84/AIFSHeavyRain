"""Korean-only validation helpers for forecaster-facing narrative fields.

Internal JSON keys, enum values, identifiers, and machine metadata are deliberately
outside this module's scope.  Callers pass only human-readable narrative fields.
The validator rejects unapproved Latin words and non-Korean alphabetic scripts while
allowing a small set of operational abbreviations, forecast-hour tokens, and units.
"""

from __future__ import annotations

import re
from typing import Any, Iterable


# These terms are internal implementation vocabulary and must never appear in a
# forecaster-facing narrative string, even if a future allowlist is broadened.
FORBIDDEN_NARRATIVE_TERMS = frozenset(
    {
        "precursor",
        "event",
        "expected_signal",
        "evidence",
        "peak",
        "candidate",
        "check",
        "hypothesis",
        "alignment",
        "organization_persistence",
        "direct_precipitation",
        "moisture_supply",
        "low_level_forcing",
        "mid_level_forcing",
        "mid_upper_forcing",
        "thermodynamic_support",
        "observed",
        "partially_observed",
        "not_observed",
        "opposite_observed",
        "uncertain",
        "insufficient_evidence",
        "request_id",
        "feature_id",
        "diagnostic_id",
        "physical_diagnostic",
        "hazard",
        "forcing",
        "episode",
        "ground truth",
    }
)

# Operational abbreviations and physical units that may legitimately remain in
# Korean prose.  Internal IDs such as CAND_*, CHK_*, REQ_*, AST_*, QL_* are NOT
# allowed here; they belong in structured fields, not narrative strings.
_ALLOWED_LATIN_WORDS = frozenset(
    {
        "aifs",
        "ai",
        "utc",
        "kst",
        "hpa",
        "pa",
        "mm",
        "cm",
        "km",
        "m",
        "s",
        "kg",
        "g",
        "gpm",
        "k",
        "c",
        "dbz",
        "tcw",
    }
)
_ALLOWED_GREEK_LETTERS = frozenset({"ω", "Ω", "θ", "Θ"})
_LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_./+\-]*")
_FORBIDDEN_STEP = re.compile(r"(?i)(?<![A-Za-z0-9_])step\s*0?[0-9]+(?![A-Za-z0-9_])")
_FORECAST_HOUR = re.compile(r"(?i)^F\d{3,}$")
_FORECAST_HOUR_RANGE_TOKEN = re.compile(r"(?i)^F\d{3,}(?:-F\d{3,})+$")
_SCI_NOTATION = re.compile(r"(?i)(?<![A-Za-z0-9_])\d+(?:\.\d+)?e[+\-]?\d+(?![A-Za-z0-9_])")
# Coordinate direction abbreviations are normalized before Step08 language validation.
# Uppercase-only matching deliberately avoids scientific notation (for example 1e-5)
# and ordinary lower-case physical units.
_COORD_SUFFIX_DIRECTION = re.compile(
    r"(?<![A-Za-z0-9_])(?P<value>\d{1,3}(?:\.\d+)?)\s*°?\s*(?P<direction>[NSEW])(?=$|[^A-Za-z0-9_])"
)
_COORD_PREFIX_DIRECTION = re.compile(
    r"(?<![A-Za-z0-9_])(?P<direction>[NSEW])\s*(?P<value>\d{1,3}(?:\.\d+)?)(?:\s*°)?(?=$|[^A-Za-z0-9_])"
)
_COORD_DIRECTION_LABEL = {
    "N": "북위",
    "S": "남위",
    "E": "동경",
    "W": "서경",
}
_COMPASS_DIRECTION_LABEL = {
    "N": "북쪽",
    "S": "남쪽",
    "E": "동쪽",
    "W": "서쪽",
}
_COMPASS_DIRECTION_STEM = {
    "N": "북",
    "S": "남",
    "E": "동",
    "W": "서",
}
_SAFE_COMPASS_SUFFIX = re.compile(
    r"(?<![A-Za-z0-9_])(?P<direction>[NSEW])(?P<suffix>쪽|측)"
)
_SAFE_COMPASS_NOUN_CONTEXT = re.compile(
    r"(?<![A-Za-z0-9_])(?P<direction>[NSEW])(?=\s*(?:방향|경계|부근|해상|해역|지역|영역|섹터|사면))"
)
_UNIT_SPLIT = re.compile(r"[^A-Za-z]+")
_FORBIDDEN_KOREAN_SCOPE_TERMS = frozenset({"호우", "집중호우"})
_FORBIDDEN_KOREAN_TERM_REPLACEMENTS = {
    "6시간 누적 강수량": "6시간 강수량 또는 초기시각부터의 누적강수량으로 구분하십시오.",
}

_HANGUL_RANGES = (
    (0x1100, 0x11FF),
    (0x3130, 0x318F),
    (0xA960, 0xA97F),
    (0xAC00, 0xD7A3),
    (0xD7B0, 0xD7FF),
)


def _is_hangul_alpha(char: str) -> bool:
    code = ord(char)
    return any(start <= code <= end for start, end in _HANGUL_RANGES)


def _inside_any(span: tuple[int, int], protected: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start >= p_start and end <= p_end for p_start, p_end in protected)


def _allowed_latin_token(token: str) -> bool:
    lowered = token.lower()
    if lowered in _ALLOWED_LATIN_WORDS:
        return True
    if _FORECAST_HOUR.fullmatch(token) or _FORECAST_HOUR_RANGE_TOKEN.fullmatch(token):
        return True
    # Compound unit forms such as m/s, Pa/s, g/kg, kg/m2, m2/s2.
    chunks = [chunk.lower() for chunk in _UNIT_SPLIT.split(token) if chunk]
    return bool(chunks) and all(chunk in _ALLOWED_LATIN_WORDS for chunk in chunks)


def normalize_korean_coordinate_notation(text: str) -> tuple[str, int]:
    """Normalize compact Latin coordinate directions into Korean notation.

    Only explicit numeric coordinate patterns are changed.  Meteorological prose,
    internal identifiers, forecast-hour tokens, and scientific notation are left
    untouched.  This is a presentation normalization, not a meteorological rewrite.
    """
    if not isinstance(text, str) or not text:
        return text, 0

    count = 0

    def _replacement(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        direction = match.group("direction")
        value = match.group("value")
        return f"{_COORD_DIRECTION_LABEL[direction]} {value}°"

    normalized = _COORD_SUFFIX_DIRECTION.sub(_replacement, text)
    normalized = _COORD_PREFIX_DIRECTION.sub(_replacement, normalized)
    return normalized, count


def normalize_step07_response_directions(payload: dict[str, Any]) -> int:
    """Normalize only unambiguous coordinate/compass notation in Step07 prose.

    The function is deliberately deterministic: numeric coordinate suffix/prefix
    forms such as ``35N``/``E127`` are converted to Korean coordinate notation,
    and a standalone N/S/E/W token is converted only when it directly modifies a
    recognized Korean spatial noun (for example ``N 경계`` -> ``북쪽 경계``).
    Ambiguous uses such as ``N=3`` are left untouched for the language validator
    and full-Check regeneration path.  Structured assessment/evidence fields are
    never changed.
    """
    fixes = 0

    def _normalize_text(value: str) -> tuple[str, int]:
        normalized, changed = normalize_korean_coordinate_notation(value)

        def _compass_suffix_replacement(match: re.Match[str]) -> str:
            nonlocal changed
            changed += 1
            return _COMPASS_DIRECTION_STEM[match.group("direction")] + match.group("suffix")

        def _compass_noun_replacement(match: re.Match[str]) -> str:
            nonlocal changed
            changed += 1
            return _COMPASS_DIRECTION_LABEL[match.group("direction")]

        normalized = _SAFE_COMPASS_SUFFIX.sub(_compass_suffix_replacement, normalized)
        normalized = _SAFE_COMPASS_NOUN_CONTEXT.sub(_compass_noun_replacement, normalized)
        return normalized, changed

    for assessment in payload.get("assessments", []):
        if not isinstance(assessment, dict):
            continue
        for field in ("findings", "contradictory_findings"):
            rows = assessment.get(field)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("statement"), str):
                    continue
                normalized, changed = _normalize_text(row["statement"])
                if changed:
                    row["statement"] = normalized
                    fixes += changed
        uncertainties = assessment.get("uncertainties")
        if isinstance(uncertainties, list):
            for index, value in enumerate(uncertainties):
                if not isinstance(value, str):
                    continue
                normalized, changed = _normalize_text(value)
                if changed:
                    uncertainties[index] = normalized
                    fixes += changed
    return fixes


def normalize_step08_response_coordinates(payload: dict[str, Any]) -> int:
    """Normalize coordinate notation only in Step08 human-readable fields in-place.

    Structured IDs, enums, hashes, provenance, and machine metadata are deliberately
    excluded so coordinate cleanup cannot alter the Step08 identity contract.
    Returns the number of coordinate tokens normalized.
    """
    fixes = 0

    def _normalize_key(container: dict[str, Any], key: str) -> None:
        nonlocal fixes
        value = container.get(key)
        if not isinstance(value, str):
            return
        normalized, changed = normalize_korean_coordinate_notation(value)
        if changed:
            container[key] = normalized
            fixes += changed

    def _normalize_list(values: Any) -> None:
        nonlocal fixes
        if not isinstance(values, list):
            return
        for index, value in enumerate(values):
            if not isinstance(value, str):
                continue
            normalized, changed = normalize_korean_coordinate_notation(value)
            if changed:
                values[index] = normalized
                fixes += changed

    for item in payload.get("hypothesis_assessments", []):
        if not isinstance(item, dict):
            continue
        for key in ("statement", "rationale"):
            _normalize_key(item, key)
        _normalize_list(item.get("uncertainties"))

    for item in payload.get("forecaster_question_answers", []):
        if not isinstance(item, dict):
            continue
        for key in ("question", "answer"):
            _normalize_key(item, key)
        _normalize_list(item.get("uncertainties"))

    signal = payload.get("signal_assessment")
    if isinstance(signal, dict):
        for key in (
            "direct_precipitation_assessment",
            "precursor_environment_assessment",
            "physical_mechanism_summary",
            "operational_surface_reason",
        ):
            _normalize_key(signal, key)
        for field in ("mechanism_evidence", "monitoring_evidence"):
            for evidence in signal.get(field, []):
                if isinstance(evidence, dict):
                    _normalize_key(evidence, "reason")
        _normalize_list(signal.get("contradictory_evidence"))

    _normalize_list(payload.get("uncertainties"))
    _normalize_key(payload, "final_assessment")

    brief = payload.get("operational_brief")
    if isinstance(brief, dict):
        for key in ("key_signal", "key_limitation", "next_check"):
            _normalize_key(brief, key)

    guidance = payload.get("operational_guidance")
    if isinstance(guidance, dict):
        for key in ("current_action", "next_check", "interpretation_limit"):
            _normalize_key(guidance, key)

    return fixes


def validate_korean_text(value: Any, *, path: str) -> list[str]:
    """Validate one human-readable string and return machine-loggable errors."""
    if value is None:
        return []
    if not isinstance(value, str):
        return [f"{path}: 자연어 필드는 문자열이어야 합니다."]
    text = value.strip()
    if not text:
        return []

    errors: list[str] = []
    lowered = text.lower()
    scope_term = next(
        (term for term in sorted(_FORBIDDEN_KOREAN_SCOPE_TERMS, key=len, reverse=True) if term in text),
        None,
    )
    if scope_term is not None:
        errors.append(
            f"{path}: 현재 강수 감시 범위에서는 사용자 표시용 자연어에 '{scope_term}'를 단정적으로 사용할 수 없습니다."
        )
    for term, replacement in _FORBIDDEN_KOREAN_TERM_REPLACEMENTS.items():
        if term in text:
            errors.append(f"{path}: '{term}' 대신 {replacement}")
    for term in sorted(FORBIDDEN_NARRATIVE_TERMS, key=len, reverse=True):
        if term == "ground truth":
            found = term in lowered
        else:
            found = re.search(rf"(?i)(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])", text) is not None
        if found:
            errors.append(f"{path}: 사용자 표시용 자연어에 내부 영문 용어 '{term}'를 사용할 수 없습니다.")
    if _FORBIDDEN_STEP.search(text):
        errors.append(f"{path}: 사용자 표시용 자연어에 내부 단계명(Step 번호)을 사용할 수 없습니다.")

    scientific_spans = [match.span() for match in _SCI_NOTATION.finditer(text)]
    invalid_tokens: list[str] = []
    for match in _LATIN_TOKEN.finditer(text):
        if _inside_any(match.span(), scientific_spans):
            continue
        token = match.group(0)
        if not _allowed_latin_token(token):
            invalid_tokens.append(token)
    if invalid_tokens:
        unique = list(dict.fromkeys(invalid_tokens))
        errors.append(
            f"{path}: 허용되지 않은 영문 토큰이 포함되어 있습니다: {', '.join(unique[:8])}"
        )

    foreign_letters: list[str] = []
    for char in text:
        if not char.isalpha():
            continue
        if _is_hangul_alpha(char) or char.isascii() or char in _ALLOWED_GREEK_LETTERS:
            continue
        foreign_letters.append(char)
    if foreign_letters:
        unique_chars = "".join(dict.fromkeys(foreign_letters))
        errors.append(
            f"{path}: 한글 자연어에 허용되지 않은 외국문자가 포함되어 있습니다: {unique_chars[:12]}"
        )
    return errors


def _validate_entries(entries: Iterable[tuple[str, Any]]) -> list[str]:
    errors: list[str] = []
    for path, value in entries:
        errors.extend(validate_korean_text(value, path=path))
    return errors


def step04_discovery_narrative_entries(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    entries: list[tuple[str, Any]] = []
    for i, candidate in enumerate(payload.get("candidates", [])):
        if not isinstance(candidate, dict):
            continue
        prefix = f"candidates[{i}]"
        entries.append((f"{prefix}.candidate_name", candidate.get("candidate_name")))
        hypothesis = candidate.get("hypothesis")
        if isinstance(hypothesis, dict):
            entries.append((f"{prefix}.hypothesis.statement", hypothesis.get("statement")))
        for j, diagnostic in enumerate(candidate.get("diagnostic_plan", [])):
            if isinstance(diagnostic, dict):
                entries.append((f"{prefix}.diagnostic_plan[{j}].purpose", diagnostic.get("purpose")))
        for field in ("supporting_reasons", "opposing_reasons", "uncertainties"):
            for j, value in enumerate(candidate.get(field, [])):
                entries.append((f"{prefix}.{field}[{j}]", value))
        if candidate.get("separation_reason") is not None:
            entries.append((f"{prefix}.separation_reason", candidate.get("separation_reason")))
    for i, group in enumerate(payload.get("candidate_evidence", [])):
        if not isinstance(group, dict):
            continue
        for j, item in enumerate(group.get("evidence_items", [])):
            if isinstance(item, dict):
                entries.append((f"candidate_evidence[{i}].evidence_items[{j}].reason", item.get("reason")))
    for i, value in enumerate(payload.get("global_uncertainties", [])):
        entries.append((f"global_uncertainties[{i}]", value))
    assessment = payload.get("precursor_candidate_assessment")
    if isinstance(assessment, dict):
        entries.append(("precursor_candidate_assessment.rationale", assessment.get("rationale")))
    for i, opportunity in enumerate(payload.get("precursor_opportunities", [])):
        if not isinstance(opportunity, dict):
            continue
        relation = opportunity.get("relation_basis")
        if isinstance(relation, dict):
            for field in ("time_relation", "spatial_relation", "mechanism_relation", "reason"):
                entries.append((f"precursor_opportunities[{i}].relation_basis.{field}", relation.get(field)))
    return entries


def step04_plan_narrative_entries(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    entries: list[tuple[str, Any]] = []
    plans: list[dict[str, Any]] = []
    singular = payload.get("candidate_analysis_plan")
    if isinstance(singular, dict):
        plans.append(singular)
    plans.extend(item for item in payload.get("candidate_analysis_plans", []) if isinstance(item, dict))
    for i, plan in enumerate(plans):
        prefix = f"candidate_analysis_plans[{i}]"
        entries.append((f"{prefix}.analysis_objective", plan.get("analysis_objective")))
        for j, hypothesis in enumerate(plan.get("hypotheses", [])):
            if isinstance(hypothesis, dict):
                entries.append((f"{prefix}.hypotheses[{j}].statement", hypothesis.get("statement")))
        for field in ("target_areas", "analysis_phases"):
            for j, item in enumerate(plan.get(field, [])):
                if isinstance(item, dict):
                    entries.append((f"{prefix}.{field}[{j}].purpose", item.get("purpose")))
        for j, check in enumerate(plan.get("variable_checks", [])):
            if not isinstance(check, dict):
                continue
            entries.append((f"{prefix}.variable_checks[{j}].expected_signal", check.get("expected_signal")))
            entries.append((f"{prefix}.variable_checks[{j}].decision_relevance", check.get("decision_relevance")))
            line = check.get("cross_section_line")
            if isinstance(line, dict):
                entries.append((f"{prefix}.variable_checks[{j}].cross_section_line.selection_reason", line.get("selection_reason")))
        for j, question in enumerate(plan.get("forecaster_questions", [])):
            entries.append((f"{prefix}.forecaster_questions[{j}]", question))
    for i, request in enumerate(payload.get("analysis_data_requests", [])):
        if isinstance(request, dict):
            entries.append((f"analysis_data_requests[{i}].purpose", request.get("purpose")))
    return entries



def step04_compact_plan_narrative_entries(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    """Return forecaster-facing strings from the OpenAI compact Step04 plan transport."""
    entries: list[tuple[str, Any]] = [
        ("analysis_objective", payload.get("analysis_objective")),
        ("context_domain_reason", payload.get("context_domain_reason")),
    ]
    for i, hypothesis in enumerate(payload.get("hypotheses", [])):
        if isinstance(hypothesis, dict):
            entries.append((f"hypotheses[{i}].statement", hypothesis.get("statement")))
    for i, phase in enumerate(payload.get("analysis_phases", [])):
        if isinstance(phase, dict):
            entries.append((f"analysis_phases[{i}].purpose", phase.get("purpose")))
    for i, check in enumerate(payload.get("diagnostic_checks", [])):
        if not isinstance(check, dict):
            continue
        prefix = f"diagnostic_checks[{i}]"
        for field in ("purpose", "expected_signal", "decision_relevance"):
            entries.append((f"{prefix}.{field}", check.get(field)))
        line = check.get("cross_section_line")
        if isinstance(line, dict):
            entries.append((f"{prefix}.cross_section_line.selection_reason", line.get("selection_reason")))
    for i, question in enumerate(payload.get("forecaster_questions", [])):
        entries.append((f"forecaster_questions[{i}]", question))
    return entries


def validate_step04_compact_plan_narrative(payload: dict[str, Any]) -> list[str]:
    return _validate_entries(step04_compact_plan_narrative_entries(payload))

def validate_step04_discovery_narrative(payload: dict[str, Any]) -> list[str]:
    return _validate_entries(step04_discovery_narrative_entries(payload))


def validate_step04_plan_narrative(payload: dict[str, Any]) -> list[str]:
    return _validate_entries(step04_plan_narrative_entries(payload))


def validate_step04_narrative(payload: dict[str, Any]) -> list[str]:
    return [
        *validate_step04_discovery_narrative(payload),
        *validate_step04_plan_narrative(payload),
    ]


def step07_response_narrative_entries(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    entries: list[tuple[str, Any]] = []
    for i, assessment in enumerate(payload.get("assessments", [])):
        if not isinstance(assessment, dict):
            continue
        prefix = f"assessments[{i}]"
        for field in ("findings", "contradictory_findings"):
            for j, finding in enumerate(assessment.get(field, [])):
                if isinstance(finding, dict):
                    entries.append((f"{prefix}.{field}[{j}].statement", finding.get("statement")))
        for j, value in enumerate(assessment.get("uncertainties", [])):
            entries.append((f"{prefix}.uncertainties[{j}]", value))
    return entries


def validate_step07_response_narrative(payload: dict[str, Any]) -> list[str]:
    return _validate_entries(step07_response_narrative_entries(payload))


def step07_result_narrative_entries(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    entries: list[tuple[str, Any]] = [
        ("expected_signal", payload.get("expected_signal")),
        ("decision_relevance", payload.get("decision_relevance")),
    ]
    for field in ("findings", "contradictory_findings"):
        for i, finding in enumerate(payload.get(field, [])):
            if isinstance(finding, dict):
                entries.append((f"{field}[{i}].statement", finding.get("statement")))
    for i, value in enumerate(payload.get("uncertainties", [])):
        entries.append((f"uncertainties[{i}]", value))
    return entries


def validate_step07_result_narrative(payload: dict[str, Any]) -> list[str]:
    return _validate_entries(step07_result_narrative_entries(payload))


def step08_response_narrative_entries(payload: dict[str, Any], *, include_source_text: bool = True) -> list[tuple[str, Any]]:
    entries: list[tuple[str, Any]] = []
    for i, item in enumerate(payload.get("hypothesis_assessments", [])):
        if not isinstance(item, dict):
            continue
        prefix = f"hypothesis_assessments[{i}]"
        if include_source_text:
            entries.append((f"{prefix}.statement", item.get("statement")))
        entries.append((f"{prefix}.rationale", item.get("rationale")))
        for j, value in enumerate(item.get("uncertainties", [])):
            entries.append((f"{prefix}.uncertainties[{j}]", value))
    for i, item in enumerate(payload.get("forecaster_question_answers", [])):
        if not isinstance(item, dict):
            continue
        prefix = f"forecaster_question_answers[{i}]"
        if include_source_text:
            entries.append((f"{prefix}.question", item.get("question")))
        entries.append((f"{prefix}.answer", item.get("answer")))
        for j, value in enumerate(item.get("uncertainties", [])):
            entries.append((f"{prefix}.uncertainties[{j}]", value))
    signal = payload.get("signal_assessment")
    if isinstance(signal, dict):
        entries.extend(
            [
                ("signal_assessment.direct_precipitation_assessment", signal.get("direct_precipitation_assessment")),
                ("signal_assessment.precursor_environment_assessment", signal.get("precursor_environment_assessment")),
                ("signal_assessment.physical_mechanism_summary", signal.get("physical_mechanism_summary")),
                ("signal_assessment.operational_surface_reason", signal.get("operational_surface_reason")),
            ]
        )
        for field in ("mechanism_evidence", "monitoring_evidence"):
            for i, evidence in enumerate(signal.get(field, [])):
                if isinstance(evidence, dict):
                    entries.append((f"signal_assessment.{field}[{i}].reason", evidence.get("reason")))
        for i, value in enumerate(signal.get("contradictory_evidence", [])):
            entries.append((f"signal_assessment.contradictory_evidence[{i}]", value))
    revision = payload.get("revision")
    if isinstance(revision, dict):
        if revision.get("revised_candidate_name") is not None:
            entries.append(("revision.revised_candidate_name", revision.get("revised_candidate_name")))
        if revision.get("candidate_name_revision_reason") is not None:
            entries.append(("revision.candidate_name_revision_reason", revision.get("candidate_name_revision_reason")))
    for i, value in enumerate(payload.get("uncertainties", [])):
        entries.append((f"uncertainties[{i}]", value))
    entries.append(("final_assessment", payload.get("final_assessment")))
    brief = payload.get("operational_brief")
    if isinstance(brief, dict):
        for field in ("key_signal", "key_limitation", "next_check"):
            entries.append((f"operational_brief.{field}", brief.get(field)))
    return entries


def validate_step08_response_narrative(payload: dict[str, Any], *, include_source_text: bool = True) -> list[str]:
    return _validate_entries(step08_response_narrative_entries(payload, include_source_text=include_source_text))


def forecaster_product_narrative_entries(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    entries: list[tuple[str, Any]] = []
    for group_name in ("surfaced_candidates", "non_surfaced_candidates"):
        for i, candidate in enumerate(payload.get(group_name, [])):
            if not isinstance(candidate, dict):
                continue
            prefix = f"{group_name}[{i}]"
            entries.append((f"{prefix}.candidate_name", candidate.get("candidate_name")))
            entries.append((f"{prefix}.final_assessment", candidate.get("final_assessment")))
            entries.append((f"{prefix}.physical_mechanism_summary", candidate.get("physical_mechanism_summary")))
            entries.append((f"{prefix}.operational_surface_reason", candidate.get("operational_surface_reason")))
            brief = candidate.get("operational_brief")
            if isinstance(brief, dict):
                for field in ("key_signal", "key_limitation", "next_check"):
                    entries.append((f"{prefix}.operational_brief.{field}", brief.get(field)))
            for field in ("mechanism_evidence", "monitoring_evidence"):
                for j, evidence in enumerate(candidate.get(field, [])):
                    if isinstance(evidence, dict):
                        entries.append((f"{prefix}.{field}[{j}].reason", evidence.get("reason")))
            if group_name == "non_surfaced_candidates":
                entries.append((f"{prefix}.validation_reason", candidate.get("validation_reason")))
                continue
            entries.append((f"{prefix}.direct_precipitation_assessment", candidate.get("direct_precipitation_assessment")))
            entries.append((f"{prefix}.precursor_environment_assessment", candidate.get("precursor_environment_assessment")))
            for field in ("contradictory_evidence", "uncertainties"):
                for j, value in enumerate(candidate.get(field, [])):
                    entries.append((f"{prefix}.{field}[{j}]", value))
            for j, answer in enumerate(candidate.get("forecaster_question_answers", [])):
                if not isinstance(answer, dict):
                    continue
                qprefix = f"{prefix}.forecaster_question_answers[{j}]"
                entries.append((f"{qprefix}.question", answer.get("question")))
                entries.append((f"{qprefix}.answer", answer.get("answer")))
                for k, value in enumerate(answer.get("uncertainties", [])):
                    entries.append((f"{qprefix}.uncertainties[{k}]", value))
            focus = candidate.get("key_environment_focus")
            if isinstance(focus, dict):
                entries.append((f"{prefix}.key_environment_focus.area_label", focus.get("area_label")))
            for j, block in enumerate(candidate.get("evidence_blocks", [])):
                if not isinstance(block, dict):
                    continue
                bprefix = f"{prefix}.evidence_blocks[{j}]"
                entries.append((f"{bprefix}.expected_signal", block.get("expected_signal")))
                for field in ("findings", "contradictory_findings", "uncertainties"):
                    for k, value in enumerate(block.get(field, [])):
                        entries.append((f"{bprefix}.{field}[{k}]", value))
                for k, quantitative in enumerate(block.get("quantitative_evidence", [])):
                    if isinstance(quantitative, dict):
                        entries.append((f"{bprefix}.quantitative_evidence[{k}].region_name", quantitative.get("region_name")))
    return entries


def validate_forecaster_product_narrative(payload: dict[str, Any]) -> list[str]:
    return _validate_entries(forecaster_product_narrative_entries(payload))
