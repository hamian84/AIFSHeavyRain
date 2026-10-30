"""Pass1/Pass2 시그널 분류, 분석 범위, 결정론적 감시 순위 규칙입니다."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Iterable


DETECTION_BASIS = {"direct_precipitation", "precursor_environment", "mixed"}
PRECIPITATION_STATUS = {"present", "weak", "absent", "uncertain"}
PRECURSOR_CATEGORIES = {
    "moisture_supply",
    "low_level_forcing",
    "mid_upper_forcing",
    "thermodynamic_support",
    "organization_persistence",
    "direct_precipitation",
}
PRECURSOR_CATEGORY_ORDER = (
    "moisture_supply",
    "low_level_forcing",
    "mid_upper_forcing",
    "thermodynamic_support",
    "organization_persistence",
    "direct_precipitation",
)
OPERATIONAL_PRECURSOR_CATEGORIES = (
    "moisture_supply",
    "low_level_forcing",
    "mid_upper_forcing",
)
OPERATIONAL_PRECURSOR_FORCING_CATEGORIES = {
    "low_level_forcing",
    "mid_upper_forcing",
}

# Korean directional qualifiers that may be attached to a standardized
# operational-region name in public narratives.  These labels are normalized
# to the same sector keys used by structured spatial_localization and
# final_spatial_identity.
OPERATIONAL_REGION_SECTOR_LABELS_KO = {
    "북동부": "northeast",
    "북동쪽": "northeast",
    "북동측": "northeast",
    "남동부": "southeast",
    "남동쪽": "southeast",
    "남동측": "southeast",
    "남서부": "southwest",
    "남서쪽": "southwest",
    "남서측": "southwest",
    "북서부": "northwest",
    "북서쪽": "northwest",
    "북서측": "northwest",
    "북부": "north",
    "북쪽": "north",
    "북측": "north",
    "남부": "south",
    "남쪽": "south",
    "남측": "south",
    "동부": "east",
    "동쪽": "east",
    "동측": "east",
    "서부": "west",
    "서쪽": "west",
    "서측": "west",
    "중심부": "central",
    "중앙부": "central",
    "중부": "central",
}
_OPERATIONAL_REGION_SECTOR_LABEL_PATTERN = re.compile(
    r"^(?:\s|의|내|해상|지역|부근|일대){0,8}(?P<label>"
    + "|".join(sorted((re.escape(label) for label in OPERATIONAL_REGION_SECTOR_LABELS_KO), key=len, reverse=True))
    + r")"
)


def operational_region_sector_mentions(
    text: Any,
    region_names_by_id: dict[str, str],
) -> list[dict[str, str]]:
    """Return explicit region+sector mentions from Korean public prose.

    A candidate-area overlap is not evidence for a sector.  This helper only
    recognizes a directional qualifier when it is syntactically attached to a
    standardized operational-region name (for example ``동해중부 광역 남부``
    or ``동해중부 북동쪽``).  Direction words later in a sentence, such as
    ``동해중부 광역의 강수영역이 북쪽으로 확대``, are intentionally not
    interpreted as a region-sector label.
    """
    if not isinstance(text, str) or not text.strip() or not isinstance(region_names_by_id, dict):
        return []
    variants: list[tuple[str, str, str]] = []
    for region_id, raw_name in region_names_by_id.items():
        name = str(raw_name or "").strip()
        if not name:
            continue
        names = {name}
        if name.endswith(" 광역"):
            names.add(name[:-3].strip())
        for variant in names:
            if variant:
                variants.append((variant, str(region_id), name))
    # Prefer the full standardized name before a compact alias that is its prefix.
    variants.sort(key=lambda row: (-len(row[0]), row[0], row[1]))

    mentions: list[dict[str, str]] = []
    seen: set[tuple[int, str, str]] = set()
    for variant, region_id, standard_name in variants:
        for match in re.finditer(re.escape(variant), text):
            tail = text[match.end(): match.end() + 24]
            sector_match = _OPERATIONAL_REGION_SECTOR_LABEL_PATTERN.search(tail)
            if sector_match is None:
                continue
            label = sector_match.group("label")
            sector = OPERATIONAL_REGION_SECTOR_LABELS_KO[label]
            key = (match.start(), region_id, sector)
            if key in seen:
                continue
            seen.add(key)
            mentions.append({
                "region_id": region_id,
                "region_name": standard_name,
                "matched_region_name": variant,
                "sector": sector,
                "sector_label": label,
            })
    return mentions
# This is the single deterministic source for Pass2 category-to-Check
# compatibility.  Categories without an explicit variable_role mapping are
# intentionally not accepted as independent anchors.
PRECURSOR_CATEGORY_COMPATIBLE_VARIABLE_ROLES = {
    "moisture_supply": {"moisture_supply"},
    "low_level_forcing": {"low_level_forcing"},
    "mid_upper_forcing": {"midlevel_forcing"},
    "thermodynamic_support": {"thermodynamic_support"},
    "organization_persistence": {"organization_persistence"},
    "direct_precipitation": {"hazard_magnitude"},
}

# ``assessment=observed`` means that a Check's *expected state* was observed.
# Some expected states are explicitly limiting for the operational precursor
# categories even when Step04 accidentally authored ``evidence_role=supporting``.
# These states must never become positive precursor anchors.  Ambiguous states
# such as ``move``, ``located_at``, ``greater_than`` and ``less_than`` are not
# classified here because their physical direction depends on the variable and
# the proposition; for those states the Step04 evidence role remains relevant.
NON_POSITIVE_PRECURSOR_EXPECTED_STATES = {
    "absent",
    "decrease",
    "not_strengthen",
    "weaken",
    "not_overlap",
    "contract",
    "split",
    "reorganize",
}

PRECURSOR_CATEGORY_SIGNAL_COMPONENT = {
    "moisture_supply": "moisture_supply",
    "low_level_forcing": "low_level_forcing",
    "mid_upper_forcing": "mid_upper_forcing",
    "thermodynamic_support": "thermodynamic_support",
    "organization_persistence": "organization_persistence",
    "direct_precipitation": "direct_precipitation",
}
SIGNAL_CLASSES = {
    "direct_environment_supported",
    "direct_environment_conditional",
    "direct_environment_unsupported",
    "direct_environment_unresolved",
    "precursor_watch",
    "partially_supported",
    "rejected",
    "insufficient_evidence",
}
CANDIDATE_SIGNAL_STATUSES = {
    "confirmed",
    "partial",
    "not_confirmed",
    "insufficient_evidence",
}
DIRECT_PRECIPITATION_MINIMUM_MM_6H = 1.0
SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY = "SYS_DIRECT_PRECIPITATION_PRESENCE"
OPERATIONAL_SURFACE_DECISIONS = {"surface", "do_not_surface", "not_applicable"}
PHYSICAL_CONSISTENCY_STATUSES = {
    "coherent",
    "conditionally_coherent",
    "incoherent",
    "insufficient_evidence",
}
MONITORING_VALUES = {"meaningful", "marginal", "low", "undetermined"}
DECISION_EVIDENCE_EFFECTS = {"supporting", "limiting", "mixed", "context"}
POSITIVE_VALIDATED_PRECURSOR_ASSESSMENTS = {"observed", "partially_observed"}
MONITORING_EMPHASIS = {"priority", "standard"}
CANDIDATE_TYPES = {"direct_precipitation", "precursor_environment"}
DISPLAY_TIERS = {"priority", "elevated", "general", "hidden"}
ENVIRONMENT_PERSISTENCE_STATUSES = {
    "confirmed",
    "limited",
    "insufficient_evidence",
    "not_applicable",
}
SIGNAL_CLASS_ORDER = {
    "direct_environment_supported": 0,
    "direct_environment_conditional": 1,
    "precursor_watch": 2,
    "direct_environment_unresolved": 3,
    "direct_environment_unsupported": 4,
    "partially_supported": 5,
    "insufficient_evidence": 6,
    "rejected": 7,
}


def derive_signal_class(
    direct_precipitation_status: Any,
    environment_support_status: Any,
) -> str:
    """Return the compatibility signal class from the two independent statuses."""
    direct = str(direct_precipitation_status)
    environment = str(environment_support_status)
    if direct not in CANDIDATE_SIGNAL_STATUSES or environment not in CANDIDATE_SIGNAL_STATUSES:
        raise ValueError("candidate signal statuses are invalid")
    if direct == "confirmed":
        if environment == "confirmed":
            return "direct_environment_supported"
        if environment == "partial":
            return "direct_environment_conditional"
        if environment == "insufficient_evidence":
            return "direct_environment_unresolved"
        return "direct_environment_unsupported"
    if environment == "confirmed":
        return "precursor_watch"
    if direct == environment == "insufficient_evidence":
        return "insufficient_evidence"
    if "partial" in {direct, environment}:
        return "partially_supported"
    return "rejected"


def normalize_bbox(value: Any) -> dict[str, float] | None:
    """Return the canonical bbox form or ``None`` when the supplied bbox is invalid."""
    if not isinstance(value, dict):
        return None
    try:
        bbox = {
            "west": float(value["west"]),
            "east": float(value["east"]),
            "south": float(value["south"]),
            "north": float(value["north"]),
        }
    except (KeyError, TypeError, ValueError):
        return None
    if bbox["west"] >= bbox["east"] or bbox["south"] >= bbox["north"]:
        return None
    if not (-180.0 <= bbox["west"] <= 360.0 and -180.0 <= bbox["east"] <= 360.0):
        return None
    if not (-90.0 <= bbox["south"] <= 90.0 and -90.0 <= bbox["north"] <= 90.0):
        return None
    return bbox


def normalize_time_window(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    try:
        window = {
            "forecast_hour_start": int(value["forecast_hour_start"]),
            "forecast_hour_end": int(value["forecast_hour_end"]),
        }
    except (KeyError, TypeError, ValueError):
        return None
    if window["forecast_hour_start"] > window["forecast_hour_end"]:
        return None
    return window


def _deduplicate_categories(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        category = str(value)
        if category in PRECURSOR_CATEGORIES and category not in result:
            result.append(category)
    return result


def derive_validated_precursor_categories(evidence: Any) -> list[str]:
    """Return category evidence labels in the fixed Pass2 category order."""
    if not isinstance(evidence, list):
        return []
    seen = {
        str(item.get("category"))
        for item in evidence
        if isinstance(item, dict) and str(item.get("category")) in PRECURSOR_CATEGORIES
    }
    return [category for category in PRECURSOR_CATEGORY_ORDER if category in seen]


def _is_pure_system_direct_precipitation_presence_check(check_specification: Any) -> bool:
    """Return whether a Check is the canonical positive direct-precipitation presence anchor.

    ``assessment=observed`` means that a Check's *expected signal* was observed.
    Therefore a model-authored Check whose expected state is ``absent`` (for
    example, "no continuous precipitation band") must never become positive
    evidence that precipitation exists.  Direct-precipitation presence is
    intentionally narrower than the generic ``hazard_magnitude`` role.
    """
    if not isinstance(check_specification, dict):
        return False
    intent = check_specification.get("intent_contract")
    if not isinstance(intent, dict):
        return False
    if (
        intent.get("direct_precipitation_anchor") is not True
        or str(intent.get("source_check_key") or "")
        != SYSTEM_DIRECT_PRECIPITATION_ANCHOR_CHECK_KEY
        or intent.get("signal_components") != ["direct_precipitation"]
        or str(intent.get("verification_mode") or "") != "spatial_distribution"
        or str(intent.get("domain_scope") or "") != "candidate"
    ):
        return False
    if (
        check_specification.get("required_signal_components")
        != ["direct_precipitation"]
        or str(check_specification.get("check_scope") or "") != "single_variable"
    ):
        return False
    claim_contract = check_specification.get("atomic_claim_contract")
    claims = claim_contract.get("canonical_claims") if isinstance(claim_contract, dict) else None
    if not isinstance(claims, list) or len(claims) != 1:
        return False
    claim = claims[0]
    return bool(
        isinstance(claim, dict)
        and claim.get("claim_components") == ["direct_precipitation"]
        and str(claim.get("expected_state") or "") == "present"
        and str(claim.get("scope_quantifier") or "") == "at_least_one"
    )


def check_intent_evidence_role(check_specification: Any) -> str:
    """Return the Step04-authored evidence polarity for one Check.

    ``assessment=observed`` means that the *expected signal* was observed; it does
    not by itself mean that the Check supports the candidate.  Step04
    ``evidence_role`` is therefore one required polarity input, while positive
    precursor eligibility additionally checks the atomic claim state and scope.
    """
    if not isinstance(check_specification, dict):
        return ""
    intent = check_specification.get("intent_contract")
    if not isinstance(intent, dict):
        return ""
    source_roles = {
        str(item.get("evidence_role") or "")
        for item in intent.get("source_intents", [])
        if isinstance(item, dict)
    } - {""}
    if len(source_roles) > 1:
        # A continuation merge that combined different evidence polarities is
        # intentionally not eligible for positive validated support.
        return "mixed"
    if len(source_roles) == 1:
        source_role = next(iter(source_roles))
        if source_role in {"supporting", "opposing", "context"}:
            return source_role
    role = str(intent.get("evidence_role") or "")
    if role in {"supporting", "opposing", "context"}:
        return role
    return ""


def _canonical_atomic_claims(check_specification: Any) -> list[dict[str, Any]]:
    """Return normalized canonical atomic claims for one Step04 Check."""
    if not isinstance(check_specification, dict):
        return []
    contract = check_specification.get("atomic_claim_contract")
    claims = contract.get("canonical_claims") if isinstance(contract, dict) else None
    if not isinstance(claims, list):
        return []
    return [item for item in claims if isinstance(item, dict)]


def _category_relevant_claims(category: str, check_specification: Any) -> list[dict[str, Any]]:
    component = PRECURSOR_CATEGORY_SIGNAL_COMPONENT.get(category)
    if not component:
        return []
    return [
        claim
        for claim in _canonical_atomic_claims(check_specification)
        if component in {
            str(value) for value in claim.get("claim_components", []) if str(value)
        }
    ]


def positive_support_claim_keys(
    category: Any,
    check_specification: Any,
) -> set[str]:
    """Return claim-level positive-support paths for one precursor category.

    The function deliberately separates two concepts that were previously
    conflated:

    * Step04 ``evidence_role``: whether the proposition was authored as a
      candidate-supporting/limiting/context Check; and
    * atomic ``expected_state``: what meteorological state is actually being
      verified.

    A category-specific Check is eligible only when it is authored as
    ``supporting`` *and* its relevant atomic claims are not explicitly limiting.
    A supporting ``multivariable_alignment`` Check is also eligible for a core
    precursor category when it contains an explicit positive ``overlap`` claim
    tying direct precipitation to that category.  This allows a single
    multivariable Check to provide distinct claim-level evidence paths for
    moisture, low-level forcing and mid-level forcing without treating a later
    weakening Check as positive support.

    ``__legacy_check__`` is returned only for old category-specific products that
    predate atomic claims; this preserves backwards readability without weakening
    current products.
    """
    category_text = str(category)
    if not isinstance(check_specification, dict):
        return set()
    if category_text == "direct_precipitation":
        if not _is_pure_system_direct_precipitation_presence_check(check_specification):
            return set()
        claims = _canonical_atomic_claims(check_specification)
        return {
            str(claim.get("claim_key") or "__direct_presence__")
            for claim in claims
            if str(claim.get("expected_state") or "") == "present"
        } or {"__direct_presence__"}

    if check_intent_evidence_role(check_specification) != "supporting":
        return set()

    # Primary category-specific path.
    if category_check_is_compatible(category_text, check_specification):
        claims = _category_relevant_claims(category_text, check_specification)
        if not claims:
            # Legacy Step04 checkpoints can lack atomic_claim_contract entirely.
            return {"__legacy_check__"}
        states = {str(claim.get("expected_state") or "") for claim in claims}
        if states & NON_POSITIVE_PRECURSOR_EXPECTED_STATES:
            return set()
        return {
            str(claim.get("claim_key") or f"claim_{index}")
            for index, claim in enumerate(claims, start=1)
        }

    # Claim-specific multivariable path.  Only operational core categories are
    # allowed, and the positive path must explicitly link direct precipitation
    # with that category through an overlap claim.
    if (
        category_text in OPERATIONAL_PRECURSOR_CATEGORIES
        and str(check_specification.get("check_scope") or "") == "multivariable_alignment"
    ):
        component = PRECURSOR_CATEGORY_SIGNAL_COMPONENT.get(category_text)
        if not component:
            return set()
        claim_keys: set[str] = set()
        for index, claim in enumerate(_canonical_atomic_claims(check_specification), start=1):
            components = {
                str(value) for value in claim.get("claim_components", []) if str(value)
            }
            if (
                str(claim.get("expected_state") or "") == "overlap"
                and "direct_precipitation" in components
                and component in components
            ):
                claim_keys.add(str(claim.get("claim_key") or f"claim_{index}"))
        return claim_keys
    return set()


def check_is_positive_spatiotemporal_alignment(check_specification: Any) -> bool:
    """Whether a Check can anchor *positive* precipitation-environment alignment.

    A multivariable Check that verifies weakening/loss of overlap is useful as a
    limiting Check, but it is not evidence that positive alignment exists.
    """
    if not isinstance(check_specification, dict):
        return False
    if str(check_specification.get("check_scope") or "") != "multivariable_alignment":
        return False
    if check_intent_evidence_role(check_specification) != "supporting":
        return False
    claims = _canonical_atomic_claims(check_specification)
    if not claims:
        return False
    positive_overlap_found = False
    for claim in claims:
        components = {
            str(value) for value in claim.get("claim_components", []) if str(value)
        }
        state = str(claim.get("expected_state") or "")
        if state in NON_POSITIVE_PRECURSOR_EXPECTED_STATES:
            return False
        if state == "overlap" and "direct_precipitation" in components:
            if components & {
                "moisture_supply",
                "low_level_forcing",
                "mid_upper_forcing",
                "thermodynamic_support",
            }:
                positive_overlap_found = True
    return positive_overlap_found


SPATIAL_LOCALIZATION_ENVIRONMENT_COMPONENTS = frozenset({
    "moisture_supply",
    "low_level_forcing",
    "mid_upper_forcing",
})


def alignment_requires_spatial_localization(check_specification: Any) -> bool:
    """Whether a positive precipitation-environment overlap must name a place.

    The public ``시공간 일치`` state is stronger than a generic visual
    association: when an operational precipitation/environment overlap is used
    as positive alignment evidence, Step07 must also identify where that overlap
    occurs inside the closed operational-region namespace.  This covers both
    the historical four-field overlap and reduced three-/two-field positive
    overlaps built from the localizable operational fields.
    """
    if not check_is_positive_spatiotemporal_alignment(check_specification):
        return False
    components = {
        str(value)
        for value in check_specification.get("required_signal_components", [])
        if str(value)
    }
    if "direct_precipitation" not in components:
        return False
    return bool(components & SPATIAL_LOCALIZATION_ENVIRONMENT_COMPONENTS)


def complete_coupled_environment_alignment_check_ids(
    alignment_check_ids: Iterable[Any],
    *,
    check_specifications: dict[str, dict[str, Any]],
    check_results: dict[str, dict[str, Any]],
    candidate_peak_hours: Iterable[Any],
) -> list[str]:
    """Return fully observed, localized core-environment coupling Checks.

    This is a separate environment-*existence* pathway, not a shortcut that
    turns one multivariable claim into several independent precursor-category
    anchors.  A Check qualifies only when the whole expected multivariable
    state is observed at a candidate peak hour, with complete usable evidence
    and structured localization, and the positive overlap claim set covers
    direct precipitation, moisture, low-level forcing and mid/upper forcing.
    """
    required_components = {
        "direct_precipitation",
        "moisture_supply",
        "low_level_forcing",
        "mid_upper_forcing",
    }
    peak_hours = {
        int(value)
        for value in candidate_peak_hours
        if isinstance(value, int) and not isinstance(value, bool)
    }
    qualified: list[str] = []
    for raw_check_id in alignment_check_ids:
        check_id = str(raw_check_id)
        specification = check_specifications.get(check_id)
        result = check_results.get(check_id)
        if not isinstance(specification, dict) or not isinstance(result, dict):
            continue
        if (
            str(specification.get("check_scope") or "") != "multivariable_alignment"
            or check_intent_evidence_role(specification) != "supporting"
            or not required_components.issubset(
                {str(value) for value in specification.get("required_signal_components", [])}
            )
        ):
            continue
        positive_components: set[str] = set()
        invalid_claim = False
        for claim in _canonical_atomic_claims(specification):
            state = str(claim.get("expected_state") or "")
            if state in NON_POSITIVE_PRECURSOR_EXPECTED_STATES:
                invalid_claim = True
                break
            if state == "overlap":
                positive_components.update(
                    str(value) for value in claim.get("claim_components", []) if str(value)
                )
        if invalid_claim or not required_components.issubset(positive_components):
            continue
        localization = result.get("spatial_localization")
        if not isinstance(localization, dict):
            continue
        hour = localization.get("forecast_hour")
        regions = localization.get("regions")
        if (
            result.get("assessment") != "observed"
            or result.get("scope_coverage") != "complete_expected_scope"
            or result.get("evidence_status") != "complete"
            or result.get("evidence_complete") is not True
            or result.get("usable_for_synthesis") is False
            or bool(result.get("contradictory_findings"))
            or bool(result.get("uncertainties"))
            or localization.get("status") != "localized"
            or not isinstance(hour, int)
            or isinstance(hour, bool)
            or hour not in peak_hours
            or not isinstance(regions, list)
            or not regions
        ):
            continue
        qualified.append(check_id)
    return sorted(set(qualified))


def _is_single_explicit_region_quantitative_check(check_specification: Any) -> bool:
    """Whether a Check is purely quantitative evidence for one named region.

    Such a Check remains valid local evidence, but it must not be promoted into
    candidate-wide environment-persistence evidence.  Candidate persistence is a
    broader spatial judgment and must come from candidate-overlap, multivariable,
    or otherwise non-local evidence.
    """
    claims = _canonical_atomic_claims(check_specification)
    if not claims:
        return False
    quantitative_claims = []
    for claim in claims:
        scope = claim.get("quantitative_scope")
        if not isinstance(scope, dict):
            return False
        quantitative_claims.append(scope)
    return bool(quantitative_claims) and all(
        str(scope.get("region_scope") or "") == "explicit_regions"
        and isinstance(scope.get("region_ids"), list)
        and len(scope.get("region_ids")) == 1
        and bool(str(scope.get("region_ids")[0] or ""))
        for scope in quantitative_claims
    )


def _environment_persistence_check_is_eligible(
    check_specification: Any,
    *,
    analysis_phases: list[dict[str, Any]] | None = None,
) -> bool:
    """Whether a Check can be cited for candidate-wide environment persistence."""
    if not isinstance(check_specification, dict):
        return False
    if _is_single_explicit_region_quantitative_check(check_specification):
        return False
    components = {
        str(value) for value in check_specification.get("required_signal_components", []) if str(value)
    }
    environment_components = {
        "moisture_supply",
        "low_level_forcing",
        "mid_upper_forcing",
        "thermodynamic_support",
        "organization_persistence",
    }
    if not (components & environment_components):
        return False
    scope = str(check_specification.get("check_scope") or "")
    role = str(check_specification.get("variable_role") or "")
    claims = _canonical_atomic_claims(check_specification)
    phase_keys = {
        str(phase_key)
        for claim in claims
        for phase_key in claim.get("phase_keys", [])
        if str(phase_key)
    }
    # Environment persistence is explicitly a post-core/peak question.  A
    # pre-to-peak strengthening comparison may explain environment formation,
    # but it cannot by itself prove that the environment persists afterwards.
    # Legacy semantic phase keys include POST.  Compact phase keys such as P03
    # are opaque and must be resolved through canonical Step04 analysis_phases.
    has_post_peak_phase = any("POST" in phase_key.upper() for phase_key in phase_keys)
    if claims and not has_post_peak_phase and isinstance(analysis_phases, list):
        post_ids = {
            str(phase.get("phase_id"))
            for phase in analysis_phases
            if isinstance(phase, dict) and phase.get("phase_type") == "post_peak"
        }
        check_ids = set(map(str, check_specification.get("phase_ids", [])))
        contract = check_specification.get("intent_contract")
        intents = contract.get("source_intents") if isinstance(contract, dict) else None
        contract_ids = contract.get("phase_ids") if isinstance(contract, dict) else None
        if (
            post_ids & check_ids
            and isinstance(intents, list) and len(intents) == 1
            and isinstance(intents[0], dict)
            and isinstance(contract_ids, list)
            and set(map(str, contract_ids)) == check_ids
        ):
            source_keys = {
                str(key) for key in intents[0].get("phase_keys", []) if str(key)
            }
            # One source intent must correspond to the same canonical phases,
            # and at least one atomic claim must cover the whole source intent.
            # Merged/ambiguous mappings remain ineligible (fail closed).
            if len(source_keys) == len(check_ids) and source_keys:
                has_post_peak_phase = any(
                    source_keys.issubset(
                        {str(key) for key in claim.get("phase_keys", [])}
                    )
                    for claim in claims
                )
    if scope == "temporal_evolution" or role == "organization_persistence":
        return has_post_peak_phase if claims else True
    if scope != "multivariable_alignment":
        return False
    if claims:
        return has_post_peak_phase and len(phase_keys) >= 2
    return len(phase_keys) >= 2


def environment_persistence_candidate_check_ids(
    *,
    check_specifications: dict[str, dict[str, Any]],
    check_results: dict[str, dict[str, Any]],
    analysis_phases: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Return usable Checks that may ground environment persistence."""
    return sorted(
        check_id
        for check_id, specification in check_specifications.items()
        if _environment_persistence_check_is_eligible(specification, analysis_phases=analysis_phases)
        and isinstance(check_results.get(check_id), dict)
        and check_results[check_id].get("evidence_status") == "complete"
        and check_results[check_id].get("evidence_complete") is True
        and check_results[check_id].get("usable_for_synthesis") is not False
    )


