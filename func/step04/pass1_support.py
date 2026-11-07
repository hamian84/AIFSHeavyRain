"""Step step04 support responsibility; original function bodies preserved."""
from __future__ import annotations
from . import pass1_context as _step_context
AIFS_NATIVE_SPATIAL_INTERVAL_DEGREES = getattr(_step_context, 'AIFS_NATIVE_SPATIAL_INTERVAL_DEGREES')
CANDIDATE_DISCOVERY_RECONCILIATION_ASSET_LIMIT = getattr(_step_context, 'CANDIDATE_DISCOVERY_RECONCILIATION_ASSET_LIMIT')
CANDIDATE_DISCOVERY_RECONCILIATION_EVIDENCE_LIMIT = getattr(_step_context, 'CANDIDATE_DISCOVERY_RECONCILIATION_EVIDENCE_LIMIT')

# Original func/step04/pass1_engine.py:561
def _verification_mode_from_diagnostic_check(row: dict[str, Any]) -> str:
    diagnostic_id = str(row.get("diagnostic_id") or "")
    scope = str(row.get("check_scope") or "")
    methods = {str(value) for value in row.get("analysis_methods", [])}
    if diagnostic_id == "multivariable_composite":
        return "multivariable_alignment"
    if diagnostic_id == "vertical_cross_section":
        return "vertical_structure"
    if diagnostic_id == "time_height_section":
        return "vertical_time_evolution"
    if diagnostic_id == "accumulated_precipitation":
        return "precipitation_amount"
    if diagnostic_id == "temporal_evolution" or "temporal_evolution" in methods:
        return "temporal_change"
    if scope == "temporal_evolution":
        return "spatial_evolution"
    if methods & {"regional_statistics", "regional_ranking", "regional_max_location"}:
        return "regional_intensity"
    return "spatial_distribution"

# Original func/step04/pass1_engine.py:638
def _effective_compact_check_coverage_roles(row: dict[str, Any]) -> list[str]:
    """Return canonical coverage roles implied by one verification Check.

    Structural coverage must follow the executable verification contract rather
    than a free model-authored tag.  Non-structural roles (for example
    forecast_uncertainty or terrain_or_regional_effect) remain model-planned.
    """
    declared = [
        str(value)
        for value in row.get("coverage_roles", [])
        if str(value) in COMPACT_COVERAGE_ROLES
        and str(value) not in _DETERMINISTIC_COMPACT_COVERAGE_ROLES
    ]

    components_raw = row.get("required_signal_components")
    if not isinstance(components_raw, list):
        components_raw = row.get("signal_components", [])
    components = [str(value) for value in components_raw if str(value)]

    inferred: list[str] = []
    for component in components:
        role = _VERIFICATION_COMPONENT_TO_COVERAGE_ROLE.get(component)
        if role:
            inferred.append(role)

    mode = str(row.get("verification_mode") or "")
    if not mode and (row.get("diagnostic_id") or row.get("check_scope") or row.get("analysis_methods")):
        mode = _verification_mode_from_diagnostic_check(row)

    # A spatial-evolution diagnosis necessarily compares multiple times and
    # therefore satisfies both time-evolution and spatial-evolution coverage.
    if mode in {"temporal_change", "spatial_evolution", "vertical_time_evolution"}:
        inferred.append("temporal_evolution")
    if mode == "spatial_evolution":
        inferred.append("spatial_evolution")

    evidence_role = str(row.get("evidence_role") or "context")
    if evidence_role == "supporting":
        inferred.append("supporting_evidence")
    elif evidence_role == "opposing":
        inferred.append("opposing_evidence")

    return list(dict.fromkeys([*declared, *inferred]))

# Original func/step04/pass1_engine.py:828
def _minimum_forecast_hours_for_check(
    diagnostic_id: str,
    check_scope: str,
) -> int:
    """Return the minimum distinct forecast hours required by one Check."""
    return max(
        MINIMUM_FORECAST_HOURS_BY_DIAGNOSTIC.get(diagnostic_id, 1),
        2 if check_scope == "temporal_evolution" else 1,
    )

# Original func/step04/pass1_engine.py:1029
def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

# Original func/step04/pass1_engine.py:1033
def rel(root: Path, path: Path) -> str:
    return logical_ref(root, path)

# Original func/step04/pass1_engine.py:1037
def setup_logging(root: Path, init: str) -> Path:
    path = runtime_path(root, "logs", init, "05_run_ai_pass1.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(path, mode="w", encoding="utf-8"), logging.StreamHandler(sys.stdout)],
        force=True,
    )
    return path

# Original func/step04/pass1_engine.py:1049
def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return value

