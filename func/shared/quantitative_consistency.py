"""Deterministic quantitative-relation checks for Pass2.

The AI decides which feature, region, statistic, and forecast hours are relevant.
This module does not decide meteorological importance. It only computes whether
an AI-authored quantitative relation is arithmetically consistent with the
resolved Step05 values.
"""

from __future__ import annotations

from numbers import Real
from typing import Any


RELATION_TYPES = frozenset({"pairwise_comparison", "temporal_trend", "extremum"})
PAIRWISE_RELATIONS = frozenset({
    "first_greater_than_second",
    "first_less_than_second",
    "approximately_equal",
})
TREND_RELATIONS = frozenset({"increasing", "decreasing", "approximately_steady", "non_monotonic"})
EXTREMUM_RELATIONS = frozenset({"maximum_at", "minimum_at"})
COMPUTED_RELATIONS = PAIRWISE_RELATIONS | TREND_RELATIONS | EXTREMUM_RELATIONS | frozenset({"ambiguous_extremum"})

# Relative tolerance is deliberately small. These are source values already
# produced by the deterministic Step05 calculation rather than noisy image
# measurements. The tolerance only avoids floating-point equality artifacts.
_RELATIVE_TOLERANCE = 1.0e-9
_ABSOLUTE_TOLERANCE = 1.0e-12


_DISPLAY_PRECIPITATION_FEATURES = frozenset({"tp_6h_increment_mm", "tp_accum_mm"})


def format_quantitative_display_value(feature_id: str, value: float) -> str:
    """Format one quantitative value exactly as Step10 presents it to forecasters."""
    numeric = float(value)
    if feature_id in _DISPLAY_PRECIPITATION_FEATURES:
        return f"{numeric:.1f}"
    if numeric == 0:
        return "0.00"
    if 0 < numeric < 0.005:
        return "<0.01"
    if -0.005 < numeric < 0:
        return ">-0.01"
    return f"{numeric:.2f}"


def _display_comparison_value(feature_id: str, value: float) -> float:
    """Map a raw value to the numerical bucket represented by its public display text."""
    numeric = float(value)
    if feature_id in _DISPLAY_PRECIPITATION_FEATURES:
        return float(f"{numeric:.1f}")
    if numeric == 0:
        return 0.0
    if 0 < numeric < 0.005:
        return 0.005
    if -0.005 < numeric < 0:
        return -0.005
    return float(f"{numeric:.2f}")


def compute_display_quantitative_relation(resolved_relation: dict[str, Any]) -> dict[str, Any]:
    """Recompute a verified relation at the precision shown to forecasters.

    Raw Step05 values remain authoritative for deterministic validation.  This
    function only prevents public prose from claiming a distinction that
    disappears after Step10's display rounding.
    """
    relation_type = str(resolved_relation.get("relation_type") or "")
    points = resolved_relation.get("points")
    if relation_type not in RELATION_TYPES or not isinstance(points, list) or len(points) < 2:
        raise QuantitativeConsistencyError("resolved relation is incomplete for display comparison")
    feature_id = str(points[0].get("feature_id") or points[0].get("physical_diagnostic") or "")
    if not feature_id:
        raise QuantitativeConsistencyError("resolved relation has no feature identity")
    values = [_number(point.get("resolved_value"), label="resolved_value") for point in points]
    display_values = [_display_comparison_value(feature_id, value) for value in values]
    display_text_values = [format_quantitative_display_value(feature_id, value) for value in values]
    hours = [int(point["forecast_hour"]) for point in points]

    display_forecast_hour: int | None = None
    if relation_type == "pairwise_comparison":
        if len(display_values) != 2:
            raise QuantitativeConsistencyError("pairwise display comparison requires exactly two points")
        comparison = _compare(display_values[0], display_values[1], 0.0)
        display_relation = (
            "first_greater_than_second" if comparison > 0 else
            "first_less_than_second" if comparison < 0 else
            "approximately_equal"
        )
    elif relation_type == "temporal_trend":
        comparisons = [
            _compare(display_values[index + 1], display_values[index], 0.0)
            for index in range(len(display_values) - 1)
        ]
        if comparisons and all(value > 0 for value in comparisons):
            display_relation = "increasing"
        elif comparisons and all(value < 0 for value in comparisons):
            display_relation = "decreasing"
        elif comparisons and all(value == 0 for value in comparisons):
            display_relation = "approximately_steady"
        else:
            display_relation = "non_monotonic"
    else:
        raw_relation = str(resolved_relation.get("computed_relation") or "")
        if raw_relation == "maximum_at":
            target = max(display_values)
            relation_name = "maximum_at"
        elif raw_relation == "minimum_at":
            target = min(display_values)
            relation_name = "minimum_at"
        else:
            return {
                "display_relation": raw_relation or "ambiguous_extremum",
                "display_forecast_hour": None,
                "display_values": display_text_values,
            }
        matching = [hour for hour, value in zip(hours, display_values, strict=True) if value == target]
        if len(matching) == 1:
            display_relation = relation_name
            display_forecast_hour = matching[0]
        else:
            display_relation = "ambiguous_extremum"

    return {
        "display_relation": display_relation,
        "display_forecast_hour": display_forecast_hour,
        "display_values": display_text_values,
    }