def _environment_persistence_effect(
    check_specification: dict[str, Any],
    check_result: dict[str, Any],
) -> str:
    """Map an eligible Check to supporting/limiting/mixed/context persistence effect.

    This uses only the authored proposition polarity and Step07 assessment.  It
    does not create a meteorological conclusion; it is a consistency guard for
    the model-authored persistence status.
    """
    assessment = str(check_result.get("assessment") or "")
    role = check_intent_evidence_role(check_specification)
    claims = _canonical_atomic_claims(check_specification)
    has_explicit_limiting_state = any(
        str(claim.get("expected_state") or "") in NON_POSITIVE_PRECURSOR_EXPECTED_STATES
        for claim in claims
    )
    proposition = "context"
    if has_explicit_limiting_state:
        proposition = "limiting"
    elif role == "supporting":
        proposition = "supporting"
    elif role == "opposing":
        proposition = "limiting"

    if assessment == "partially_observed":
        return "mixed"
    if assessment == "observed":
        # A temporal Check can satisfy the authored persistence proposition overall
        # while still containing an explicit contradictory temporal finding (for
        # example, a transient weakening at one forecast hour).  Preserve both
        # sides as ``mixed`` so the model may judge the operational importance;
        # do not let the validator erase the limiting evidence.
        if proposition in {"supporting", "limiting"} and bool(check_result.get("contradictory_findings")):
            return "mixed"
        return proposition
    if assessment == "opposite_observed":
        if proposition == "supporting":
            return "limiting"
        if proposition == "limiting":
            return "supporting"
        return "context"
    return "context"


