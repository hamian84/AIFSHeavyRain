"""AI-first discovery feature package의 구조화 산출물을 생성한다."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any


def _series_id(feature_id: str, spatial_id: str) -> str:
    return f"SERIES_{feature_id.upper()}_{spatial_id.upper()}"


def build_feature_timeseries(
    *,
    init: str,
    rows: list[dict[str, Any]],
    feature_specs: list[dict[str, Any]],
    tile_ids: set[str],
    spatial_grid_bbox: dict[str, float],
    map_domain_bbox: dict[str, float],
    spatial_coverage_status: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """threshold로 행을 제거하지 않고 전체 통계와 tile 통계를 만든다."""

    previous: dict[str, float | None] = {}
    output_rows: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda x: (str(x["feature_id"]), str(x["domain_id"]), int(x["forecast_hour"]))):
        sid = _series_id(str(row["feature_id"]), str(row["domain_id"]))
        current = row.get("mean")
        prior = previous.get(sid)
        change = round(float(current) - float(prior), 6) if current is not None and prior is not None else None
        previous[sid] = current
        output_rows.append(
            {
                "series_id": sid,
                "feature_id": row["feature_id"],
                "spatial_id": row["domain_id"],
                "spatial_type": "neutral_tile",
                "forecast_hour": int(row["forecast_hour"]),
                "valid_time_utc": row["valid_time_utc"],
                "units": row["units"],
                "mean": row.get("mean"),
                "max": row.get("max"),
                "p95": row.get("p95"),
                "min": row.get("min"),
                "valid_grid_count": row.get("grid_count"),
                "max_location": row.get("max_location"),
                "min_location": row.get("min_location"),
                "previous_step_change": change,
                "signal_annotation": row.get("signal_level"),
                "selection_applied": False,
            }
        )

    catalog = []
    for spec in feature_specs:
        catalog.append(
            {
                "feature_id": spec["feature_id"],
                "name_ko": spec["name_ko"],
                "units": spec["units"],
                "series_ids": sorted({x["series_id"] for x in output_rows if x["feature_id"] == spec["feature_id"]}),
                "threshold_role": "annotation_only",
            }
        )
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    common = {
        "init": init,
        "created_at_utc": created,
        "threshold_filtering_applied": False,
        "forecast_hours": sorted({x["forecast_hour"] for x in output_rows}),
        "spatial_grid_bbox": spatial_grid_bbox,
        "map_domain_bbox": map_domain_bbox,
        "spatial_coverage_status": spatial_coverage_status,
        "spatial_role": "neutral_discovery",
        "candidate_discovery_order": 1,
    }
    timeseries = common | {
        "package_type": "feature_timeseries",
        "row_count": len(output_rows),
        "series_count": len({x["series_id"] for x in output_rows}),
        "rows": output_rows,
    }
    tile_rows = [x for x in output_rows if x["spatial_type"] == "neutral_tile"]
    tiles = common | {
        "package_type": "discovery_tile_summaries",
        "row_count": len(tile_rows),
        "rows": tile_rows,
    }
    feature_catalog = common | {
        "package_type": "discovery_feature_catalog",
        "feature_count": len(catalog),
        "features": catalog,
    }
    return timeseries, tiles, feature_catalog


def build_source_panel_groups(init: str, manifest: dict[str, Any]) -> dict[str, Any]:
    groups: list[dict[str, Any]] = []
    for product_id in sorted({str(x["product_id"]) for x in manifest.get("source_panels", []) if x.get("status") == "declared"}):
        assets = sorted(
            [x for x in manifest["source_panels"] if x.get("status") == "declared" and x["product_id"] == product_id],
            key=lambda x: int(x["forecast_hour"]),
        )
        groups.append(
            {
                "group_id": f"GROUP_{product_id}",
                "product_id": product_id,
                "forecast_hours": [int(x["forecast_hour"]) for x in assets],
                "source_panel_ids": [x["source_panel_id"] for x in assets],
            }
        )
    fingerprint = hashlib.sha256("|".join(x for g in groups for x in g["source_panel_ids"]).encode()).hexdigest()
    return {
        "init": init,
        "manifest_type": "discovery_image_groups",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "group_count": len(groups),
        "source_panel_count": sum(len(x["source_panel_ids"]) for x in groups),
        "asset_fingerprint": fingerprint,
        "groups": groups,
    }
