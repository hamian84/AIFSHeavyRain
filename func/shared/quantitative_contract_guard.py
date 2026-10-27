"""Shared read-only guard for quantitative comparison identity.

A comparator's left/right order is independent of chronological forecast hours.
This guard rejects only unambiguous two-hour Korean `Fxxx보다` propositions.
"""
from __future__ import annotations

import re

_HOUR = re.compile(r"(?<![A-Za-z0-9])F(\d{1,3})(?!\d)")


def explicit_pairwise_hour_order(statement: str) -> tuple[int, int] | None:
    """Return (left, right) from an unambiguous two-hour `보다` sentence.

    Supports both `F018이 F006보다 크다` and `F006보다 F018이 크다`.
    Ambiguous/multi-comparator prose is left to existing full semantic checks.
    """
    text = str(statement or "")
    mentions = list(_HOUR.finditer(text))
    if len(mentions) != 2 or len({int(m.group(1)) for m in mentions}) != 2:
        return None
    right = []
    for match in mentions:
        tail = text[match.end():]
        if re.match(r"\s*보다", tail):
            right.append(match)
    if len(right) != 1:
        return None
    rhs = right[0]
    lhs = next(m for m in mentions if m is not rhs)
    return int(lhs.group(1)), int(rhs.group(1))


def inconsistent_pairwise_hour_order(
    statement: str, comparison_hours: object,
) -> tuple[int, int] | None:
    """Expected LHS/RHS hours if a structured pair contradicts explicit prose."""
    expected = explicit_pairwise_hour_order(statement)
    if expected is None:
        return None
    if not isinstance(comparison_hours, (list, tuple)) or tuple(comparison_hours) != expected:
        return expected
    return None


def opposite_majority_not_strengthen_wording(statement: str) -> bool:
    """Detect explicit *majority strengthens* prose in a not_strengthen claim.

    Conservative: requires a majority quantifier plus an unnegated strengthen
    predicate in the same short clause. Ambiguous/mixed statements are left to
    human/LLM semantic review, not silently rewritten.
    """
    text = str(statement or "")
    majority = re.search(r"(?:대부분|과반|절반\s*초과)", text)
    if majority is None:
        return False
    clause = re.split(r"[.!?\n;]", text[majority.end():], maxsplit=1)[0][:160]
    positive = re.search(r"(?:강화된다|강화되는|강화되었다|강화됨|강해진다)", clause)
    if positive is None:
        return False
    # Conservatively ignore contrastive sentences containing a negative claim.
    if re.search(r"(?:강화되지|강화되지\s*않|강해지지)", clause[:positive.start()]):
        return False
    return True


# The currently enforced specific v2 comparator is *only* a comparison
# between two forecast hours. A fixed numeric cutoff is a different physical
# proposition and cannot be represented by comparison_forecast_hours.
_FIXED_NUMERIC_THRESHOLD = re.compile(
    r"(?<![\w.])(?:\d{1,4}(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(?:mm|㎜|hPa|%|m/s)\s*(?:를|을|이|가|보다)?\s*"
    r"(?:초과(?:한다|하는|했다|함)?|넘(?:는다|는|었다|음|어)|"
    r"이상|미만|이하|웃돌|밑돌|상회|하회|크(?:다|며|고)|작(?:다|으며))"
)

def absolute_threshold_in_hour_comparison(statement: str) -> str | None:
    """Return a clear absolute-unit threshold phrase, if any.

    Use only for a one-claim greater_than/less_than *time-pair* contract.
    A time-pair numeric comparison cannot establish `F096 > 1 mm`; rejecting
    the malformed contract is safer than changing the hypothesis or verdict.
    Statements without an explicit F-hour or cutoff predicate are untouched.
    """
    text = str(statement or "")
    if not _HOUR.search(text):
        return None
    match = _FIXED_NUMERIC_THRESHOLD.search(text)
    return match.group(0) if match else None