def environment_persistence_evidence_contract(
    *,
    check_specifications: dict[str, dict[str, Any]],
    check_results: dict[str, dict[str, Any]],
    analysis_phases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the exact persistence evidence classification shared by LLM and validators."""
    allowed_ids = environment_persistence_candidate_check_ids(
        check_specifications=check_specifications,
        check_results=check_results,
        analysis_phases=analysis_phases,
    )
    effect_by_check_id = {
        check_id: _environment_persistence_effect(
            check_specifications[check_id],
            check_results[check_id],
        )
        for check_id in allowed_ids
    }
    buckets = {effect: [] for effect in ("supporting", "limiting", "mixed", "context")}
    for check_id, effect in effect_by_check_id.items():
        buckets.setdefault(effect, []).append(check_id)
    categories_by_check_id = {
        check_id: sorted(
            {
                str(value)
                for value in check_specifications[check_id].get("required_signal_components", [])
                if str(value) in OPERATIONAL_PRECURSOR_CATEGORIES
            }
        )
        for check_id in allowed_ids
    }
    return {
        "allowed_evidence_check_ids": allowed_ids,
        "effect_by_check_id": effect_by_check_id,
        "environment_categories_by_check_id": categories_by_check_id,
        "supporting_check_ids": sorted(buckets.get("supporting", [])),
        "limiting_check_ids": sorted(buckets.get("limiting", [])),
        "mixed_check_ids": sorted(buckets.get("mixed", [])),
        "context_check_ids": sorted(buckets.get("context", [])),
    }


def validate_environment_persistence_contract(
    status: Any,
    evidence_check_ids: Any,
    *,
    environment_support_status: Any,
    check_specifications: dict[str, dict[str, Any]],
    check_results: dict[str, dict[str, Any]],
    analysis_phases: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Validate a model-authored persistence status against temporal evidence."""
    errors: list[str] = []
    status_text = str(status or "")
    environment_text = str(environment_support_status or "")
    if status_text not in ENVIRONMENT_PERSISTENCE_STATUSES:
        return [f"environment_persistence_status is invalid: {status_text}"]
    if not isinstance(evidence_check_ids, list) or len(set(map(str, evidence_check_ids))) != len(evidence_check_ids):
        return ["environment_persistence_evidence_check_ids must be a unique array"]
    evidence_ids = [str(value) for value in evidence_check_ids]
    evidence_contract = environment_persistence_evidence_contract(
        check_specifications=check_specifications,
        check_results=check_results,
        analysis_phases=analysis_phases,
    )
    allowed = set(evidence_contract["allowed_evidence_check_ids"])
    invalid = [check_id for check_id in evidence_ids if check_id not in allowed]
    if invalid:
        errors.append(
            "environment persistence evidence uses non-temporal, non-environment, incomplete, or unusable Checks: "
            + ", ".join(invalid)
        )

    environment_exists_or_partial = environment_text in {"confirmed", "partial"}
    if not environment_exists_or_partial:
        if status_text != "not_applicable":
            errors.append("environment persistence must be not_applicable when environment support is not confirmed/partial")
        if evidence_ids:
            errors.append("not_applicable environment persistence must not retain evidence IDs")
        return errors

    if status_text == "not_applicable":
        errors.append("environment persistence cannot be not_applicable when environment support is confirmed/partial")
        return errors
    if status_text in {"confirmed", "limited"} and not evidence_ids:
        errors.append(f"environment persistence {status_text} requires at least one temporal environment evidence Check")
        return errors
    if invalid:
        return errors

    effect_by_check_id = evidence_contract["effect_by_check_id"]
    effects = [effect_by_check_id[check_id] for check_id in evidence_ids]
    if status_text == "limited" and not any(effect in {"limiting", "mixed"} for effect in effects):
        cited = ", ".join(f"{check_id}={effect_by_check_id[check_id]}" for check_id in evidence_ids) or "none"
        available = ", ".join(
            [
                *(f"{check_id}=limiting" for check_id in evidence_contract["limiting_check_ids"]),
                *(f"{check_id}=mixed" for check_id in evidence_contract["mixed_check_ids"]),
            ]
        ) or "none"
        errors.append(
            "limited environment persistence requires limiting or mixed temporal environment evidence; "
            f"cited=[{cited}]; available_limiting_or_mixed=[{available}]"
        )
    if status_text == "confirmed":
        if not any(effect == "supporting" for effect in effects):
            errors.append("confirmed environment persistence requires supporting temporal environment evidence")
        if any(effect in {"limiting", "mixed"} for effect in effects):
            cited = ", ".join(f"{check_id}={effect_by_check_id[check_id]}" for check_id in evidence_ids)
            errors.append(
                "confirmed environment persistence cannot cite limiting or mixed temporal environment evidence; "
                f"cited=[{cited}]"
            )
        supporting_categories = {
            category
            for check_id in evidence_ids
            if effect_by_check_id.get(check_id) == "supporting"
            for category in evidence_contract["environment_categories_by_check_id"].get(check_id, [])
        }
        if len(supporting_categories) < 2:
            errors.append(
                "confirmed environment persistence requires supporting temporal evidence from at least "
                "two environment categories; "
                f"categories={sorted(supporting_categories)}"
            )
        available_adverse = sorted(
            set(evidence_contract["limiting_check_ids"])
            | set(evidence_contract["mixed_check_ids"])
        )
        if available_adverse:
            errors.append(
                "confirmed environment persistence is incompatible with available limiting or mixed "
                "temporal environment evidence; available_limiting_or_mixed="
                + ", ".join(available_adverse)
            )
    return errors


def category_check_is_compatible(category: Any, check_specification: Any) -> bool:
    """Whether one Step04 Check has an approved physical role for a category.

    This function answers physical-role compatibility only.  Positive support
    additionally requires ``category_check_is_positive_support_compatible``.
    For ``direct_precipitation`` the compatible path is intentionally restricted
    to the SYSTEM-owned pure positive presence anchor.
    """
    if str(category) == "direct_precipitation":
        return _is_pure_system_direct_precipitation_presence_check(check_specification)
    allowed_roles = PRECURSOR_CATEGORY_COMPATIBLE_VARIABLE_ROLES.get(str(category), set())
    role = check_specification.get("variable_role") if isinstance(check_specification, dict) else None
    return str(role) in allowed_roles


def category_check_is_positive_support_compatible(
    category: Any,
    check_specification: Any,
) -> bool:
    """Whether one Check may serve as *positive* validated category support.

    A Check authored as ``opposing`` or ``context`` can still be used by Step08 as
    limiting, mixed, uncertainty, or contextual evidence, but must never satisfy
    ``validated_precursor_evidence`` or the deterministic confirmed-environment
    precursor gate merely because its expected (possibly negative) signal was
    observed.
    """
    return bool(positive_support_claim_keys(category, check_specification))


def validate_validated_precursor_evidence(
    evidence: Any,
    *,
    check_specifications: dict[str, dict[str, Any]],
    check_results: dict[str, dict[str, Any]],
) -> tuple[list[str], dict[str, set[str]], list[str]]:
    """Validate positive category-specific Pass2 anchors without inferring weather.

    ``validated_precursor_evidence`` is a positive-support structure.  Checks that
    are not observed/partially observed remain available to Step08 as physical
    limiting or uncertainty evidence, but cannot masquerade as validated support.
    """
    errors: list[str] = []
    anchors: dict[str, set[str]] = {}
    if not isinstance(evidence, list):
        return [], anchors, ["validated_precursor_evidence must be an array"]
    for item in evidence:
        if not isinstance(item, dict):
            errors.append("validated_precursor_evidence item must be an object")
            continue
        category = str(item.get("category") or "")
        evidence_check_ids = item.get("evidence_check_ids")
        if category not in PRECURSOR_CATEGORIES:
            errors.append(f"validated precursor category is invalid: {category}")
            continue
        if category in anchors:
            errors.append(f"validated precursor category is duplicated: {category}")
            continue
        if not isinstance(evidence_check_ids, list) or not evidence_check_ids:
            errors.append(f"validated precursor category has no evidence checks: {category}")
            continue
        if len(set(evidence_check_ids)) != len(evidence_check_ids):
            errors.append(f"validated precursor category has duplicate evidence checks: {category}")
            continue
        category_anchors: set[str] = set()
        for raw_check_id in evidence_check_ids:
            check_id = str(raw_check_id)
            specification = check_specifications.get(check_id)
            result = check_results.get(check_id)
            if specification is None or result is None:
                errors.append(f"validated precursor evidence check does not exist: {category}/{check_id}")
                continue
            assessment = result.get("assessment")
            if assessment not in POSITIVE_VALIDATED_PRECURSOR_ASSESSMENTS:
                errors.append(
                    f"validated precursor evidence check is not positive support: "
                    f"{category}/{check_id} assessment={assessment}"
                )
                continue
            if result.get("usable_for_synthesis") is False:
                errors.append(f"validated precursor evidence check failed deterministic consistency validation: {category}/{check_id}")
                continue
            if not category_check_is_positive_support_compatible(category, specification):
                # Opposing/context Checks remain available to Step08 as
                # limiting/context evidence, but cannot masquerade as positive
                # validated precursor support.  A shared auxiliary Check is
                # permitted only when this category also has a positive
                # supporting anchor elsewhere in its evidence list.
                continue
            category_anchors.add(check_id)
        anchors[category] = category_anchors
        if not category_anchors:
            errors.append(f"validated precursor category has no compatible evidence path: {category}")
    return derive_validated_precursor_categories(evidence), anchors, errors


def has_distinct_category_specific_anchors(
    category_anchors: dict[str, set[str]],
    *,
    check_specifications: dict[str, dict[str, Any]] | None = None,
) -> bool:
    """Require distinct evidence paths across operational precursor categories.

    Historically an evidence path was identified only by ``check_id``.  That was
    too coarse for a valid multivariable-alignment Check containing separate
    atomic overlap claims for moisture, low-level forcing and mid-level forcing.
    Current products therefore compare claim-level tokens when specifications are
    available.  A shared Check is accepted only when each category has a distinct
    positive atomic claim path; a single undifferentiated Check still cannot be
    the sole evidence for multiple categories.
    """
    categories = [
        category for category in OPERATIONAL_PRECURSOR_CATEGORIES
        if category in category_anchors
    ]
    if len(categories) < 2:
        return False
    if check_specifications is None:
        for category in categories:
            own = category_anchors[category]
            other = set().union(*(category_anchors[other_category] for other_category in categories if other_category != category))
            if not own - other:
                return False
        return True

    path_tokens: dict[str, set[str]] = {}
    for category in categories:
        tokens: set[str] = set()
        for check_id in category_anchors[category]:
            specification = check_specifications.get(check_id)
            claim_keys = positive_support_claim_keys(category, specification)
            if not claim_keys:
                continue
            for claim_key in claim_keys:
                if claim_key == "__legacy_check__":
                    tokens.add(f"{check_id}:__check__")
                else:
                    tokens.add(f"{check_id}:{claim_key}")
        path_tokens[category] = tokens

    for category in categories:
        own = path_tokens.get(category, set())
        other = set().union(*(
            path_tokens.get(other_category, set())
            for other_category in categories
            if other_category != category
        ))
        if not own - other:
            return False
    return True


def has_independent_precursor_support(categories: Iterable[Any], *, spatial_temporal_overlap: bool) -> bool:
    """Evaluate the operational precursor rule using only validated core categories."""
    unique = set(_deduplicate_categories(categories)) & set(OPERATIONAL_PRECURSOR_CATEGORIES)
    return (
        len(unique) >= 2
        and bool(unique & OPERATIONAL_PRECURSOR_FORCING_CATEGORIES)
        and spatial_temporal_overlap
    )


def validate_operational_precursor_support_contract(
    categories: Iterable[Any],
    category_anchors: dict[str, set[str]],
    *,
    spatial_temporal_overlap: bool,
    check_specifications: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Validate the shared Step08/Step09 confirmed-environment support contract.

    ``validated_precursor_evidence`` may contain more categories than are needed
    for the operational minimum, but every category it declares as positive
    support must retain a distinct category-specific evidence path.  In
    particular, one undifferentiated multivariable atomic claim must not be
    copied into multiple operational categories.  A multivariable Check may
    support several categories only when the atomic contract provides distinct
    positive claim keys for those categories.

    Keeping this validation in one helper prevents Step08 from persisting a
    synthesis that Step09 would later reject at the publication gate.
    """
    core_categories = [
        category
        for category in _deduplicate_categories(categories)
        if category in OPERATIONAL_PRECURSOR_CATEGORIES
    ]
    errors: list[str] = []
    if not has_independent_precursor_support(
        core_categories, spatial_temporal_overlap=spatial_temporal_overlap
    ):
        errors.append(
            "precursor_watch lacks two operational core categories, forcing support, or positive alignment"
        )
    core_anchors = {
        category: category_anchors.get(category, set())
        for category in core_categories
    }
    if len(core_categories) >= 2 and not has_distinct_category_specific_anchors(
        core_anchors,
        check_specifications=check_specifications,
    ):
        errors.append(
            "operational precursor categories do not have distinct category-specific evidence anchors"
        )
    return core_categories, errors


def validate_candidate_signal_contract(candidate: dict[str, Any]) -> list[str]:
    """Return human-readable candidate classification contract violations."""
    errors: list[str] = []
    basis = str(candidate.get("detection_basis", ""))
    status = str(candidate.get("precipitation_signal_status", ""))
    categories = candidate.get("precursor_signal_categories", [])
    overlap = bool(
        candidate.get("precursor_spatiotemporal_overlap")
        or candidate.get("hypothesis", {}).get("spatiotemporal_overlap")
        if isinstance(candidate.get("hypothesis"), dict)
        else candidate.get("precursor_spatiotemporal_overlap")
    )
    if basis not in DETECTION_BASIS:
        errors.append("detection_basis is invalid")
    if status not in PRECIPITATION_STATUS:
        errors.append("precipitation_signal_status is invalid")
    if not isinstance(categories, list):
        errors.append("precursor_signal_categories must be an array")
        categories = []
    unknown = sorted(set(str(item) for item in categories) - PRECURSOR_CATEGORIES)
    if unknown:
        errors.append(f"precursor_signal_categories contains unsupported values: {unknown}")
    if basis == "direct_precipitation" and status not in {"present", "weak", "uncertain"}:
        errors.append("direct_precipitation requires a non-absent precipitation signal")
    if basis == "mixed" and status == "absent":
        errors.append("mixed requires a non-absent precipitation signal")
    if basis in {"precursor_environment", "mixed"} and not has_independent_precursor_support(
        categories, spatial_temporal_overlap=overlap
    ):
        errors.append("precursor_environment requires two operational core categories, low/mid-level forcing support, and spatiotemporal overlap")
    return errors


def infer_legacy_candidate_signal_contract(candidate: dict[str, Any]) -> dict[str, Any]:
    """Add conservative defaults to old Pass1 candidates without changing their evidence."""
    result = copy.deepcopy(candidate)
    signal_types = {str(item) for item in result.get("signal_types", [])}
    reasons = " ".join(str(item) for item in result.get("supporting_reasons", []))
    direct = bool(signal_types & {"direct_precipitation", "heavy_rain", "precipitation"}) or "강수" in reasons
    categories: list[str] = []
    mapping = {
        "moisture": "moisture_supply",
        "tcw": "moisture_supply",
        "convergence": "low_level_forcing",
        "low_level": "low_level_forcing",
        "omega": "mid_upper_forcing",
        "upper": "mid_upper_forcing",
        "jet": "mid_upper_forcing",
        "shear": "organization_persistence",
        "persistence": "organization_persistence",
    }
    joined = " ".join(sorted(signal_types)).lower() + " " + reasons.lower()
    for token, category in mapping.items():
        if token in joined and category not in categories:
            categories.append(category)
    result.setdefault("detection_basis", "direct_precipitation" if direct else "precursor_environment")
    result.setdefault("precipitation_signal_status", "present" if direct else "uncertain")
    result.setdefault("precursor_signal_categories", categories)
    result.setdefault("precursor_spatiotemporal_overlap", bool(categories))
    result.setdefault(
        "hypothesis",
        {
            "statement": str(result.get("candidate_name", "위험기상 후보")),
            "spatiotemporal_overlap": bool(categories),
        },
    )
    result.setdefault("analysis_envelope_request", {"temporal_padding_hours": 6})
    result.setdefault("diagnostic_plan", [])
    return result


def bbox_within(inner: Any, outer: Any) -> bool:
    candidate = normalize_bbox(inner)
    envelope = normalize_bbox(outer)
    return bool(
        candidate
        and envelope
        and envelope["west"] <= candidate["west"] <= candidate["east"] <= envelope["east"]
        and envelope["south"] <= candidate["south"] <= candidate["north"] <= envelope["north"]
    )


def time_window_within(inner: Any, outer: Any) -> bool:
    candidate = normalize_time_window(inner)
    envelope = normalize_time_window(outer)
    return bool(
        candidate
        and envelope
        and envelope["forecast_hour_start"] <= candidate["forecast_hour_start"] <= candidate["forecast_hour_end"] <= envelope["forecast_hour_end"]
    )


_CANDIDATE_NAME_SPATIAL_OPPOSITES = {
    "북부": ("남부", "남쪽"),
    "북쪽": ("남부", "남쪽"),
    "남부": ("북부", "북쪽"),
    "남쪽": ("북부", "북쪽"),
    "동부": ("서부", "서쪽"),
    "동쪽": ("서부", "서쪽"),
    "서부": ("동부", "동쪽"),
    "서쪽": ("동부", "동쪽"),
    "내륙": ("해상",),
    "해상": ("내륙",),
}
_CANDIDATE_NAME_SPATIAL_NEGATION = re.compile(
    r"(?:아닌|아니|않|못|되지\s*않|확인되지|성립하지|나타나지|위치하지)"
)


def _explicitly_reversed_candidate_name_terms(source_name: str, result: dict[str, Any]) -> list[str]:
    """Return source-name spatial terms explicitly reversed in one Step07 finding.

    This intentionally requires both a negation of the source term and an
    opposite spatial term in the same finding.  A mere movement failure such as
    '동쪽으로 이동하지 않음' does not force a rename unless the finding also
    states a conflicting alternative location.
    """
    source_terms = [term for term in _CANDIDATE_NAME_SPATIAL_OPPOSITES if term in source_name]
    if not source_terms:
        return []
    texts: list[str] = []
    for field in ("findings", "contradictory_findings"):
        for item in result.get(field, []) if isinstance(result.get(field), list) else []:
            if isinstance(item, dict) and isinstance(item.get("statement"), str):
                texts.append(item["statement"])
    reversed_terms: list[str] = []
    for text in texts:
        for term in source_terms:
            match = re.search(rf"{re.escape(term)}.{{0,32}}(?:아닌|아니|않|못|되지\s*않|확인되지|성립하지|나타나지|위치하지)", text)
            if not match:
                continue
            if any(opposite in text for opposite in _CANDIDATE_NAME_SPATIAL_OPPOSITES[term]):
                reversed_terms.append(term)
    return list(dict.fromkeys(reversed_terms))


def candidate_display_name(candidate: Any, revision: Any) -> str:
    """Return the user-facing candidate name without mutating Step04 provenance.

    ``candidate.candidate_name`` remains the immutable discovery name.  A
    validated Step08 revision may provide ``revised_candidate_name`` solely for
    downstream display.
    """
    source = str(candidate.get("candidate_name") or "").strip() if isinstance(candidate, dict) else ""
    revised = revision.get("revised_candidate_name") if isinstance(revision, dict) else None
    if isinstance(revised, str) and revised.strip():
        return revised.strip().replace("강우", "강수")
    return source.replace("강우", "강수")


FINAL_SPATIAL_SECTOR_LABELS_KO = {
    "unspecified": "",
    "north": "북부",
    "northeast": "북동부",
    "east": "동부",
    "southeast": "남동부",
    "south": "남부",
    "southwest": "남서부",
    "west": "서부",
    "northwest": "북서부",
    "central": "중부",
}
FINAL_SPATIAL_ADJACENT_DIRECTION_LABELS_KO = {
    "unspecified": "",
    "north": "북쪽",
    "northeast": "북동쪽",
    "east": "동쪽",
    "southeast": "남동쪽",
    "south": "남쪽",
    "southwest": "남서쪽",
    "west": "서쪽",
    "northwest": "북서쪽",
    "central": "인근",
}



def _compact_peak_precip_region_name(region_name: Any) -> str:
    """Return a concise operational-region label for peak-precipitation display."""
    name = str(region_name or "").strip()
    if name.endswith(" 광역"):
        name = name[:-3].rstrip()
    return name


def candidate_effective_peak_forecast_hours(candidate: Any, revision: Any) -> list[int]:
    """Return Step04 peak hours that remain inside the final effective time window."""
    if not isinstance(candidate, dict):
        return []
    peak_hours: list[int] = []
    for value in candidate.get("peak_forecast_hours", []):
        if isinstance(value, int) and not isinstance(value, bool) and value not in peak_hours:
            peak_hours.append(value)
    revised_window = revision.get("revised_time_window") if isinstance(revision, dict) else None
    window = normalize_time_window(revised_window)
    if window is None:
        window = normalize_time_window(candidate.get("time_window"))
    if window is None:
        return peak_hours
    return [
        hour
        for hour in peak_hours
        if window["forecast_hour_start"] <= hour <= window["forecast_hour_end"]
    ]


def candidate_peak_precipitation_details_from_group(
    group: Any,
    peak_forecast_hours: Iterable[int],
) -> list[dict[str, Any]]:
    """Resolve the deterministic Step05 maximum-precipitation region at each peak hour.

    Only Step05 ``tp_6h_increment_mm`` regional statistics are used.  Narrative
    text is never parsed for geography.  Missing quantitative rows remain
    unresolved so callers can decide whether to use another naming fallback.
    """
    if not isinstance(group, dict):
        return []
    peak_hours = [
        int(hour)
        for hour in peak_forecast_hours
        if isinstance(hour, int) and not isinstance(hour, bool)
    ]
    if not peak_hours:
        return []
    peak_set = set(peak_hours)
    best_by_hour: dict[int, tuple[float, str, str]] = {}
    requests = group.get("analysis_data_requests")
    if not isinstance(requests, list):
        return []
    for entry in requests:
        if not isinstance(entry, dict):
            continue
        request = entry.get("request")
        if not isinstance(request, dict) or request.get("data_type") != "quantitative_excerpt":
            continue
        step05 = entry.get("step05")
        stored = step05.get("result") if isinstance(step05, dict) else None
        if not isinstance(stored, dict) or stored.get("status") != "success":
            continue
        features = stored.get("features")
        feature = features.get("tp_6h_increment_mm") if isinstance(features, dict) else None
        if not isinstance(feature, dict):
            continue
        per_hour = feature.get("per_forecast_hour")
        if not isinstance(per_hour, list):
            continue
        for hour_row in per_hour:
            if not isinstance(hour_row, dict):
                continue
            forecast_hour = hour_row.get("forecast_hour")
            if (
                isinstance(forecast_hour, bool)
                or not isinstance(forecast_hour, int)
                or forecast_hour not in peak_set
            ):
                continue
            regional = hour_row.get("regional_statistics")
            if not isinstance(regional, list):
                continue
            for region in regional:
                if not isinstance(region, dict):
                    continue
                region_id = region.get("region_id")
                region_name = region.get("region_name")
                statistics = region.get("statistics")
                max_value = statistics.get("max") if isinstance(statistics, dict) else None
                if (
                    not isinstance(region_id, str)
                    or not region_id
                    or not isinstance(region_name, str)
                    or not region_name
                    or isinstance(max_value, bool)
                    or not isinstance(max_value, (int, float))
                ):
                    continue
                resolved = (float(max_value), region_id, region_name)
                previous = best_by_hour.get(forecast_hour)
                if (
                    previous is None
                    or resolved[0] > previous[0]
                    or (resolved[0] == previous[0] and resolved[1] < previous[1])
                ):
                    best_by_hour[forecast_hour] = resolved

    # If Step04 did not request a quantitative excerpt at the candidate peak
    # hours, use the deterministic operational-region maxima embedded in the
    # SYSTEM direct-precipitation spatial request.  These values come from the
    # same Step05 6-hour precipitation field and are already used by the public
    # timeline; using them here prevents a generic multi-region fallback name.
    unresolved_hours = {hour for hour in peak_set if hour not in best_by_hour}
    if unresolved_hours:
        context = group.get("candidate_context")
        plan = context.get("analysis_plan") if isinstance(context, dict) else None
        checks = plan.get("variable_checks") if isinstance(plan, dict) else None
        anchor_ids: set[str] = set()
        if isinstance(checks, list):
            for check in checks:
                if not isinstance(check, dict):
                    continue
                intent = check.get("intent_contract")
                if not isinstance(intent, dict):
                    continue
                if (
                    intent.get("direct_precipitation_anchor") is True
                    and str(intent.get("source_check_key") or "")
                    == "SYS_DIRECT_PRECIPITATION_PRESENCE"
                ):
                    check_id = str(check.get("check_id") or "").strip()
                    if check_id:
                        anchor_ids.add(check_id)
        if len(anchor_ids) == 1:
            anchor_id = next(iter(anchor_ids))
            for entry in requests:
                if not isinstance(entry, dict):
                    continue
                request = entry.get("request")
                if not isinstance(request, dict):
                    continue
                source_ids = request.get("source_check_ids")
                normalized_source_ids = (
                    {str(value) for value in source_ids}
                    if isinstance(source_ids, list)
                    else {str(request.get("check_id") or "")}
                )
                if anchor_id not in normalized_source_ids:
                    continue
                if request.get("data_type") != "spatial_field" or request.get("product_id") != "QL_PRECIP_6H":
                    continue
                step05 = entry.get("step05")
                stored = step05.get("result") if isinstance(step05, dict) else None
                if not isinstance(stored, dict) or stored.get("status") != "success":
                    continue
                images = stored.get("generated_images")
                if not isinstance(images, list):
                    continue
                for image in images:
                    if not isinstance(image, dict) or image.get("physical_diagnostic") != "QL_PRECIP_6H":
                        continue
                    hour = image.get("forecast_hour")
                    if isinstance(hour, bool) or not isinstance(hour, int) or hour not in unresolved_hours:
                        continue
                    regional = image.get("operational_scope_region_summaries")
                    if not isinstance(regional, list):
                        continue
                    for region in regional:
                        if not isinstance(region, dict):
                            continue
                        region_id = region.get("region_id")
                        region_name = region.get("region_name")
                        max_value = region.get("maximum_6h_mm")
                        if (
                            not isinstance(region_id, str)
                            or not region_id
                            or not isinstance(region_name, str)
                            or not region_name
                            or isinstance(max_value, bool)
                            or not isinstance(max_value, (int, float))
                            or float(max_value) < DIRECT_PRECIPITATION_MINIMUM_MM_6H
                        ):
                            continue
                        resolved = (float(max_value), region_id, region_name)
                        previous = best_by_hour.get(hour)
                        if (
                            previous is None
                            or resolved[0] > previous[0]
                            or (resolved[0] == previous[0] and resolved[1] < previous[1])
                        ):
                            best_by_hour[hour] = resolved

    details: list[dict[str, Any]] = []
    for hour in peak_hours:
        best = best_by_hour.get(hour)
        if best is None:
            details.append(
                {
                    "forecast_hour": hour,
                    "region_id": None,
                    "region_name": None,
                    "value_mm": None,
                }
            )
            continue
        details.append(
            {
                "forecast_hour": hour,
                "region_id": best[1],
                "region_name": _compact_peak_precip_region_name(best[2]),
                "value_mm": best[0],
            }
        )
    return details


def _candidate_name_from_peak_precipitation(details: Any) -> str:
    """Return a concise title from deterministic peak-hour precipitation regions.

    User-facing candidate names are identifiers, not exhaustive impact-region
    inventories.  Keep at most the two strongest distinct peak regions and leave
    the full multi-region footprint to the detailed evidence/timeline.
    """
    if not isinstance(details, list):
        return ""
    best_by_label: dict[str, tuple[float, int]] = {}
    for index, detail in enumerate(details):
        if not isinstance(detail, dict):
            continue
        region_name = _compact_peak_precip_region_name(detail.get("region_name"))
        value_mm = detail.get("value_mm")
        if (
            not region_name
            or isinstance(value_mm, bool)
            or not isinstance(value_mm, (int, float))
            or float(value_mm) < DIRECT_PRECIPITATION_MINIMUM_MM_6H
        ):
            continue
        value = float(value_mm)
        previous = best_by_label.get(region_name)
        if previous is None or value > previous[0]:
            best_by_label[region_name] = (value, index)
    if not best_by_label:
        return ""
    ranked = sorted(
        best_by_label.items(),
        key=lambda item: (-item[1][0], item[1][1], item[0]),
    )
    labels = [item[0] for item in ranked[:2]]
    return f"{'·'.join(labels)} 강수"


def _candidate_name_from_confirmed_direct_precipitation(
    check_specifications: Any,
    check_results: Any,
    region_names: Any,
) -> str:
    """Return a conservative title from usable confirmed direct-precipitation evidence.

    Prefer exact Step07 quantitative claims. When the direct presence anchor is
    image-only, use only Python-enriched operational-region localization/annotation
    and the closed operational-region name catalog; never infer geography from free prose.
    """
    if isinstance(check_specifications, list):
        specifications = {
            str(item.get("check_id")): item
            for item in check_specifications
            if isinstance(item, dict) and str(item.get("check_id") or "")
        }
    elif isinstance(check_specifications, dict):
        specifications = check_specifications
    else:
        specifications = {}
    results = check_results if isinstance(check_results, dict) else {}
    names = region_names if isinstance(region_names, dict) else {}
    best: tuple[float, str] | None = None
    labels: list[str] = []

    def add_label(raw: Any) -> None:
        label = _compact_peak_precip_region_name(raw)
        if label and label not in labels:
            labels.append(label)

    for check_id, result in results.items():
        if not isinstance(result, dict) or result.get("usable_for_synthesis") is not True:
            continue
        if result.get("assessment") != "observed":
            continue
        spec = specifications.get(str(check_id))
        if not isinstance(spec, dict):
            continue
        components = spec.get("required_signal_components")
        if not isinstance(components, list) or "direct_precipitation" not in components:
            continue

        localization = result.get("spatial_localization")
        if isinstance(localization, dict) and localization.get("status") == "localized":
            for item in localization.get("regions", []):
                if not isinstance(item, dict):
                    continue
                region_id = str(item.get("region_id") or "")
                if region_id in names:
                    add_label(names[region_id])

        for claim in result.get("quantitative_claims", []):
            if not isinstance(claim, dict) or claim.get("physical_diagnostic") != "tp_6h_increment_mm":
                continue
            region_name = _compact_peak_precip_region_name(claim.get("region_name"))
            value = claim.get("resolved_value")
            if (
                not region_name
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or float(value) < DIRECT_PRECIPITATION_MINIMUM_MM_6H
            ):
                continue
            add_label(region_name)
            candidate = (float(value), region_name)
            if best is None or candidate[0] > best[0] or (candidate[0] == best[0] and candidate[1] < best[1]):
                best = candidate

        # Step07 enriches the direct-presence finding with this exact marker from
        # deterministic operational-region evidence. Match only official catalog names.
        for finding in result.get("findings", []):
            statement = finding.get("statement") if isinstance(finding, dict) else None
            if not isinstance(statement, str) or "1.0 mm 이상 확인 운영권역:" not in statement:
                continue
            tail = statement.split("1.0 mm 이상 확인 운영권역:", 1)[1]
            for region_id, raw_name in names.items():
                _ = region_id
                if isinstance(raw_name, str) and raw_name and raw_name in tail:
                    add_label(raw_name)

    if best is not None:
        return f"{best[1]} 강수"
    if len(labels) == 1:
        return f"{labels[0]} 강수"
    if len(labels) >= 2:
        return f"{labels[0]}·{labels[1]} 강수"
    return ""


def _movement_candidate_name_from_region_pair(
    region_pair: Any,
    region_names: Any,
) -> str:
    """Return a movement title only from validated Step07 operational-region IDs."""
    if (
        not isinstance(region_pair, tuple)
        or len(region_pair) != 2
        or not isinstance(region_names, dict)
    ):
        return ""
    origin_id, destination_id = region_pair
    if not isinstance(origin_id, str) or not isinstance(destination_id, str):
        return ""
    origin = _compact_peak_precip_region_name(region_names.get(origin_id))
    destination = _compact_peak_precip_region_name(region_names.get(destination_id))
    if not origin or not destination or origin == destination:
        return ""
    return f"{origin} → {destination} 이동 강수"


def verified_precipitation_movement_check_ids(
    check_specifications: Any,
    check_results: Any,
) -> list[str]:
    """Return adopted direct-precipitation Checks that verify movement.

    A user-facing movement title is stronger than a generic precipitation
    title. It is therefore allowed only for a pure direct-precipitation move
    Check whose three Step07 members unanimously adopt ``observed`` and
    ``coherent_movement`` and also agree on one non-null operational-region
    origin/destination pair. Endpoint differences or deterministic peak
    locations alone are not sufficient evidence of coherent movement.
    """
    if isinstance(check_specifications, list):
        specifications = {
            str(item.get("check_id")): item
            for item in check_specifications
            if isinstance(item, dict) and str(item.get("check_id") or "")
        }
    elif isinstance(check_specifications, dict):
        specifications = check_specifications
    else:
        specifications = {}
    results = check_results if isinstance(check_results, dict) else {}

    verified: list[str] = []
    for check_id, spec in specifications.items():
        if not isinstance(spec, dict):
            continue
        result = results.get(check_id)
        if not isinstance(result, dict):
            continue
        if spec.get("variable_role") != "hazard_magnitude" or spec.get("check_scope") != "temporal_evolution":
            continue
        components = spec.get("required_signal_components")
        if not isinstance(components, list) or set(components) != {"direct_precipitation"}:
            continue
        intent = spec.get("intent_contract") if isinstance(spec.get("intent_contract"), dict) else {}
        if intent.get("verification_mode") != "spatial_evolution":
            continue
        atomic = spec.get("atomic_claim_contract") if isinstance(spec.get("atomic_claim_contract"), dict) else {}
        claims = atomic.get("canonical_claims") if isinstance(atomic.get("canonical_claims"), list) else []
        if len(claims) != 1:
            continue
        claim = claims[0]
        if not (
            isinstance(claim, dict)
            and claim.get("expected_state") == "move"
            and claim.get("claim_components") == ["direct_precipitation"]
            and claim.get("scope_quantifier") == "specific"
        ):
            continue
        if result.get("evidence_complete") is not True or result.get("usable_for_synthesis") is not True:
            continue
        if result.get("assessment") != "observed":
            continue
        consensus = result.get("ensemble_consensus") if isinstance(result.get("ensemble_consensus"), dict) else {}
        if consensus.get("assessment_agreement") != "unanimous":
            continue
        if consensus.get("consensus_assessment") != "observed":
            continue
        if consensus.get("precipitation_motion_agreement") != "unanimous":
            continue
        if consensus.get("consensus_precipitation_motion_classification") != "coherent_movement":
            continue
        motion = result.get("precipitation_motion") if isinstance(result.get("precipitation_motion"), dict) else {}
        origin = motion.get("origin_region_id")
        destination = motion.get("destination_region_id")
        if not isinstance(origin, str) or not origin or not isinstance(destination, str) or not destination or origin == destination:
            continue
        verified.append(str(check_id))
    return verified


def verified_precipitation_movement_region_pair(
    check_specifications: Any,
    check_results: Any,
) -> tuple[str, str] | None:
    """Return one unanimous coherent movement pair, or None when checks disagree."""
    results = check_results if isinstance(check_results, dict) else {}
    pairs: set[tuple[str, str]] = set()
    for check_id in verified_precipitation_movement_check_ids(check_specifications, check_results):
        result = results.get(check_id)
        motion = result.get("precipitation_motion") if isinstance(result, dict) and isinstance(result.get("precipitation_motion"), dict) else {}
        origin = motion.get("origin_region_id")
        destination = motion.get("destination_region_id")
        if isinstance(origin, str) and isinstance(destination, str) and origin and destination and origin != destination:
            pairs.add((origin, destination))
    if len(pairs) != 1:
        return None
    return next(iter(pairs))




def _candidate_name_from_precipitation_object_identity(identity: Any, region_names: Any) -> str:
    """Build a concise title from SYSTEM precipitation-footprint identity."""
    if not isinstance(identity, dict) or identity.get("status") != "precipitation_object":
        return ""
    names = region_names if isinstance(region_names, dict) else {}
    regions = identity.get("regions")
    if not isinstance(regions, list) or not regions:
        return ""
    labels: list[str] = []
    for item in regions[:2]:
        if not isinstance(item, dict):
            continue
        region_id = str(item.get("region_id") or "").strip()
        region_name = _compact_peak_precip_region_name(names.get(region_id))
        if not region_id or not region_name:
            continue
        sector_key = str(item.get("relative_sector") or "unspecified")
        coverage_class = str(item.get("coverage_class") or "partial")
        relation = str(item.get("object_relation") or "interior")
        region_role = str(item.get("region_role") or "")
        if relation == "adjacent":
            direction = FINAL_SPATIAL_ADJACENT_DIRECTION_LABELS_KO.get(sector_key, "")
            adjacent_noun = "인접 해상" if region_role == "marine_macro" else "인접 영역"
            label = " ".join(part for part in (region_name, direction, adjacent_noun) if part).strip()
        else:
            sector = FINAL_SPATIAL_SECTOR_LABELS_KO.get(sector_key, "")
            if coverage_class == "broad":
                label = region_name
            elif coverage_class == "local":
                label = " ".join(part for part in (region_name, sector, "국지") if part).strip()
            else:
                label = " ".join(part for part in (region_name, sector) if part).strip()
        if label and label not in labels:
            labels.append(label)
    return f"{'·'.join(labels)} 강수" if labels else ""


def candidate_operational_display_name(
    candidate: Any,
    revision: Any,
    final_spatial_identity: Any,
    region_names: Any,
    *,
    direct_precipitation_status: Any = None,
    peak_precipitation_details: Any = None,
    check_specifications: Any = None,
    check_results: Any = None,
) -> str:
    """Return the final user-facing candidate name from validated spatial evidence.

    Current Step08 products use the LLM-selected ``revised_candidate_name``
    after it has been validated against a SYSTEM-computed closed naming
    contract.  The deterministic branches below are retained only for legacy
    stored products that predate LLM-grounded final naming.
    """
    identity = final_spatial_identity if isinstance(final_spatial_identity, dict) else {}
    names = region_names if isinstance(region_names, dict) else {}

    # New Step08 products let the LLM select the final user-facing name from a
    # SYSTEM-computed closed set of spatially grounded options.  Validation is
    # completed before Step09, so a non-empty revised name is authoritative for
    # display.  Deterministic naming below remains only as a backward-compatible
    # fallback for legacy stored Step08 results.
    revised = revision.get("revised_candidate_name") if isinstance(revision, dict) else None
    if isinstance(revised, str) and revised.strip():
        return revised.strip().replace("강우", "강수")

    if identity.get("status") == "localized":
        raw_regions = identity.get("regions")
        if not isinstance(raw_regions, list) or not raw_regions:
            return ""
        labels: list[str] = []
        for item in raw_regions:
            if not isinstance(item, dict):
                return ""
            region_id = str(item.get("region_id") or "").strip()
            region_name = str(names.get(region_id) or "").strip()
            if not region_id or not region_name:
                return ""
            sector = FINAL_SPATIAL_SECTOR_LABELS_KO.get(
                str(item.get("relative_sector") or "unspecified"), ""
            )
            label = f"{region_name} {sector}".strip()
            if label not in labels:
                labels.append(label)
        if not labels:
            return ""
        return f"{'·'.join(labels[:2])} 강수"

    if str(direct_precipitation_status or "") == "confirmed":
        movement_pair = verified_precipitation_movement_region_pair(
            check_specifications, check_results
        )
        if movement_pair is not None:
            movement_name = _movement_candidate_name_from_region_pair(
                movement_pair, names
            )
            if movement_name:
                return movement_name
        object_name = _candidate_name_from_precipitation_object_identity(identity, names)
        if object_name:
            return object_name
        peak_name = _candidate_name_from_peak_precipitation(peak_precipitation_details)
        if peak_name:
            return peak_name
        direct_name = _candidate_name_from_confirmed_direct_precipitation(
            check_specifications, check_results, names
        )
        if direct_name:
            return direct_name
        return "강수 감시후보"

    return candidate_display_name(candidate, revision)


def _candidate_name_revision_evidence_contract(
    *,
    candidate: Any,
    check_specifications: Any,
    check_results: Any,
) -> dict[str, Any]:
    """Return deterministic evidence classes allowed to support a display-name revision.

    A name change is intentionally stricter than ordinary candidate synthesis.
    It requires both (1) a usable direct-precipitation spatial proposition whose
    expected spatial evolution/distribution was explicitly refuted and (2) a
    usable Step07 structured localization that provides an alternative validated
    place.  This prevents a narrow environmental localization alone from
    silently renaming the whole candidate episode.
    """
    specifications = check_specifications if isinstance(check_specifications, dict) else {}
    results = check_results if isinstance(check_results, dict) else {}
    source_name = str(candidate.get("candidate_name") or "").strip() if isinstance(candidate, dict) else ""
    contradiction_ids: list[str] = []
    localized_ids: list[str] = []
    reversed_source_terms: list[str] = []
    for raw_check_id, specification in specifications.items():
        check_id = str(raw_check_id)
        result = results.get(check_id)
        if not isinstance(specification, dict) or not isinstance(result, dict):
            continue
        usable = (
            result.get("evidence_status") == "complete"
            and result.get("evidence_complete") is True
            and result.get("usable_for_synthesis") is not False
            and result.get("assessment") != "insufficient_evidence"
        )
        if not usable:
            continue

        intent = specification.get("intent_contract") if isinstance(specification.get("intent_contract"), dict) else {}
        verification_mode = str(intent.get("verification_mode") or specification.get("verification_mode") or "")
        check_scope = str(specification.get("check_scope") or "")
        if (
            specification.get("variable_role") == "hazard_magnitude"
            and "direct_precipitation" in {str(v) for v in specification.get("required_signal_components", [])}
            and (verification_mode in {"spatial_evolution", "spatial_distribution"} or check_scope in {"spatial_evolution", "spatial_distribution", "temporal_evolution"})
            and result.get("assessment") in {"not_observed", "opposite_observed"}
        ):
            contradiction_ids.append(check_id)
            reversed_source_terms.extend(_explicitly_reversed_candidate_name_terms(source_name, result))

        localization = result.get("spatial_localization")
        if isinstance(localization, dict):
            regions = localization.get("regions")
            if (
                localization.get("status") == "localized"
                and isinstance(regions, list)
                and any(isinstance(item, dict) and str(item.get("region_id") or "") for item in regions)
            ):
                localized_ids.append(check_id)

    reversed_source_terms = list(dict.fromkeys(reversed_source_terms))
    contradiction_ids = sorted(set(contradiction_ids))
    localized_ids = sorted(set(localized_ids))
    return {
        "source_candidate_name": source_name,
        "direct_precipitation_spatial_contradiction_check_ids": contradiction_ids,
        "localized_spatial_check_ids": localized_ids,
        "explicitly_contradicted_source_spatial_terms": reversed_source_terms,
        "revision_required": bool(reversed_source_terms and contradiction_ids and localized_ids),
    }


def candidate_name_revision_evidence_contract(
    *,
    candidate: Any,
    check_specifications: Any,
    check_results: Any,
) -> dict[str, Any]:
    """Public wrapper used by Step08 prompt-grounding and validators."""
    return _candidate_name_revision_evidence_contract(
        candidate=candidate,
        check_specifications=check_specifications,
        check_results=check_results,
    )


def validate_candidate_name_revision(
    revision: Any,
    *,
    candidate: Any,
    check_specifications: Any,
    check_results: Any,
    grounding_contract: Any = None,
) -> list[str]:
    """Validate Step08's user-facing candidate name.

    New Step08 products use a SYSTEM-computed closed naming contract and let
    the LLM choose the final display name from that set.  The legacy explicit-
    contradiction contract remains available when no grounding contract is
    supplied so older stored products can still be validated.
    """
    if not isinstance(revision, dict):
        return ["revision must be an object"]
    revised = revision.get("revised_candidate_name")
    reason = revision.get("candidate_name_revision_reason")
    evidence_ids = revision.get("candidate_name_revision_evidence_check_ids")
    errors: list[str] = []

    if isinstance(grounding_contract, dict):
        options = grounding_contract.get("name_options")
        if not isinstance(options, list) or not options:
            return ["candidate-name grounding contract has no name options"]
        option_by_name = {
            str(item.get("name") or ""): item
            for item in options
            if isinstance(item, dict) and str(item.get("name") or "")
        }
        if not isinstance(revised, str) or not revised.strip():
            errors.append("revised_candidate_name is required by the LLM-grounded naming contract")
            return errors
        revised = revised.strip().replace("강우", "강수")
        option = option_by_name.get(revised)
        if option is None:
            errors.append(
                "revised_candidate_name must exactly match one of candidate_name_grounding_contract.allowed_display_names"
            )
        if len(revised) > 60:
            errors.append("revised_candidate_name must be 60 characters or fewer")
        if re.search(r"외\s*\d+개\s*권역", revised):
            errors.append("revised_candidate_name must not use an '외 N개 권역' summary")
        if not isinstance(reason, str) or not reason.strip():
            errors.append("candidate_name_revision_reason is required for the LLM-selected display name")
        elif len(reason.strip()) > 240:
            errors.append("candidate_name_revision_reason must be 240 characters or fewer")
        if not isinstance(evidence_ids, list) or len(set(map(str, evidence_ids))) != len(evidence_ids):
            errors.append("candidate_name_revision_evidence_check_ids must be a unique array")
            return errors
        normalized_ids = {str(value) for value in evidence_ids}
        known_checks = set(check_results if isinstance(check_results, dict) else {})
        unknown = sorted(normalized_ids - known_checks)
        if unknown:
            errors.append(f"candidate-name selection references unknown Step07 checks: {unknown}")
        if option is not None:
            required_ids = {str(value) for value in option.get("evidence_check_ids", []) if str(value)}
            if not required_ids.issubset(normalized_ids):
                errors.append(
                    "candidate_name_revision_evidence_check_ids must include the evidence IDs attached to the selected name option"
                )
        return errors

    contract = _candidate_name_revision_evidence_contract(
        candidate=candidate,
        check_specifications=check_specifications,
        check_results=check_results,
    )
    if revised is None:
        if reason not in (None, "") or evidence_ids not in (None, []):
            errors.append("candidate-name revision metadata must be empty when revised_candidate_name is null")
        if contract.get("revision_required") is True:
            errors.append(
                "revised_candidate_name is required because a source spatial term is explicitly reversed by direct precipitation evidence and a localized replacement location is available"
            )
        return errors

    if not isinstance(revised, str) or not revised.strip():
        errors.append("revised_candidate_name must be non-empty text or null")
        return errors
    revised = revised.strip()
    source_name = str(candidate.get("candidate_name") or "").strip() if isinstance(candidate, dict) else ""
    if revised == source_name:
        errors.append("revised_candidate_name must differ from the immutable Step04 candidate_name")
    if len(revised) > 60:
        errors.append("revised_candidate_name must be 60 characters or fewer")
    if "이동" in revised or "→" in revised or "->" in revised:
        errors.append(
            "revised_candidate_name must not assert precipitation movement; movement titles are generated only from unanimous Step07 coherent-movement evidence"
        )
    if not isinstance(reason, str) or not reason.strip():
        errors.append("candidate_name_revision_reason is required when revised_candidate_name is set")
    elif len(reason.strip()) > 240:
        errors.append("candidate_name_revision_reason must be 240 characters or fewer")
    if not isinstance(evidence_ids, list) or not evidence_ids or len(set(map(str, evidence_ids))) != len(evidence_ids):
        errors.append("candidate_name_revision_evidence_check_ids must be a non-empty unique array when revised_candidate_name is set")
        return errors

    normalized_ids = {str(value) for value in evidence_ids}
    unknown = sorted(normalized_ids - set(check_results if isinstance(check_results, dict) else {}))
    if unknown:
        errors.append(f"candidate-name revision references unknown Step07 checks: {unknown}")

    if contract.get("revision_required") is not True:
        errors.append("candidate-name revision is not allowed without an explicit reversal of the source spatial identity")
    contradiction_ids = set(contract["direct_precipitation_spatial_contradiction_check_ids"])
    localized_ids = set(contract["localized_spatial_check_ids"])
    if not normalized_ids.intersection(contradiction_ids):
        errors.append("candidate-name revision requires direct precipitation spatial contradiction evidence")
    if not normalized_ids.intersection(localized_ids):
        errors.append("candidate-name revision requires localized spatial evidence for the replacement location")
    return errors


def validate_pass2_revision(
    revision: Any,
    envelope: dict[str, Any],
    *,
    candidate_peak_hours: Iterable[Any] | None = None,
) -> list[str]:
    if not isinstance(revision, dict):
        return ["revision must be an object"]
    errors: list[str] = []
    revised_area = revision.get("revised_area")
    revised_window = revision.get("revised_time_window")
    if revised_area is not None and not bbox_within(revised_area, envelope.get("spatial")):
        errors.append("revised_area must be inside analysis_envelope")
    if revised_window is not None and not time_window_within(revised_window, envelope.get("temporal")):
        errors.append("revised_time_window must be inside analysis_envelope")
    if revised_window is not None and candidate_peak_hours is not None:
        normalized = normalize_time_window(revised_window)
        peaks: list[int] = []
        for value in candidate_peak_hours:
            if isinstance(value, bool):
                continue
            try:
                hour = int(value)
            except (TypeError, ValueError):
                continue
            if hour >= 0:
                peaks.append(hour)
        if normalized and peaks and not any(
            normalized["forecast_hour_start"] <= hour <= normalized["forecast_hour_end"]
            for hour in peaks
        ):
            errors.append("revised_time_window must retain at least one candidate peak_forecast_hour")
    return errors


def _evidence_complete(value: dict[str, Any]) -> bool:
    evidence = value.get("evidence_completeness")
    if isinstance(evidence, dict):
        return bool(evidence.get("sufficient"))
    return bool(evidence)


def derive_candidate_type(detection_basis: Any, direct_precipitation_status: Any) -> str:
    """Return the operational candidate path after detailed direct-precipitation validation.

    A confirmed direct signal always uses the direct-precipitation path.  When
    detailed validation does not confirm direct precipitation, only candidates
    that were discovered as precursor/mixed signals may fall back to the
    precursor-environment path.  A failed direct-only discovery must not become
    a precursor candidate accidentally.
    """
    basis = str(detection_basis or "")
    direct = str(direct_precipitation_status or "")
    if direct == "confirmed":
        return "direct_precipitation"
    if basis in {"precursor_environment", "mixed"}:
        return "precursor_environment"
    return "direct_precipitation"


def _has_partial_precursor_environment_basis(categories: Iterable[Any]) -> bool:
    """Return whether partial precursor support still contains two core paths and forcing.

    This intentionally relaxes only the *alignment* requirement of the confirmed
    precursor contract.  It still requires at least two operational precursor
    categories and at least one low/mid-level forcing category, so a moisture-only
    signal cannot be promoted to a visible general watch.
    """
    unique = set(_deduplicate_categories(categories)) & set(OPERATIONAL_PRECURSOR_CATEGORIES)
    return (
        len(unique) >= 2
        and bool(unique & OPERATIONAL_PRECURSOR_FORCING_CATEGORIES)
    )


def has_operational_escalation_evidence(
    signal: dict[str, Any],
    check_results: dict[str, dict[str, Any]],
    validated_categories: Iterable[Any],
) -> bool:
    """Return whether a direct boundary signal has evidence for elevated attention.

    ``monitoring_value=meaningful`` alone is intentionally insufficient.  An
    elevated direct candidate must also retain a dynamical anchor (validated
    low/mid-level forcing or validated multivariable spatiotemporal alignment)
    and at least one supporting monitoring Check that Step07 actually observed
    and marked complete/usable for synthesis.
    """
    categories = set(_deduplicate_categories(validated_categories))
    has_dynamical_anchor = (
        bool(categories & OPERATIONAL_PRECURSOR_FORCING_CATEGORIES)
        or bool(signal.get("validated_spatiotemporal_alignment"))
    )
    if not has_dynamical_anchor:
        return False

    monitoring_evidence = signal.get("monitoring_evidence")
    if not isinstance(monitoring_evidence, list):
        return False
    for item in monitoring_evidence:
        if not isinstance(item, dict) or str(item.get("effect") or "") != "supporting":
            continue
        check_id = str(item.get("check_id") or "")
        result = check_results.get(check_id)
        if not isinstance(result, dict):
            continue
        if str(result.get("assessment") or "") != "observed":
            continue
        if result.get("evidence_complete") is not True:
            continue
        if result.get("usable_for_synthesis") is not True:
            continue
        return True
    return False


def derive_display_tier(assessment: dict[str, Any]) -> str:
    """Return deterministic four-level operational display tier.

    priority:
        Confirmed direct precipitation with validated complete core coupling.
    elevated:
        A surfaced direct signal with meaningful monitoring value, or a confirmed
        precursor environment with meaningful monitoring value.
    general:
        A coherent boundary direct signal with marginal monitoring value, or a
        validated precursor-environment signal that remains meteorologically
        plausible but is not strong enough for elevated emphasis.
    hidden:
        Failed validation, weak/incoherent evidence, failed direct-only discovery,
        or an explicit Step08 do-not-surface boundary decision.
    """
    if str(assessment.get("validation_status") or "") != "valid":
        return "hidden"

    candidate_type = str(assessment.get("candidate_type") or "")
    if candidate_type not in CANDIDATE_TYPES:
        return "hidden"

    direct = str(assessment.get("direct_precipitation_status") or "")
    environment = str(assessment.get("environment_support_status") or "")
    physical = str(assessment.get("physical_consistency_status") or "")
    monitoring = str(assessment.get("monitoring_value") or "")
    decision = str(assessment.get("operational_surface_decision") or "not_applicable")
    coherent = physical in {"coherent", "conditionally_coherent"}
    evidence_complete = _evidence_complete(assessment)

    if candidate_type == "direct_precipitation":
        if direct != "confirmed":
            return "hidden"
        if environment == "confirmed":
            if (
                bool(assessment.get("complete_core_environment_coupling"))
                and coherent
            ):
                return "priority"
            if coherent and monitoring == "meaningful":
                return "elevated"
            return "hidden"
        if environment == "partial":
            if decision != "surface" or not coherent:
                return "hidden"
            if monitoring == "meaningful":
                return "elevated" if bool(assessment.get("operational_escalation_evidence")) else "general"
            if monitoring == "marginal":
                return "general"
        return "hidden"

    # Precursor candidates are allowed to surface without direct precipitation.
    # Confirmed precursor support retains the full independent-support + alignment
    # contract; partial precursor support can appear only as a general watch and
    # must still retain two core categories including dynamical forcing.
    if direct == "confirmed":
        return "hidden"
    categories = assessment.get("independent_signal_categories", [])
    if environment == "confirmed":
        precursor_valid = has_independent_precursor_support(
            categories,
            spatial_temporal_overlap=bool(assessment.get("spatiotemporal_alignment")),
        )
        if not (precursor_valid and evidence_complete and coherent):
            return "hidden"
        if monitoring == "meaningful":
            return "elevated"
        if monitoring == "marginal":
            return "general"
        return "hidden"
    if environment == "partial":
        if (
            evidence_complete
            and coherent
            and _has_partial_precursor_environment_basis(categories)
            and monitoring in {"meaningful", "marginal"}
        ):
            return "general"
    return "hidden"


def should_surface(assessment: dict[str, Any]) -> bool:
    """Return whether a validated candidate belongs in the visible monitoring list.

    New Step09 callers should provide ``candidate_type`` and use the four-level
    policy.  The legacy branch preserves the previous public helper contract for
    external callers that still pass the older assessment shape.
    """
    if "candidate_type" in assessment:
        return derive_display_tier(assessment) != "hidden"

    direct = str(assessment.get("direct_precipitation_status") or "")
    environment = str(assessment.get("environment_support_status") or "")
    decision = str(assessment.get("operational_surface_decision") or "not_applicable")
    monitoring_value = str(assessment.get("monitoring_value") or "")
    if direct == "confirmed":
        if environment == "confirmed":
            return True
        if environment == "partial":
            return decision == "surface" and monitoring_value == "meaningful"
        return False
    if environment == "confirmed":
        categories = assessment.get("independent_signal_categories", [])
        return has_independent_precursor_support(
            categories,
            spatial_temporal_overlap=bool(assessment.get("spatiotemporal_alignment")),
        ) and _evidence_complete(assessment)
    return False


def monitoring_emphasis(assessment: dict[str, Any]) -> str:
    """Return legacy emphasis while supporting the four-level display policy."""
    if "candidate_type" in assessment:
        return "priority" if derive_display_tier(assessment) == "priority" else "standard"
    if (
        str(assessment.get("direct_precipitation_status")) == "confirmed"
        and str(assessment.get("environment_support_status")) == "confirmed"
        and bool(assessment.get("complete_core_environment_coupling"))
        and bool(assessment.get("validation_status") == "valid")
    ):
        return "priority"
    return "standard"


@dataclass(frozen=True)
class RankedAssessment:
    candidate_id: str
    should_surface: bool
    signal_class: str
    score: float
    evidence_complete: bool
    pass1_discovery_order: int


def rank_assessments(rows: Iterable[RankedAssessment]) -> list[RankedAssessment]:
    """Return deterministic final ordering. Lower tuple values rank first."""
    return sorted(
        rows,
        key=lambda item: (
            not item.should_surface,
            SIGNAL_CLASS_ORDER.get(item.signal_class, len(SIGNAL_CLASS_ORDER)),
            -float(item.score),
            not item.evidence_complete,
            int(item.pass1_discovery_order),
            item.candidate_id,
        ),
    )
