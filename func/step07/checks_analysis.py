"""Step step07 analysis responsibility; original function bodies preserved."""
from __future__ import annotations
from . import checks_context as _step_context

# Original func/step07/checks.py:195
def _bbox_signature(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        return tuple(float(value[key]) for key in ("west", "east", "south", "north"))
    except (KeyError, TypeError, ValueError):
        return None

# Original func/step07/checks.py:204
def _normalized_bbox(value: Any) -> dict[str, float] | None:
    signature = _bbox_signature(value)
    if signature is None:
        return None
    return dict(zip(("west", "east", "south", "north"), signature))

# Original func/step07/checks.py:211
def _spatial_scope_context(
    group: dict[str, Any],
    check: dict[str, Any],
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build neutral, Check-local spatial metadata for independent VLM reading."""
    candidate_context = group.get("candidate_context")
    candidate = candidate_context.get("candidate") if isinstance(candidate_context, dict) else None
    plan = candidate_context.get("analysis_plan") if isinstance(candidate_context, dict) else None
    candidate_area = _normalized_bbox(candidate.get("candidate_area")) if isinstance(candidate, dict) else None
    if candidate_area is None or not isinstance(plan, dict):
        raise Pass2PipelineError(
            "SPATIAL_SCOPE_CONTEXT_INVALID",
            f"{group.get('candidate_id')} check={check.get('check_id')} lacks valid candidate spatial context.",
        )
    targets = plan.get("target_areas")
    if not isinstance(targets, list):
        raise Pass2PipelineError(
            "SPATIAL_SCOPE_CONTEXT_INVALID",
            f"{group.get('candidate_id')} has no target_areas for spatial context.",
        )
    target_by_id = {
        str(target.get("target_id")): target
        for target in targets
        if isinstance(target, dict) and target.get("target_id")
    }
    evidence_targets: list[dict[str, Any]] = []
    for entry in sorted(entries, key=lambda item: str(item.get("request", {}).get("request_id") or "")):
        request = entry.get("request") if isinstance(entry, dict) else None
        step05 = entry.get("step05") if isinstance(entry, dict) else None
        if not isinstance(request, dict):
            continue
        request_id = str(request.get("request_id") or "")
        target_id = str(request.get("target_id") or "")
        target = target_by_id.get(target_id)
        if target is None:
            raise Pass2PipelineError(
                "SPATIAL_SCOPE_CONTEXT_INVALID",
                f"{group.get('candidate_id')} check={check.get('check_id')} request={request_id} has unknown target_id={target_id!r}.",
            )
        target_type = str(target.get("target_type") or "")
        resolution_method = str(target.get("resolution_method") or "")
        if target_type == "candidate_domain":
            relation = "exact_candidate_area"
        elif target_type == "context_domain":
            relation = "contains_candidate_area"
        elif target_type == "operational_regions" and resolution_method == "candidate_area_overlap":
            relation = "candidate_area_overlap"
        elif target_type == "operational_regions" and resolution_method == "explicit_region_ids":
            relation = "explicit_operational_regions"
        else:
            raise Pass2PipelineError(
                "SPATIAL_SCOPE_CONTEXT_INVALID",
                f"{group.get('candidate_id')} check={check.get('check_id')} target={target_id} has unsupported spatial contract.",
            )
        result = step05.get("result") if isinstance(step05, dict) else None
        selected_domain = (
            result.get("selected_domain")
            if isinstance(result, dict) and result.get("selected_domain") is not None
            else request.get("requested_domain")
        )
        evidence_targets.append({
            "request_id": request_id,
            "target_id": target_id,
            "target_type": target_type,
            "resolution_method": resolution_method,
            "selected_domain": _normalized_bbox(selected_domain),
            "relation_to_candidate_area": relation,
        })
    return {
        "coordinate_system": "EPSG:4326",
        "candidate_area": candidate_area,
        "candidate_area_is_spatial_metadata_only": True,
        "evidence_targets": evidence_targets,
    }

# Original func/step07/checks.py:311
def _candidate_operational_region_naming_context(
    root: Path,
    group: dict[str, Any],
) -> dict[str, Any]:
    """Expose standardized region names as vocabulary, never as evidence.

    The candidate-overlap catalog tells the VLM which operational names may be
    used when an observed feature can actually be located there from image or
    quantitative evidence.  Mere bbox overlap must never create a weather fact.
    """
    candidate_context = group.get("candidate_context")
    candidate = candidate_context.get("candidate") if isinstance(candidate_context, dict) else None
    candidate_area = _normalized_bbox(candidate.get("candidate_area")) if isinstance(candidate, dict) else None
    if candidate_area is None:
        raise Pass2PipelineError(
            "REGION_NAMING_CONTEXT_INVALID",
            f"{group.get('candidate_id')} has no valid candidate area for region naming.",
        )
    catalog_path = runtime_path(root, *SPATIAL_LOCALIZATION_REGION_CATALOG.parts)
    try:
        regions = load_operational_regions(catalog_path)
    except (OSError, TypeError, ValueError) as exc:
        raise Pass2PipelineError(
            "REGION_NAMING_CONTEXT_INVALID",
            f"Unable to load operational-region catalog for region naming: {catalog_path}",
        ) from exc
    selected = selectable_pass1_regions(regions=regions, candidate_bbox=candidate_area)
    rows: list[dict[str, Any]] = []
    for region in selected:
        bbox = region.get("bbox") if isinstance(region, dict) else None
        if not isinstance(bbox, dict):
            continue
        region_bbox = {
            "west": float(bbox["lon_min"]),
            "east": float(bbox["lon_max"]),
            "south": float(bbox["lat_min"]),
            "north": float(bbox["lat_max"]),
        }
        overlap = bbox_intersection(candidate_area, region_bbox)
        if overlap is None:
            continue
        rows.append({
            "region_id": str(region.get("region_id") or ""),
            "name_ko": str(region.get("name_ko") or ""),
            "region_role": str(region.get("region_role") or ""),
            "candidate_overlap_bbox": overlap,
        })
    return {
        "scope": "candidate_overlap_operational_forecast_regions",
        "usage": "naming_vocabulary_only",
        "regions": rows,
        "instruction": (
            "이 목록은 사용자 문장에서 사용할 표준 운영권역명과 위치 lookup 정보일 뿐 기상현상 위치의 근거가 아니다. "
            "이미지·정량값·구조화 위치판독으로 실제 위치가 확인된 경우에만 해당 name_ko를 사용한다. "
            "구조화 근거가 있는데도 '후보영역', '운영 예보권역', '일부 권역', '해당 영역'만으로 위치 설명을 끝내지 않는다. "
            "운영권역으로 안정적으로 대응할 수 없으면 지명을 추정하지 말고 운영권역 단위 위치가 미확정임을 명시한다."
        ),
    }

# Original func/step07/checks.py:371
def _candidate_operational_localization_context(
    root: Path,
    group: dict[str, Any],
    specification: dict[str, Any],
) -> dict[str, Any]:
    """Build the closed operational-region namespace available to the VLM.

    Region bboxes are geographic lookup metadata only.  The VLM still has to
    read the supplied Step05 weather fields to decide whether the overlap is
    present.  Source-corridor regions are intentionally excluded from the
    localization namespace because the public card describes operational
    forecast regions, not upstream context.
    """
    if not _spatial_localization_enabled(specification):
        return {
            "enabled": False,
            "selection_scope": "not_applicable",
            "allowed_regions": [],
            "relative_sector_values": sorted(SPATIAL_LOCALIZATION_SECTORS),
        }
    candidate_context = group.get("candidate_context")
    candidate = candidate_context.get("candidate") if isinstance(candidate_context, dict) else None
    candidate_area = _normalized_bbox(candidate.get("candidate_area")) if isinstance(candidate, dict) else None
    if candidate_area is None:
        raise Pass2PipelineError(
            "SPATIAL_LOCALIZATION_CONTEXT_INVALID",
            f"{group.get('candidate_id')} has no valid candidate area for spatial localization.",
        )
    catalog_path = runtime_path(root, *SPATIAL_LOCALIZATION_REGION_CATALOG.parts)
    try:
        regions = load_operational_regions(catalog_path)
    except (OSError, TypeError, ValueError) as exc:
        raise Pass2PipelineError(
            "SPATIAL_LOCALIZATION_CONTEXT_INVALID",
            f"Unable to load operational-region catalog for spatial localization: {catalog_path}",
        ) from exc
    selected = selectable_pass1_regions(regions=regions, candidate_bbox=candidate_area)
    allowed_regions: list[dict[str, Any]] = []
    for region in selected:
        bbox = region.get("bbox") if isinstance(region, dict) else None
        if not isinstance(bbox, dict):
            continue
        region_bbox = {
            "west": float(bbox["lon_min"]),
            "east": float(bbox["lon_max"]),
            "south": float(bbox["lat_min"]),
            "north": float(bbox["lat_max"]),
        }
        overlap = bbox_intersection(candidate_area, region_bbox)
        if overlap is None:
            continue
        allowed_regions.append({
            "region_id": str(region["region_id"]),
            "name_ko": str(region["name_ko"]),
            "region_role": str(region["region_role"]),
            "candidate_overlap_bbox": overlap,
        })
    return {
        "enabled": True,
        "selection_scope": "candidate_overlap_operational_forecast_regions",
        "required_signal_components": [
            component
            for component in SPATIAL_LOCALIZATION_DIAGNOSTIC_BY_COMPONENT
            if component in {
                str(value)
                for value in specification.get("required_signal_components", [])
                if isinstance(value, str) and value
            }
        ],
        "required_diagnostics": sorted(_required_spatial_localization_diagnostics(specification)),
        "allowed_regions": allowed_regions,
        "relative_sector_values": sorted(SPATIAL_LOCALIZATION_SECTORS),
        "instruction": (
            "기상장 중첩이 실제로 확인된 운영 예보권역만 선택합니다. 권역 bbox는 위치 식별용이며 "
            "해당 권역 전체에서 중첩이 성립한다는 의미가 아닙니다."
        ),
    }

# Original func/step07/checks.py:450
def _empty_spatial_localization(*, enabled: bool) -> dict[str, Any]:
    return {
        "status": "indeterminate" if enabled else "not_applicable",
        "forecast_hour": None,
        "confidence": None,
        "regions": [],
    }

# Original func/step07/checks.py:499
def _precipitation_motion_context(
    root: Path,
    group: dict[str, Any],
    specification: dict[str, Any],
) -> dict[str, Any]:
    """Return the closed naming namespace for a pure precipitation-move Check."""
    enabled = _pure_direct_precipitation_move_check(specification)
    if not enabled:
        return {
            "enabled": False,
            "allowed_regions": [],
            "instruction": (
                "이 Check는 사용자 표시용 '이동 강수' 명명의 직접 근거로 사용하지 않습니다. "
                "precipitation_motion.classification=not_applicable로 반환합니다."
            ),
        }
    naming = _candidate_operational_region_naming_context(root, group)
    return {
        "enabled": True,
        "allowed_regions": copy.deepcopy(naming.get("regions", [])),
        "instruction": (
            "동일하게 식별 가능한 주강수영역 또는 강수 중심이 중간 시각을 포함해 끊김 없이 추적될 때만 "
            "coherent_movement로 판정합니다. 시작·종료 위치의 단순 차이, 최대강수 위치 변화, 영역 확대, "
            "약화·분산 뒤 다른 위치의 재형성, 분리·병합·재조직은 coherent_movement가 아닙니다. "
            "origin_region_id와 destination_region_id는 실제 공간영상에서 안정적으로 대응 가능한 경우에만 "
            "allowed_regions의 region_id를 그대로 사용합니다."
        ),
    }

# Original func/step07/checks.py:627
def _precipitation_motion_vote_counts(member_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for result in member_results:
        motion = result.get("precipitation_motion") if isinstance(result.get("precipitation_motion"), dict) else {}
        classification = str(motion.get("classification") or "indeterminate")
        counts[classification] = counts.get(classification, 0) + 1
    return [
        {"classification": classification, "count": counts[classification]}
        for classification in sorted(counts)
    ]

# Original func/step07/checks.py:639
def _consensus_precipitation_motion(
    member_results: list[dict[str, Any]],
) -> tuple[dict[str, Any], str, int | None, list[dict[str, Any]]]:
    """Aggregate motion class independently from operational-region naming.

    The class is voted across all three members.  A start/end region pair is
    retained only when all three members unanimously classify coherent movement
    and independently return the same non-null operational-region pair.
    """
    votes = _precipitation_motion_vote_counts(member_results)
    if not votes:
        return _empty_precipitation_motion(enabled=False), "not_applicable", None, []
    if len(votes) == 1 and votes[0]["classification"] == "not_applicable":
        return _empty_precipitation_motion(enabled=False), "not_applicable", None, votes

    max_count = max(int(row["count"]) for row in votes)
    winners = [row for row in votes if int(row["count"]) == max_count]
    if max_count < 2 or len(winners) != 1:
        return _empty_precipitation_motion(enabled=True), "unresolved", None, votes

    classification = str(winners[0]["classification"])
    selected = next(
        index
        for index, result in enumerate(member_results, start=1)
        if isinstance(result.get("precipitation_motion"), dict)
        and result["precipitation_motion"].get("classification") == classification
    )
    agreement = "unanimous" if max_count == ENSEMBLE_MEMBER_COUNT else "majority"
    winner_motions = [
        result["precipitation_motion"]
        for result in member_results
        if isinstance(result.get("precipitation_motion"), dict)
        and result["precipitation_motion"].get("classification") == classification
    ]

    if classification == "indeterminate":
        return _empty_precipitation_motion(enabled=True), agreement, selected, votes
    if classification == "not_applicable":
        return _empty_precipitation_motion(enabled=False), agreement, selected, votes

    confidence = (
        "high"
        if winner_motions and all(item.get("confidence") == "high" for item in winner_motions)
        else "medium"
    )
    consensus = {
        "classification": classification,
        "origin_region_id": None,
        "destination_region_id": None,
        "confidence": confidence,
    }
    if classification == "coherent_movement" and agreement == "unanimous":
        pairs = [
            (item.get("origin_region_id"), item.get("destination_region_id"))
            for item in winner_motions
        ]
        if (
            len(pairs) == ENSEMBLE_MEMBER_COUNT
            and all(
                isinstance(origin, str)
                and origin
                and isinstance(destination, str)
                and destination
                and origin != destination
                for origin, destination in pairs
            )
            and len(set(pairs)) == 1
        ):
            consensus["origin_region_id"], consensus["destination_region_id"] = pairs[0]
    return consensus, agreement, selected, votes

# Original func/step07/checks.py:715
def _assessment_vote_counts(member_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for result in member_results:
        assessment = str(result.get("assessment") or "")
        counts[assessment] = counts.get(assessment, 0) + 1
    return [
        {"assessment": assessment, "count": counts[assessment]}
        for assessment in sorted(counts)
    ]

# Original func/step07/checks.py:726
def _localization_key(localization: Any) -> tuple[Any, ...] | None:
    if not isinstance(localization, dict) or localization.get("status") != "localized":
        return None
    hour = localization.get("forecast_hour")
    if not isinstance(hour, int) or isinstance(hour, bool):
        return None
    regions = localization.get("regions")
    if not isinstance(regions, list) or not regions:
        return None
    rows: list[tuple[str, str]] = []
    for region in regions:
        if not isinstance(region, dict):
            return None
        region_id = region.get("region_id")
        sector = region.get("relative_sector")
        if not isinstance(region_id, str) or not region_id or not isinstance(sector, str) or not sector:
            return None
        rows.append((region_id, sector))
    return (hour, tuple(sorted(rows)))

# Original func/step07/checks.py:747
def _consensus_spatial_localization(
    member_results: list[dict[str, Any]],
    *,
    consensus_assessment: str,
) -> tuple[dict[str, Any], str]:
    """Build a conservative location consensus from the observed members.

    Assessment and localization are separate ensemble axes.  For an observed
    Check, forecast hour and operational region are voted independently so that
    {A, B}, {A}, {C} preserves the real 2/3 agreement on A instead of treating
    the three complete region sets as wholly different locations.
    """
    eligible = [result for result in member_results if result.get("assessment") == consensus_assessment]
    if consensus_assessment != "observed":
        enabled = any(
            isinstance(result.get("spatial_localization"), dict)
            and result["spatial_localization"].get("status") != "not_applicable"
            for result in member_results
        )
        return _empty_spatial_localization(enabled=enabled), "not_applicable"

    localized_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    statuses: list[str] = []
    for result in eligible:
        localization = result.get("spatial_localization")
        if not isinstance(localization, dict):
            continue
        status = str(localization.get("status") or "")
        statuses.append(status)
        if _localization_key(localization) is not None:
            localized_rows.append((result, localization))

    if not localized_rows:
        enabled = any(status != "not_applicable" for status in statuses)
        if statuses and len(statuses) == len(eligible) and len(set(statuses)) == 1:
            status = statuses[0]
            if status == "indeterminate":
                return _empty_spatial_localization(enabled=True), "unanimous"
            if status == "not_applicable":
                return _empty_spatial_localization(enabled=False), "not_applicable"
        return _empty_spatial_localization(enabled=enabled), "unresolved" if enabled else "not_applicable"

    hour_votes: dict[int, int] = {}
    for _, localization in localized_rows:
        hour = int(localization["forecast_hour"])
        hour_votes[hour] = hour_votes.get(hour, 0) + 1
    winning_hour, winning_hour_count = max(hour_votes.items(), key=lambda item: (item[1], -item[0]))
    if winning_hour_count < 2:
        return _empty_spatial_localization(enabled=True), "unresolved"

    hour_rows = [
        (result, localization)
        for result, localization in localized_rows
        if localization.get("forecast_hour") == winning_hour
    ]
    region_votes: dict[str, list[dict[str, Any]]] = {}
    for _, localization in hour_rows:
        for region in localization.get("regions", []):
            if not isinstance(region, dict):
                continue
            region_id = str(region.get("region_id") or "")
            if region_id:
                region_votes.setdefault(region_id, []).append(region)

    majority_region_ids = sorted(
        region_id for region_id, rows in region_votes.items() if len(rows) >= 2
    )
    if not majority_region_ids:
        return _empty_spatial_localization(enabled=True), "unresolved"

    consensus_regions: list[dict[str, str]] = []
    for region_id in majority_region_ids:
        rows = region_votes[region_id]
        sector_counts: dict[str, int] = {}
        for row in rows:
            sector = str(row.get("relative_sector") or "")
            if sector:
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
        sector = "unspecified"
        if sector_counts:
            best_sector, best_sector_count = max(
                sector_counts.items(), key=lambda item: (item[1], item[0])
            )
            if best_sector_count >= 2:
                sector = best_sector
        consensus_regions.append({
            "region_id": region_id,
            "relative_sector": sector,
        })

    consensus_region_key = tuple(
        sorted((row["region_id"], row["relative_sector"]) for row in consensus_regions)
    )
    member_region_keys = []
    confidences = []
    for _, localization in hour_rows:
        member_region_keys.append(
            tuple(sorted(
                (str(row.get("region_id") or ""), str(row.get("relative_sector") or ""))
                for row in localization.get("regions", [])
                if isinstance(row, dict)
            ))
        )
        confidences.append(str(localization.get("confidence") or ""))

    unanimous = (
        len(hour_rows) == len(eligible)
        and all(key == consensus_region_key for key in member_region_keys)
    )
    confidence = "high" if unanimous and confidences and all(value == "high" for value in confidences) else "medium"
    return {
        "status": "localized",
        "forecast_hour": winning_hour,
        "confidence": confidence,
        "regions": consensus_regions,
    }, "unanimous" if unanimous else "majority"

# Original func/step07/checks.py:865
def _consensus_localization_member_index(
    member_results: list[dict[str, Any]],
    *,
    consensus_assessment: str,
    localization: dict[str, Any],
) -> int | None:
    """Return a member whose validated localization exactly matches consensus.

    The ensemble may vote assessment and localization on separate axes.  A
    final localized result must nevertheless inherit its prose, uncertainties,
    and evidence bookkeeping from a member that actually authored the same
    localization.  Otherwise a non-localized assessment member can be copied
    and then have only its structured location overwritten, leaving stale
    location uncertainty or contradictory prose in the final result.

    When per-region voting synthesizes a localization that no single member
    authored exactly, return ``None`` so the caller can fail closed to an
    indeterminate localization rather than publish a synthetic location with no
    matching validated narrative.
    """
    consensus_key = _localization_key(localization)
    if consensus_key is None:
        return None
    for index, result in enumerate(member_results, start=1):
        if result.get("assessment") != consensus_assessment:
            continue
        if _localization_key(result.get("spatial_localization")) == consensus_key:
            return index
    return None

# Original func/step07/checks.py:896
def guard_unresolved_localization_narrative(result: dict[str, Any]) -> None:
    """Remove selected-member location claims when ensemble location has no consensus.

    The assessment vote and the localization vote are independent.  When the
    former succeeds but the latter does not, copying the selected member's
    region-specific finding would turn a minority location into a public
    consensus claim.  Preserve the evidence references, but replace that prose
    with an explicit location-unresolved statement.  Per-member locations remain
    available in the minority report.
    """
    assessment = str(result.get("assessment") or "")
    if assessment not in {"observed", "partially_observed"}:
        return

    evidence_refs: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    for finding in result.get("findings", []):
        if not isinstance(finding, dict):
            continue
        for ref in finding.get("evidence_refs", []):
            if not isinstance(ref, dict):
                continue
            key = json.dumps(ref, ensure_ascii=False, sort_keys=True)
            if key in seen_refs:
                continue
            seen_refs.add(key)
            evidence_refs.append(copy.deepcopy(ref))

    state = "확인되었으나" if assessment == "observed" else "부분적으로 확인되었으나"
    result["findings"] = [{
        "statement": (
            f"요청한 다변수 공간 결합 자체는 {state} "
            "운영권역 단위 결합 위치는 독립 판독 간 합의가 성립하지 않았습니다."
        ),
        "evidence_refs": evidence_refs,
    }]
    # A selected member's location-specific contradiction is equally unsafe to
    # promote as consensus prose.  The full member narratives remain in the
    # minority report.
    result["contradictory_findings"] = []
    uncertainties = [
        str(value).strip()
        for value in result.get("uncertainties", [])
        if isinstance(value, str) and str(value).strip()
    ]
    location_uncertainty = "독립 판독 3회에서 운영권역 단위 결합 위치 합의가 성립하지 않았습니다."
    if location_uncertainty not in uncertainties:
        uncertainties.append(location_uncertainty)
    result["uncertainties"] = uncertainties

# Original func/step07/checks.py:947
def normalize_step07_result_for_synthesis(result: dict[str, Any]) -> dict[str, Any]:
    """Return the publication/synthesis view of one Step07 result.

    Historical Step07 products may contain a selected-member region narrative
    even when ensemble localization_agreement is unresolved.  Step08 and Step09
    must consume the exact same normalized view so provenance fingerprints and
    deterministic validation remain symmetric.  The stored Step07 file and the
    minority report are not modified.
    """
    normalized = copy.deepcopy(result)
    consensus = normalized.get("ensemble_consensus")
    if isinstance(consensus, dict) and consensus.get("localization_agreement") == "unresolved":
        guard_unresolved_localization_narrative(normalized)
    return normalized

# Original func/step07/checks.py:963
def _minority_member_report(member_index: int, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "member_index": member_index,
        "assessment": result.get("assessment"),
        "scope_coverage": result.get("scope_coverage"),
        "spatial_localization": copy.deepcopy(result.get("spatial_localization") or {}),
        "findings": copy.deepcopy(result.get("findings") or []),
        "contradictory_findings": copy.deepcopy(result.get("contradictory_findings") or []),
        "uncertainties": copy.deepcopy(result.get("uncertainties") or []),
    }

# Original func/step07/checks.py:975
def build_step07_ensemble_consensus(
    member_results: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return one conservative 3-member consensus and an optional minority record.

    The model-authored structured assessment, spatial localization, and precipitation-motion
    classification are voted. Quantitative relations, evidence availability, and source integrity remain governed
    by the existing deterministic validators for every member before this function runs.
    """
    if len(member_results) != ENSEMBLE_MEMBER_COUNT:
        raise Pass2PipelineError(
            "STEP07_ENSEMBLE_INVALID",
            f"Step07 ensemble requires exactly {ENSEMBLE_MEMBER_COUNT} validated members.",
        )
    identities = {
        (str(row.get("candidate_id") or ""), str(row.get("check_id") or ""), str(row.get("input_fingerprint") or ""))
        for row in member_results
    }
    if len(identities) != 1:
        raise Pass2PipelineError("STEP07_ENSEMBLE_INVALID", "Step07 ensemble members do not share one Check identity/fingerprint.")

    vote_counts = _assessment_vote_counts(member_results)
    max_count = max(int(row["count"]) for row in vote_counts)
    winners = [str(row["assessment"]) for row in vote_counts if int(row["count"]) == max_count]
    if max_count >= 2 and len(winners) == 1:
        consensus_assessment = winners[0]
        assessment_agreement = "unanimous" if max_count == ENSEMBLE_MEMBER_COUNT else "majority"
        assessment_selected_member_index = next(
            index for index, row in enumerate(member_results, start=1)
            if row.get("assessment") == consensus_assessment
        )
        localization, localization_agreement = _consensus_spatial_localization(
            member_results, consensus_assessment=consensus_assessment
        )
        localization_selected_member_index = None
        if isinstance(localization, dict) and localization.get("status") == "localized":
            localization_selected_member_index = _consensus_localization_member_index(
                member_results,
                consensus_assessment=consensus_assessment,
                localization=localization,
            )
            if localization_selected_member_index is None:
                # Per-region voting can create a synthetic subset that no single
                # member authored exactly (for example {A,B}, {A,C}, {A,D} -> A).
                # Such a location has no matching fully validated narrative, so
                # preserve the assessment consensus but fail closed on location.
                localization = _empty_spatial_localization(enabled=True)
                localization_agreement = "unresolved"

        selected_member_index = (
            localization_selected_member_index
            if localization_selected_member_index is not None
            else assessment_selected_member_index
        )
        result = copy.deepcopy(member_results[selected_member_index - 1])
        result["assessment"] = consensus_assessment
        result["spatial_localization"] = localization
        if localization_agreement == "unresolved":
            guard_unresolved_localization_narrative(result)
        disagreement_dimensions: list[str] = []
        if assessment_agreement != "unanimous":
            disagreement_dimensions.append("assessment")
        localized_keys = [_localization_key(row.get("spatial_localization")) for row in member_results]
        applicable_localized_keys = [key for key in localized_keys if key is not None]
        if localization_agreement in {"majority", "unresolved"} or len(set(applicable_localized_keys)) > 1:
            disagreement_dimensions.append("localization")
        disagreement_dimensions = list(dict.fromkeys(disagreement_dimensions))
        disagreement_severity = "none" if not disagreement_dimensions else "minor"
        minority_indices = [
            index for index, row in enumerate(member_results, start=1)
            if row.get("assessment") != consensus_assessment
        ]
        if "localization" in disagreement_dimensions:
            consensus_key = _localization_key(localization)
            localization_minority_indices = [
                index for index, row in enumerate(member_results, start=1)
                if row.get("assessment") == consensus_assessment
                and (consensus_key is None or _localization_key(row.get("spatial_localization")) != consensus_key)
            ]
            minority_indices = sorted(set(minority_indices) | set(localization_minority_indices))
    else:
        consensus_assessment = "uncertain"
        assessment_agreement = "unresolved"
        selected_member_index = None
        result = copy.deepcopy(member_results[0])
        result["assessment"] = "uncertain"
        result["scope_coverage"] = "indeterminate"
        localization_enabled = any(
            isinstance(row.get("spatial_localization"), dict)
            and row["spatial_localization"].get("status") != "not_applicable"
            for row in member_results
        )
        result["spatial_localization"] = _empty_spatial_localization(enabled=localization_enabled)
        result["findings"] = []
        result["contradictory_findings"] = []
        result["quantitative_claims"] = []
        result["quantitative_relations"] = []
        result["quantitative_consistency_status"] = "not_applicable"
        result["quantitative_consistency_failure_reasons"] = []
        result["quantitative_consistency_failure_details"] = []
        result["usable_for_synthesis"] = False
        result["excluded_from_synthesis_reasons"] = ["ENSEMBLE_NO_CONSENSUS"]
        result["uncertainties"] = ["독립 판독 3회에서 구조화 판정 합의가 성립하지 않았습니다."]
        localization_agreement = "unresolved" if localization_enabled else "not_applicable"
        disagreement_dimensions = ["assessment"]
        if localization_enabled:
            disagreement_dimensions.append("localization")
        disagreement_severity = "material"
        minority_indices = [1, 2, 3]

    consensus_motion, motion_agreement, motion_selected_member_index, motion_vote_counts = (
        _consensus_precipitation_motion(member_results)
    )
    result["precipitation_motion"] = consensus_motion
    result["ensemble_consensus"] = {
        "contract_version": ENSEMBLE_CONTRACT_VERSION,
        "mode": "llm_ensemble",
        "member_count": ENSEMBLE_MEMBER_COUNT,
        "assessment_agreement": assessment_agreement,
        "consensus_assessment": consensus_assessment,
        "selected_member_index": selected_member_index,
        "vote_counts": vote_counts,
        "localization_agreement": localization_agreement,
        "precipitation_motion_agreement": motion_agreement,
        "consensus_precipitation_motion_classification": consensus_motion.get("classification"),
        "precipitation_motion_selected_member_index": motion_selected_member_index,
        "precipitation_motion_vote_counts": motion_vote_counts,
        "disagreement_severity": disagreement_severity,
        "disagreement_dimensions": disagreement_dimensions,
    }

    report_entry = None
    if disagreement_dimensions:
        candidate_id = str(result.get("candidate_id") or "")
        check_id = str(result.get("check_id") or "")
        report_entry = {
            "candidate_id": candidate_id,
            "check_id": check_id,
            "consensus_assessment": consensus_assessment,
            "assessment_agreement": assessment_agreement,
            "selected_member_index": selected_member_index,
            "vote_counts": copy.deepcopy(vote_counts),
            "localization_agreement": localization_agreement,
            "disagreement_severity": disagreement_severity,
            "disagreement_dimensions": copy.deepcopy(disagreement_dimensions),
            "minority_member_indices": minority_indices,
            "member_reports": [
                _minority_member_report(index, member_results[index - 1])
                for index in minority_indices
            ],
        }
    return result, report_entry

# Original func/step07/checks.py:1536
def _system_direct_precipitation_anchor_context(
    group: dict[str, Any],
    check: dict[str, Any],
    specification: dict[str, Any],
    entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Expose only deterministic scope metadata for the SYSTEM presence Check."""
    if not _is_system_direct_precipitation_anchor_check(specification):
        return None
    candidate_context = group.get("candidate_context")
    candidate = candidate_context.get("candidate") if isinstance(candidate_context, dict) else None
    if not isinstance(candidate, dict):
        raise Pass2PipelineError(
            "STEP06_PACKAGE_INVALID",
            f"{group.get('candidate_id')} SYSTEM anchor has no candidate context.",
        )
    peak_hours = sorted(
        int(value)
        for value in candidate.get("peak_forecast_hours", [])
        if isinstance(value, int) and not isinstance(value, bool)
    )
    candidate_area = candidate.get("candidate_area")
    selected_domains: list[Any] = []
    threshold_by_hour: dict[int, dict[str, Any]] = {}
    for entry in entries:
        request = entry.get("request") if isinstance(entry, dict) else None
        step05 = entry.get("step05") if isinstance(entry, dict) else None
        if not isinstance(request, dict):
            continue
        result = step05.get("result") if isinstance(step05, dict) else None
        selected_domains.append(
            result.get("selected_domain") if isinstance(result, dict) else request.get("requested_domain")
        )
        generated_images = result.get("generated_images") if isinstance(result, dict) else None
        if isinstance(generated_images, list):
            for image in generated_images:
                if not isinstance(image, dict) or image.get("physical_diagnostic") != "QL_PRECIP_6H":
                    continue
                hour = image.get("forecast_hour")
                if not isinstance(hour, int) or isinstance(hour, bool) or hour not in peak_hours:
                    continue
                scope_summary = image.get("operational_scope_field_value_summary")
                region_summaries = image.get("operational_scope_region_summaries")
                scope_summary_available = isinstance(scope_summary, dict)
                region_summaries_available = isinstance(region_summaries, list)
                scope_summary = scope_summary if scope_summary_available else {}
                region_summaries = region_summaries if region_summaries_available else []
                scope_mask = image.get("operational_scope_mask")
                selected_region_ids = (
                    scope_mask.get("selected_region_ids")
                    if isinstance(scope_mask, dict)
                    else None
                )
                operational_grid_count = (
                    scope_mask.get("operational_grid_count")
                    if isinstance(scope_mask, dict)
                    else None
                )
                scope_mask_metadata_known = bool(
                    isinstance(operational_grid_count, int)
                    and not isinstance(operational_grid_count, bool)
                    and operational_grid_count >= 0
                    and isinstance(selected_region_ids, list)
                )
                has_operational_scope = bool(
                    scope_mask_metadata_known
                    and operational_grid_count > 0
                ) or bool(selected_region_ids) or bool(region_summaries)
                finite_count = scope_summary.get("finite_count")
                maximum = scope_summary.get("maximum")
                maximum_value = (
                    float(maximum)
                    if isinstance(maximum, Real)
                    and not isinstance(maximum, bool)
                    and math.isfinite(float(maximum))
                    else None
                )
                has_valid_precipitation_values = (
                    isinstance(finite_count, int)
                    and not isinstance(finite_count, bool)
                    and finite_count > 0
                    and maximum_value is not None
                )
                if has_operational_scope and not region_summaries_available:
                    evaluation_status = "unavailable_scope_metadata"
                elif has_operational_scope and not scope_summary_available:
                    evaluation_status = "unavailable_precipitation_values"
                elif has_operational_scope and has_valid_precipitation_values:
                    evaluation_status = "evaluated"
                elif (
                    not has_operational_scope
                    and scope_mask_metadata_known
                    and operational_grid_count == 0
                    and not selected_region_ids
                    and scope_summary_available
                    and region_summaries_available
                ):
                    evaluation_status = "unavailable_no_operational_overlap"
                elif has_operational_scope:
                    evaluation_status = "unavailable_precipitation_values"
                else:
                    evaluation_status = "unavailable_scope_metadata"
                normalized_regions: list[dict[str, Any]] = []
                for region in region_summaries:
                    if not isinstance(region, dict):
                        continue
                    region_id = str(region.get("region_id") or "")
                    region_name = str(region.get("region_name") or "")
                    region_max = region.get("maximum_6h_mm")
                    if not region_id or not region_name or not isinstance(region_max, (int, float)) or isinstance(region_max, bool):
                        continue
                    region_max_value = float(region_max)
                    if region_max_value >= DIRECT_PRECIPITATION_MINIMUM_MM_6H:
                        normalized_regions.append({
                            "region_id": region_id,
                            "region_name": region_name,
                            "maximum_6h_mm": region_max_value,
                        })
                threshold_by_hour[hour] = {
                    "maximum_6h_mm": maximum_value,
                    "evaluation_status": evaluation_status,
                    "evaluated": evaluation_status == "evaluated",
                    "meets_minimum": bool(
                        evaluation_status == "evaluated"
                        and maximum_value is not None
                        and maximum_value >= DIRECT_PRECIPITATION_MINIMUM_MM_6H
                    ),
                    "threshold_met_regions": normalized_regions,
                }
    selected_domain_is_candidate_area = bool(selected_domains) and all(
        _bbox_signature(value) == _bbox_signature(candidate_area)
        for value in selected_domains
    )
    threshold_hours = sorted(hour for hour, row in threshold_by_hour.items() if row["meets_minimum"])
    evaluation_complete = bool(peak_hours) and all(
        hour in threshold_by_hour and threshold_by_hour[hour]["evaluated"]
        for hour in peak_hours
    )
    unavailable_hours = [
        hour for hour in peak_hours
        if hour not in threshold_by_hour or not threshold_by_hour[hour]["evaluated"]
    ]
    if threshold_hours:
        evaluation_status = "threshold_met"
    elif evaluation_complete:
        evaluation_status = "not_met"
    elif not any(row["evaluated"] for row in threshold_by_hour.values()):
        evaluation_status = "unavailable"
    else:
        evaluation_status = "incomplete"
    return {
        "direct_precipitation_anchor": True,
        "candidate_area": candidate_area,
        "threshold_evaluation_scope": "candidate_intersection_operational_forecast_regions",
        "threshold_evaluation_scope_ko": "후보영역과 겹치는 운영 예보권역(육상·해상)",
        "peak_forecast_hours": peak_hours,
        "selected_domain_is_candidate_area": selected_domain_is_candidate_area,
        "presence_scope_quantifier": "at_least_one",
        "minimum_6h_precipitation_mm": DIRECT_PRECIPITATION_MINIMUM_MM_6H,
        "threshold_evaluation_status": evaluation_status,
        "threshold_evaluation_complete": evaluation_complete,
        "threshold_evaluation_unavailable_forecast_hours": unavailable_hours,
        "threshold_evaluation_by_forecast_hour": {
            f"F{hour:03d}": {
                "maximum_6h_mm": threshold_by_hour[hour]["maximum_6h_mm"],
                "evaluation_status": threshold_by_hour[hour]["evaluation_status"],
                "evaluated": threshold_by_hour[hour]["evaluated"],
                "meets_minimum": threshold_by_hour[hour]["meets_minimum"],
            }
            for hour in sorted(threshold_by_hour)
        },
        "threshold_met_regions_by_forecast_hour": {
            f"F{hour:03d}": threshold_by_hour[hour].get("threshold_met_regions", [])
            for hour in sorted(threshold_by_hour)
        },
        "threshold_met_forecast_hours": threshold_hours,
        "threshold_met": bool(threshold_hours),
    }

# Original func/step07/checks.py:1738
def _public_intent_context(specification: dict[str, Any]) -> dict[str, Any]:
    """Return only Check-local intent needed for an independent Step07 reading.

    Deliberately omit Step04 evidence_role/coverage_roles and hypotheses so the
    VLM understands WHAT/HOW/WHERE/WHEN/WHY without being told whether the Check
    was intended as supporting or opposing evidence.
    """
    intent = specification.get("intent_contract")
    intent = intent if isinstance(intent, dict) else {}
    capability = specification.get("capability_profile")
    capability = capability if isinstance(capability, dict) else {}
    return {
        "verification_mode": intent.get("verification_mode"),
        "domain_scope": intent.get("domain_scope"),
        "spatial_relation_scope": intent.get("spatial_relation_scope", "not_applicable"),
        "phase_ids": intent.get("phase_ids", []),
        "representative_spatial_hours": intent.get("representative_spatial_hours", []),
        "purpose": intent.get("purpose"),
        "source_intent_hash": specification.get("intent_hash"),
        "atomic_claims": copy.deepcopy(
            specification.get("atomic_claim_contract", {}).get("canonical_claims", [])
        ) if isinstance(specification.get("atomic_claim_contract"), dict) else [],
        "capability": {
            "execution_product_id": capability.get("execution_product_id"),
            "signal_components": capability.get("signal_components", []),
            "observable_ids": capability.get("observable_ids", []),
        },
    }

# Original func/step07/checks.py:2412
def _allowed_quantitative_feature_ids(entry: dict[str, Any]) -> list[str]:
    """Return the exact Step05 feature keys that an AI selector may use."""
    request = entry.get("request")
    step05 = entry.get("step05")
    if not isinstance(request, dict) or request.get("data_type") != "quantitative_excerpt":
        return []
    if not isinstance(step05, dict) or step05.get("status") != "success":
        return []
    result = step05.get("result")
    features = result.get("features") if isinstance(result, dict) else None
    if not isinstance(features, dict):
        return []
    return sorted(feature_id for feature_id, feature in features.items() if isinstance(feature_id, str) and feature_id and isinstance(feature, dict))

# Original func/step07/checks.py:3813
def _insufficient_result(
    candidate_id: str,
    check: dict[str, Any],
    specification: dict[str, Any],
    fingerprint: str,
    metadata: dict[str, str],
) -> dict[str, Any]:
    missing_components = [str(value) for value in check.get("missing_signal_components", [])]
    labels = {
        "direct_precipitation": "강수 분포",
        "moisture_supply": "수분 공급",
        "low_level_forcing": "하층 수렴",
        "mid_upper_forcing": "중층 상승운동",
        "thermodynamic_support": "열역학적 환경",
        "organization_persistence": "바람의 연직구조",
        "synoptic_support": "종관 환경",
        "uncertainty": "판독 보조자료",
    }
    if missing_components:
        missing_text = ", ".join(labels.get(value, value) for value in missing_components)
        reason = f"확인에 필요한 상세 예측자료 중 {missing_text} 자료가 부족하여 해당 특징을 판정하지 않습니다."
    else:
        reason = "사용 가능한 상세 예측자료가 없어 해당 특징을 판정할 수 없습니다."
    return {
        "candidate_id": candidate_id,
        "check_id": check["check_id"],
        "source_intent_hash": check.get("source_intent_hash"),
        "intent_contract_version": check.get("intent_contract_version"),
        "expected_signal": check["expected_signal"],
        "decision_relevance": check["decision_relevance"],
        "request_ids": check["request_ids"],
        "available_request_ids": check["available_request_ids"],
        "missing_request_ids": check["missing_request_ids"],
        "evidence_complete": False,
        "evidence_status": str(check.get("evidence_status") or "none"),
        "input_fingerprint": fingerprint,
        "assessment": "insufficient_evidence",
        "scope_coverage": "indeterminate",
        "spatial_localization": _empty_spatial_localization(
            enabled=_spatial_localization_enabled(check)
        ),
        "precipitation_motion": _empty_precipitation_motion(enabled=False),
        "ensemble_consensus": {
            "contract_version": ENSEMBLE_CONTRACT_VERSION,
            "mode": "deterministic_insufficient_evidence",
            "member_count": 0,
            "assessment_agreement": "not_applicable",
            "consensus_assessment": "insufficient_evidence",
            "selected_member_index": None,
            "vote_counts": [],
            "localization_agreement": "not_applicable",
            "precipitation_motion_agreement": "not_applicable",
            "consensus_precipitation_motion_classification": "not_applicable",
            "precipitation_motion_selected_member_index": None,
            "precipitation_motion_vote_counts": [],
            "disagreement_severity": "none",
            "disagreement_dimensions": [],
        },
        "findings": [],
        "contradictory_findings": [],
        "quantitative_claims": [],
        "quantitative_relations": [],
        "quantitative_consistency_status": "not_applicable",
        "quantitative_consistency_failure_reasons": [],
        "quantitative_consistency_failure_details": [],
        "usable_for_synthesis": False,
        "excluded_from_synthesis_reasons": ["INSUFFICIENT_EVIDENCE"],
        "uncertainties": [reason],
        "provider_metadata": metadata,
    }

# Original func/step07/checks.py:3885
def _bundle_messages(
    root: Path, init: str, group: dict[str, Any], checks: list[dict[str, Any]], entries: list[dict[str, Any]], prompt_text: str,
    grounding_retry_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    specifications = _check_specifications(group)
    entries_by_id = {str(entry["request"]["request_id"]): entry for entry in entries}
    check_rows = []
    for check in checks:
        specification = specifications.get(str(check.get("check_id")))
        if specification is None:
            raise Pass2PipelineError("VARIABLE_CHECKS_INVALID", f"{group.get('candidate_id')} has no Step04 definition for {check.get('check_id')}.")
        check_entries = [
            entries_by_id[request_id]
            for request_id in check.get("request_ids", [])
            if request_id in entries_by_id
        ]
        check_row = {
            **{key: check.get(key) for key in ("check_id", "diagnostic_id", "expected_signal", "decision_relevance", "evidence_complete", "evidence_status", "request_ids", "cross_section_context", "time_height_context", "check_scope", "required_signal_components", "available_signal_components", "missing_signal_components", "allowed_quantitative_forecast_hours")},
            **{key: specification.get(key) for key in ("product_id", "variable_role", "analysis_methods")},
            "intent_context": _public_intent_context(specification),
            "spatial_scope_context": _spatial_scope_context(group, check, check_entries),
            "operational_region_naming_context": _candidate_operational_region_naming_context(root, group),
            "spatial_localization_context": _candidate_operational_localization_context(root, group, specification),
            "precipitation_motion_context": _precipitation_motion_context(root, group, specification),
            "temporal_precipitation_primary_feature": requires_temporal_precipitation_increment_claim(specification),
        }
        required_selector_context = _required_specific_quantitative_selectors(
            specification, check, check_entries,
        )
        if required_selector_context is not None:
            check_row["specific_quantitative_selector_context"] = required_selector_context
        anchor_context = _system_direct_precipitation_anchor_context(
            group, check, specification, check_entries,
        )
        if anchor_context is not None:
            check_row["system_direct_precipitation_anchor_context"] = anchor_context
        check_rows.append(check_row)
    allowed_evidence_namespace = [
        {
            "check_id": check["check_id"],
            "allowed_evidence_refs": _allowed_evidence_reference_rows(
                check, [entries_by_id[request_id] for request_id in check["available_request_ids"] if request_id in entries_by_id]
            ),
            "allowed_quantitative_selector_namespace": _allowed_quantitative_selector_rows(
                check, [entries_by_id[request_id] for request_id in check["available_request_ids"] if request_id in entries_by_id]
            ),
        }
        for check in checks
    ]
    signed_feature_semantics = {}
    for namespace in allowed_evidence_namespace:
        for row in namespace.get("allowed_quantitative_selector_namespace", []):
            feature_id = row.get("feature_id") if isinstance(row, dict) else None
            if isinstance(feature_id, str) and feature_id not in signed_feature_semantics:
                semantics = quantitative_feature_semantics(feature_id)
                if semantics is not None:
                    signed_feature_semantics[feature_id] = semantics
    bundle_context = {
        "init": init,
        "candidate_id": group["candidate_id"],
        "checks": check_rows,
        "allowed_evidence_namespace": allowed_evidence_namespace,
        "quantitative_feature_semantics": signed_feature_semantics,
        "instruction": (
            "Evaluate only whether each expected_signal is observed in the supplied Step05 evidence. "
            "expected_signal and decision_relevance are not ground truth. The initial assessment is independent: "
            "no Python-computed comparison result is supplied. If you make a quantitative comparison, trend, or extremum claim, "
            "declare the exact cells in quantitative_claims and the relation in quantitative_relations so Python can verify it afterwards."
        ),
    }
    if any(
        image.get("physical_diagnostic") == "QL_MOISTURE_FLUX850_WIND850"
        for entry in entries
        for image in (entry.get("step05") or {}).get("generated_images", [])
        if isinstance(image, dict)
    ):
        bundle_context["moisture_transport_geometry_instruction"] = (
            "850 hPa 수증기 플럭스 크기 음영(q|V|)는 수송 방향을 나타내지 않으며, 화살표는 실제 850 hPa 바람벡터입니다. "
            "북동향·북향 등의 절대 바람 방향만으로 후보영역으로의 유입 여부를 긍정 또는 부정하지 마십시오. "
            "수증기 플럭스가 큰 공급원의 좌표·분포, 후보영역의 상대 위치와 경계, 바람벡터가 그 사이를 실제로 연결하는지를 영상에서 대조하십시오. "
            "방향과 공간적 연결이 불분명하면 유입 여부는 확인되지 않았다고 기술하되, '북동향이므로 후보로 향하지 않는다'처럼 "
            "방향 하나만을 근거로 부재를 단정하지 마십시오. 정량적인 경계 수증기 유입량은 주어진 자료로 계산하지 마십시오."
        )
    majority_bindings = [
        _majority_convergence_binding_scope(specifications.get(str(check.get("check_id")), {}), check)
        for check in checks
    ]
    if any(
        binding is not None and binding[0].get("expected_state") == "strengthen"
        for binding in majority_bindings
    ):
        # Target only applicable checks, not the global Step07 prompt. The
        # independent AI pass receives no Python-computed outcome here.
        bundle_context["majority_convergence_strengthen_instruction"] = (
            "For an exact 925 hPa convergence majority-strengthen claim, assess "
            "whether more than half of the source-overlap operational regions "
            "show physical convergence strengthening between the Check's two "
            "stated forecast hours. Positive convergence increasing or a "
            "transition into positive convergence is strengthening; becoming "
            "less negative while still divergent is not. Keep the model's "
            "meteorological assessment independent. For this exact majority "
            "comparison, return empty quantitative_claims and "
            "quantitative_relations arrays instead of manually enumerating "
            "all operational-region pairs: Python binds and verifies those "
            "source cells after the independent assessment. Keep image and "
            "quantitative evidence references grounded in the supplied data."
        )
    if any("specific_quantitative_selector_context" in row for row in check_rows):
        bundle_context["specific_quantitative_selector_instruction"] = (
            "For each specific Check with status=available, build its deciding quantitative relation "
            "using exactly the selectors in specific_quantitative_selector_context.required_quantitative_selectors. "
            "Declare the same selectors in quantitative_claims; do not substitute another region, feature, "
            "statistic, forecast hour, or request. For status=missing_source_cells, never invent values or "
            "selectors; report missing evidence consistently with the schema. "
            "This context specifies WHAT to compare, not the outcome. Determine the relation and assessment "
            "independently from Step05 evidence. expected_state is a hypothesis, not an answer. "
            "For majority/non-specific Checks, keep the existing full selector namespace."
        )
    if any("system_direct_precipitation_anchor_context" in row for row in check_rows):
        threshold = f"{DIRECT_PRECIPITATION_MINIMUM_MM_6H:g}"
        bundle_context["system_direct_precipitation_anchor_instruction"] = (
            "SYSTEM 직접강수 존재 Anchor는 이동, 지속성, 조직화, 최대강도 시각, 시각 간 강도 비교를 판정하지 않는다. "
            f"모델 강수 예측의 운영상 최소기준은 6시간 강수량 {threshold} mm이다. system_direct_precipitation_anchor_context의 "
            "threshold_evaluation_scope는 후보영역과 겹치는 운영 예보권역(육상·해상)으로 제한된다. "
            "threshold_met=false이고 threshold_evaluation_complete=true이면, 이미지에 극미량 강수값이 있거나 후보영역의 운영 예보권역 밖에 강수가 있더라도 "
            "평가 가능한 모든 핵심시각의 운영권역 자료에서 기준을 만족하지 않은 것이므로 assessment=not_observed, scope_coverage=complete_expected_scope로 판정한다. "
            "threshold_met=false이고 threshold_evaluation_complete=false이면 운영권역 중첩 또는 해당 영역의 유효 강수값이 없어 기준을 완전히 평가하지 못한 것입니다. "
            "이를 강수 미발생으로 해석하지 말고 assessment=insufficient_evidence, scope_coverage=indeterminate로 판정하며, "
            "threshold_evaluation_unavailable_forecast_hours와 해당 시각의 evaluation_status에 따라 운영권역 부재·유효 강수값 부재·범위 메타자료 부재를 uncertainty에 구분해 기록하십시오. "
            "threshold_met=true이면 threshold_met_forecast_hours에 포함된 핵심시각의 상세 6시간 강수 이미지에서 운영 예보권역에 "
            f"{threshold} mm 이상을 나타내는 유색 강수영역이 식별 가능한 공간범위로 확인될 때 assessment=observed, "
            "scope_coverage=complete_expected_scope로 판정한다. 임계값 이상 값이 한 격자 또는 영상 잡음처럼 고립되어 식별 가능한 강수영역으로 "
            "판단하기 어려우면 partially_observed 또는 uncertain을 근거에 맞게 사용한다. 다른 핵심시각에 강수가 약하거나 없다는 이유만으로 "
            "observed를 partially_observed로 낮추지 않는다. selected_domain_is_candidate_area=true이면 제공된 공간영상 전체 범위가 후보영역이므로 "
            "별도 후보경계선이 없다는 이유로 uncertain을 선택하지 않는다. "
            "threshold_met_regions_by_forecast_hour는 운영권역 마스크 안에서 최소기준 이상 값이 존재한 표준 권역명이다. "
            "강수 존재 finding을 작성할 때 영상에서 강수영역이 확인된 시각에는 이 목록의 region_name을 사용해 어느 운영권역인지 문장 안에 직접 명시한다. "
            "이 목록에 없는 지역명을 추정하지 않는다."
        )
    parts: list[dict[str, Any]] = [{"type": "text", "text": json.dumps(bundle_context, ensure_ascii=False)},]
    if grounding_retry_context is not None:
        parts.append({"type": "text", "text": json.dumps(grounding_retry_context, ensure_ascii=False)})
    for entry in entries:
        request = entry["request"]
        step05 = entry["step05"]
        result = step05.get("result")
        parts.append({"type": "text", "text": json.dumps({
            "request_id": request["request_id"], "diagnostic_id": request.get("diagnostic_id"), "data_type": request.get("data_type"),
            "forecast_hours": request.get("forecast_hours"), "selected_domain": (result or {}).get("selected_domain") if isinstance(result, dict) else request.get("requested_domain"),
            "step05_status": step05.get("status"), "quantitative_features": (result or {}).get("features") if isinstance(result, dict) else None,
            "allowed_feature_ids": _allowed_quantitative_feature_ids(entry),
        }, ensure_ascii=False)})
        for image in step05.get("generated_images", []):
            parts.append({"type": "text", "text": json.dumps({
                "image_metadata": {key: image.get(key) for key in ("request_id", "diagnostic_id", "physical_diagnostic", "forecast_hour", "valid_time_utc", "request_forecast_hours", "request_valid_times_utc", "units", "selected_domain")}
            }, ensure_ascii=False)})
            parts.append({"type": "image_url", "image_url": image_data_url(resolve_logical_ref(root, image["path"]))})
    return [{"role": "system", "content": prompt_text}, {"role": "user", "content": parts}]

# Original func/step07/checks.py:4021
def _call_bundle(provider: AIProvider, root: Path, messages: list[dict[str, Any]], config: dict[str, str]) -> dict[str, Any]:
    canonical = load_schema(root, CHECK_RESPONSE_SCHEMA)
    schema = build_openai_transport_schema(canonical) if provider.api_family == "responses" else canonical
    response = provider.complete(
        messages=messages, model=config["model"], max_tokens=8000, schema=schema, schema_name="AIFSPass2CheckAssessment",
        structured_output_mode="auto", image_detail=config["image_detail"], reasoning_effort=config["reasoning_effort"],
    )
    return _decode_validate_checks_provider_response(root, response, canonical, provider.api_family)

# Original func/step07/checks.py:4068
def _replace_step07_assessment_unit(
    response: dict[str, Any], check_id: str, replacement: dict[str, Any],
) -> dict[str, Any]:
    """Replace one complete Step07 assessment object; never merge individual fields."""
    if str(replacement.get("check_id") or "") != check_id:
        raise Pass2PipelineError(
            "ASSESSMENT_REGENERATION_INVALID",
            f"Step07 regenerated assessment changed check_id expected={check_id} actual={replacement.get('check_id')}",
        )
    merged = copy.deepcopy(response)
    rows = merged.get("assessments")
    if not isinstance(rows, list):
        raise Pass2PipelineError("ASSESSMENT_REGENERATION_INVALID", "Step07 assessment array is invalid.")
    found = False
    for index, item in enumerate(rows):
        if isinstance(item, dict) and str(item.get("check_id")) == check_id:
            rows[index] = copy.deepcopy(replacement)
            found = True
            break
    if not found:
        raise Pass2PipelineError(
            "ASSESSMENT_REGENERATION_INVALID",
            f"Step07 regeneration target is absent from bundle: {check_id}",
        )
    return merged

# Original func/step07/checks.py:4095
def _regenerate_step07_assessment_units(
    provider: AIProvider,
    root: Path,
    init: str,
    group: dict[str, Any],
    rows: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str]],
    entries_by_id: dict[str, dict[str, Any]],
    prompt_text: str,
    config: dict[str, str],
    response: dict[str, Any],
    target_check_ids: list[str],
    error: Pass2PipelineError,
) -> dict[str, Any]:
    """Regenerate each failed Check assessment as one atomic semantic unit.

    The previous assessment object is discarded.  The provider receives the
    original Step04 Check definition and Step05 evidence again and returns the
    complete assessment object (assessment, findings, contradictions, uncertainty,
    quantitative claims and relations) in one response.
    """
    row_by_check_id = {str(row[1]["check_id"]): row for row in rows}
    current = copy.deepcopy(response)
    for check_id in target_check_ids:
        row = row_by_check_id.get(check_id)
        if row is None:
            raise Pass2PipelineError(
                "ASSESSMENT_REGENERATION_INVALID",
                f"Step07 regeneration target is not part of the active bundle: {check_id}",
            )
        _group, check, _entries, _fingerprint = row
        available_entries = [
            entries_by_id[request_id]
            for request_id in check.get("available_request_ids", [])
            if request_id in entries_by_id
        ]
        retry_context = _grounding_retry_context(
            error, checks=[check], entries_by_id=entries_by_id, response=current,
            specifications_by_check=_check_specifications(group), root=root, group=group,
        )
        grounding = retry_context.setdefault("grounding_retry", {})
        grounding["regeneration_unit"] = "complete_check_assessment"
        grounding["instruction"] = (
            "이전 assessment 객체는 폐기되었습니다. 일부 statement, relation 또는 assessment 필드만 수정하지 마십시오. "
            "원래 Step04 Check와 제공된 Step05 근거를 다시 보고 이 Check의 assessment, findings, contradictory_findings, "
            "uncertainties, quantitative_claims, quantitative_relations 전체를 하나의 일관된 판단으로 처음부터 다시 생성하십시오. "
            + str(grounding.get("instruction") or "")
        )
        if error.code == "NARRATIVE_LANGUAGE_INVALID":
            grounding["instruction"] += (
                " 사람이 읽는 모든 서술은 한국어로 작성하고 방위 약어 N/S/E/W 같은 허용되지 않은 영문 토큰을 사용하지 마십시오."
            )
        messages = _bundle_messages(
            root, init, group, [check], available_entries, prompt_text,
            grounding_retry_context=retry_context,
        )
        regenerated = _call_bundle(provider, root, messages, config)
        assessments = regenerated.get("assessments")
        if not isinstance(assessments, list) or len(assessments) != 1 or not isinstance(assessments[0], dict):
            raise Pass2PipelineError(
                "ASSESSMENT_REGENERATION_INVALID",
                f"Step07 complete Check regeneration must return exactly one assessment: {check_id}",
            )
        current = _replace_step07_assessment_unit(current, check_id, assessments[0])
    return current

# Original func/step07/checks.py:4197
def _step07_narrative_rewrite_targets(
    response: dict[str, Any], language_errors: list[str],
) -> list[dict[str, Any]]:
    """Resolve language-validator paths to immutable narrative slots.

    Language repair is intentionally narrower than the full Step07 response.  The
    provider may rewrite only strings that the Korean narrative validator flagged;
    identifiers, arrays, evidence links, assessments, and quantitative structures
    never enter the rewrite output contract.
    """
    by_path: dict[str, dict[str, Any]] = {}
    assessments = response.get("assessments")
    if not isinstance(assessments, list):
        raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", "Step07 한글 재작성 원본의 확인 항목 구조가 유효하지 않습니다.")
    for assessment_index, assessment in enumerate(assessments):
        if not isinstance(assessment, dict):
            continue
        check_id = assessment.get("check_id")
        if not isinstance(check_id, str) or not check_id:
            continue
        for field in ("findings", "contradictory_findings"):
            rows = assessment.get(field)
            if not isinstance(rows, list):
                continue
            for index, row in enumerate(rows):
                if not isinstance(row, dict) or not isinstance(row.get("statement"), str):
                    continue
                path = f"assessments[{assessment_index}].{field}[{index}].statement"
                by_path[path] = {
                    "check_id": check_id,
                    "field": field,
                    "index": index,
                    "current_text": row["statement"],
                    "path": path,
                }
        uncertainties = assessment.get("uncertainties")
        if isinstance(uncertainties, list):
            for index, value in enumerate(uncertainties):
                if not isinstance(value, str):
                    continue
                path = f"assessments[{assessment_index}].uncertainties[{index}]"
                by_path[path] = {
                    "check_id": check_id,
                    "field": "uncertainties",
                    "index": index,
                    "current_text": value,
                    "path": path,
                }

    targets: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for error in language_errors:
        path = str(error).split(":", 1)[0].strip()
        if path in seen_paths:
            continue
        target = by_path.get(path)
        if target is None:
            raise Pass2PipelineError(
                "NARRATIVE_REWRITE_INVALID",
                f"Step07 한글 재작성 대상 경로를 원본 응답에서 찾을 수 없습니다: {path}",
            )
        targets.append(target)
        seen_paths.add(path)
    if not targets:
        raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", "Step07 한글 재작성 대상이 비어 있습니다.")
    return targets

# Original func/step07/checks.py:4265
def _step07_narrative_patch_schema(max_items: int) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "AIFSHeavyRain Step07 narrative text patch",
        "type": "object",
        "additionalProperties": False,
        "required": ["rewrites"],
        "properties": {
            "rewrites": {
                "type": "array",
                "minItems": 1,
                "maxItems": max_items,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["check_id", "field", "index", "text"],
                    "properties": {
                        "check_id": {"type": "string", "minLength": 1},
                        "field": {"type": "string", "enum": ["findings", "contradictory_findings", "uncertainties"]},
                        "index": {"type": "integer", "minimum": 0},
                        "text": {"type": "string", "minLength": 1},
                    },
                },
            }
        },
    }

# Original func/step07/checks.py:4293
def _apply_step07_narrative_patch(
    original: dict[str, Any], patch: dict[str, Any], targets: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply only explicitly permitted text replacements to a Step07 response."""
    if set(patch) != {"rewrites"} or not isinstance(patch.get("rewrites"), list):
        raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", "Step07 한글 재작성 패치 구조가 유효하지 않습니다.")
    rewrites = patch["rewrites"]
    if len(rewrites) != len(targets):
        raise Pass2PipelineError(
            "NARRATIVE_REWRITE_INVALID",
            f"Step07 한글 재작성 패치 수가 대상 수와 다릅니다: expected={len(targets)} actual={len(rewrites)}",
        )

    allowed = {
        (str(target["check_id"]), str(target["field"]), int(target["index"])): target
        for target in targets
    }
    seen: set[tuple[str, str, int]] = set()
    normalized: dict[tuple[str, str, int], str] = {}
    for item in rewrites:
        if not isinstance(item, dict) or set(item) != {"check_id", "field", "index", "text"}:
            raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", "Step07 한글 재작성 패치 항목이 유효하지 않습니다.")
        check_id = item.get("check_id")
        field = item.get("field")
        index = item.get("index")
        text = item.get("text")
        if (
            not isinstance(check_id, str)
            or field not in {"findings", "contradictory_findings", "uncertainties"}
            or isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or not isinstance(text, str)
            or not text.strip()
        ):
            raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", "Step07 한글 재작성 패치 값이 유효하지 않습니다.")
        key = (check_id, str(field), index)
        if key not in allowed:
            raise Pass2PipelineError(
                "NARRATIVE_REWRITE_INVALID",
                f"Step07 한글 재작성 패치가 허용되지 않은 위치를 요청했습니다: {check_id} {field}[{index}]",
            )
        if key in seen:
            raise Pass2PipelineError(
                "NARRATIVE_REWRITE_INVALID",
                f"Step07 한글 재작성 패치가 같은 위치를 중복 요청했습니다: {check_id} {field}[{index}]",
            )
        seen.add(key)
        normalized[key] = text.strip()
    if seen != set(allowed):
        missing = sorted(set(allowed) - seen)
        raise Pass2PipelineError(
            "NARRATIVE_REWRITE_INVALID",
            f"Step07 한글 재작성 패치에 필수 대상이 누락되었습니다: {missing[:4]}",
        )

    merged = copy.deepcopy(original)
    rows_by_check_id = {
        str(item.get("check_id")): item
        for item in merged.get("assessments", [])
        if isinstance(item, dict) and isinstance(item.get("check_id"), str)
    }
    for (check_id, field, index), text in normalized.items():
        assessment = rows_by_check_id.get(check_id)
        if assessment is None:
            raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", f"Step07 한글 재작성 대상 확인 항목이 없습니다: {check_id}")
        values = assessment.get(field)
        if not isinstance(values, list) or index >= len(values):
            raise Pass2PipelineError(
                "NARRATIVE_REWRITE_INVALID",
                f"Step07 한글 재작성 대상 위치가 유효하지 않습니다: {check_id} {field}[{index}]",
            )
        if field == "uncertainties":
            if not isinstance(values[index], str):
                raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", f"Step07 {check_id} uncertainties[{index}]가 문자열이 아닙니다.")
            values[index] = text
        else:
            row = values[index]
            if not isinstance(row, dict) or not isinstance(row.get("statement"), str):
                raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", f"Step07 {check_id} {field}[{index}]가 유효하지 않습니다.")
            row["statement"] = text
    return merged

# Original func/step07/checks.py:4377
def _rewrite_step07_narrative(
    provider: AIProvider,
    root: Path,
    config: dict[str, str],
    response: dict[str, Any],
    language_errors: list[str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Repair only language-invalid Step07 narrative strings.

    A first repair can itself reintroduce an unapproved Latin token (for example
    a compass abbreviation such as ``E`` in a coordinate).  Retry the narrow
    text-only repair once more against only the still-invalid strings instead of
    failing the whole Step07 bundle.  The meteorological assessment, evidence
    references, quantitative selectors, and JSON structure remain immutable.
    """
    current_response = copy.deepcopy(response)
    current_errors = list(language_errors)
    rewrite_usages: list[dict[str, Any] | None] = []
    max_rewrite_attempts = 2

    for rewrite_attempt in range(1, max_rewrite_attempts + 1):
        targets = _step07_narrative_rewrite_targets(current_response, current_errors)
        canonical = _step07_narrative_patch_schema(len(targets))
        schema = build_openai_transport_schema(canonical) if provider.api_family == "responses" else canonical
        messages = [
            {
                "role": "system",
                "content": (
                    "기상학적 판단이나 JSON 구조를 다시 만들지 않습니다. 지정된 자연어 문자열만 한글 기상용어로 고쳐 "
                    "rewrites 배열의 텍스트 패치만 반환하십시오. 대상 식별자, 필드, 순서는 입력과 정확히 같아야 합니다. "
                    "사용자 표시 문장에는 허용 목록 밖의 영문 알파벳을 남기지 마십시오. "
                    "특히 방위 약어 N/S/E/W와 35N, 127E 같은 좌표 약어는 사용하지 말고, 원문 의미가 좌표인 경우 "
                    "북위·남위·동경·서경과 숫자·도를 사용해 같은 위치를 표현하십시오."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "rewrite_targets의 문자열만 재작성하십시오. 각 대상마다 정확히 하나의 패치를 반환하십시오.",
                        "repair_attempt": rewrite_attempt,
                        "violations": current_errors,
                        "rewrite_targets": targets,
                        "rules": [
                            "check_id, field, index는 rewrite_targets의 값을 그대로 복사하십시오.",
                            "text만 수정하고 새로운 대상이나 배열 항목을 만들거나 삭제하지 마십시오.",
                            "수치, 시각, 위치, 기상학적 판단의 강도와 방향을 변경하지 마십시오.",
                            "Step 번호와 내부 영문 용어를 제거하고 실제 기상요소 중심의 한글 문장으로 작성하십시오.",
                            "자연어의 영문은 AIFS, UTC, KST, F084 같은 예측시간, hPa·mm·m/s·Pa/s·g/kg 등 허용된 운영 약어와 물리단위만 유지하십시오.",
                            "N, S, E, W 같은 영문 방위 약어는 남기지 마십시오. 좌표 의미라면 북위·남위·동경·서경으로 표현하고 원래 숫자는 유지하십시오.",
                            "반환 전에 각 text의 영문 알파벳을 다시 확인하여 허용 목록 밖 토큰이 없도록 하십시오.",
                            "rewrites 외의 키는 반환하지 마십시오.",
                        ],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        provider_response = provider.complete(
            messages=messages,
            model=config["model"],
            max_tokens=4000,
            schema=schema,
            schema_name="AIFSStep07NarrativeTextPatch",
            structured_output_mode="auto",
            image_detail=config["image_detail"],
            reasoning_effort="low",
        )
        rewrite_usages.append(provider_response.usage if isinstance(provider_response.usage, dict) else None)
        content = normalize_openai_transport_json(provider_response.content, canonical) if provider.api_family == "responses" else provider_response.content
        try:
            patch = json.loads(content)
        except json.JSONDecodeError as exc:
            raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", "Step07 한글 재작성 패치가 JSON이 아닙니다.") from exc
        if not isinstance(patch, dict):
            raise Pass2PipelineError("NARRATIVE_REWRITE_INVALID", "Step07 한글 재작성 패치의 최상위가 객체가 아닙니다.")

        current_response = _apply_step07_narrative_patch(current_response, patch, targets)
        remaining = validate_step07_response_narrative(current_response)
        if not remaining:
            if len(rewrite_usages) == 1:
                return current_response, rewrite_usages[0]
            return current_response, {
                "rewrite_attempts": len(rewrite_usages),
                "attempts": rewrite_usages,
            }
        current_errors = remaining

    raise Pass2PipelineError("NARRATIVE_LANGUAGE_INVALID", "; ".join(current_errors[:12]))
