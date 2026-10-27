"""Fail-closed quantitative grounding for a forecaster's Step08 question answer.

Only unambiguous, Python-verified, display-distinguishable quantitative
comparisons of the *same* region/quantity are eligible, including reversed
pairwise point order and two-point temporal trends. The rule does not infer
meteorological importance, infer values for other regions, or replace the AI's
qualitative assessment. It catches omission/denial of already verified facts
when the original question explicitly asks for a temporal comparison.
"""
from __future__ import annotations

import copy
from decimal import Decimal
import re
from typing import Any

CONTRACT_VERSION = "1.2.0"
_PRECIP = frozenset({"tp_6h_increment_mm", "tp_accum_mm"})
# A peak/maximum at one lead time is a value/position question, not a
# temporal comparison.  Do not force a different statistic's two-time QREL.
_TIME_QUESTION = re.compile(r"이후|전후|변화|약해|약화|감소|줄어|증가|늘어|강해|유지|지속")
_MAX_QUESTION = re.compile(r"최대\s*(?:6\s*시간\s*)?강수량|6\s*시간\s*강수량(?:의|\s*)\s*(?:최대|최댓값)|강수\s*강도.{0,15}최대|최대\s*위치")
_MEAN_QUESTION = re.compile(r"평균\s*(?:6\s*시간\s*)?강수량|6\s*시간\s*강수량(?:의|\s*)\s*평균|권역\s*평균")
_PRECIP_QUESTION = re.compile(r"강수량|강수\s*강도")
_NUMBER_BOUNDARY = r"(?<![\d.]){}(?![\d.])"


def _point_id(point: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(point.get("physical_diagnostic") or point.get("feature_id") or ""),
        str(point.get("region_id") or ""),
        str(point.get("statistic") or ""),
        str(point.get("units") or ""),
    )


def _extremum_time_question(question: str) -> bool:
    """Only recognize a maximum's *time*, never a one-hour maximum location."""
    return bool(
        _MAX_QUESTION.search(question)
        and re.search(r"F\d{3}", question)
        and re.search(r"최대.{0,30}(?:나타나|시각|언제|시간|인가|맞나)", question)
        and not re.search(r"최대\s*위치(?:는|가|를|에서|와)", question)
    )


def _eligible(question: str, relation: dict[str, Any]) -> dict[str, Any] | None:
    if not (
        _PRECIP_QUESTION.search(question)
        and (_TIME_QUESTION.search(question) or _extremum_time_question(question))
    ):
        return None
    relation_type = relation.get("relation_type")
    if relation_type not in {"pairwise_comparison", "temporal_trend"}:
        return None
    points = relation.get("points")
    if not isinstance(points, list) or len(points) != 2 or not all(isinstance(p, dict) for p in points):
        return None
    p0, p1 = points
    identity = _point_id(p0)
    if identity != _point_id(p1) or identity[0] not in _PRECIP or identity[2] not in {"max", "mean"} or identity[3] != "mm":
        return None
    # Never substitute a verified regional average for a maximum, or vice versa.
    if identity[2] == "mean" and _MAX_QUESTION.search(question):
        return None
    if identity[2] == "max" and _MEAN_QUESTION.search(question):
        return None
    if relation_type == "temporal_trend" and identity[0] != "tp_6h_increment_mm":
        return None
    if identity[2] == "mean" and identity[0] != "tp_6h_increment_mm":
        return None
    try:
        h0, h1 = int(p0["forecast_hour"]), int(p1["forecast_hour"])
    except (TypeError, ValueError, KeyError):
        return None
    if h0 == h1:
        return None
    # QREL points are not guaranteed chronological. The Python-calculated
    # first_greater_than_second relation uses their *original* order. Normalize
    # by lead hour only after independently checking that raw display contract.
    raw_values = (str(p0.get("display_value") or ""), str(p1.get("display_value") or ""))
    if not all(re.fullmatch(r"\d+(?:\.\d+)?", x) for x in raw_values):
        return None
    if raw_values[0] == raw_values[1]:
        return None
    raw_direction = relation.get("display_relation")
    if relation_type == "pairwise_comparison":
        if raw_direction not in {"first_greater_than_second", "first_less_than_second"}:
            return None
        if (float(raw_values[0]) > float(raw_values[1])) != (raw_direction == "first_greater_than_second"):
            return None
    else:
        if raw_direction not in {"increasing", "decreasing"} or h0 >= h1:
            return None
        if (float(raw_values[0]) < float(raw_values[1])) != (raw_direction == "increasing"):
            return None
    if h0 > h1:
        h0, h1 = h1, h0
        raw_values = (raw_values[1], raw_values[0])
    specific_hours = {int(v) for v in re.findall(r"F(\d{3})", question)}
    if specific_hours and not specific_hours.issubset({h0, h1}):
        return None
    return {
        "relation_ref": str(relation.get("relation_ref") or ""),
        "check_id": str(relation.get("check_id") or ""),
        "region_name": str(p0.get("region_name") or ""),
        "statistic": identity[2],
        "hours": (h0, h1),
        "display_values": raw_values,
        "direction": "increase" if float(raw_values[0]) < float(raw_values[1]) else "decrease",
    }