# Original func/step04/pass1_engine.py:1056
def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)

# Original func/step04/pass1_engine.py:1267
def sha256sum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

# Original func/step04/pass1_engine.py:1275
def canonical_hash(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

# Original func/step04/pass1_engine.py:1280
def json_utf8_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

# Original func/step04/pass1_engine.py:1410
def _retry_after_seconds(headers: Any) -> float | None:
    try:
        value = headers.get("Retry-After")
    except Exception:  # noqa: BLE001
        return None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None

# Original func/step04/pass1_engine.py:1423
def _error_mentions(body: str, terms: Iterable[str]) -> bool:
    lowered = body.lower()
    return any(term.lower() in lowered for term in terms)

# Original func/step04/pass1_engine.py:1636
def _attachment_by_id(attachment_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    attachments = attachment_manifest.get("attachments")
    if not isinstance(attachments, list):
        raise ValueError("attachment manifest attachments must be an array")
    result: dict[str, dict[str, Any]] = {}
    for item in attachments:
        if not isinstance(item, dict):
            raise ValueError("attachment manifest item must be an object")
        asset_id = str(item.get("asset_id", ""))
        if not asset_id:
            raise ValueError("attachment asset_id is missing")
        if asset_id in result:
            raise ValueError(f"duplicate attachment asset_id: {asset_id}")
        result[asset_id] = item
    return result

# Original func/step04/pass1_engine.py:2228
def _unreferenced_compact_phase_keys(chunks: list[dict[str, Any]]) -> list[str]:
    """Return every declared compact phase that no Check references."""
    declared_phase_keys = {
        str(phase.get("phase_key", "")).strip()
        for chunk in chunks
        for phase in _compact_plan_rows(chunk, "analysis_phases")
        if str(phase.get("phase_key", "")).strip()
    }
    referenced_phase_keys = {
        str(phase_key).strip()
        for chunk in chunks
        for check in _compact_plan_rows(chunk, "diagnostic_checks")
        for phase_key in check.get("phase_keys", [])
        if str(phase_key).strip()
    }
    return sorted(declared_phase_keys - referenced_phase_keys)

# Original func/step04/pass1_engine.py:4437
def _normalized_bbox_for_validation(value: Any) -> dict[str, float] | None:
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
    return bbox

# Original func/step04/pass1_engine.py:4819
def _peak_anchor_minimum_mm(support_catalog: dict[str, Any] | None) -> float:
    config = support_catalog.get("direct_precipitation_peak_anchor") if isinstance(support_catalog, dict) else None
    value = config.get("regional_maximum_6h_mm") if isinstance(config, dict) else 5.0
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        threshold = 5.0
    return max(threshold, 0.0)

# Original func/step04/pass1_engine.py:4894
def build_system_direct_precipitation_coverage_guidance(
    overview_data: dict[str, Any],
    *,
    support_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build compact SYSTEM coverage guidance for candidate discovery."""
    reference_threshold = _peak_anchor_minimum_mm(support_catalog)
    signals = _system_direct_precipitation_signal_rows(
        overview_data,
        candidate_reference_threshold_mm=reference_threshold,
    )
    required = [row for row in signals if row["candidate_generation_required"]]
    by_hour: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in signals:
        by_hour[int(row["forecast_hour"])].append(row)
    hour_summary: list[dict[str, Any]] = []
    for hour in sorted(by_hour):
        rows = by_hour[hour]
        strongest = max(rows, key=lambda row: (float(row["maximum_6h_mm"]), str(row["region_id"])))
        hour_summary.append(
            {
                "forecast_hour": hour,
                "qualifying_region_count": len(rows),
                "strongest_region_id": strongest["region_id"],
                "strongest_region_name_ko": strongest["region_name_ko"],
                "strongest_maximum_6h_mm": strongest["maximum_6h_mm"],
                "candidate_generation_required": any(bool(row["candidate_generation_required"]) for row in rows),
            }
        )
    return {
        "contract_version": SYSTEM_DIRECT_PRECIPITATION_COVERAGE_AUDIT_VERSION,
        "source": "step02.operational_region_timeseries.tp_6h_increment_mm",
        "minimum_direct_signal_6h_mm": DIRECT_PRECIPITATION_MINIMUM_MM_6H,
        "candidate_reference_threshold_6h_mm": reference_threshold,
        "required_reference_signals": [
            {
                key: row[key]
                for key in (
                    "forecast_hour", "region_id", "region_name_ko",
                    "maximum_6h_mm", "p95_6h_mm", "mean_6h_mm", "max_location",
                )
            }
            for row in required
        ],
        "minimum_signal_hour_summary": hour_summary,
        "usage_rules": [
            "6시간 최대강수량 1.0 mm 이상 신호는 SYSTEM이 전수 감사하며 모델의 present/absent 판독과 독립적입니다.",
            f"지역 최대 6시간 강수량 {reference_threshold:g} mm 이상 신호가 어떤 후보에도 포함되지 않으면 SYSTEM 전용 coverage reconciliation에서 기존 후보 확장 또는 별도 후보 추가 여부를 재판정합니다.",
            f"1.0 mm 이상 {reference_threshold:g} mm 미만의 미커버 신호는 감사 기록에 남기되 그 값만으로 신규 후보 생성을 강제하지 않습니다.",
            "한 후보가 여러 정량 신호를 포괄할 수 있으며, 동일 기상계인지 여부와 후보 병합·분리는 AI가 영상·환경장과 함께 판단합니다.",
        ],
    }

# Original func/step04/pass1_engine.py:4948
def _precipitation_asset_ids_for_hour(
    attachment_manifest: dict[str, Any],
    forecast_hour: int,
) -> list[str]:
    result: list[str] = []
    for item in attachment_manifest.get("attachments", []):
        if not isinstance(item, dict) or str(item.get("product_id")) != "QL_PRECIP_6H":
            continue
        hours = {
            int(value)
            for value in item.get("forecast_hours", [])
            if isinstance(value, int) and not isinstance(value, bool)
        }
        asset_id = str(item.get("asset_id") or "")
        if asset_id and forecast_hour in hours:
            result.append(asset_id)
    return result

# Original func/step04/pass1_engine.py:5287
def _required_uncovered_system_direct_precipitation_signals(
    audit: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        row
        for row in audit.get("signals", [])
        if isinstance(row, dict)
        and row.get("coverage_status") == "uncovered"
        and row.get("candidate_generation_required") is True
    ]

# Original func/step04/pass1_engine.py:5517
def _coverage_reconciliation_capacity(
    response: dict[str, Any], *, asset_limit: int = CANDIDATE_DISCOVERY_RECONCILIATION_ASSET_LIMIT,
    evidence_limit: int = CANDIDATE_DISCOVERY_RECONCILIATION_EVIDENCE_LIMIT,
) -> list[dict[str, Any]]:
    """Expose occupied evidence slots; never infer storm identity from capacity."""
    by_id = {
        str(group.get("candidate_id")): group
        for group in response.get("candidate_evidence", [])
        if isinstance(group, dict)
    }
    rows: list[dict[str, Any]] = []
    for candidate in response.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        related = list(candidate.get("related_overview_asset_ids", []))
        existing_items = list(by_id.get(candidate_id, {}).get("evidence_items", []))
        rows.append({
            "candidate_id": candidate_id,
            "related_asset_count": len(related),
            "related_asset_limit": asset_limit,
            "available_related_asset_slots": max(0, asset_limit - len(related)),
            "evidence_item_count": len(existing_items),
            "evidence_item_limit": evidence_limit,
            "available_evidence_slots": max(0, evidence_limit - len(existing_items)),
            "precipitation_evidence_assets": [
                str(item.get("asset_id")) for item in existing_items
                if isinstance(item, dict) and str(item.get("product_id")) == "QL_PRECIP_6H"
            ],
        })
    return rows

# Original func/step04/pass1_engine.py:5956
def build_precipitation_peak_guidance(
    overview_data: dict[str, Any],
    *,
    minimum_regional_max_mm: float = 5.0,
) -> dict[str, Any]:
    """Build deterministic quantitative peak guidance from Step02 region series.

    The guidance is an anchor, not a candidate generator. The model still decides
    whether distinct peaks belong to one meteorological event after considering
    spatial continuity and the environmental mechanism.
    """
    timeline = _aggregate_precipitation_timeline(overview_data)
    primary_peak_hours = _local_peak_hours_from_timeline(timeline)
    group_ids = sorted(
        {
            str(row.get("overlap_group_id"))
            for row in _operational_precipitation_series(overview_data)
            if str(row.get("overlap_group_id", "")).strip()
        }
    )
    anchors: list[dict[str, Any]] = []
    for group_id in group_ids:
        group_timeline = _aggregate_precipitation_timeline(
            overview_data,
            overlap_group_id=group_id,
        )
        group_peaks = _local_peak_hours_from_timeline(group_timeline)
        row_by_hour = {int(row["forecast_hour"]): row for row in group_timeline}
        for hour in group_peaks:
            row = row_by_hour.get(hour)
            if row is None or float(row.get("max_mm", 0.0)) < float(minimum_regional_max_mm):
                continue
            anchors.append(
                {
                    "forecast_hour": hour,
                    "overlap_group_id": group_id,
                    "region_id": row.get("region_id"),
                    "region_name_ko": row.get("region_name_ko"),
                    "max_mm": row.get("max_mm"),
                    "p95_mm": row.get("p95_mm"),
                    "mean_mm": row.get("mean_mm"),
                    "max_location": row.get("max_location"),
                    "score": round(float(row.get("score", 0.0)), 3),
                }
            )
    # Keep the strongest anchor for one overlap group/hour and bound prompt size.
    unique: dict[tuple[int, str], dict[str, Any]] = {}
    for anchor in anchors:
        key = (int(anchor["forecast_hour"]), str(anchor["overlap_group_id"]))
        if key not in unique or float(anchor["score"]) > float(unique[key]["score"]):
            unique[key] = anchor
    bounded = sorted(unique.values(), key=lambda row: (-float(row["score"]), int(row["forecast_hour"])))[:36]
    bounded.sort(key=lambda row: (int(row["forecast_hour"]), str(row["overlap_group_id"])))
    return {
        "source": "step02.operational_region_timeseries.tp_6h_increment_mm",
        "metric": "0.40*regional_max + 0.45*regional_p95 + 0.15*regional_mean",
        "regional_maximum_6h_anchor_mm": float(minimum_regional_max_mm),
        "primary_peak_hours": primary_peak_hours,
        "operational_timeline": [
            {
                key: row.get(key)
                for key in (
                    "forecast_hour", "max_mm", "p95_mm", "mean_mm", "region_id",
                    "region_name_ko", "max_location", "score"
                )
            }
            for row in timeline
        ],
        "regional_peak_anchors": bounded,
        "usage_rules": [
            "정량 강수 peak는 direct_precipitation 및 mixed 후보의 peak 판단을 위한 참고 anchor이며 최종 peak_forecast_hours는 AI가 영상·정량 근거를 함께 해석해 판단합니다.",
            "overview 그룹의 첫 시각을 자동으로 peak로 선택하지 않습니다.",
            "정량 peak와 AI 판단 peak가 다르거나 여러 peak가 시간적으로 분리되어도 Python은 후보를 자동 merge/split하지 않으며 후속 상세분석에서 재검토할 QC 참고정보로 사용합니다.",
            "precursor_environment 후보의 peak_forecast_hours는 강수 peak가 아니라 선행환경 중첩이 가장 강한 대표시각입니다.",
        ],
    }

# Original func/step04/pass1_engine.py:7789
def _unique_nonempty_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result

# Original func/step04/pass1_engine.py:7801
def _truncate_utf8_text(value: str, *, max_bytes: int) -> str:
    """Trim a narrative string without splitting a UTF-8 code point."""
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    suffix = "..."
    limit = max(0, max_bytes - len(suffix.encode("utf-8")))
    return encoded[:limit].decode("utf-8", errors="ignore").rstrip() + suffix

# Original func/step04/pass1_engine.py:8311
def _minimal_support_catalog(support_catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "supported_request_types": support_catalog.get("supported_request_types", []),
        "spatial_field_products": [
            {
                "product_id": item.get("product_id"),
                "allowed_forecast_hours": item.get("allowed_forecast_hours", []),
                "signal_components": item.get("signal_components", []),
            }
            for item in support_catalog.get("spatial_field_products", [])
            if isinstance(item, dict)
        ],
        "quantitative_excerpt_support": {
            key: value
            for key, value in support_catalog.get("quantitative_excerpt_support", {}).items()
            if key in {
                "data_type", "forecast_hours_required", "requested_domain_required",
                "supported_statistics", "supported_derived_outputs", "ranking_statistics", "maximum_top_n"
            }
        },
        "analysis_plan_support": support_catalog.get("analysis_plan_support", {}),
        "diagnostic_execution_support": support_catalog.get("diagnostic_execution_support", {}),
    }

# Original func/step04/pass1_engine.py:8336
def _minimal_feature_index(feature_catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "features": [
            {key: item.get(key) for key in ("feature_id", "name_ko", "units")}
            for item in feature_catalog.get("features", [])
            if isinstance(item, dict)
        ]
    }

# Original func/step04/pass1_engine.py:8346
def _minimal_operational_region_index(operational_region_catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "regions": [
            {
                key: item.get(key)
                for key in (
                    "region_id", "name_ko", "bbox", "region_role",
                    "eligible_for_risk_rank", "allowed_feature_ids"
                )
            }
            for item in operational_region_catalog.get("regions", [])
            if isinstance(item, dict) and item.get("enabled_in_pass1")
        ]
    }

# Original func/step04/pass1_engine.py:8362
def _candidate_discovery_operational_scope(overview_data: dict[str, Any]) -> dict[str, Any]:
    """Expose the exact Step04 candidate boundary policy to the discovery model.

    Candidate discovery previously received operational precipitation guidance but
    not the catalog geometry used by the semantic validator.  That made the model
    guess whether a remote precipitation area was an operational forecast target
    or only an upstream/source corridor.  Keep the policy deterministic and compact:
    direct/mixed candidates may overlap only Pass1 operational_land or marine_macro
    regions; source_corridor remains contextual/precursor evidence.
    """
    catalog = overview_data.get("operational_region_catalog", {}) if isinstance(overview_data, dict) else {}
    regions = catalog.get("regions", []) if isinstance(catalog, dict) else []

    eligible: list[dict[str, Any]] = []
    context_only: list[dict[str, Any]] = []
    for region in regions:
        if not isinstance(region, dict) or not bool(region.get("enabled_in_pass1")):
            continue
        if str(region.get("stage")) != "pass1":
            continue
        raw_bbox = region.get("bbox")
        bbox = _region_bbox_for_peak_validation(raw_bbox)
        if bbox is None:
            continue
        row = {
            "region_id": str(region.get("region_id") or ""),
            "name_ko": str(region.get("name_ko") or ""),
            "region_role": str(region.get("region_role") or ""),
            "bbox": bbox,
        }
        if row["region_role"] in {"operational_land", "marine_macro"}:
            eligible.append(row)
        elif row["region_role"] == "source_corridor":
            context_only.append(row)

    return {
        "contract_version": CANDIDATE_DISCOVERY_OPERATIONAL_SCOPE_CONTRACT_VERSION,
        "eligible_forecast_regions": sorted(eligible, key=lambda item: item["region_id"]),
        "context_only_source_corridors": sorted(context_only, key=lambda item: item["region_id"]),
        "rules": [
            "direct_precipitation 또는 mixed 후보의 candidate_area는 eligible_forecast_regions 중 하나 이상과 실제로 겹쳐야 합니다.",
            "source_corridor에만 존재하는 강수는 독립 direct/mixed 후보로 만들지 않고 주변 맥락 또는 선행환경 근거로 유지합니다.",
            "운영권역과 겹치게 만들기 위해 실제 강수 핵심영역의 candidate_area를 임의로 확대하거나 이동하지 않습니다.",
        ],
    }

# Original func/step04/pass1_engine.py:8550
def _candidate_operational_region_overlap(
    *,
    candidate: dict[str, Any],
    operational_region_catalog: dict[str, Any],
) -> dict[str, Any]:
    """Expose candidate-specific operational-region execution availability.

    Regional quantitative diagnostics are only executable when the immutable
    candidate bbox overlaps an enabled operational_land/marine_macro region.
    The model previously received the whole catalog and had to infer this
    intersection itself, which is both unnecessary and a source of avoidable
    OPERATIONAL_REGION_TARGET_EMPTY failures.
    """
    bbox = _normalized_bbox_for_validation(candidate.get("candidate_area"))
    selected: list[dict[str, Any]] = []
    if bbox is not None:
        selected = _selectable_candidate_operational_regions(
            candidate_bbox=bbox,
            operational_region_catalog=operational_region_catalog,
        )
    compact_rows: list[dict[str, Any]] = []
    for region in selected:
        region_bbox = _region_bbox_for_peak_validation(region.get("bbox"))
        compact_rows.append(
            {
                "region_id": str(region.get("region_id") or ""),
                "name_ko": str(region.get("name_ko") or ""),
                "region_role": str(region.get("region_role") or ""),
                "bbox": region_bbox,
            }
        )
    compact_rows.sort(key=lambda item: item["region_id"])
    regional_available = bool(compact_rows)
    allowed_modes = [
        mode
        for mode in VERIFICATION_MODES
        if regional_available or mode not in REGIONAL_VERIFICATION_MODES
    ]
    return {
        "candidate_area": bbox,
        "selectable_regions": compact_rows,
        "regional_verification_available": regional_available,
        "allowed_verification_modes": allowed_modes,
        "forbidden_verification_modes": sorted(
            REGIONAL_VERIFICATION_MODES if not regional_available else set()
        ),
        "rule": (
            "regional_intensity/temporal_change/precipitation_amount may be used only "
            "when regional_verification_available=true. Never expand or move candidate_area "
            "to create an operational-region overlap."
        ),
    }

# Original func/step04/pass1_engine.py:9974
def _candidate_discovery_asset_index(
    attachment_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Return only asset metadata required during candidate discovery."""
    fields = ("asset_id", "product_id", "forecast_hours")
    return {
        "asset_count": int(attachment_manifest.get("attachment_count", 0)),
        "assets": [
            {field: item.get(field) for field in fields}
            for item in attachment_manifest.get("attachments", [])
            if isinstance(item, dict)
        ],
    }

# Original func/step04/pass1_engine.py:9989
def _candidate_discovery_group_summary_projection(
    group_summaries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build a transport-only projection for candidate discovery.

    Canonical group-summary files and downstream validation continue to use the
    original validated summaries.  This projection only removes repeated
    provenance fields and bounds narrative text before the provider request.
    """
    projected: list[dict[str, Any]] = []
    for summary in group_summaries:
        if not isinstance(summary, dict):
            continue

        rows: list[dict[str, Any]] = []
        for row in summary.get("integrated_observations", []):
            if not isinstance(row, dict):
                continue
            direct_signal = _normalize_direct_precipitation_signal(
                row.get("direct_precipitation_signal")
            )
            direct_signal["rationale"] = _truncate_utf8_text(
                str(direct_signal.get("rationale", "")), max_bytes=120
            )
            rows.append(
                {
                    "asset_id": str(row.get("asset_id", "")),
                    "product_id": str(row.get("product_id", "")),
                    "forecast_hours": [int(value) for value in row.get("forecast_hours", [])],
                    "observed_signals": [
                        _truncate_utf8_text(value, max_bytes=96)
                        for value in _unique_nonempty_strings(row.get("observed_signals", []))[:2]
                    ],
                    "spatial_pattern": _truncate_utf8_text(
                        str(row.get("spatial_pattern", "")), max_bytes=160
                    ),
                    "temporal_evolution": _truncate_utf8_text(
                        str(row.get("temporal_evolution", "")), max_bytes=160
                    ),
                    "confidence": str(row.get("confidence", "medium")),
                    "limitations": [
                        _truncate_utf8_text(value, max_bytes=96)
                        for value in _unique_nonempty_strings(row.get("limitations", []))[:1]
                    ],
                    "direct_precipitation_signal": direct_signal,
                }
            )

        audit = summary.get("direct_precipitation_audit")
        if isinstance(audit, dict):
            audit_projection = {
                "status": audit.get("status"),
                "source_asset_ids": list(audit.get("source_asset_ids", [])),
                "significant_forecast_hours": [
                    int(value) for value in audit.get("significant_forecast_hours", [])
                ],
                "rationale": _truncate_utf8_text(
                    str(audit.get("rationale", "")), max_bytes=160
                ),
            }
        else:
            audit_projection = _direct_precipitation_audit_from_rows(rows)

        item = {
            "summary_id": summary.get("summary_id"),
            "analysis_tier": summary.get("analysis_tier"),
            "group_id": summary.get("group_id"),
            "forecast_hours": [int(value) for value in summary.get("forecast_hours", [])],
            "direct_precipitation_audit": audit_projection,
            "integrated_observations": rows,
            "cross_product_relationships": [
                _truncate_utf8_text(value, max_bytes=160)
                for value in _unique_nonempty_strings(
                    summary.get("cross_product_relationships", [])
                )[:1]
            ],
            "uncertainty": [
                _truncate_utf8_text(value, max_bytes=120)
                for value in _unique_nonempty_strings(summary.get("uncertainty", []))[:1]
            ],
            "omitted_or_unresolved_signals": [
                _truncate_utf8_text(value, max_bytes=120)
                for value in _unique_nonempty_strings(
                    summary.get("omitted_or_unresolved_signals", [])
                )[:1]
            ],
        }

        if json_utf8_bytes(item) > CANDIDATE_DISCOVERY_GROUP_SUMMARY_COMPACTION_TRIGGER_BYTES:
            for row in rows:
                row["observed_signals"] = [
                    _truncate_utf8_text(value, max_bytes=72)
                    for value in row.get("observed_signals", [])[:2]
                ]
                row["spatial_pattern"] = _truncate_utf8_text(
                    str(row.get("spatial_pattern", "")), max_bytes=112
                )
                row["temporal_evolution"] = _truncate_utf8_text(
                    str(row.get("temporal_evolution", "")), max_bytes=112
                )
                row["limitations"] = [
                    _truncate_utf8_text(value, max_bytes=72)
                    for value in row.get("limitations", [])[:1]
                ]
                signal = row.get("direct_precipitation_signal")
                if isinstance(signal, dict):
                    signal["rationale"] = _truncate_utf8_text(
                        str(signal.get("rationale", "")), max_bytes=96
                    )
            item["cross_product_relationships"] = [
                _truncate_utf8_text(value, max_bytes=112)
                for value in item.get("cross_product_relationships", [])[:1]
            ]
            item["uncertainty"] = [
                _truncate_utf8_text(value, max_bytes=96)
                for value in item.get("uncertainty", [])[:1]
            ]
            item["omitted_or_unresolved_signals"] = [
                _truncate_utf8_text(value, max_bytes=96)
                for value in item.get("omitted_or_unresolved_signals", [])[:1]
            ]

        projected.append(item)
    return projected

# Original func/step04/pass1_engine.py:10265
def _related_group_summaries(
    candidate: dict[str, Any],
    group_summaries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    related_assets = {str(value) for value in candidate.get("related_overview_asset_ids", [])}
    selected = [
        summary
        for summary in group_summaries
        if related_assets & {str(value) for value in summary.get("source_asset_ids", [])}
    ]
    if not selected:
        raise Pass1Error(
            f"candidate has no related group summaries candidate_id={candidate.get('candidate_id')}"
        )
    return selected

# Original func/step04/pass1_engine.py:11357
def _compact_plan_rows(value: dict[str, Any], name: str) -> list[dict[str, Any]]:
    rows = value.get(name)
    if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
        raise ValueError(f"compact candidate plan {name} must be an object array")
    return rows

# Original func/step04/pass1_engine.py:11393
def _compact_phase_hours(row: dict[str, Any]) -> tuple[int, ...]:
    raw = row.get("forecast_hours", [])
    if not isinstance(raw, list):
        raise ValueError("compact analysis phase forecast_hours must be an array")
    try:
        hours = tuple(sorted({int(value) for value in raw}))
    except (TypeError, ValueError) as exc:
        raise ValueError("compact analysis phase forecast_hours must contain integers") from exc
    if not hours:
        raise ValueError("compact analysis phase requires forecast_hours")
    return hours

# Original func/step04/pass1_engine.py:11741
def _compact_phase_key_aliases(chunks: list[dict[str, Any]]) -> dict[str, str]:
    """Map only exact phase restatements to the first canonical phase key.

    The same phase type may legitimately occur more than once when forecast-hour
    windows are temporally distinct.  Only an identical ``phase_type`` and
    ``forecast_hours`` combination is treated as a continuation alias.
    """
    canonical_by_signature: dict[tuple[str, tuple[int, ...]], str] = {}
    phase_signature_by_key: dict[str, tuple[str, tuple[int, ...]]] = {}
    aliases: dict[str, str] = {}
    for chunk in chunks:
        for row in _compact_plan_rows(chunk, "analysis_phases"):
            phase_key = str(row.get("phase_key", "")).strip()
            if not phase_key:
                continue
            signature = _compact_phase_signature(row)
            previous_signature = phase_signature_by_key.setdefault(phase_key, signature)
            if previous_signature != signature:
                raise ValueError(
                    "compact candidate plan reuses phase_key with a different phase definition "
                    f"phase_key={phase_key!r} previous={previous_signature!r} current={signature!r}"
                )
            canonical_key = canonical_by_signature.setdefault(signature, phase_key)
            aliases[phase_key] = canonical_key
    return aliases

# Original func/step04/pass1_engine.py:11767
def _compact_check_combinations_with_aliases(
    rows: list[dict[str, Any]],
    phase_aliases: dict[str, str],
) -> set[tuple[str, str, str, str]]:
    """Return combinations after continuation phase aliases are canonicalized."""
    combinations: set[tuple[str, str, str, str]] = set()
    for diagnostic_id, variable_role, domain_scope, phase_key in _compact_check_combinations(rows):
        combinations.add(
            (
                diagnostic_id,
                variable_role,
                domain_scope,
                phase_aliases.get(phase_key, phase_key),
            )
        )
    return combinations

# Original func/step04/pass1_engine.py:14266
def _candidate_relevant_coverage_roles(candidate: dict[str, Any]) -> set[str]:
    relevant = {"hazard_magnitude", "temporal_evolution", "spatial_evolution", "supporting_evidence"}
    status = str(candidate.get("precipitation_signal_status", ""))
    if status in {"present", "weak", "uncertain"}:
        relevant.add("hazard_magnitude")
    category_map = {
        "moisture_supply": "moisture_supply",
        "low_level_forcing": "low_level_forcing",
        "mid_upper_forcing": "mid_level_forcing",
        "thermodynamic_support": "instability_or_stability",
        "organization_persistence": "forecast_uncertainty",
    }
    relevant.update(
        category_map[str(item)]
        for item in candidate.get("precursor_signal_categories", [])
        if str(item) in category_map
    )
    if candidate.get("opposing_reasons"):
        relevant.add("opposing_evidence")
    if candidate.get("uncertainties") or str(candidate.get("confidence")) in {"low", "medium"}:
        relevant.add("forecast_uncertainty")
    return relevant

# Original func/step04/pass1_engine.py:14290
def evaluate_compact_candidate_plan_coverage(
    chunks: list[dict[str, Any]],
    *,
    candidate: dict[str, Any],
) -> dict[str, str]:
    """Evaluate coverage from normalized diagnostic checks, not model completion claims."""
    covered: set[str] = set()
    for chunk in chunks:
        for check in _compact_plan_rows(chunk, "diagnostic_checks"):
            covered.update(_effective_compact_check_coverage_roles(check))

    # Every candidate receives a mandatory system-owned precipitation-presence
    # Check after the AI plan is accepted. Compact-plan completion must not depend
    # on the LLM reproducing that system invariant, including precursor candidates.
    if str(candidate.get("detection_basis")) in {
        "direct_precipitation",
        "precursor_environment",
        "mixed",
    }:
        covered.add("hazard_magnitude")

    required_roles = _candidate_relevant_coverage_roles(candidate)
    result: dict[str, str] = {}
    for role in COMPACT_COVERAGE_ROLES:
        if role in covered:
            result[role] = "covered"
        elif role in required_roles:
            result[role] = "missing_but_relevant"
        else:
            result[role] = "unknown"
    return result

# Original func/step04/pass1_engine.py:14458
def _available_candidate_forecast_hours(
    candidate: dict[str, Any],
    attachment_manifest: dict[str, Any],
) -> list[int]:
    return list(_candidate_temporal_feasibility_contract(candidate, attachment_manifest)["effective_hours"])

# Original func/step04/pass1_engine.py:14465
def _compact_candidate_phase_hours(
    candidate: dict[str, Any],
    attachment_manifest: dict[str, Any],
) -> dict[str, list[int]]:
    """Return only phase types that have concrete forecast-hour coverage."""
    available_hours = _available_candidate_forecast_hours(candidate, attachment_manifest)
    peak_hours = sorted({int(item) for item in candidate.get("peak_forecast_hours", [])})
    if not available_hours or not peak_hours:
        return {}
    phase_hours = {
        "peak": peak_hours,
        "pre_peak": [hour for hour in available_hours if hour < min(peak_hours)],
        "post_peak": [hour for hour in available_hours if hour > max(peak_hours)],
        "context": available_hours,
    }
    return {
        phase_type: hours
        for phase_type, hours in phase_hours.items()
        if hours
    }

# Original func/step04/pass1_engine.py:15643
def _capability_profile_for_check(
    support_catalog: dict[str, Any],
    *,
    diagnostic_id: str,
    product_id: str,
) -> dict[str, Any]:
    contract = support_catalog.get("product_capability_contract", {})
    if not isinstance(contract, dict):
        return {}
    diagnostics = contract.get("diagnostics", {})
    diagnostic = diagnostics.get(diagnostic_id, {}) if isinstance(diagnostics, dict) else {}
    profiles = diagnostic.get("profiles", {}) if isinstance(diagnostic, dict) else {}
    profile = profiles.get(product_id, {}) if isinstance(profiles, dict) else {}
    if not isinstance(profile, dict):
        return {}
    resolved = copy.deepcopy(profile)
    resolved["registry_hash"] = contract.get("contract_hash")
    return resolved

# Original func/step04/pass1_engine.py:18070
def ensure_hierarchical_payload_budget(diagnostics: dict[str, int]) -> None:
    actual = diagnostics["http_json_utf8_bytes"]
    if actual > HIERARCHICAL_FINAL_MAX_BYTES:
        raise Pass1Error(
            f"hierarchical final payload exceeds hard limit {HIERARCHICAL_FINAL_MAX_BYTES} UTF-8 bytes: "
            + json.dumps(diagnostics, ensure_ascii=True, sort_keys=True)
        )
    if actual > HIERARCHICAL_PAYLOAD_WARN_BYTES:
        logging.warning(
            "hierarchical final payload exceeds warning threshold %s UTF-8 bytes "
            "but is within hard limit %s; continuing: %s",
            HIERARCHICAL_PAYLOAD_WARN_BYTES,
            HIERARCHICAL_FINAL_MAX_BYTES,
            json.dumps(diagnostics, ensure_ascii=True, sort_keys=True),
        )