SIGNED_FEATURE_SEMANTICS: dict[str, dict[str, str]] = {
    "convergence925_1e5_s": {
        "positive_state": "convergence",
        "negative_state": "divergence",
        "zero_state": "neutral",
    },
}


def quantitative_feature_semantics(feature_id: str) -> dict[str, str] | None:
    """Return deterministic sign semantics for features whose sign has physical meaning."""
    semantics = SIGNED_FEATURE_SEMANTICS.get(str(feature_id))
    return dict(semantics) if semantics is not None else None


def compute_physical_transition(feature_id: str, values: list[float]) -> str | None:
    """Describe only sign-state transitions; never generate forecaster narrative."""
    semantics = SIGNED_FEATURE_SEMANTICS.get(str(feature_id))
    if semantics is None or len(values) < 2:
        return None
    first = float(values[0])
    last = float(values[-1])

    def state(value: float) -> str:
        if value > 0:
            return semantics["positive_state"]
        if value < 0:
            return semantics["negative_state"]
        return semantics["zero_state"]

    first_state = state(first)
    last_state = state(last)
    if first_state != last_state:
        return f"{first_state}_to_{last_state}"
    if first_state == "neutral":
        return "neutral_steady"
    if last > first:
        return "convergence_strengthening" if first_state == "convergence" else "divergence_weakening"
    if last < first:
        return "convergence_weakening" if first_state == "convergence" else "divergence_strengthening"
    return f"{first_state}_steady"


class QuantitativeConsistencyError(ValueError):
    """Invalid relation structure that cannot be compared deterministically."""


def _number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise QuantitativeConsistencyError(f"{label} must be numeric")
    return float(value)


def _tolerance(values: list[float]) -> float:
    scale = max([1.0, *[abs(value) for value in values]])
    return max(_ABSOLUTE_TOLERANCE, scale * _RELATIVE_TOLERANCE)


def _selector_identity(points: list[dict[str, Any]]) -> tuple[str, str, str, str | None]:
    if len(points) < 2:
        raise QuantitativeConsistencyError("quantitative relation requires at least two resolved points")
    first = points[0]
    feature = str(first.get("physical_diagnostic") or "")
    statistic = str(first.get("statistic") or "")
    units = str(first.get("units") or "")
    if not all((feature, statistic, units)):
        raise QuantitativeConsistencyError("resolved quantitative points have incomplete identity")
    regions: list[str] = []
    selector_keys: set[tuple[str, int, str, str]] = set()
    for point in points:
        point_feature = str(point.get("physical_diagnostic") or "")
        point_statistic = str(point.get("statistic") or "")
        point_units = str(point.get("units") or "")
        region = str(point.get("region_id") or "")
        hour = point.get("forecast_hour")
        if (point_feature, point_statistic, point_units) != (feature, statistic, units):
            raise QuantitativeConsistencyError(
                "all points in one quantitative relation must use the same feature, statistic, and units"
            )
        if not region or isinstance(hour, bool) or not isinstance(hour, int):
            raise QuantitativeConsistencyError("resolved quantitative relation has invalid region/hour identity")
        selector_key = (region, int(hour), point_feature, point_statistic)
        if selector_key in selector_keys:
            raise QuantitativeConsistencyError("resolved quantitative relation repeats the same region/hour selector")
        selector_keys.add(selector_key)
        regions.append(region)
    common_region = regions[0] if all(region == regions[0] for region in regions) else None
    return feature, statistic, units, common_region


def _compare(a: float, b: float, tolerance: float) -> int:
    delta = a - b
    if abs(delta) <= tolerance:
        return 0
    return 1 if delta > 0 else -1