def required_facts(question: str, verified_relations: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Require only an unambiguous comparison of the statistic and time scope."""
    matching = []
    for ref, relation in verified_relations.items():
        if not isinstance(relation, dict):
            continue
        fact = _eligible(question, relation)
        if fact is not None and fact["relation_ref"] == ref and fact["region_name"] and fact["check_id"]:
            matching.append(fact)
    explicit_regions = [f for f in matching if f["region_name"] in question]
    if explicit_regions:
        matching = explicit_regions
    if len(matching) == 1:
        return matching
    # A generic two-time "precipitation intensity" question may have verified
    # maximum and average statistics for exactly one region and identical hours.
    # Include both *only* when their directions agree. Never infer the full
    # candidate's intensity/continuity from a single region's two-time values.
    if len(matching) == 2 and "강수 강도" in question and len(re.findall(r"F\d{3}", question)) >= 2:
        a, b = matching
        if (a["region_name"], a["hours"], a["direction"]) == (b["region_name"], b["hours"], b["direction"]) and {a["statistic"], b["statistic"]} == {"max", "mean"}:
            return sorted(matching, key=lambda f: 0 if f["statistic"] == "max" else 1)
    return []


def requirements_for_questions(questions: list[dict[str, Any]], verified_relations: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    required = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        for fact in required_facts(str(question.get("question") or ""), verified_relations):
            required.append({"forecaster_question_id": question.get("forecaster_question_id"), **fact})
    return required


def question_answer_errors(questions: list[dict[str, Any]], verified_relations: dict[str, dict[str, Any]]) -> list[str]:
    """Verify scoped, displayed facts; broader-scope uncertainties remain legal."""
    errors: list[str] = []
    for row in questions:
        if not isinstance(row, dict):
            continue
        question_id = str(row.get("forecaster_question_id") or "")
        facts = required_facts(str(row.get("question") or ""), verified_relations)
        for fact in facts:
            answer = str(row.get("answer") or "")
            ref, check = fact["relation_ref"], fact["check_id"]
            if check not in (row.get("evidence_check_ids") or []):
                errors.append(f"{question_id}: verified check {check} is missing from evidence_check_ids")
            if ref not in (row.get("quantitative_relation_ids") or []):
                errors.append(f"{question_id}: verified comparison {ref} is missing from quantitative_relation_ids")
            region = fact["region_name"]
            if region not in answer:
                errors.append(f"{question_id}: region {region} is missing from answer")
            for value in fact["display_values"]:
                if not re.search(_NUMBER_BOUNDARY.format(re.escape(value)), answer):
                    errors.append(f"{question_id}: verified displayed precipitation value {value} mm is missing")
            # If lead-hour labels are used, the values must belong to the
            # correct times, not merely appear somewhere in the same answer.
            for hour, value in zip(fact["hours"], fact["display_values"], strict=True):
                hour_token = f"F{hour:03d}"
                if hour_token not in answer:
                    errors.append(f"{question_id}: verified forecast hour {hour_token} is missing")
                    continue
                between = r"(?:(?!F\d{3}).){0,60}?"
                paired = (
                    re.search(re.escape(hour_token) + between + _NUMBER_BOUNDARY.format(re.escape(value)), answer)
                    or re.search(_NUMBER_BOUNDARY.format(re.escape(value)) + between + re.escape(hour_token), answer)
                )
                if not paired:
                    errors.append(f"{question_id}: {hour_token} is not paired with its verified value {value} mm")
            if fact["statistic"] == "max":
                if not re.search(r"(?:최대\s*6\s*시간\s*강수량|6\s*시간\s*강수량(?:의|\s*)\s*(?:최대|최댓값))", answer):
                    errors.append(f"{question_id}: answer must identify regional maximum 6-hour precipitation")
            else:
                if not re.search(r"(?:평균\s*6\s*시간\s*강수량|6\s*시간\s*강수량(?:의|\s*)\s*평균|6\s*시간\s*평균\s*강수량)", answer):
                    errors.append(f"{question_id}: answer must identify regional mean 6-hour precipitation")
                if not any(f["statistic"] == "max" for f in facts) and re.search(r"최대\s*6\s*시간\s*강수량|격자\s*최대(?:값)?", answer):
                    errors.append(f"{question_id}: regional mean is incorrectly described as a grid maximum")
            if fact["direction"] == "decrease":
                if not re.search(r"감소|낮아|줄어|약화", answer):
                    errors.append(f"{question_id}: verified regional decrease is missing")
            elif not re.search(r"증가|높아|늘어|강화", answer):
                errors.append(f"{question_id}: verified regional increase is missing")
            for sentence in re.split(r"(?<=[.?!。])\s*", answer):
                if re.search(r"후보\s*전체|모든\s*(?:운영)?권역", sentence) and re.search(r"감소|약화|증가|강화", sentence):
                    if not re.search(r"일반화.{0,8}(?:어렵|없|불가)|단정.{0,8}(?:어렵|없|불가)|확정.{0,8}(?:어렵|없|불가)|판단.{0,8}(?:어렵|없|불가)", sentence):
                        errors.append(f"{question_id}: one-region comparison is incorrectly generalized to the entire candidate")
            # The mandatory region/value/direction facts guard against a blanket
            # 'not known'. Reject a denial explicitly targeting the measured
            # quantitative comparison, but allow uncertainty for *other* regions.
            for sentence in re.split(r"(?<=[.?!。])\s*", answer):
                if ("정량" in sentence or "최대 6시간 강수량" in sentence or "평균 6시간 강수량" in sentence) and re.search(r"확정.{0,8}(?:어렵|불가|없)|(?:알|판단|확인|계산|산출).{0,7}수\s*없|불명확", sentence):
                    if not re.search(r"다른\s*(?:운영)?권역|후보\s*전체|전체\s*권역|다른\s*지역", sentence):
                        errors.append(f"{question_id}: denies an available quantitative comparison")
    return errors


def reconcile_legacy_question_answers(
    questions: list[dict[str, Any]],
    verified_relations: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Safely migrate *cached* invalid, uniquely grounded quantity-only answers.

    This operation is deliberately NOT an AI judgment repair: it replaces the
    entire stale question-answer evidence object with a scoped, deterministic
    statement of an already Python-verified pairwise relation. Other questions
    and the candidate's meteorological assessment are never altered. Ambiguous
    relationships are never guessed; the normal validator remains authoritative.
    """
    recovered = copy.deepcopy(questions)
    ids: list[str] = []
    for row in recovered:
        if not isinstance(row, dict):
            continue
        if not question_answer_errors([row], verified_relations):
            continue
        facts = required_facts(str(row.get("question") or ""), verified_relations)
        if not facts:
            continue
        statements: list[str] = []
        scope_names: list[str] = []
        for fact in facts:
            h0, h1 = fact["hours"]
            v0, v1 = fact["display_values"]
            delta = abs(Decimal(v0) - Decimal(v1))
            quant = max(len(v0.partition(".")[2]), len(v1.partition(".")[2]))
            delta_label = f"{delta:.{quant}f}"
            direction = "감소" if fact["direction"] == "decrease" else "증가"
            region = fact["region_name"]
            metric = "최대 6시간 강수량" if fact["statistic"] == "max" else "평균 6시간 강수량"
            scope = "격자 최대값" if fact["statistic"] == "max" else "권역 평균값"
            statements.append(
                f"{region}의 {metric}은 F{h0:03d}에 {v0} mm, "
                f"F{h1:03d}에 {v1} mm로 {delta_label} mm {direction}했습니다."
            )
            scope_names.append(scope)
            row["evidence_check_ids"] = sorted(set(row.get("evidence_check_ids") or []) | {fact["check_id"]})
            row["quantitative_relation_ids"] = sorted(set(row.get("quantitative_relation_ids") or []) | {fact["relation_ref"]})
        scope = " 및 ".join(scope_names)
        region = facts[0]["region_name"]
        row["answer"] = " ".join(statements) + (
            f" 이는 {region} 한 운영권역의 {scope} 비교이며, 다른 운영권역이나 "
            "후보 전체의 강수량 변화, 동일 강수계의 이동·연속성은 "
            "이 정량 비교만으로 단정할 수 없습니다."
        )
        row["uncertainties"] = [
            "다른 운영권역과 후보 전체의 강수량 변화 및 동일 강수계의 연속성은 이 권역의 두 시각 정량 비교만으로 판단할 수 없습니다."
        ]
        # A correct canonical sentence must still pass the same release gate.
        if question_answer_errors([row], verified_relations):
            raise ValueError("deterministic quantitative-answer migration failed its own validator")
        ids.append(str(row.get("forecaster_question_id") or ""))
    return recovered, ids
