"""Authoritative text contract for the single supported convergence majority Check.

The Step05-derived Step07 aggregation, not a language model, owns region counts,
classification and exception names.  This module never changes the model vote.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping

from func.shared.quantitative_consistency import compute_physical_transition


CONTRACT_VERSION = "convergence-majority-authoritative-narrative-v1"
_STRENGTHENING = frozenset({
    "convergence_strengthening", "divergence_to_convergence", "neutral_to_convergence",
})


class MajorityNarrativeError(ValueError):
    """Malformed or contradictory Step07 authoritative quantitative evidence."""


def _integer(value: object) -> bool:
    return type(value) is int


def validated_majority_facts(aggregation: Mapping) -> dict:
    """Recheck all 2-hour cells and aggregate counts; fail closed on ambiguity."""
    if not isinstance(aggregation, Mapping) or aggregation.get("contract_version") != "candidate-overlap-majority-v1":
        raise MajorityNarrativeError("unsupported majority aggregation contract")
    if aggregation.get("feature_id") != "convergence925_1e5_s" or aggregation.get("statistic") != "mean":
        raise MajorityNarrativeError("unexpected feature/statistic")
    hours = aggregation.get("forecast_hours")
    if not isinstance(hours, list) or len(hours) != 2 or not all(_integer(h) for h in hours) or hours[0] >= hours[1]:
        raise MajorityNarrativeError("invalid majority comparison hours")
    rows = aggregation.get("regions")
    if not isinstance(rows, list) or not rows:
        raise MajorityNarrativeError("missing majority region rows")
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    not_names: list[str] = []
    yes_names: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            raise MajorityNarrativeError("invalid region row")
        region_id, name = row.get("region_id"), row.get("region_name")
        if not isinstance(region_id, str) or not region_id or not isinstance(name, str) or not name:
            raise MajorityNarrativeError("region identity absent")
        if region_id in seen_ids or name in seen_names:
            raise MajorityNarrativeError("duplicate majority region")
        seen_ids.add(region_id)
        seen_names.add(name)
        values = row.get("values")
        if not isinstance(values, list) or len(values) != 2:
            raise MajorityNarrativeError("incomplete majority region time series")
        by_hour = {}
        for point in values:
            if not isinstance(point, dict) or not _integer(point.get("forecast_hour")):
                raise MajorityNarrativeError("invalid forecast hour in region series")
            value = point.get("resolved_value")
            if type(value) not in (float, int):
                raise MajorityNarrativeError("missing numeric cell")
            hour = point["forecast_hour"]
            if hour in by_hour:
                raise MajorityNarrativeError("duplicate hour in region series")
            by_hour[hour] = float(value)
        if set(by_hour) != set(hours):
            raise MajorityNarrativeError("time mismatch in region series")
        import math
        if not all(math.isfinite(v) for v in by_hour.values()):
            raise MajorityNarrativeError("non-finite quantitative value")
        transition = compute_physical_transition("convergence925_1e5_s", [by_hour[h] for h in hours])
        strengthen = transition in _STRENGTHENING
        if row.get("physical_transition") != transition or row.get("strengthening") is not strengthen:
            raise MajorityNarrativeError("region physical transition differs from source values")
        (yes_names if strengthen else not_names).append(name)
    if (
        not all(_integer(aggregation.get(k)) for k in ("total_regions", "not_strengthening_count", "strengthening_count"))
        or aggregation["total_regions"] != len(rows)
        or aggregation["not_strengthening_count"] != len(not_names)
        or aggregation["strengthening_count"] != len(yes_names)
        or aggregation.get("not_strengthening_regions") != not_names
        or aggregation.get("strengthening_regions") != yes_names
    ):
        raise MajorityNarrativeError("majority counts or region names disagree with physical transitions")
    return {
        "contract_version": CONTRACT_VERSION,
        "forecast_hours": hours,
        "total_regions": len(rows),
        "not_strengthening_count": len(not_names),
        "strengthening_count": len(yes_names),
        "not_strengthening_regions": not_names,
        "strengthening_regions": yes_names,
    }


def majority_statements(aggregation: Mapping) -> tuple[str, str]:
    facts = validated_majority_facts(aggregation)
    first, last = facts["forecast_hours"]
    total = facts["total_regions"]
    not_count = facts["not_strengthening_count"]
    yes_names = facts["strengthening_regions"]
    confirmed = (
        f"F{first:03d}에서 F{last:03d}까지 공통 운영권역 {total}개 중 "
        f"{not_count}개에서 925 hPa 평균 수렴이 강화되지 않았습니다."
    )
    exceptions = (
        "수렴 강화가 확인된 운영권역은 " + ", ".join(yes_names) + "입니다."
        if yes_names else "수렴 강화가 확인된 운영권역은 없습니다."
    )
    return confirmed, exceptions


def canonicalize_majority_result(result: dict) -> dict:
    """Replace only exact-contract majority Check prose; keep ensemble/model vote untouched."""
    aggregation = result.get("majority_aggregation")
    if aggregation is None:
        return result
    confirmed, exceptions = majority_statements(aggregation)
    if result.get("quantitative_consistency_status") not in {"passed", "corrected"} or result.get("usable_for_synthesis") is not True:
        raise MajorityNarrativeError("unusable majority evidence cannot be canonicalized as confirmed facts")
    if result.get("assessment") != "observed" and validated_majority_facts(aggregation)["not_strengthening_count"] > validated_majority_facts(aggregation)["total_regions"] / 2:
        raise MajorityNarrativeError("majority assessment contradicts Python aggregation")
    output = copy.deepcopy(result)
    references = []
    for field in ("findings", "contradictory_findings"):
        for item in output.get(field, []):
            if isinstance(item, dict) and isinstance(item.get("evidence_refs"), list):
                for ref in item["evidence_refs"]:
                    if ref not in references:
                        references.append(ref)
    if not references:
        raise MajorityNarrativeError("majority narrative has no evidence references")
    output["findings"] = [{"statement": confirmed, "evidence_refs": copy.deepcopy(references)}]
    output["contradictory_findings"] = [{"statement": exceptions, "evidence_refs": copy.deepcopy(references)}] if aggregation["strengthening_count"] else []
    return output


def assert_majority_narrative_current(result: dict) -> None:
    if result.get("majority_aggregation") is None:
        return
    corrected = canonicalize_majority_result(result)
    if corrected["findings"] != result.get("findings") or corrected["contradictory_findings"] != result.get("contradictory_findings"):
        raise MajorityNarrativeError("outdated or inaccurate majority narrative; rerun Step07 migration")


def majority_digest(aggregation: Mapping) -> str:
    facts = validated_majority_facts(aggregation)
    return hashlib.sha256(json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_majority_product(product: dict) -> list[str]:
    """Require exact authoritative prose for every publicly rendered majority Check.

    Returns SHA-256 markers that the HTML renderer must emit for the same facts.
    """
    digests: list[str] = []
    for candidate in product.get("surfaced_candidates", []):
        if not isinstance(candidate, dict):
            raise MajorityNarrativeError("invalid surfaced candidate")
        for block in candidate.get("evidence_blocks", []):
            if not isinstance(block, dict):
                raise MajorityNarrativeError("invalid evidence block")
            aggregation = block.get("majority_aggregation")
            if aggregation is None:
                continue
            confirmed, exception = majority_statements(aggregation)
            expected_exception = [exception] if aggregation["strengthening_count"] else []
            if block.get("findings") != [confirmed] or block.get("contradictory_findings") != expected_exception:
                raise MajorityNarrativeError(
                    f"{candidate.get('candidate_id')}/{block.get('check_id')} published prose differs from authoritative counts"
                )
            digests.append(majority_digest(aggregation))
    return digests
