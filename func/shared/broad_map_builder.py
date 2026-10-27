"""AI Pass 1 broad-map product builder.

This module borrows the product-oriented quicklook approach used by the previous
render_quicklook_images.py flow. It does not hard-code plotting into step03;
step03 supplies field loaders, and this module dispatches product rendering to
func.shared.map_renderer.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .map_renderer import (
    RenderContext,
    convert_geopotential_height,
    domain_from_config,
    render_shaded_field,
    render_shaded_plus_contour,
    render_shaded_plus_vector,
    render_contour_plus_vector,
    render_wind_speed_field,
)
from .broad_map_style import resolve_render_style_config

SAFE_ID_RE = re.compile(r'[^A-Za-z0-9]+')


def _safe_id_part(value: Any) -> str:
    text = SAFE_ID_RE.sub('_', str(value).strip().upper())
    return text.strip('_') or 'NA'


def _signal_rank(signal_level: str) -> int:
    return {'high': 3, 'medium': 2, 'low': 1, 'none': 0}.get(str(signal_level).lower(), 0)


def _p95(summary_item: dict[str, Any]) -> float:
    stats = summary_item.get('statistics', {})
    try:
        return float(stats.get('p95'))
    except (TypeError, ValueError):
        return float('-inf')


def _valid_time_utc_from_init(init: str, lead: int) -> str | None:
    try:
        base = datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=timezone.utc)
        return (base + timedelta(hours=int(lead))).isoformat(timespec="seconds")
    except Exception:
        return None


def _parse_utc_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _format_utc_minute(dt: datetime | None) -> str:
    """Format an internal UTC contract timestamp without changing its timezone."""
    if dt is None:
        return "NA"
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _format_kst_minute(dt: datetime | None) -> str:
    """Format a forecaster-facing KST timestamp for image presentation only."""
    if dt is None:
        return "NA"
    return dt.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")


def _title_context(*, init: str, lead: int, valid_time_utc: str | None) -> dict[str, Any]:
    """Build display-time context for map_renderer.

    Time is calculated in the builder from model initial time and lead time.
    map_renderer must not parse lead/valid time from a title string.
    """
    init_dt = _parse_utc_datetime(
        datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    )
    valid_dt = _parse_utc_datetime(valid_time_utc) or (init_dt + timedelta(hours=int(lead)) if init_dt is not None else None)

    return {
        "lead_hour": int(lead),
        "lead_text": f"+{int(lead):03d}h",
        "init": init,
        # Internal contract/provenance stays UTC.  KST is presentation-only.
        "init_time_utc": _format_utc_minute(init_dt),
        "valid_time_utc": _format_utc_minute(valid_dt),
        "init_time_kst": _format_kst_minute(init_dt),
        "valid_time_kst": _format_kst_minute(valid_dt),
    }



def _parse_int_list(raw: Any) -> list[int]:
    if raw is None:
        return []
    if isinstance(raw, int):
        return [int(raw)]
    if isinstance(raw, str):
        return [int(item.strip()) for item in raw.split(",") if item.strip()]
    if isinstance(raw, list):
        return [int(item) for item in raw]
    return []


def _unique_sorted(values: list[int]) -> list[int]:
    return sorted(dict.fromkeys(int(v) for v in values))


def _trigger_feature_ids(product: dict[str, Any]) -> list[str]:
    raw = product.get("trigger_feature_ids")
    values: list[str] = []
    if isinstance(raw, list):
        values.extend(str(item) for item in raw if str(item).strip())
    elif isinstance(raw, str) and raw.strip():
        values.extend(item.strip() for item in raw.split(",") if item.strip())
    elif product.get("trigger_feature_id"):
        values.append(str(product.get("trigger_feature_id")))
    # preserve order while deduplicating
    return list(dict.fromkeys(values))


def _configured_anchor_leads(product: dict[str, Any], broad_map_config: dict[str, Any]) -> list[int]:
    generation = broad_map_config.get("broad_map_generation", {})
    raw = product.get("lead_hours")
    if raw is None:
        raw = generation.get("anchor_lead_hours", broad_map_config.get("default_lead_hours", []))
    return _unique_sorted(_parse_int_list(raw))


def _trigger_policy(broad_map_config: dict[str, Any]) -> dict[str, Any]:
    generation = broad_map_config.get("broad_map_generation", {})
    policy = generation.get("triggered_lead_policy", {})
    if not isinstance(policy, dict):
        policy = {}
    return policy


def _signal_levels_from_policy(policy: dict[str, Any]) -> set[str]:
    raw = policy.get("include_signal_levels", ["high", "medium"])
    values = [str(item).lower() for item in raw] if isinstance(raw, list) else [str(raw).lower()]
    return {item for item in values if item}


def _matching_summary(
    summaries: list[dict[str, Any]],
    trigger_feature_ids: list[str],
    lead: int,
    signal_levels: set[str] | None = None,
) -> dict[str, Any] | None:
    if not trigger_feature_ids:
        return None
    candidates = [
        item for item in summaries
        if isinstance(item, dict)
        and str(item.get("feature_id")) in trigger_feature_ids
        and int(item.get("forecast_hour", -9999)) == int(lead)
        and (signal_levels is None or str(item.get("signal_level", "none")).lower() in signal_levels)
    ]
    if not candidates:
        return None
    ranked = sorted(
        candidates,
        key=lambda item: (
            -_signal_rank(str(item.get("signal_level", "none"))),
            -_p95(item),
            str(item.get("domain_id", "")),
        ),
    )
    return ranked[0]


def _bounded_neighbor_leads(base_lead: int, policy: dict[str, Any]) -> list[int]:
    offsets = _parse_int_list(policy.get("include_neighbor_hours", [0]))
    if 0 not in offsets:
        offsets.append(0)
    min_lead = int(policy.get("min_lead_hour", 0))
    max_lead = int(policy.get("max_lead_hour", 120))
    leads = [base_lead + offset for offset in offsets]
    return [lead for lead in _unique_sorted(leads) if min_lead <= lead <= max_lead]


def _pseudo_target(
    init: str,
    product: dict[str, Any],
    lead: int,
    linked: dict[str, Any] | None,
    *,
    selection_reason: str,
    trigger_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if linked:
        target = dict(linked)
    else:
        valid_time_utc = _valid_time_utc_from_init(init, lead)
        target = {
            "field_summary_id": None,
            "feature_id": product.get("trigger_feature_id"),
            "domain_id": "default_domain",
            "domain_name_ko": "기본 지도 영역",
            "forecast_hour": int(lead),
            "valid_time_utc": valid_time_utc,
            "signal_level": "configured",
            "statistics": {},
        }
    target["forecast_hour"] = int(lead)
    if not target.get("valid_time_utc"):
        target["valid_time_utc"] = _valid_time_utc_from_init(init, lead)
    target["selection_reason"] = selection_reason
    if trigger_source:
        target["trigger_source_field_summary_id"] = trigger_source.get("field_summary_id")
        target["trigger_source_feature_id"] = trigger_source.get("feature_id")
        target["trigger_source_forecast_hour"] = int(trigger_source.get("forecast_hour", lead))
        if not target.get("field_summary_id"):
            target["domain_id"] = trigger_source.get("domain_id", target.get("domain_id"))
            target["domain_name_ko"] = trigger_source.get("domain_name_ko", target.get("domain_name_ko"))
    return target


def select_product_targets(
    *,
    init: str,
    field_summaries_payload: dict[str, Any],
    product: dict[str, Any],
    broad_map_config: dict[str, Any],
    max_maps_per_product: int,
) -> list[dict[str, Any]]:
    """Select product rendering targets in hybrid mode.

    Hybrid selection order:
    1. Anchor lead-hours are always included.
    2. Additional lead-hours are added when configured trigger features show
       high/medium (or configured) signal levels in field summaries.
    3. Triggered lead-hours can include neighbor hours to preserve the temporal
       development around the detected signal.
    """

    generation = broad_map_config.get("broad_map_generation", {})
    if generation.get("mode") in {"scheduled_common_timeline", "scheduled_tiered_timeline"}:
        configured = product.get("lead_hours")
        if configured is None:
            configured = generation.get("lead_hours", generation.get("full_forecast_hours", []))
        leads = _unique_sorted(_parse_int_list(configured))
        return [
            _pseudo_target(
                init,
                product,
                lead,
                None,
                selection_reason=str(generation.get("mode", "scheduled_common_timeline")),
            )
            for lead in leads
        ]

    summaries_raw = field_summaries_payload.get("field_summaries", [])
    summaries = [item for item in summaries_raw if isinstance(item, dict)] if isinstance(summaries_raw, list) else []
    trigger_ids = _trigger_feature_ids(product)
    policy = _trigger_policy(broad_map_config)
    signal_levels = _signal_levels_from_policy(policy)
    anchor_leads = _configured_anchor_leads(product, broad_map_config)
    max_total = max(1, int(max_maps_per_product))
    max_triggered = int(policy.get("max_leads_per_product", max_total))

    selected: dict[int, dict[str, Any]] = {}

    # 1) Always include anchor leads.
    for lead in anchor_leads:
        if len(selected) >= max_total:
            break
        linked = _matching_summary(summaries, trigger_ids, int(lead), None)
        selected[int(lead)] = _pseudo_target(
            init,
            product,
            int(lead),
            linked,
            selection_reason="anchor",
        )

    # 2) Add triggered leads + neighbors.
    triggered_candidates = [
        item for item in summaries
        if str(item.get("feature_id")) in trigger_ids
        and str(item.get("signal_level", "none")).lower() in signal_levels
    ]
    ranked_candidates = sorted(
        triggered_candidates,
        key=lambda item: (
            -_signal_rank(str(item.get("signal_level", "none"))),
            -_p95(item),
            int(item.get("forecast_hour", 9999)),
            str(item.get("domain_id", "")),
        ),
    )

    triggered_added = 0
    for candidate in ranked_candidates:
        base_lead = int(candidate.get("forecast_hour", -9999))
        if base_lead < 0:
            continue
        for lead in _bounded_neighbor_leads(base_lead, policy):
            if lead in selected:
                # Promote selection reason if an anchor lead is also signal-driven.
                if selected[lead].get("selection_reason") == "anchor":
                    selected[lead]["selection_reason"] = "anchor+trigger"
                    selected[lead]["trigger_source_field_summary_id"] = candidate.get("field_summary_id")
                    selected[lead]["trigger_source_feature_id"] = candidate.get("feature_id")
                    selected[lead]["trigger_source_forecast_hour"] = base_lead
                continue
            if len(selected) >= max_total or triggered_added >= max_triggered:
                break
            exact = _matching_summary(summaries, trigger_ids, int(lead), None)
            selection_reason = "trigger" if int(lead) == base_lead else "trigger_neighbor"
            selected[int(lead)] = _pseudo_target(
                init,
                product,
                int(lead),
                exact,
                selection_reason=selection_reason,
                trigger_source=candidate,
            )
            triggered_added += 1
        if len(selected) >= max_total or triggered_added >= max_triggered:
            break

    return [selected[lead] for lead in sorted(selected.keys())]


def attach_broad_map_links(field_summaries_payload: dict[str, Any], broad_map_manifest: dict[str, Any]) -> dict[str, Any]:
    mapping: dict[str, list[str]] = defaultdict(list)
    for item in broad_map_manifest.get('broad_maps', []):
        broad_map_id = item.get('broad_map_id')
        for summary_id in item.get('linked_field_summary_ids', []):
            mapping[str(summary_id)].append(str(broad_map_id))
    payload = dict(field_summaries_payload)
    updated = []
    for item in payload.get('field_summaries', []):
        cloned = dict(item)
        cloned['linked_broad_map_ids'] = mapping.get(str(item.get('field_summary_id')), [])
        updated.append(cloned)
    payload['field_summaries'] = updated
    return payload


def _domain_bbox(domain: Any) -> dict[str, float]:
    return {
        "lon_min": float(domain.lon_min),
        "lon_max": float(domain.lon_max),
        "lat_min": float(domain.lat_min),
        "lat_max": float(domain.lat_max),
    }


def _render_domain_id(broad_map_config: dict[str, Any]) -> str:
    # Broad maps are rendered on one configured inspection domain.
    # The trigger/source domain that selected a lead is stored separately in
    # trigger_domain_id. It must not be used in the PNG filename because it can
    # make identical render extents look like different map domains.
    value = (
        broad_map_config.get("render_domain_id")
        or broad_map_config.get("default_domain_id")
        or "korea_surrounding"
    )
    return str(value)


def _render_domain_name_ko(broad_map_config: dict[str, Any]) -> str:
    return str(
        broad_map_config.get("render_domain_name_ko")
        or broad_map_config.get("default_domain_name_ko")
        or "한반도 주변 광역"
    )


def _resolve_product_render_domain(
    product: dict[str, Any], broad_map_config: dict[str, Any]
) -> tuple[str, str, Any, dict[str, float]]:
    """Resolve a deterministic render domain for one product.

    temporal_core products normally use the default Korea-surrounding domain,
    while synoptic_context products may use a wider upstream/source-tracking
    domain. The domain is selected only by fixed product configuration, never
    by a meteorological threshold or AI output.
    """

    default_id = _render_domain_id(broad_map_config)
    default_name = _render_domain_name_ko(broad_map_config)
    domain_id = str(product.get("render_domain_id") or default_id)

    render_domains = broad_map_config.get("render_domains", {})
    if render_domains is None:
        render_domains = {}
    if not isinstance(render_domains, dict):
        raise ValueError("render_domains must be a mapping")

    explicit = product.get("render_domain")
    if explicit is not None and not isinstance(explicit, dict):
        raise ValueError(f"{product.get('product_id')}: render_domain must be a mapping")

    domain_config: dict[str, Any]
    if isinstance(explicit, dict):
        domain_config = dict(explicit)
    elif domain_id in render_domains:
        configured = render_domains[domain_id]
        if not isinstance(configured, dict):
            raise ValueError(f"render_domains.{domain_id} must be a mapping")
        domain_config = dict(configured)
    else:
        domain_config = dict(
            broad_map_config.get(
                "default_domain",
                {"lon_min": 120, "lon_max": 135, "lat_min": 30, "lat_max": 43},
            )
        )

    domain = domain_from_config(domain_config)
    domain_name = str(
        product.get("render_domain_name_ko")
        or domain_config.get("name_ko")
        or (default_name if domain_id == default_id else domain_id)
    )
    return domain_id, domain_name, domain, _domain_bbox(domain)


def _broad_map_id(product_id: str, render_domain_id: str, forecast_hour: int, prefix: str = "BM") -> str:
    return f"{_safe_id_part(prefix)}_{_safe_id_part(product_id)}_{_safe_id_part(render_domain_id)}_F{int(forecast_hour):03d}"


def _load_raw_field(raw_field_loader: Callable[[str, int | None, int], tuple[Any | None, list[dict[str, Any]], list[str]]], spec: dict[str, Any], step: int):
    arr, missing, sources = raw_field_loader(str(spec['short_name']), spec.get('level'), step)
    conversion = spec.get('conversion')
    if arr is not None and conversion == 'geopotential_height_gpm':
        arr = convert_geopotential_height(arr)
    elif arr is not None and conversion == 'pressure_hpa':
        arr = arr / 100.0
    elif arr is not None and conversion == 'temperature_c':
        arr = arr - 273.15
    return arr, missing, sources


def build_broad_maps(
    *,
    init: str,
    root: Path,
    output_dir: Path,
    field_summaries_payload: dict[str, Any],
    broad_map_config: dict[str, Any],
    feature_array_loader: Callable[[str, int], tuple[Any | None, list[dict[str, Any]], list[str], Any | None]],
    raw_field_loader: Callable[[str, int | None, int], tuple[Any | None, list[dict[str, Any]], list[str]]],
    relpath_func: Callable[[Path], str],
    max_maps_per_product: int = 3,
    selected_products: set[str] | None = None,
) -> dict[str, Any]:
    broad_map_config, style_contract = resolve_render_style_config(root, broad_map_config)
    products = broad_map_config.get('products', [])
    if not isinstance(products, list):
        raise ValueError('broad_map_products.yaml must contain products list')
    products = [p for p in products if isinstance(p, dict) and p.get('enabled', True)]
    if selected_products:
        products = [p for p in products if p.get('product_id') in selected_products]

    default_domain = domain_from_config(broad_map_config.get('default_domain', {'lon_min': 120, 'lon_max': 135, 'lat_min': 30, 'lat_max': 43}))
    default_render_domain_id = _render_domain_id(broad_map_config)
    default_render_domain_name_ko = _render_domain_name_ko(broad_map_config)
    default_render_domain_bbox = _domain_bbox(default_domain)
    default_style = broad_map_config.get('default_style', {})
    # This renderer is reserved for optional Rule-QA products. Pass-1 discovery
    # panels are declared separately and rendered directly into overview images.
    output_subdir = str(broad_map_config.get('output_subdir') or 'broad_maps')
    asset_prefix = str(broad_map_config.get('asset_id_prefix') or 'BM')
    output_dir = output_dir / output_subdir
    output_dir.mkdir(parents=True, exist_ok=True)
    for old_png in output_dir.glob(f'{_safe_id_part(asset_prefix)}_*.png'):
        old_png.unlink()

    items: list[dict[str, Any]] = []
    for product in products:
        product_render_domain_id, product_render_domain_name_ko, product_domain, product_domain_bbox = _resolve_product_render_domain(
            product, broad_map_config
        )
        product_context = RenderContext(project_root=root, domain=product_domain, style=default_style)
        targets = select_product_targets(
            init=init,
            field_summaries_payload=field_summaries_payload,
            product=product,
            broad_map_config=broad_map_config,
            max_maps_per_product=max_maps_per_product,
        )
        for summary in targets:
            lead = int(summary['forecast_hour'])
            product_id = str(product['product_id'])
            broad_map_id = _broad_map_id(product_id, product_render_domain_id, lead, asset_prefix)
            path = output_dir / f'{broad_map_id}.png'
            valid_time_utc = summary.get('valid_time_utc') or _valid_time_utc_from_init(init, lead)
            title_context = _title_context(init=init, lead=lead, valid_time_utc=valid_time_utc)
            title = str(product.get('display_title') or product.get('title') or product_id)
            plot_type = product.get('plot_type')
            fields_meta: list[dict[str, Any]] = []
            missing_all: list[dict[str, Any]] = []
            sources_all: list[str] = []
            try:
                if plot_type == 'shaded_feature':
                    arr, missing, sources, _spec = feature_array_loader(str(product['shaded_feature_id']), lead)
                    missing_all.extend(missing); sources_all.extend(sources)
                    if arr is None: continue
                    result = render_shaded_field(arr, title, product.get('unit_label', ''), product_domain, product_context, path, product, title_context=title_context)
                elif plot_type == 'shaded_feature_plus_raw_vector':
                    arr, missing, sources, _spec = feature_array_loader(str(product['shaded_feature_id']), lead)
                    missing_all.extend(missing); sources_all.extend(sources)
                    vf = product['vector_field']
                    u, m1, s1 = raw_field_loader(str(vf.get('u_short_name', 'u')), vf.get('level'), lead)
                    v, m2, s2 = raw_field_loader(str(vf.get('v_short_name', 'v')), vf.get('level'), lead)
                    missing_all.extend(m1 + m2); sources_all.extend(s1 + s2)
                    if arr is None or u is None or v is None: continue
                    result = render_shaded_plus_vector(arr, u, v, title, product.get('unit_label', ''), product.get('vector_unit_label', '10 m s⁻¹'), product_domain, product_context, path, product, title_context=title_context)
                elif plot_type == 'raw_shaded_plus_raw_contour':
                    shaded, m1, s1 = _load_raw_field(raw_field_loader, product['shaded_field'], lead)
                    contour, m2, s2 = _load_raw_field(raw_field_loader, product['contour_field'], lead)
                    missing_all.extend(m1 + m2); sources_all.extend(s1 + s2)
                    if shaded is None or contour is None: continue
                    result = render_shaded_plus_contour(shaded, contour, title, product.get('unit_label', ''), product.get('contour_label', ''), product_domain, product_context, path, product, title_context=title_context)
                elif plot_type == 'raw_wind_speed_shaded':
                    vf = product['vector_field']
                    u, m1, s1 = raw_field_loader(str(vf.get('u_short_name', 'u')), vf.get('level'), lead)
                    v, m2, s2 = raw_field_loader(str(vf.get('v_short_name', 'v')), vf.get('level'), lead)
                    missing_all.extend(m1 + m2); sources_all.extend(s1 + s2)
                    if u is None or v is None: continue
                    result = render_wind_speed_field(u, v, title, product.get('unit_label', ''), product_domain, product_context, path, product, title_context=title_context)
                elif plot_type == 'raw_wind_speed_plus_raw_contour':
                    vf = product['vector_field']
                    u, m1, s1 = raw_field_loader(str(vf.get('u_short_name', 'u')), vf.get('level'), lead)
                    v, m2, s2 = raw_field_loader(str(vf.get('v_short_name', 'v')), vf.get('level'), lead)
                    contour, m3, s3 = _load_raw_field(raw_field_loader, product['contour_field'], lead)
                    missing_all.extend(m1 + m2 + m3); sources_all.extend(s1 + s2 + s3)
                    if u is None or v is None or contour is None: continue
                    speed = (u ** 2 + v ** 2) ** 0.5
                    result = render_shaded_plus_contour(speed, contour, title, product.get('unit_label', ''), product.get('contour_label', ''), product_domain, product_context, path, product, title_context=title_context)
                elif plot_type == 'raw_contour_plus_raw_vector':
                    contour, m1, s1 = _load_raw_field(raw_field_loader, product['contour_field'], lead)
                    vector = product['vector_field']
                    u, m2, s2 = raw_field_loader(str(vector.get('u_short_name', 'u')), vector.get('level'), lead)
                    v, m3, s3 = raw_field_loader(str(vector.get('v_short_name', 'v')), vector.get('level'), lead)
                    missing_all.extend(m1 + m2 + m3); sources_all.extend(s1 + s2 + s3)
                    if contour is None or u is None or v is None: continue
                    result = render_contour_plus_vector(
                        contour, u, v, title, product.get('contour_label', ''),
                        product.get('vector_unit_label', '10 m s⁻¹'), product_domain,
                        product_context, path, product, title_context=title_context,
                    )
                else:
                    continue
            except Exception as exc:
                items.append({
                    'broad_map_id': broad_map_id,
                    'product_id': product_id,
                    'status': 'not_available',
                    'reason': f'render_failed: {exc}',
                    'forecast_hour': lead,
                    'render_domain_id': product_render_domain_id,
                    'render_domain_name_ko': product_render_domain_name_ko,
                    'render_domain_bbox': product_domain_bbox,
                    'trigger_domain_id': summary.get('domain_id'),
                    'trigger_domain_name_ko': summary.get('domain_name_ko'),
                    'selection_reason': summary.get('selection_reason'),
                    'trigger_source_field_summary_id': summary.get('trigger_source_field_summary_id'),
                    'trigger_source_feature_id': summary.get('trigger_source_feature_id'),
                    'trigger_source_forecast_hour': summary.get('trigger_source_forecast_hour'),
                    'linked_field_summary_ids': [summary.get('field_summary_id')] if summary.get('field_summary_id') else [],
                })
                continue

            map_background = result.get('map_background', {})
            items.append({
                'broad_map_id': broad_map_id,
                'asset_id': broad_map_id,
                'product_id': product_id,
                'product_tier': str(product.get('product_tier', 'temporal_core')),
                'analysis_tier': str(product.get('product_tier', 'temporal_core')),
                'required_for_pass1': bool(product.get('required_for_pass1', product.get('product_tier') != 'supplementary')),
                'cadence_hours': int(product.get('cadence_hours')) if product.get('cadence_hours') is not None else None,
                'configured_lead_hours': _unique_sorted(_parse_int_list(product.get('lead_hours'))),
                'style_profile_id': str(product.get('style_profile_id', product_id)),
                'status': 'created',
                'plot_type': plot_type,
                'image_path': relpath_func(path),
                'title': title,
                'title_context': title_context,
                'forecast_hour': lead,
                'valid_time_utc': valid_time_utc,
                # Actual PNG render domain. This is intentionally fixed for
                # broad maps and matches the domain token in broad_map_id.
                'domain_id': product_render_domain_id,
                'domain_name_ko': product_render_domain_name_ko,
                'render_domain_id': product_render_domain_id,
                'render_domain_name_ko': product_render_domain_name_ko,
                'render_domain_bbox': product_domain_bbox,
                'domain_bbox': product_domain_bbox,
                # Domain/summary that selected this lead. This is provenance,
                # not the rendered map extent.
                'trigger_domain_id': summary.get('domain_id'),
                'trigger_domain_name_ko': summary.get('domain_name_ko'),
                'trigger_feature_id': product.get('trigger_feature_id'),
                'trigger_feature_ids': _trigger_feature_ids(product),
                'selection_reason': summary.get('selection_reason'),
                'trigger_source_field_summary_id': summary.get('trigger_source_field_summary_id'),
                'trigger_source_feature_id': summary.get('trigger_source_feature_id'),
                'trigger_source_forecast_hour': summary.get('trigger_source_forecast_hour'),
                'linked_field_summary_ids': [summary.get('field_summary_id')] if summary.get('field_summary_id') else [],
                'map_background_status': map_background.get('map_background_status'),
                'coastline_drawn': map_background.get('coastline_drawn'),
                'coastline_source_path': map_background.get('coastline_source_path'),
                'coastline_source_type': map_background.get('coastline_source_type'),
                'borders_drawn': map_background.get('borders_drawn'),
                'borders_source_path': map_background.get('borders_source_path'),
                'render_method': result.get('render_method'),
                'rendering_policy': result.get('rendering_policy'),
                'projection_type': result.get('projection_type'),
                'coordinate_reference': result.get('coordinate_reference'),
                'colorbar_levels': result.get('colorbar_levels'),
                'colorbar_ticks': result.get('colorbar_ticks'),
                'colorbar_colors': result.get('colorbar_colors'),
                'colorbar_extend': result.get('colorbar_extend'),
                'extend': result.get('colorbar_extend'),
                'colorbar_orientation': result.get('colorbar_orientation'),
                'colorbar_style': result.get('colorbar_style'),
                'figure_width_in': result.get('figure_width_in'),
                'figure_height_in': result.get('figure_height_in'),
                'dpi': result.get('dpi'),
                'image_width_px': result.get('image_width_px'),
                'image_height_px': result.get('image_height_px'),
                'title_info_lines': result.get('title_info_lines', []),
                'variable_description': result.get('title_info_lines', [])[1:] if len(result.get('title_info_lines', [])) > 1 else [],
                'full_title': result.get('full_title'),
                'auto_range_used': result.get('auto_range_used', False),
                'contour_levels': result.get('contour_levels'),
                'contour_label': result.get('contour_label'),
                'wind_stride': result.get('wind_stride'),
                'resolved_wind_stride': result.get('resolved_wind_stride'),
                'quiver_scale': result.get('quiver_scale'),
                'vector_rendering_mode': result.get('vector_rendering_mode'),
                'quiver_calm_threshold_ms': result.get('quiver_calm_threshold_ms'),
                'quiver_min_display_speed_ms': result.get('quiver_min_display_speed_ms'),
                'quiver_max_display_speed_ms': result.get('quiver_max_display_speed_ms'),
                'displayed_vector_count': result.get('displayed_vector_count'),
                'source_vector_count': result.get('source_vector_count'),
                'longitude_ticks': result.get('longitude_ticks'),
                'latitude_ticks': result.get('latitude_ticks'),
                'include_domain_boundary_ticks': result.get('include_domain_boundary_ticks'),
                'vector_unit_label': result.get('vector_unit_label'),
                'source_files': list(dict.fromkeys(sources_all)),
                'missing_inputs': missing_all,
            })
    created = sum(1 for item in items if item.get('status') == 'created')
    created_items = [item for item in items if item.get('status') == 'created']
    generation_mode = broad_map_config.get('broad_map_generation', {}).get('mode', 'configured_or_signal')
    return {
        'init': init,
        'manifest_type': 'broad_map_manifest',
        'created_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'broad_map_count': created,
        'item_count': len(items),
        'max_maps_per_product': max_maps_per_product,
        'broad_map_generation_mode': generation_mode,
        'selection_reason': 'rule_anchor_or_trigger',
        'anchor_lead_hours': broad_map_config.get('broad_map_generation', {}).get('anchor_lead_hours', broad_map_config.get('default_lead_hours', [])),
        'triggered_lead_policy': broad_map_config.get('broad_map_generation', {}).get('triggered_lead_policy', {}),
        'broad_map_dir': relpath_func(output_dir),
        'common_forecast_hours': _parse_int_list(broad_map_config.get('broad_map_generation', {}).get('lead_hours')) if generation_mode == 'scheduled_common_timeline' else [],
        'style_contract': style_contract,
        'render_domain_id': default_render_domain_id,
        'render_domain_name_ko': default_render_domain_name_ko,
        'render_domain_bbox': default_render_domain_bbox,
        'multi_render_domain_enabled': len({str(item.get('render_domain_id')) for item in created_items}) > 1,
        'product_render_domains': {
            product_id: {
                'render_domain_id': next(
                    str(item.get('render_domain_id')) for item in created_items
                    if str(item.get('product_id')) == product_id
                ),
                'render_domain_name_ko': next(
                    str(item.get('render_domain_name_ko')) for item in created_items
                    if str(item.get('product_id')) == product_id
                ),
                'render_domain_bbox': next(
                    item.get('render_domain_bbox') for item in created_items
                    if str(item.get('product_id')) == product_id
                ),
            }
            for product_id in sorted({str(item.get('product_id')) for item in created_items})
        },
        'filename_domain_policy': {
            'domain_token': 'render_domain_id',
            'trigger_domain_id': 'selection provenance only; not used in PNG filename',
        },
        'raw_excluded': True,
        'raw_excluded_reason': 'raw AIFS files excluded due to storage size in share/export bundles',
        'source_files_are_reference_paths': True,
        'raw_required_for_regeneration': True,
        'source_file_policy': {
            'source_files': 'reference paths to source AIFS files used for generation',
            'bundle_review_scope': 'raw-excluded bundles are for reviewing generated outputs',
            'regeneration_requirement': 'regeneration requires dain/<init>/raw/aifs source files in the same environment',
        },
        'broad_maps': items,
        'limitations': [
            'Broad maps are spatial inspection aids for AI Pass 1, not deterministic forecast evidence.',
            'No internet download or AI image generation is used.',
            'Coastlines/borders depend on local shapefile availability.',
            'source_files entries are provenance references and do not imply that raw files are included in a shared bundle.',
            'PNG filenames use each product render_domain_id. temporal_core and synoptic_context may use different fixed domains.',
        ],
    }
