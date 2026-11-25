"""Synoptic evidence availability and operational decision boundaries."""
from __future__ import annotations

from typing import Any

EXPLANATION_ONLY_DIAGNOSTICS = frozenset({"synoptic_context"})


def synoptic_context_hours(support_catalog: dict[str, Any]) -> list[int]:
    return next(
        (
            row["allowed_forecast_hours"]
            for row in support_catalog.get("spatial_field_products", [])
            if row.get("product_id") == "SYNOPTIC_CONTEXT_CORE"
        ),
        [],
    )


# Status/anchor fields are operational decisions, not background interpretation.
# mechanism_evidence and monitoring_evidence may cite a synoptic Check, but
# only with effect=context (checked below). Context cannot establish a tier.
_OPERATIONAL_EVIDENCE_FIELDS = (
    "signal_evidence_check_ids",
    "validated_precursor_evidence",
    "spatiotemporal_alignment_evidence_check_ids",
    "environment_persistence_evidence_check_ids",
)
_CONTEXT_EVIDENCE_FIELDS = frozenset({"mechanism_evidence", "monitoring_evidence"})


def synoptic_check_ids(candidate_group: dict[str, Any]) -> set[str]:
    """Return plan Checks that represent only explanatory synoptic context."""
    ctx = candidate_group.get("candidate_context")
    plan = ctx.get("analysis_plan") if isinstance(ctx, dict) else None
    rows = plan.get("variable_checks", []) if isinstance(plan, dict) else []
    return {
        str(row["check_id"])
        for row in rows
        if isinstance(row, dict)
        and row.get("diagnostic_id") in EXPLANATION_ONLY_DIAGNOSTICS
        and isinstance(row.get("check_id"), str)
        and row["check_id"]
    }


def _structured_ids(data: Any) -> set[str]:
    """Search IDs only; ignore meteorological prose to avoid false positives."""
    ids: set[str] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            if key in {"check_id", "evidence_check_id"} and isinstance(value, str):
                ids.add(value)
            elif key.endswith("_ids") and isinstance(value, list):
                ids.update(item for item in value if isinstance(item, str))
            else:
                ids.update(_structured_ids(value))
    elif isinstance(data, list):
        for value in data:
            if isinstance(value, str) and value.startswith("CHK_"):
                ids.add(value)
            else:
                ids.update(_structured_ids(value))
    return ids


def operational_synoptic_leaks(candidate_group: dict[str, Any], signal: dict[str, Any]) -> list[str]:
    forbidden = synoptic_check_ids(candidate_group)
    if not forbidden:
        return []
    referenced: set[str] = set()
    for key in _OPERATIONAL_EVIDENCE_FIELDS:
        referenced.update(_structured_ids(signal.get(key)))
    # Extra insurance: any newly added structured evidence field within the
    # signal assessment must also exclude explanation-only checks.
    for key, val in signal.items():
        if key in _CONTEXT_EVIDENCE_FIELDS:
            # An interpreted context Check belongs in the physical narrative;
            # other effects would make it a standalone strength/weakness anchor.
            if isinstance(val, list):
                referenced.update(
                    str(item["check_id"])
                    for item in val
                    if isinstance(item, dict)
                    and isinstance(item.get("check_id"), str)
                    and item.get("effect") != "context"
                )
            continue
        if key not in _OPERATIONAL_EVIDENCE_FIELDS and (
            "evidence" in key or key.endswith("_check_ids")
        ):
            referenced.update(_structured_ids(val))
    return sorted(forbidden & referenced)