def compute_quantitative_relation(
    relation: dict[str, Any],
    resolved_points: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute the deterministic relation represented by ``resolved_points``.

    ``resolved_points`` are expected to be produced by Step07's existing
    selector resolver, so this function never reads files and never chooses
    what should be compared.
    """

    relation_type = str(relation.get("relation_type") or "")
    if relation_type not in RELATION_TYPES:
        raise QuantitativeConsistencyError(f"unsupported relation_type={relation_type!r}")
    feature_id, statistic, units, common_region_id = _selector_identity(resolved_points)
    values = [_number(point.get("resolved_value"), label="resolved_value") for point in resolved_points]
    hours = [int(point["forecast_hour"]) for point in resolved_points]
    # Precipitation comparisons use the same 0.1 mm precision exposed on the
    # operational page. Raw values remain stored in Step05/Step07 and the
    # 1.0 mm direct-precipitation presence threshold still uses raw values.
    # This only prevents an AI relation such as 0.1094 > 0.0586 from being
    # treated as an operational distinction when both are displayed as 0.1 mm.
    comparison_values = (
        [_display_comparison_value(feature_id, value) for value in values]
        if feature_id in _DISPLAY_PRECIPITATION_FEATURES
        else values
    )
    tolerance = 0.0 if feature_id in _DISPLAY_PRECIPITATION_FEATURES else _tolerance(values)

    computed_forecast_hour: int | None = None
    if relation_type == "pairwise_comparison":
        if len(values) != 2:
            raise QuantitativeConsistencyError("pairwise_comparison requires exactly two points")
        comparison = _compare(comparison_values[0], comparison_values[1], tolerance)
        computed_relation = (
            "first_greater_than_second" if comparison > 0 else
            "first_less_than_second" if comparison < 0 else
            "approximately_equal"
        )
    elif relation_type == "temporal_trend":
        if len(values) < 2:
            raise QuantitativeConsistencyError("temporal_trend requires at least two points")
        if common_region_id is None:
            raise QuantitativeConsistencyError("temporal_trend requires one common region")
        if len(set(hours)) != len(hours):
            raise QuantitativeConsistencyError("temporal_trend repeats a forecast hour")
        if hours != sorted(hours):
            raise QuantitativeConsistencyError("temporal_trend selectors must be ordered by forecast_hour")
        comparisons = [
            _compare(comparison_values[index + 1], comparison_values[index], tolerance)
            for index in range(len(comparison_values) - 1)
        ]
        if comparisons and all(value > 0 for value in comparisons):
            computed_relation = "increasing"
        elif comparisons and all(value < 0 for value in comparisons):
            computed_relation = "decreasing"
        elif comparisons and all(value == 0 for value in comparisons):
            computed_relation = "approximately_steady"
        else:
            computed_relation = "non_monotonic"
    else:
        if common_region_id is None:
            raise QuantitativeConsistencyError("extremum requires one common region")
        if len(set(hours)) != len(hours):
            raise QuantitativeConsistencyError("extremum repeats a forecast hour")
        claimed_relation = str(relation.get("claimed_relation") or "")
        if claimed_relation not in EXTREMUM_RELATIONS:
            raise QuantitativeConsistencyError("extremum relation must claim maximum_at or minimum_at")
        target = max(comparison_values) if claimed_relation == "maximum_at" else min(comparison_values)
        matching = [
            hour
            for hour, value in zip(hours, comparison_values, strict=True)
            if abs(value - target) <= tolerance
        ]
        if len(matching) == 1:
            computed_relation = claimed_relation
            computed_forecast_hour = matching[0]
        else:
            computed_relation = "ambiguous_extremum"
            computed_forecast_hour = None

    claimed_relation = str(relation.get("claimed_relation") or "")
    claimed_forecast_hour = relation.get("claimed_forecast_hour")
    if claimed_forecast_hour is not None and (isinstance(claimed_forecast_hour, bool) or not isinstance(claimed_forecast_hour, int)):
        raise QuantitativeConsistencyError("claimed_forecast_hour must be an integer or null")

    consistent = claimed_relation == computed_relation
    if relation_type == "extremum":
        consistent = consistent and claimed_forecast_hour == computed_forecast_hour
    elif claimed_forecast_hour is not None:
        raise QuantitativeConsistencyError("claimed_forecast_hour is only valid for extremum relations")

    physical_transition = None
    if common_region_id is not None and len(set(hours)) == len(hours) and len(hours) >= 2:
        chronological_values = [
            value for _hour, value in sorted(zip(hours, values, strict=True), key=lambda item: item[0])
        ]
        physical_transition = compute_physical_transition(feature_id, chronological_values)

    return {
        "relation_id": str(relation.get("relation_id") or ""),
        "relation_type": relation_type,
        "feature_id": feature_id,
        "region_id": common_region_id,
        "statistic": statistic,
        "units": units,
        "claimed_relation": claimed_relation,
        "computed_relation": computed_relation,
        "claimed_forecast_hour": claimed_forecast_hour,
        "computed_forecast_hour": computed_forecast_hour,
        "physical_transition": physical_transition,
        "consistent": bool(consistent),
    }


def relation_selector_keys(selectors: Any) -> set[tuple[str, str, int, str, str]]:
    """Return exact selector keys for subset checks; malformed rows are ignored here.

    Strict selector validation remains the responsibility of Step07's existing
    resolver. This helper is only used after schema validation to verify that a
    relation is backed by the same quantitative cells exposed in
    ``quantitative_claims``.
    """

    keys: set[tuple[str, str, int, str, str]] = set()
    if not isinstance(selectors, list):
        return keys
    for selector in selectors:
        if not isinstance(selector, dict):
            continue
        request_id = selector.get("request_id")
        feature_id = selector.get("feature_id")
        forecast_hour = selector.get("forecast_hour")
        region_id = selector.get("region_id")
        statistic = selector.get("statistic")
        if (
            isinstance(request_id, str) and request_id
            and isinstance(feature_id, str) and feature_id
            and isinstance(forecast_hour, int) and not isinstance(forecast_hour, bool)
            and isinstance(region_id, str) and region_id
            and isinstance(statistic, str) and statistic
        ):
            keys.add((request_id, feature_id, forecast_hour, region_id, statistic))
    return keys
