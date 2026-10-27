"""Bind public operational tier language to Step09's deterministic decision.

Step08 meteorological evidence remains untouched/auditable. Only unambiguous
LLM-authored tier *conclusions* are neutralized; ambiguous language is rejected
rather than making up new meteorological statements.
"""
from __future__ import annotations

import re
from typing import Any

# Explicit classification statements, not '우선 확인 필요성은 경계적' or
# '일반 감시보다 우선 확인' contextual comparisons.
_EXPLICIT = (
    ("general", re.compile(r"(?P<label>일반\s*감시\s*후보|일반\s*감시)(?=\s*(?:로|으로)\s*(?:판단|해석|분류|평가|간주|지정)|\s*수준(?:이다|입니다|으로))")),
    ("elevated", re.compile(r"(?P<label>우선\s*확인\s*대상|우선\s*확인\s*후보|우선\s*확인)(?=\s*(?:로|으로)\s*(?:판단|해석|분류|평가|간주|지정)|\s*수준(?:이다|입니다|으로))")),
    ("priority", re.compile(r"(?P<label>중점\s*감시\s*대상|중점\s*감시\s*후보|중점\s*감시)(?=\s*(?:로|으로)\s*(?:판단|해석|분류|평가|간주|지정)|\s*수준(?:이다|입니다|으로))")),
)


class TierNarrativeError(ValueError):
    pass


def conflicts(text: str, tier: str) -> list[str]:
    if tier not in {"priority", "elevated", "general", "hidden"}:
        raise TierNarrativeError(f"invalid deterministic tier: {tier!r}")
    return [label for label, pattern in _EXPLICIT if label != tier and pattern.search(text or "")]


def public_reason(raw: str, tier: str) -> tuple[str, bool]:
    """Remove only an explicit contradicting *tier label* from tier claims.

    Leave meteorological assertions and uncertainty language unchanged. Tier
    belongs to Step09, so a generic '감시후보' conclusion is the most that can
    safely be retained from a conflicting Step08 authored sentence.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise TierNarrativeError("empty operational narrative")
    result = raw
    changed = False
    for label, pattern in _EXPLICIT:
        if tier == label:
            continue
        # '수준' statements cannot be safely repaired by deleting the label.
        for match in reversed(list(pattern.finditer(result))):
            suffix = result[match.end():]
            if re.match(r"\s*수준", suffix):
                raise TierNarrativeError("unrepairable explicit tier-level statement")
            original = match.group("label")
            generic = "감시후보" if "후보" in original or label == "general" else "감시 대상"
            # Preserve Korean postposition: 감시후보로, 감시 대상으로.
            result = result[:match.start()] + generic + result[match.end():]
            changed = True
    if not result.strip() or conflicts(result, tier):
        raise TierNarrativeError("operational narrative conflicts with final tier")
    return result, changed


def canonical_guidance(guidance: Any, tier: str) -> tuple[Any, bool]:
    if guidance is None:
        return None, False
    if not isinstance(guidance, dict):
        raise TierNarrativeError("operational guidance is not an object")
    result = dict(guidance)
    changed = False
    # The current-action field contains the present operational interpretation;
    # other fields are observational follow-up and remain as-authored.
    if "current_action" in result:
        result["current_action"], changed = public_reason(result["current_action"], tier)
    return result, changed


def assert_public_tier(candidate: dict[str, Any]) -> None:
    tier = str(candidate.get("display_tier") or "")
    cid = str(candidate.get("candidate_id") or "unknown")
    for field, value in (
        ("operational_surface_reason", candidate.get("operational_surface_reason")),
        ("operational_guidance.current_action", (
            (candidate.get("operational_guidance") or {}).get("current_action")
            if isinstance(candidate.get("operational_guidance"), dict) else None
        )),
    ):
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise TierNarrativeError(f"{cid} {field} is empty or invalid")
        issues = conflicts(value, tier)
        if issues:
            raise TierNarrativeError(f"{cid} {field} contradicts display_tier={tier}: {issues}")
