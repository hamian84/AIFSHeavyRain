"""Step step08 support responsibility; original function bodies preserved."""
from __future__ import annotations
from . import synthesis_context as _step_context

# Original func/step08/synthesis.py:194
def _question_rows(candidate_id: str, plan: dict[str, Any]) -> list[dict[str, str]]:
    questions = plan.get("forecaster_questions")
    if not isinstance(questions, list):
        raise Pass2PipelineError("FORECASTER_QUESTIONS_INVALID", f"{candidate_id} forecaster_questions must be a list.")
    rows: list[dict[str, str]] = []
    for index, question in enumerate(questions, start=1):
        if not isinstance(question, str) or not question.strip():
            raise Pass2PipelineError("FORECASTER_QUESTIONS_INVALID", f"{candidate_id} has an invalid forecaster question.")
        rows.append({"forecaster_question_id": f"FQ_{candidate_id}_{index:02d}", "question": question})
    return rows

# Original func/step08/synthesis.py:206
def _check_map(candidate_id: str, step07: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in step07.get("results", []):
        if not isinstance(item, dict) or item.get("candidate_id") != candidate_id:
            continue
        rows.append(normalize_step07_result_for_synthesis(item))
    return {str(item["check_id"]): item for item in rows}

# Original func/step08/synthesis.py:222
def _usable_check_ids(checks: dict[str, dict[str, Any]]) -> set[str]:
    """Return checks that may support Step08 synthesis.

    Legacy results without the new flag remain readable unless they are explicitly
    insufficient. Newly generated Step07 results persist usable_for_synthesis.
    """
    return {
        check_id
        for check_id, result in checks.items()
        if result.get("assessment") != "insufficient_evidence"
        and result.get("usable_for_synthesis") is not False
    }

# Original func/step08/synthesis.py:1092
def _check_specifications(group: dict[str, Any]) -> dict[str, dict[str, Any]]:
    plan = group["candidate_context"]["analysis_plan"]
    values = plan.get("variable_checks")
    if not isinstance(values, list):
        raise Pass2PipelineError("VARIABLE_CHECKS_INVALID", f"{group.get('candidate_id')} has no variable_checks.")
    return {
        str(item["check_id"]): item
        for item in values
        if isinstance(item, dict) and isinstance(item.get("check_id"), str) and item["check_id"]
    }

# Original func/step08/synthesis.py:1104
def _candidate_detection_basis(group: dict[str, Any]) -> str:
    candidate = ((group.get("candidate_context") or {}).get("candidate") or {})
    return str(candidate.get("detection_basis") or "") if isinstance(candidate, dict) else ""

# Original func/step08/synthesis.py:1660
def _usable_precursor_evidence_paths(
    group: dict[str, Any], checks: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Return category-specific usable Step07 anchors for Step08 grounding.

    This is deterministic reference scoping only.  ``validated_precursor_evidence``
    is reserved for positive support, so not-observed/opposite/uncertain Checks are
    excluded from these anchor lists and remain available separately as physical
    limiting evidence.  Empty lists mean that Step08 must not declare that category
    as validated precursor support.
    """
    specifications = _check_specifications(group)
    usable = _usable_check_ids(checks)
    return {
        category: sorted(
            check_id
            for check_id, specification in specifications.items()
            if check_id in usable
            and category_check_is_positive_support_compatible(category, specification)
            and checks.get(check_id, {}).get("assessment") in POSITIVE_VALIDATED_PRECURSOR_ASSESSMENTS
        )
        for category in PRECURSOR_CATEGORY_ORDER
    }

# Original func/step08/synthesis.py:1714
def _candidate_analysis_region_index(root: Path, group: dict[str, Any]) -> dict[str, Any]:
    """Return standardized operational-region names that overlap candidate_area.

    This index is **naming context only** for Step08 natural-language synthesis.
    It does not change the candidate bbox, Step05 diagnostic domain, Step07
    evidence, or the population used by any quantitative relation.  The model
    may use these names to explain what the analysis domain covers, but may not
    infer that a meteorological feature is located in a region merely because
    the region overlaps the candidate bbox.
    """
    candidate = (group.get("candidate_context") or {}).get("candidate") or {}
    candidate_bbox = candidate.get("candidate_area")
    if not isinstance(candidate_bbox, dict):
        return {
            "contract_version": ANALYSIS_REGION_NAMING_CONTRACT_VERSION,
            "scope": "candidate_area_overlap",
            "usage": "analysis_area_naming_only",
            "regions": [],
        }

    catalog_path = runtime_path(
        root,
        "daba",
        "ai_pass1",
        "discovery_operational_regions.yaml",
    )
    regions = load_operational_regions(catalog_path)
    selected = selectable_pass1_regions(
        regions=regions,
        candidate_bbox=candidate_bbox,
    )
    return {
        "contract_version": ANALYSIS_REGION_NAMING_CONTRACT_VERSION,
        "scope": "candidate_area_overlap",
        "usage": "analysis_area_naming_only",
        "regions": [
            {
                "region_id": str(item.get("region_id", "")),
                "name_ko": str(item.get("name_ko", "")),
                "region_role": str(item.get("region_role", "")),
                "eligible_for_risk_rank": bool(item.get("eligible_for_risk_rank")),
            }
            for item in selected
        ],
    }

# Original func/step08/synthesis.py:2729
def _usable_alignment_check_ids(group: dict[str, Any], checks: dict[str, dict[str, Any]]) -> list[str]:
    """Return only alignment Checks that can survive the Step09 publication gate."""
    specifications = _check_specifications(group)
    return sorted(
        check_id
        for check_id in specifications
        if _is_publication_eligible_alignment_check(
            check_id,
            specifications=specifications,
            checks=checks,
        )
    )

# Original func/step08/synthesis.py:2743
def _observed_direct_precipitation_anchor_hours(
    group: dict[str, Any], checks: dict[str, dict[str, Any]],
) -> set[int]:
    """Return image-evidenced hours from a confirmed SYSTEM presence anchor."""
    hours: set[int] = set()
    for check_id in _confirmed_direct_precipitation_anchor_ids(group, checks):
        result = checks.get(check_id)
        if not isinstance(result, dict):
            continue
        for finding in result.get("findings", []):
            if not isinstance(finding, dict):
                continue
            for ref in finding.get("evidence_refs", []):
                if not isinstance(ref, dict):
                    continue
                hour = ref.get("forecast_hour")
                if (
                    ref.get("evidence_type") == "image"
                    and ref.get("physical_diagnostic") == "QL_PRECIP_6H"
                    and isinstance(hour, int)
                    and not isinstance(hour, bool)
                ):
                    hours.add(int(hour))
    return hours

# Original func/step08/synthesis.py:2769
def _derive_environment_localization(
    group: dict[str, Any], checks: dict[str, dict[str, Any]], signal: dict[str, Any],
) -> dict[str, Any] | None:
    """Select one already-validated Step07 localization for public display.

    This is deterministic reference bookkeeping.  It does not infer a new
    overlap, region, or meteorological score.
    """
    if signal.get("direct_precipitation_status") != "confirmed":
        return None
    if signal.get("environment_support_status") not in {"confirmed", "partial"}:
        return None
    if signal.get("validated_spatiotemporal_alignment") is not True:
        return None
    alignment_ids = signal.get("spatiotemporal_alignment_evidence_check_ids")
    if not isinstance(alignment_ids, list) or not alignment_ids:
        return None
    anchor_hours = _observed_direct_precipitation_anchor_hours(group, checks)
    if not anchor_hours:
        return None
    candidate = group.get("candidate_context", {}).get("candidate", {})
    peak_hours = {
        int(value) for value in candidate.get("peak_forecast_hours", [])
        if isinstance(value, int) and not isinstance(value, bool)
    } if isinstance(candidate, dict) else set()
    candidates: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
    confidence_rank = {"high": 0, "medium": 1}
    specifications = _check_specifications(group)
    for order, raw_check_id in enumerate(alignment_ids):
        check_id = str(raw_check_id)
        result = checks.get(check_id)
        if not isinstance(result, dict):
            continue
        if not (
            result.get("assessment") == "observed"
            and result.get("scope_coverage") == "complete_expected_scope"
            and result.get("evidence_status") == "complete"
            and result.get("evidence_complete") is True
            and result.get("usable_for_synthesis") is not False
            and not result.get("contradictory_findings")
            and not result.get("uncertainties")
        ):
            continue
        localization = result.get("spatial_localization")
        if not isinstance(localization, dict) or localization.get("status") != "localized":
            continue
        confidence = localization.get("confidence")
        hour = localization.get("forecast_hour")
        regions = localization.get("regions")
        if confidence not in confidence_rank or not isinstance(hour, int) or isinstance(hour, bool) or not isinstance(regions, list) or not regions:
            continue
        if hour not in anchor_hours:
            continue
        specification = specifications.get(check_id, {})
        signal_components = [
            component
            for component in (
                "direct_precipitation",
                "moisture_supply",
                "low_level_forcing",
                "mid_upper_forcing",
            )
            if component in {
                str(value)
                for value in specification.get("required_signal_components", [])
                if isinstance(value, str) and value
            }
        ]
        if len(signal_components) < 2 or "direct_precipitation" not in signal_components:
            continue
        public_value = {
            "status": "localized",
            "forecast_hour": int(hour),
            "confidence": str(confidence),
            "evidence_check_id": check_id,
            "signal_components": signal_components,
            "regions": copy.deepcopy(regions),
            "source": "step07_validated_spatial_localization",
        }
        rank = (confidence_rank[str(confidence)], 0 if hour in peak_hours else 1, order, int(hour))
        candidates.append((rank, public_value))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]

# Original func/step08/synthesis.py:2858
def _relative_sector_from_centroid(centroid: Any, region_bbox: Any) -> str:
    if not isinstance(centroid, dict) or not isinstance(region_bbox, dict):
        return "unspecified"
    try:
        lon = float(centroid["longitude"])
        lat = float(centroid["latitude"])
        west = float(region_bbox["west"])
        east = float(region_bbox["east"])
        south = float(region_bbox["south"])
        north = float(region_bbox["north"])
    except (KeyError, TypeError, ValueError):
        return "unspecified"
    if not (west < east and south < north):
        return "unspecified"
    x = (lon - west) / (east - west)
    y = (lat - south) / (north - south)
    horizontal = "west" if x < 1.0 / 3.0 else ("east" if x > 2.0 / 3.0 else "")
    vertical = "south" if y < 1.0 / 3.0 else ("north" if y > 2.0 / 3.0 else "")
    if vertical and horizontal:
        return f"{vertical}{horizontal}"
    if vertical:
        return vertical
    if horizontal:
        return horizontal
    return "central"

# Original func/step08/synthesis.py:2885
def _precipitation_object_region_identity(overlap: dict[str, Any], obj: dict[str, Any]) -> dict[str, Any] | None:
    """Summarize one object/region overlap without letting one max grid define scope.

    ``coverage_class`` is only a deterministic safety tier for generating the
    *allowed* naming vocabulary. GPT-6 Sol still chooses the final display name.
    The safety tier uses the primary object's own footprint inside the full
    operational region rather than all precipitation in that region.
    """
    region_id = str(overlap.get("region_id") or "").strip()
    if not region_id:
        return None
    overlap_count = int(overlap.get("overlap_grid_count") or 0)
    full_count = int(overlap.get("full_region_grid_count") or 0)
    region_precip_count = int(overlap.get("region_precip_grid_count") or 0)
    object_region_raw = overlap.get("object_region_coverage_fraction")
    try:
        object_region_coverage = (
            float(object_region_raw)
            if object_region_raw is not None
            else (float(overlap_count / full_count) if full_count > 0 else 0.0)
        )
    except (TypeError, ValueError, ZeroDivisionError):
        object_region_coverage = 0.0
    object_region_coverage = max(0.0, min(1.0, object_region_coverage))

    # Size-aware guardrails. These do not choose the final name; they only
    # prevent clearly tiny footprints from being offered as an unqualified
    # whole-region title. Small catalog regions need a larger fractional
    # footprint before a broad name is even eligible.
    if full_count <= 0:
        broad_name_allowed = False
        local_name_supported = overlap_count <= 2
    elif full_count <= 8:
        broad_name_allowed = overlap_count >= 2 and object_region_coverage >= 0.50
        local_name_supported = overlap_count <= 1
    elif full_count <= 32:
        broad_name_allowed = overlap_count >= 4 and object_region_coverage >= 0.25
        local_name_supported = overlap_count <= 2 or object_region_coverage < 0.10
    else:
        broad_name_allowed = overlap_count >= 6 and object_region_coverage >= 0.20
        local_name_supported = overlap_count <= 2 or object_region_coverage < 0.08

    coverage_class = "broad" if broad_name_allowed else ("local" if local_name_supported else "partial")
    region_bbox = overlap.get("region_bbox")
    object_centroid = obj.get("centroid") if isinstance(obj, dict) else None
    overlap_centroid = overlap.get("overlap_centroid")
    relation = "interior"
    object_fraction = 0.0
    try:
        object_fraction = float(overlap.get("object_overlap_fraction") or 0.0)
    except (TypeError, ValueError):
        object_fraction = 0.0
    if isinstance(object_centroid, dict) and isinstance(region_bbox, dict):
        try:
            lon = float(object_centroid["longitude"])
            lat = float(object_centroid["latitude"])
            inside = (
                float(region_bbox["west"]) <= lon <= float(region_bbox["east"])
                and float(region_bbox["south"]) <= lat <= float(region_bbox["north"])
            )
            if not inside:
                relation = "adjacent"
            elif object_fraction < 0.25:
                relation = "edge"
        except (KeyError, TypeError, ValueError):
            pass
    # For an adjacent object the direction must be based on the object's center,
    # not the small sliver that happens to overlap the operational region.
    sector_source = object_centroid if relation == "adjacent" else overlap_centroid
    sector = _relative_sector_from_centroid(sector_source, region_bbox)
    share_raw = overlap.get("object_share_of_region_precip")
    try:
        object_share = float(share_raw) if share_raw is not None else None
    except (TypeError, ValueError):
        object_share = None
    return {
        "region_id": region_id,
        "relative_sector": sector,
        "coverage_class": coverage_class,
        "object_relation": relation,
        "region_role": str(overlap.get("region_role") or ""),
        "precip_coverage_fraction": object_region_coverage,
        "object_overlap_fraction": max(0.0, min(1.0, object_fraction)),
        "overlap_grid_count": max(1, overlap_count),
        "region_precip_grid_count": max(0, region_precip_count),
        "full_region_grid_count": max(0, full_count),
        "object_share_of_region_precip": None if object_share is None else max(0.0, min(1.0, object_share)),
        "broad_name_allowed": bool(broad_name_allowed),
        "local_name_supported": bool(local_name_supported),
    }

# Original func/step08/synthesis.py:2977
def _derive_system_precipitation_object_identity(
    group: dict[str, Any],
    signal: dict[str, Any],
) -> dict[str, Any] | None:
    """Derive a public precipitation footprint identity from SYSTEM Step05 metadata."""
    if str(signal.get("direct_precipitation_status") or "") != "confirmed":
        return None
    context = group.get("candidate_context")
    plan = context.get("analysis_plan") if isinstance(context, dict) else None
    candidate = context.get("candidate") if isinstance(context, dict) else None
    if not isinstance(plan, dict) or not isinstance(candidate, dict):
        return None
    peak_hours = {
        int(hour)
        for hour in candidate.get("peak_forecast_hours", [])
        if isinstance(hour, int) and not isinstance(hour, bool)
    }
    if not peak_hours:
        return None
    anchor_ids = [
        str(check.get("check_id") or "")
        for check in plan.get("variable_checks", [])
        if isinstance(check, dict)
        and isinstance(check.get("intent_contract"), dict)
        and check["intent_contract"].get("direct_precipitation_anchor") is True
        and str(check["intent_contract"].get("source_check_key") or "") == "SYS_DIRECT_PRECIPITATION_PRESENCE"
        and str(check.get("check_id") or "")
    ]
    if len(anchor_ids) != 1:
        return None
    anchor_id = anchor_ids[0]
    object_rows: list[tuple[tuple[float, int, float, int], dict[str, Any], int]] = []
    for entry in group.get("analysis_data_requests", []):
        if not isinstance(entry, dict):
            continue
        request = entry.get("request")
        if not isinstance(request, dict) or request.get("product_id") != "QL_PRECIP_6H":
            continue
        source_ids = request.get("source_check_ids")
        if not isinstance(source_ids, list) or anchor_id not in {str(value) for value in source_ids}:
            continue
        stored = ((entry.get("step05") or {}).get("result")) if isinstance(entry.get("step05"), dict) else None
        if not isinstance(stored, dict) or stored.get("status") != "success":
            continue
        for image in stored.get("generated_images", []):
            if not isinstance(image, dict) or image.get("physical_diagnostic") != "QL_PRECIP_6H":
                continue
            hour = image.get("forecast_hour")
            if isinstance(hour, bool) or not isinstance(hour, int) or hour not in peak_hours:
                continue
            objects = image.get("precipitation_objects")
            if not isinstance(objects, list):
                continue
            for obj in objects:
                if not isinstance(obj, dict):
                    continue
                overlap_count = int(obj.get("operational_overlap_grid_count") or 0)
                op_max = obj.get("operational_maximum_6h_mm")
                if overlap_count <= 0 or not isinstance(op_max, (int, float)) or isinstance(op_max, bool) or float(op_max) < 1.0:
                    continue
                rank = (
                    float(obj.get("operational_precipitation_sum_mm") or 0.0),
                    overlap_count,
                    float(op_max),
                    -int(hour),
                )
                object_rows.append((rank, obj, int(hour)))
    if not object_rows:
        return None
    object_rows.sort(key=lambda item: item[0], reverse=True)
    _rank, primary, hour = object_rows[0]
    overlaps = [item for item in primary.get("region_overlaps", []) if isinstance(item, dict)]
    overlaps.sort(
        key=lambda item: (
            -float(item.get("overlap_precipitation_sum_mm") or 0.0),
            -int(item.get("overlap_grid_count") or 0),
            str(item.get("region_id") or ""),
        )
    )
    regions: list[dict[str, Any]] = []
    if overlaps:
        primary_sum = float(overlaps[0].get("overlap_precipitation_sum_mm") or 0.0)
        for overlap in overlaps:
            if regions and (
                len(regions) >= 2
                or float(overlap.get("overlap_precipitation_sum_mm") or 0.0) < primary_sum * 0.50
                or int(overlap.get("overlap_grid_count") or 0) < 2
            ):
                continue
            identity = _precipitation_object_region_identity(overlap, primary)
            if identity is not None:
                regions.append(identity)
    if not regions:
        return None
    confidence = "high" if (
        int(regions[0].get("overlap_grid_count") or 0) >= 3
        and float(regions[0].get("precip_coverage_fraction") or 0.0) >= 0.10
        and float(regions[0].get("object_overlap_fraction") or 0.0) >= 0.25
        and str(regions[0].get("object_relation") or "") != "adjacent"
    ) else "medium"
    return {
        "status": "precipitation_object",
        "forecast_hour": hour,
        "confidence": confidence,
        "evidence_check_ids": [anchor_id],
        "regions": regions,
        "source": "system_precipitation_object",
        "precipitation_object_id": str(primary.get("object_id") or ""),
        "object_grid_count": int(primary.get("grid_count") or 0),
        "object_centroid": copy.deepcopy(primary.get("centroid")),
        "object_bbox": copy.deepcopy(primary.get("bbox")),
        "operational_overlap_fraction": float(primary.get("operational_overlap_fraction") or 0.0),
    }
