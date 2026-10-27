"""Operational-region statistics for AIFSHeavyRain AI Discovery Pass 1.

This module projects already-decoded feature arrays onto fixed operational regions.
It does not select candidate hours, candidate features, or candidate regions.
Neutral discovery tiles remain the primary autonomous-discovery space.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

from func.shared.runtime_paths import resolve_logical_ref


_SCHEMA_VERSION = "1.0.0"
_ALLOWED_ROLES = {"source_corridor", "operational_land", "marine_macro", "marine_detail"}
_ALLOWED_STAGES = {"pass1", "pass2"}
_ALLOWED_MASK_TYPES = {"land", "sea", "mixed"}
_ALLOWED_MASK_FALLBACKS = {"error", "bbox_if_empty"}
_ALLOWED_GRID_MEMBERSHIP_MODES = {"fixed_aifs_grid_points"}
_REGION_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")

_LAND_GEOMETRY_CACHE: dict[str, Any] = {}
_REGION_MASK_CACHE: dict[tuple[Any, ...], tuple[Any, str, str | None]] = {}
_OPERATIONAL_GRID_MAPPING_CACHE: dict[str, dict[str, tuple[tuple[float, float], ...]]] = {}


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover - project dependency guard.
        raise RuntimeError("PyYAML is required to read operational-region configuration") from exc
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return payload


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bbox_dict(value: Any, *, owner: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError(f"{owner}.bbox must be a mapping")
    required = ("lat_min", "lat_max", "lon_min", "lon_max")
    missing = [key for key in required if key not in value]
    if missing:
        raise ValueError(f"{owner}.bbox missing keys: {missing}")
    bbox = {key: float(value[key]) for key in required}
    if not (-90.0 <= bbox["lat_min"] < bbox["lat_max"] <= 90.0):
        raise ValueError(f"{owner}.bbox invalid latitude bounds: {bbox}")
    if not (-180.0 <= bbox["lon_min"] < bbox["lon_max"] <= 360.0):
        raise ValueError(f"{owner}.bbox invalid longitude bounds: {bbox}")
    return bbox


def load_operational_regions(path: str | Path) -> list[dict[str, Any]]:
    """Load and normalize the operational-region hierarchy."""
    source_path = Path(path)
    payload = _load_yaml(source_path)
    raw_regions = payload.get("regions")
    if not isinstance(raw_regions, list) or not raw_regions:
        raise ValueError(f"regions must be a non-empty list: {source_path}")

    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_regions):
        if not isinstance(raw, dict):
            raise ValueError(f"regions[{index}] must be a mapping")
        owner = f"regions[{index}]"
        region_id = str(raw.get("region_id", "")).strip()
        name_ko = str(raw.get("name_ko", "")).strip()
        role = str(raw.get("region_role", "")).strip()
        stage = str(raw.get("stage", "")).strip()
        mask_type = str(raw.get("mask_type", "")).strip()
        fallback = str(raw.get("mask_fallback", "error")).strip()
        if not region_id or not name_ko:
            raise ValueError(f"{owner} requires region_id and name_ko")
        if role not in _ALLOWED_ROLES:
            raise ValueError(f"{owner}.region_role unsupported: {role}")
        if stage not in _ALLOWED_STAGES:
            raise ValueError(f"{owner}.stage unsupported: {stage}")
        if mask_type not in _ALLOWED_MASK_TYPES:
            raise ValueError(f"{owner}.mask_type unsupported: {mask_type}")
        if fallback not in _ALLOWED_MASK_FALLBACKS:
            raise ValueError(f"{owner}.mask_fallback unsupported: {fallback}")
        aliases = raw.get("aliases", [])
        if aliases is None:
            aliases = []
        if not isinstance(aliases, list) or any(not isinstance(item, str) for item in aliases):
            raise ValueError(f"{owner}.aliases must be a string list")
        grid_membership_mode = str(raw.get("grid_membership_mode", "")).strip()
        expected_aifs_grid_count_raw = raw.get("expected_aifs_grid_count")
        expected_aifs_grid_count = None if expected_aifs_grid_count_raw is None else int(expected_aifs_grid_count_raw)
        if role == "operational_land":
            if grid_membership_mode not in _ALLOWED_GRID_MEMBERSHIP_MODES:
                raise ValueError(
                    f"{owner}.grid_membership_mode must be one of {sorted(_ALLOWED_GRID_MEMBERSHIP_MODES)} "
                    "for operational_land"
                )
            if expected_aifs_grid_count is None or expected_aifs_grid_count <= 0:
                raise ValueError(f"{owner}.expected_aifs_grid_count must be a positive integer for operational_land")
        elif grid_membership_mode:
            raise ValueError(f"{owner}.grid_membership_mode is only supported for operational_land")

        region = dict(raw)
        region.update(
            {
                "region_id": region_id,
                "name_ko": name_ko,
                "aliases": [item.strip() for item in aliases if item.strip()],
                "region_role": role,
                "stage": stage,
                "enabled_in_pass1": bool(raw.get("enabled_in_pass1", False)),
                "eligible_for_risk_rank": bool(raw.get("eligible_for_risk_rank", False)),
                "bbox": _bbox_dict(raw.get("bbox"), owner=owner),
                "mask_type": mask_type,
                "mask_fallback": fallback,
                "mask_buffer_degrees": float(raw.get("mask_buffer_degrees", 0.0)),
                "grid_membership_mode": grid_membership_mode or None,
                "expected_aifs_grid_count": expected_aifs_grid_count,
                "parent_region_id": raw.get("parent_region_id"),
                "overlap_group_id": raw.get("overlap_group_id"),
                "definition_policy_id": str(payload.get("policy_id", "")),
                "definition_policy_version": str(payload.get("policy_version", "")),
                "definition_source": source_path.as_posix(),
                "mask_resources": payload.get("mask_resources", {}),
            }
        )
        if region["mask_buffer_degrees"] < 0:
            raise ValueError(f"{owner}.mask_buffer_degrees must be >= 0")
        normalized.append(region)
    return normalized


def operational_region_catalog_metadata(path: str | Path) -> dict[str, str]:
    """Return the stable public identity of the operational-region catalog."""
    source_path = Path(path)
    payload = _load_yaml(source_path)
    return {
        "region_catalog_version": str(payload.get("policy_version", "")),
        "region_catalog_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
    }


def _standard_bbox(value: Any) -> dict[str, float] | None:
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


def _region_standard_bbox(region: dict[str, Any]) -> dict[str, float]:
    bbox = _bbox_dict(region.get("bbox"), owner=str(region.get("region_id", "region")))
    return {
        "west": bbox["lon_min"], "east": bbox["lon_max"],
        "south": bbox["lat_min"], "north": bbox["lat_max"],
    }


def bbox_intersection(first: dict[str, Any], second: dict[str, Any]) -> dict[str, float] | None:
    """Return a non-empty EPSG:4326 bbox intersection in canonical key names."""
    left, right = _standard_bbox(first), _standard_bbox(second)
    if left is None or right is None:
        return None
    result = {
        "west": max(left["west"], right["west"]),
        "east": min(left["east"], right["east"]),
        "south": max(left["south"], right["south"]),
        "north": min(left["north"], right["north"]),
    }
    return result if result["west"] < result["east"] and result["south"] < result["north"] else None


def selectable_pass1_regions(*, regions: list[dict[str, Any]], candidate_bbox: dict[str, Any]) -> list[dict[str, Any]]:
    """Select catalog regions eligible for a Pass1 operational target by bbox.

    This is deliberately only the inexpensive candidate search.  Final target
    resolution still requires the land/sea mask and valid calculated grid in
    ``operational_stats_for``.
    """
    if _standard_bbox(candidate_bbox) is None:
        return []
    eligible_roles = {"operational_land", "marine_macro"}
    selected = [
        region for region in regions
        if bool(region.get("enabled_in_pass1"))
        and str(region.get("stage")) == "pass1"
        and str(region.get("region_role")) in eligible_roles
        and bbox_intersection(candidate_bbox, _region_standard_bbox(region)) is not None
    ]
    return sorted(selected, key=lambda item: str(item["region_id"]))


def pass1_operational_forecast_region_scope(
    *,
    arr: Any,
    candidate_bbox: dict[str, Any],
    regions: list[dict[str, Any]],
    root: str | Path,
    np: Any,
) -> tuple[Any, Any, dict[str, Any]]:
    """Return the candidate-domain field plus the Pass1 operational-region mask.

    This separates two concerns that used to be coupled by
    ``mask_to_pass1_operational_forecast_regions``:

    - the precipitation field shown to the forecaster/VLM remains intact over
      the whole candidate domain;
    - the boolean mask still identifies cells that are eligible to satisfy the
      SYSTEM direct-precipitation presence anchor.

    The mask contains only ``operational_land`` and ``marine_macro`` regions.
    Source corridors remain visible in the rendered meteorological field but do
    not become eligible direct-precipitation evidence.
    """
    standard = _standard_bbox(candidate_bbox)
    if standard is None:
        raise ValueError(f"candidate_bbox is invalid: {candidate_bbox!r}")
    clip_bbox = {
        "lat_min": standard["south"],
        "lat_max": standard["north"],
        "lon_min": standard["west"],
        "lon_max": standard["east"],
    }
    sub = _subset_bbox(arr, clip_bbox)
    if any(int(size) == 0 for size in sub.shape):
        return sub, np.zeros(sub.shape, dtype=bool), {
            "scope": "candidate_intersection_operational_forecast_regions",
            "selected_region_ids": [],
            "operational_grid_count": 0,
            "status": "empty_candidate_grid",
        }

    selected = selectable_pass1_regions(regions=regions, candidate_bbox=standard)
    lat_name, lon_name = _lat_lon_names(sub)
    lat_grid = np.asarray(sub[lat_name].broadcast_like(sub).values, dtype="float64")
    lon_grid = np.asarray(sub[lon_name].broadcast_like(sub).values, dtype="float64")
    lon_grid = np.where(lon_grid > 180.0, lon_grid - 360.0, lon_grid)
    union_mask = np.zeros(sub.shape, dtype=bool)
    contributing_regions: list[str] = []

    for region in selected:
        overlap = bbox_intersection(standard, _region_standard_bbox(region))
        if overlap is None:
            continue
        effective_region = {
            **region,
            "bbox": {
                "lat_min": overlap["south"],
                "lat_max": overlap["north"],
                "lon_min": overlap["west"],
                "lon_max": overlap["east"],
            },
        }
        geometry_mask, _mask_status, _mask_resource = _region_mask(
            sub, effective_region, Path(root), np
        )
        geometry_mask = np.asarray(geometry_mask, dtype=bool)
        if not geometry_mask.any() and str(effective_region.get("mask_fallback", "error")) == "bbox_if_empty":
            geometry_mask = np.ones(sub.shape, dtype=bool)

        west, east = _normalize_lon_bounds(
            sub[lon_name], overlap["west"], overlap["east"]
        )
        if west <= east:
            lon_inside = (lon_grid >= west) & (lon_grid <= east)
        else:
            lon_inside = (lon_grid >= west) | (lon_grid <= east)
        bbox_mask = (
            (lat_grid >= overlap["south"])
            & (lat_grid <= overlap["north"])
            & lon_inside
        )
        effective_mask = geometry_mask & bbox_mask
        if effective_mask.any():
            union_mask |= effective_mask
            contributing_regions.append(str(region.get("region_id") or ""))

    return sub, union_mask, {
        "scope": "candidate_intersection_operational_forecast_regions",
        "selected_region_ids": contributing_regions,
        "operational_grid_count": int(np.count_nonzero(union_mask)),
        "status": "applied" if union_mask.any() else "empty_operational_overlap",
    }


def mask_to_pass1_operational_forecast_regions(
    *,
    arr: Any,
    candidate_bbox: dict[str, Any],
    regions: list[dict[str, Any]],
    root: str | Path,
    np: Any,
) -> tuple[Any, dict[str, Any]]:
    """Mask a candidate-domain field to the union of Pass1 forecast regions.

    This API retains the historical masking behavior for calculations that
    explicitly require masked values. Rendering code should use
    ``pass1_operational_forecast_region_scope`` when it needs to show the full
    meteorological field while displaying the operational eligibility boundary
    separately.
    """
    sub, union_mask, metadata = pass1_operational_forecast_region_scope(
        arr=arr,
        candidate_bbox=candidate_bbox,
        regions=regions,
        root=root,
        np=np,
    )
    return sub.where(union_mask), metadata


def region_overlay_records(*, regions: list[dict[str, Any]], candidate_bbox: dict[str, Any]) -> list[dict[str, Any]]:
    """Build concise map overlay records without filesystem implementation data."""
    result: list[dict[str, Any]] = []
    for region in sorted(regions, key=lambda item: str(item["region_id"])):
        overlap = bbox_intersection(candidate_bbox, _region_standard_bbox(region))
        if overlap is None:
            continue
        result.append({
            "region_id": str(region["region_id"]),
            "region_name": str(region.get("name_ko", region["region_id"])),
            "region_role": str(region.get("region_role", "")),
            "mask_type": str(region.get("mask_type", "mixed")),
            "bbox": overlap,
        })
    return result


def load_operational_feature_policy(path: str | Path) -> dict[str, Any]:
    """Load the fixed feature contract for each operational-region role."""
    source_path = Path(path)
    payload = _load_yaml(source_path)
    role_features = payload.get("region_role_features")
    if not isinstance(role_features, dict):
        raise ValueError("operational feature policy requires region_role_features")
    normalized: dict[str, list[str]] = {}
    for role in _ALLOWED_ROLES:
        values = role_features.get(role, [])
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ValueError(f"region_role_features.{role} must be a string list")
        cleaned = [item.strip() for item in values if item.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError(f"region_role_features.{role} contains duplicates")
        normalized[role] = cleaned
    result = dict(payload)
    result["region_role_features"] = normalized
    result["policy_id"] = str(payload.get("policy_id", ""))
    result["policy_version"] = str(payload.get("policy_version", ""))
    result["source_path"] = source_path.as_posix()
    return result


def feature_allowed_for_region(feature_id: str, region_role: str, policy: dict[str, Any]) -> bool:
    values = policy.get("region_role_features", {}).get(region_role, [])
    return str(feature_id) in {str(item) for item in values}


def _lat_lon_names(arr: Any) -> tuple[str, str]:
    lat_candidates = ("latitude", "lat", "y")
    lon_candidates = ("longitude", "lon", "x")
    lat_name = next((name for name in lat_candidates if name in arr.coords or name in arr.dims), None)
    lon_name = next((name for name in lon_candidates if name in arr.coords or name in arr.dims), None)
    if not lat_name or not lon_name:
        raise RuntimeError(f"could not identify latitude/longitude coordinates in dims={arr.dims}")
    return lat_name, lon_name


def _normalize_lon_bounds(lon_values: Any, lon_min: float, lon_max: float) -> tuple[float, float]:
    try:
        grid_min = float(lon_values.min())
        grid_max = float(lon_values.max())
    except Exception:  # noqa: BLE001
        return lon_min, lon_max
    if grid_max > 180.0 and (lon_min < 0.0 or lon_max < 0.0):
        return lon_min % 360.0, lon_max % 360.0
    if grid_min < 0.0 and (lon_min > 180.0 or lon_max > 180.0):
        return ((lon_min + 180.0) % 360.0) - 180.0, ((lon_max + 180.0) % 360.0) - 180.0
    return lon_min, lon_max


def _subset_bbox(arr: Any, bbox: dict[str, float]) -> Any:
    lat_name, lon_name = _lat_lon_names(arr)
    lat = arr[lat_name]
    lon = arr[lon_name]
    lon_min, lon_max = _normalize_lon_bounds(lon, bbox["lon_min"], bbox["lon_max"])
    lat_mask = (lat >= bbox["lat_min"]) & (lat <= bbox["lat_max"])
    if lon_min <= lon_max:
        lon_mask = (lon >= lon_min) & (lon <= lon_max)
    else:
        lon_mask = (lon >= lon_min) | (lon <= lon_max)
    return arr.where(lat_mask & lon_mask, drop=True)


def _resolve_land_shapefile(root: Path, region: dict[str, Any]) -> Path:
    resources = region.get("mask_resources") or {}
    candidates = resources.get("land_shapefile_candidates") or [
        "daba/map/ne_50m_land/ne_50m_land.shp",
        "daba/map/ne_10m_land/ne_10m_land.shp",
    ]
    for value in candidates:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = resolve_logical_ref(root, candidate)
        if candidate.is_file():
            return candidate.resolve()
    joined = ", ".join(str(value) for value in candidates)
    raise FileNotFoundError(f"land-mask shapefile not found; checked: {joined}")


def _resolve_operational_land_grid_mapping(root: Path, region: dict[str, Any]) -> Path:
    resources = region.get("mask_resources") or {}
    candidates = resources.get("operational_land_grid_mapping_candidates") or [
        "daba/analysis/aifs_operational_region_grid_map.csv",
    ]
    for value in candidates:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = resolve_logical_ref(root, candidate)
        if candidate.is_file():
            return candidate.resolve()
    joined = ", ".join(str(value) for value in candidates)
    raise FileNotFoundError(f"operational-land AIFS grid mapping not found; checked: {joined}")


def _load_operational_land_grid_mapping(path: Path) -> dict[str, tuple[tuple[float, float], ...]]:
    cache_key = f"{path}:{path.stat().st_size}:{path.stat().st_mtime_ns}"
    cached = _OPERATIONAL_GRID_MAPPING_CACHE.get(cache_key)
    if cached is not None:
        return cached

    by_region: dict[str, list[tuple[float, float]]] = defaultdict(list)
    coordinate_owner: dict[tuple[float, float], str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"latitude", "longitude", "region_id"}
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"operational-land grid mapping missing columns {missing}: {path}")
        for row_number, row in enumerate(reader, start=2):
            region_id = str(row.get("region_id") or "").strip()
            if not region_id:
                raise ValueError(f"operational-land grid mapping row {row_number} has empty region_id")
            try:
                latitude = float(row["latitude"])
                longitude = float(row["longitude"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"operational-land grid mapping row {row_number} has invalid coordinates") from exc
            if not (-90.0 <= latitude <= 90.0):
                raise ValueError(f"operational-land grid mapping row {row_number} latitude out of range: {latitude}")
            longitude = ((longitude + 180.0) % 360.0) - 180.0
            key = (round(latitude, 8), round(longitude, 8))
            previous = coordinate_owner.get(key)
            if previous is not None:
                if previous != region_id:
                    raise ValueError(
                        "operational-land AIFS grid point belongs to multiple regions: "
                        f"lat={latitude}, lon={longitude}, regions={[previous, region_id]}"
                    )
                raise ValueError(
                    "duplicate operational-land AIFS grid point: "
                    f"lat={latitude}, lon={longitude}, region_id={region_id}"
                )
            coordinate_owner[key] = region_id
            by_region[region_id].append((latitude, longitude))

    if not by_region:
        raise ValueError(f"operational-land grid mapping is empty: {path}")
    result = {region_id: tuple(points) for region_id, points in by_region.items()}
    _OPERATIONAL_GRID_MAPPING_CACHE.clear()
    _OPERATIONAL_GRID_MAPPING_CACHE[cache_key] = result
    return result


def _operational_land_grid_mask(sub: Any, region: dict[str, Any], root: Path, np: Any) -> tuple[Any, str, str | None]:
    lat_name, lon_name = _lat_lon_names(sub)
    mapping_path = _resolve_operational_land_grid_mapping(root, region)
    mapping = _load_operational_land_grid_mapping(mapping_path)
    region_id = str(region.get("region_id", ""))
    points = mapping.get(region_id, ())
    expected = int(region.get("expected_aifs_grid_count") or 0)
    if expected <= 0:
        raise ValueError(f"{region_id} expected_aifs_grid_count must be positive")
    if len(points) != expected:
        raise ValueError(
            f"{region_id} AIFS grid mapping count mismatch: expected={expected}, actual={len(points)}"
        )

    resources = region.get("mask_resources") or {}
    tolerance = float(resources.get("operational_land_grid_coordinate_tolerance_degrees", 1.0e-7))
    if tolerance <= 0.0:
        raise ValueError("operational_land_grid_coordinate_tolerance_degrees must be > 0")

    cache_key = (
        region_id,
        "fixed_aifs_grid_points",
        tolerance,
        str(mapping_path),
        mapping_path.stat().st_size,
        mapping_path.stat().st_mtime_ns,
        _coordinate_signature(sub, lat_name, lon_name),
    )
    cached = _REGION_MASK_CACHE.get(cache_key)
    if cached is not None:
        return cached

    lat_grid = np.asarray(sub[lat_name].broadcast_like(sub).values, dtype="float64")
    lon_grid = np.asarray(sub[lon_name].broadcast_like(sub).values, dtype="float64")
    lon_grid = np.where(lon_grid > 180.0, lon_grid - 360.0, lon_grid)
    region_mask = np.zeros(sub.shape, dtype=bool)
    for latitude, longitude in points:
        region_mask |= (
            np.isclose(lat_grid, latitude, rtol=0.0, atol=tolerance)
            & np.isclose(lon_grid, longitude, rtol=0.0, atol=tolerance)
        )

    result = (region_mask, "applied_fixed_aifs_operational_land_grid_map", mapping_path.as_posix())
    _REGION_MASK_CACHE[cache_key] = result
    return result


def _load_land_geometry(path: Path) -> Any:
    cache_key = f"{path}:{path.stat().st_size}:{path.stat().st_mtime_ns}"
    cached = _LAND_GEOMETRY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        import shapefile
        from shapely.geometry import shape as shapely_shape
        from shapely.ops import unary_union
    except ModuleNotFoundError as exc:
        raise RuntimeError("pyshp and shapely are required for land/sea operational masks") from exc

    reader = shapefile.Reader(str(path))
    geometries = []
    for item in reader.shapes():
        try:
            geometry = shapely_shape(item.__geo_interface__)
            if not geometry.is_empty:
                geometries.append(geometry)
        except Exception:  # noqa: BLE001 - malformed individual polygon should not abort valid shapes.
            continue
    if not geometries:
        raise RuntimeError(f"land shapefile contains no usable geometries: {path}")
    land = unary_union(geometries)
    try:
        from shapely import make_valid

        land = make_valid(land)
    except Exception:  # noqa: BLE001
        if not land.is_valid:
            land = land.buffer(0)
    _LAND_GEOMETRY_CACHE.clear()
    _LAND_GEOMETRY_CACHE[cache_key] = land
    return land


def _coordinate_signature(sub: Any, lat_name: str, lon_name: str) -> tuple[Any, ...]:
    lat_values = sub[lat_name].values
    lon_values = sub[lon_name].values
    return (
        tuple(sub.shape),
        str(sub.dims),
        tuple(getattr(lat_values, "shape", ())),
        tuple(getattr(lon_values, "shape", ())),
        float(lat_values.flat[0]) if lat_values.size else None,
        float(lat_values.flat[-1]) if lat_values.size else None,
        float(lon_values.flat[0]) if lon_values.size else None,
        float(lon_values.flat[-1]) if lon_values.size else None,
    )


def _region_mask(sub: Any, region: dict[str, Any], root: Path, np: Any) -> tuple[Any, str, str | None]:
    lat_name, lon_name = _lat_lon_names(sub)
    if str(region.get("region_role", "")) == "operational_land":
        if str(region.get("grid_membership_mode", "")) != "fixed_aifs_grid_points":
            raise ValueError(f"{region.get('region_id')} operational_land requires fixed_aifs_grid_points")
        return _operational_land_grid_mask(sub, region, root, np)

    mask_type = str(region.get("mask_type", "mixed"))
    if mask_type == "mixed":
        return np.ones(sub.shape, dtype=bool), "bbox_only_mixed", None

    shape_path = _resolve_land_shapefile(root, region)
    cache_key = (
        str(region["region_id"]),
        mask_type,
        float(region.get("mask_buffer_degrees", 0.0)),
        str(shape_path),
        _coordinate_signature(sub, lat_name, lon_name),
    )
    cached = _REGION_MASK_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        from shapely import intersects_xy
        from shapely.geometry import box
    except ModuleNotFoundError as exc:
        raise RuntimeError("shapely is required for land/sea operational masks") from exc

    land = _load_land_geometry(shape_path)
    bbox = region["bbox"]
    local_land = land.intersection(box(bbox["lon_min"], bbox["lat_min"], bbox["lon_max"], bbox["lat_max"]))
    buffer_degrees = float(region.get("mask_buffer_degrees", 0.0))
    if buffer_degrees > 0.0 and not local_land.is_empty:
        local_land = local_land.buffer(buffer_degrees)

    lat_grid = np.asarray(sub[lat_name].broadcast_like(sub).values, dtype="float64")
    lon_grid = np.asarray(sub[lon_name].broadcast_like(sub).values, dtype="float64")
    lon_grid = np.where(lon_grid > 180.0, lon_grid - 360.0, lon_grid)
    if local_land.is_empty:
        land_mask = np.zeros(sub.shape, dtype=bool)
    else:
        land_mask = np.asarray(intersects_xy(local_land, lon_grid, lat_grid), dtype=bool)
    region_mask = land_mask if mask_type == "land" else ~land_mask
    status = f"applied_{mask_type}_mask"
    result = (region_mask, status, shape_path.as_posix())
    _REGION_MASK_CACHE[cache_key] = result
    return result


def normalize_json_number(value: float | int | None) -> float | None:
    """Return a finite JSON number without fixed-decimal precision loss."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return float(f"{number:.10g}")


def _location_from_index(
    sub: Any,
    index: tuple[int, ...],
    lat_name: str,
    lon_name: str,
    *,
    value: float | int | None,
) -> dict[str, float | None]:
    lat_values = sub[lat_name].broadcast_like(sub).values
    lon_values = sub[lon_name].broadcast_like(sub).values
    longitude = float(lon_values[index])
    if longitude > 180.0:
        longitude -= 360.0
    return {
        "latitude": normalize_json_number(float(lat_values[index])),
        "longitude": normalize_json_number(longitude),
        "value": normalize_json_number(value),
    }


def _array_sha256(*, values: Any, finite_mask: Any, np: Any) -> str:
    """Hash physical field values while keeping NaN bit patterns deterministic."""
    normalized = np.where(np.asarray(finite_mask, dtype=bool), np.asarray(values, dtype="float64"), 0.0)
    digest = hashlib.sha256()
    digest.update(np.ascontiguousarray(np.asarray(finite_mask, dtype=bool).astype("uint8")).tobytes())
    digest.update(np.ascontiguousarray(normalized, dtype="float64").tobytes())
    return digest.hexdigest()


def _geometry_mask_sha256(
    *,
    sub: Any,
    region: dict[str, Any],
    effective_bbox: dict[str, float],
    geometry_mask: Any,
    geometry_source_type: str,
    geometry_source_version: str | None,
    np: Any,
) -> str:
    """Hash only geometry/grid inputs, never field values or lead time metadata."""
    lat_name, lon_name = _lat_lon_names(sub)
    lat_grid = np.asarray(sub[lat_name].broadcast_like(sub).values, dtype="float64")
    lon_grid = np.asarray(sub[lon_name].broadcast_like(sub).values, dtype="float64")
    digest = hashlib.sha256()
    digest.update(_canonical_hash({
        "region_id": str(region.get("region_id", "")),
        "effective_bbox": effective_bbox,
        "geometry_source_type": geometry_source_type,
        "geometry_source_version": geometry_source_version,
        "mask_type": str(region.get("mask_type", "mixed")),
    }).encode("utf-8"))
    digest.update(np.ascontiguousarray(lat_grid).tobytes())
    digest.update(np.ascontiguousarray(lon_grid).tobytes())
    digest.update(np.ascontiguousarray(np.asarray(geometry_mask, dtype=bool).astype("uint8")).tobytes())
    return digest.hexdigest()


def operational_stats_for(
    *,
    arr: Any,
    region: dict[str, Any],
    np: Any,
    root: str | Path,
    clip_bbox: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate candidate-clipped, land/sea-masked operational statistics.

    ``clip_bbox`` is used by operational targets so a broad catalog
    region cannot silently contribute grid cells outside the candidate domain.
    """
    region_bbox = _region_standard_bbox(region)
    effective_bbox = bbox_intersection(region_bbox, clip_bbox) if clip_bbox is not None else region_bbox
    if effective_bbox is None:
        return {
            "valid_grid_count": 0,
            "mean": None,
            "area_weighted_mean": None,
            "min": None,
            "p05": None,
            "max": None,
            "p95": None,
            "median": None,
            "standard_deviation": None,
            "max_location": None,
            "min_location": None,
            "coverage_fraction": None,
            "candidate_overlap_fraction": 0.0,
            "valid_data_fraction": None,
            "region_coverage_fraction": 0.0,
            "mask_status": "empty_candidate_overlap",
            "mask_resource_path": None,
            "region_geometry_mask_sha256": None,
            "regional_statistics_input_sha256": None,
            "effective_bbox": None,
        }
    effective_region = {**region, "bbox": {
        "lat_min": effective_bbox["south"], "lat_max": effective_bbox["north"],
        "lon_min": effective_bbox["west"], "lon_max": effective_bbox["east"],
    }}
    sub = _subset_bbox(arr, effective_region["bbox"])
    if any(int(size) == 0 for size in sub.shape):
        return {
            "valid_grid_count": 0,
            "mean": None,
            "area_weighted_mean": None,
            "min": None,
            "p05": None,
            "max": None,
            "p95": None,
            "median": None,
            "standard_deviation": None,
            "max_location": None,
            "min_location": None,
            "coverage_fraction": None,
            "candidate_overlap_fraction": None,
            "valid_data_fraction": None,
            "region_coverage_fraction": None,
            "mask_status": "empty_bbox",
            "mask_resource_path": None,
            "region_geometry_mask_sha256": None,
            "regional_statistics_input_sha256": None,
            "effective_bbox": effective_bbox,
        }
    raw = np.asarray(sub.values, dtype="float64")
    mask, mask_status, mask_resource = _region_mask(sub, effective_region, Path(root), np)
    geometry_mask = np.asarray(mask, dtype=bool)
    finite_mask = np.isfinite(raw) & geometry_mask

    if not finite_mask.any() and str(effective_region.get("mask_fallback", "error")) == "bbox_if_empty":
        geometry_mask = np.ones(raw.shape, dtype=bool)
        finite_mask = np.isfinite(raw)
        mask_status = f"fallback_bbox_after_empty_{effective_region.get('mask_type', 'unknown')}_mask"
    if str(effective_region.get("region_role", "")) == "operational_land":
        geometry_source_type = "fixed_aifs_grid_points"
        geometry_source_path = _resolve_operational_land_grid_mapping(Path(root), effective_region)
        geometry_source_version = _file_sha256(geometry_source_path)
    elif str(effective_region.get("mask_type", "mixed")) != "mixed":
        geometry_source_type = "bbox_plus_land_sea_mask"
        geometry_source_path = _resolve_land_shapefile(Path(root), effective_region)
        geometry_source_version = _file_sha256(geometry_source_path)
    else:
        geometry_source_type = "bbox_only_mixed"
        geometry_source_version = None
    region_geometry_mask_sha256 = _geometry_mask_sha256(
        sub=sub, region=region, effective_bbox=effective_bbox, geometry_mask=geometry_mask,
        geometry_source_type=geometry_source_type, geometry_source_version=geometry_source_version, np=np,
    )
    full_sub = _subset_bbox(arr, {"lat_min": region_bbox["south"], "lat_max": region_bbox["north"], "lon_min": region_bbox["west"], "lon_max": region_bbox["east"]})
    full_geometry_grid_count: int | None = None
    if not any(int(size) == 0 for size in full_sub.shape):
        full_mask, _, _ = _region_mask(full_sub, region, Path(root), np)
        full_geometry_mask = np.asarray(full_mask, dtype=bool)
        if not full_geometry_mask.any() and str(region.get("mask_fallback", "error")) == "bbox_if_empty":
            full_geometry_mask = np.ones(full_sub.shape, dtype=bool)
        full_geometry_grid_count = int(np.count_nonzero(full_geometry_mask))
    geometry_grid_count = int(np.count_nonzero(geometry_mask))
    values = raw[finite_mask]
    if values.size == 0:
        return {
            "valid_grid_count": 0,
            "mean": None,
            "area_weighted_mean": None,
            "min": None,
            "p05": None,
            "max": None,
            "p95": None,
            "median": None,
            "standard_deviation": None,
            "max_location": None,
            "min_location": None,
            "coverage_fraction": 0.0,
            "candidate_overlap_fraction": normalize_json_number(geometry_grid_count / full_geometry_grid_count) if full_geometry_grid_count else None,
            "valid_data_fraction": 0.0 if geometry_grid_count else None,
            "region_coverage_fraction": 0.0 if full_geometry_grid_count else None,
            "mask_status": mask_status,
            "mask_resource_path": mask_resource,
            "region_geometry_mask_sha256": region_geometry_mask_sha256,
            "regional_statistics_input_sha256": _array_sha256(values=raw, finite_mask=finite_mask, np=np),
            "effective_bbox": effective_bbox,
        }

    masked = np.where(finite_mask, raw, np.nan)
    max_index = np.unravel_index(int(np.nanargmax(masked)), masked.shape)
    min_index = np.unravel_index(int(np.nanargmin(masked)), masked.shape)
    lat_name, lon_name = _lat_lon_names(sub)
    lat_grid = np.asarray(sub[lat_name].broadcast_like(sub).values, dtype="float64")
    weights = np.where(finite_mask, np.cos(np.deg2rad(lat_grid)), 0.0)
    weight_total = float(np.sum(weights))
    weighted_mean = float(np.sum(np.where(finite_mask, raw, 0.0) * weights) / weight_total) if weight_total > 0.0 else float(np.nanmean(values))
    field_value_sha256 = _array_sha256(values=raw, finite_mask=np.isfinite(raw), np=np)
    return {
        "valid_grid_count": int(values.size),
        "mean": normalize_json_number(float(np.nanmean(values))),
        "area_weighted_mean": normalize_json_number(weighted_mean),
        "min": normalize_json_number(float(np.nanmin(values))),
        "max": normalize_json_number(float(np.nanmax(values))),
        "p05": normalize_json_number(float(np.nanpercentile(values, 5))),
        "p95": normalize_json_number(float(np.nanpercentile(values, 95))),
        "median": normalize_json_number(float(np.nanmedian(values))),
        "standard_deviation": normalize_json_number(float(np.nanstd(values))),
        "max_location": _location_from_index(sub, max_index, lat_name, lon_name, value=raw[max_index]),
        "min_location": _location_from_index(sub, min_index, lat_name, lon_name, value=raw[min_index]),
        "coverage_fraction": normalize_json_number(values.size / geometry_grid_count) if geometry_grid_count else None,
        "candidate_overlap_fraction": normalize_json_number(geometry_grid_count / full_geometry_grid_count) if full_geometry_grid_count else None,
        "valid_data_fraction": normalize_json_number(values.size / geometry_grid_count) if geometry_grid_count else None,
        "region_coverage_fraction": normalize_json_number(values.size / full_geometry_grid_count) if full_geometry_grid_count else None,
        "mask_status": mask_status,
        "mask_resource_path": mask_resource,
        "region_geometry_mask_sha256": region_geometry_mask_sha256,
        "regional_statistics_input_sha256": _canonical_hash({
            "region_geometry_mask_sha256": region_geometry_mask_sha256,
            "field_value_sha256": field_value_sha256,
        }),
        "effective_bbox": effective_bbox,
    }


def operational_threshold_footprint_for(
    *,
    arr: Any,
    region: dict[str, Any],
    np: Any,
    root: str | Path,
    threshold: float,
    clip_bbox: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return deterministic threshold-footprint metadata for one operational region.

    Unlike ``operational_stats_for``, this helper is concerned with the spatial
    representativeness of a thresholded footprint.  It reports both the fraction
    of the *candidate-clipped* operational region and the fraction of the full
    catalog region covered by cells meeting ``arr >= threshold``.  No candidate
    or meteorological interpretation is performed here.
    """
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        raise ValueError("threshold must be numeric")
    threshold_value = float(threshold)
    region_bbox = _region_standard_bbox(region)
    effective_bbox = bbox_intersection(region_bbox, clip_bbox) if clip_bbox is not None else region_bbox
    if effective_bbox is None:
        return {
            "valid_grid_count": 0,
            "full_region_grid_count": 0,
            "threshold_grid_count": 0,
            "candidate_precip_coverage_fraction": 0.0,
            "region_precip_coverage_fraction": 0.0,
            "threshold_mean": None,
            "threshold_p95": None,
            "threshold_max": None,
            "threshold_centroid": None,
            "threshold_bbox": None,
        }
    effective_region = {**region, "bbox": {
        "lat_min": effective_bbox["south"], "lat_max": effective_bbox["north"],
        "lon_min": effective_bbox["west"], "lon_max": effective_bbox["east"],
    }}
    sub = _subset_bbox(arr, effective_region["bbox"])
    if any(int(size) == 0 for size in sub.shape):
        return {
            "valid_grid_count": 0,
            "full_region_grid_count": 0,
            "threshold_grid_count": 0,
            "candidate_precip_coverage_fraction": 0.0,
            "region_precip_coverage_fraction": 0.0,
            "threshold_mean": None,
            "threshold_p95": None,
            "threshold_max": None,
            "threshold_centroid": None,
            "threshold_bbox": None,
        }
    raw = np.asarray(sub.values, dtype="float64")
    geometry_mask, _mask_status, _mask_resource = _region_mask(sub, effective_region, Path(root), np)
    geometry_mask = np.asarray(geometry_mask, dtype=bool)
    finite_mask = np.isfinite(raw) & geometry_mask
    if not finite_mask.any() and str(effective_region.get("mask_fallback", "error")) == "bbox_if_empty":
        geometry_mask = np.ones(raw.shape, dtype=bool)
        finite_mask = np.isfinite(raw)

    full_sub = _subset_bbox(
        arr,
        {
            "lat_min": region_bbox["south"], "lat_max": region_bbox["north"],
            "lon_min": region_bbox["west"], "lon_max": region_bbox["east"],
        },
    )
    full_region_grid_count = 0
    if not any(int(size) == 0 for size in full_sub.shape):
        full_mask, _, _ = _region_mask(full_sub, region, Path(root), np)
        full_mask = np.asarray(full_mask, dtype=bool)
        if not full_mask.any() and str(region.get("mask_fallback", "error")) == "bbox_if_empty":
            full_mask = np.ones(full_sub.shape, dtype=bool)
        full_region_grid_count = int(np.count_nonzero(full_mask))

    valid_grid_count = int(np.count_nonzero(finite_mask))
    threshold_mask = finite_mask & (raw >= threshold_value)
    threshold_grid_count = int(np.count_nonzero(threshold_mask))
    if threshold_grid_count <= 0:
        return {
            "valid_grid_count": valid_grid_count,
            "full_region_grid_count": full_region_grid_count,
            "threshold_grid_count": 0,
            "candidate_precip_coverage_fraction": 0.0 if valid_grid_count else None,
            "region_precip_coverage_fraction": 0.0 if full_region_grid_count else None,
            "threshold_mean": None,
            "threshold_p95": None,
            "threshold_max": None,
            "threshold_centroid": None,
            "threshold_bbox": None,
        }

    lat_name, lon_name = _lat_lon_names(sub)
    lat_grid = np.asarray(sub[lat_name].broadcast_like(sub).values, dtype="float64")
    lon_grid = np.asarray(sub[lon_name].broadcast_like(sub).values, dtype="float64")
    lon_grid = np.where(lon_grid > 180.0, lon_grid - 360.0, lon_grid)
    values = raw[threshold_mask]
    lats = lat_grid[threshold_mask]
    lons = lon_grid[threshold_mask]
    return {
        "valid_grid_count": valid_grid_count,
        "full_region_grid_count": full_region_grid_count,
        "threshold_grid_count": threshold_grid_count,
        "candidate_precip_coverage_fraction": normalize_json_number(threshold_grid_count / valid_grid_count) if valid_grid_count else None,
        "region_precip_coverage_fraction": normalize_json_number(threshold_grid_count / full_region_grid_count) if full_region_grid_count else None,
        "threshold_mean": normalize_json_number(float(np.nanmean(values))),
        "threshold_p95": normalize_json_number(float(np.nanpercentile(values, 95))),
        "threshold_max": normalize_json_number(float(np.nanmax(values))),
        "threshold_centroid": {
            "latitude": normalize_json_number(float(np.nanmean(lats))),
            "longitude": normalize_json_number(float(np.nanmean(lons))),
        },
        "threshold_bbox": {
            "west": normalize_json_number(float(np.nanmin(lons))),
            "east": normalize_json_number(float(np.nanmax(lons))),
            "south": normalize_json_number(float(np.nanmin(lats))),
            "north": normalize_json_number(float(np.nanmax(lats))),
        },
    }


def operational_region_mask_for_candidate(
    *,
    arr: Any,
    region: dict[str, Any],
    candidate_bbox: dict[str, Any],
    root: str | Path,
    np: Any,
) -> tuple[Any, dict[str, Any]]:
    """Return one catalog-region mask aligned to the candidate-domain field."""
    standard = _standard_bbox(candidate_bbox)
    if standard is None:
        raise ValueError(f"candidate_bbox is invalid: {candidate_bbox!r}")
    sub = _subset_bbox(
        arr,
        {
            "lat_min": standard["south"], "lat_max": standard["north"],
            "lon_min": standard["west"], "lon_max": standard["east"],
        },
    )
    overlap = bbox_intersection(standard, _region_standard_bbox(region))
    if overlap is None:
        return np.zeros(sub.shape, dtype=bool), {"status": "no_bbox_overlap", "bbox": None}
    effective_region = {
        **region,
        "bbox": {
            "lat_min": overlap["south"], "lat_max": overlap["north"],
            "lon_min": overlap["west"], "lon_max": overlap["east"],
        },
    }
    geometry_mask, mask_status, _mask_resource = _region_mask(sub, effective_region, Path(root), np)
    geometry_mask = np.asarray(geometry_mask, dtype=bool)
    if not geometry_mask.any() and str(effective_region.get("mask_fallback", "error")) == "bbox_if_empty":
        geometry_mask = np.ones(sub.shape, dtype=bool)
        mask_status = f"fallback_bbox_after_empty_{effective_region.get('mask_type', 'unknown')}_mask"
    lat_name, lon_name = _lat_lon_names(sub)
    lat_grid = np.asarray(sub[lat_name].broadcast_like(sub).values, dtype="float64")
    lon_grid = np.asarray(sub[lon_name].broadcast_like(sub).values, dtype="float64")
    lon_grid = np.where(lon_grid > 180.0, lon_grid - 360.0, lon_grid)
    west, east = _normalize_lon_bounds(sub[lon_name], overlap["west"], overlap["east"])
    if west <= east:
        lon_inside = (lon_grid >= west) & (lon_grid <= east)
    else:
        lon_inside = (lon_grid >= west) | (lon_grid <= east)
    bbox_mask = (
        (lat_grid >= overlap["south"])
        & (lat_grid <= overlap["north"])
        & lon_inside
    )
    mask = geometry_mask & bbox_mask
    return mask, {
        "status": mask_status,
        "bbox": {
            "west": float(overlap["west"]), "east": float(overlap["east"]),
            "south": float(overlap["south"]), "north": float(overlap["north"]),
        },
    }


def _bbox_within(inner: dict[str, float], outer: dict[str, float]) -> bool:
    return (
        inner["lat_min"] >= float(outer["lat_min"])
        and inner["lat_max"] <= float(outer["lat_max"])
        and inner["lon_min"] >= float(outer["lon_min"])
        and inner["lon_max"] <= float(outer["lon_max"])
    )


def validate_operational_regions(
    *,
    regions: list[dict[str, Any]],
    feature_policy: dict[str, Any],
    neutral_grid_bbox: dict[str, Any],
    expected_counts: dict[str, Any] | None,
    statistics_rows: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Validate definitions and, when supplied, generated statistics rows."""
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    expected_counts = {str(key): int(value) for key, value in (expected_counts or {}).items()}
    ids = [str(item.get("region_id", "")) for item in regions]
    id_counts = Counter(ids)
    for region_id, count in sorted(id_counts.items()):
        if count != 1:
            errors.append({"code": "DUPLICATE_REGION_ID", "region_id": region_id, "count": count})
    region_map = {str(item["region_id"]): item for item in regions if item.get("region_id")}
    aliases_seen: dict[str, str] = {}
    role_counts = Counter(str(item.get("region_role")) for item in regions)
    pass1_regions = [item for item in regions if item.get("enabled_in_pass1") and item.get("stage") == "pass1"]
    pass2_regions = [item for item in regions if item.get("stage") == "pass2"]
    role_features = feature_policy.get("region_role_features", {})

    for region in regions:
        region_id = str(region.get("region_id", ""))
        role = str(region.get("region_role", ""))
        stage = str(region.get("stage", ""))
        enabled = bool(region.get("enabled_in_pass1", False))
        eligible = bool(region.get("eligible_for_risk_rank", False))
        if not _REGION_ID_RE.fullmatch(region_id):
            errors.append({"code": "INVALID_REGION_ID", "region_id": region_id})
        if role not in _ALLOWED_ROLES:
            errors.append({"code": "INVALID_REGION_ROLE", "region_id": region_id, "region_role": role})
        if stage not in _ALLOWED_STAGES:
            errors.append({"code": "INVALID_STAGE", "region_id": region_id, "stage": stage})
        try:
            bbox = _bbox_dict(region.get("bbox"), owner=region_id)
        except Exception as exc:  # noqa: BLE001
            errors.append({"code": "INVALID_BBOX", "region_id": region_id, "message": str(exc)})
            bbox = None
        if role == "source_corridor" and eligible:
            errors.append({"code": "SOURCE_CORRIDOR_RISK_RANK_FORBIDDEN", "region_id": region_id})
        if role == "marine_detail" and (stage != "pass2" or enabled or eligible):
            errors.append({"code": "MARINE_DETAIL_PASS1_FORBIDDEN", "region_id": region_id})
        if role in {"operational_land", "marine_macro"} and stage == "pass1" and enabled and not eligible:
            errors.append({"code": "PASS1_OPERATIONAL_REGION_NOT_RANK_ELIGIBLE", "region_id": region_id})
        if role == "operational_land":
            if str(region.get("grid_membership_mode", "")) != "fixed_aifs_grid_points":
                errors.append({"code": "INVALID_OPERATIONAL_LAND_GRID_MEMBERSHIP_MODE", "region_id": region_id})
            if int(region.get("expected_aifs_grid_count") or 0) <= 0:
                errors.append({"code": "INVALID_EXPECTED_AIFS_GRID_COUNT", "region_id": region_id})
        if enabled and not role_features.get(role):
            errors.append({"code": "EMPTY_FEATURE_CONTRACT", "region_id": region_id, "region_role": role})
        parent_id = region.get("parent_region_id")
        if parent_id:
            parent = region_map.get(str(parent_id))
            if parent is None:
                errors.append({"code": "UNKNOWN_PARENT_REGION", "region_id": region_id, "parent_region_id": parent_id})
            elif role == "marine_detail" and parent.get("region_role") != "marine_macro":
                errors.append({"code": "INVALID_MARINE_DETAIL_PARENT", "region_id": region_id, "parent_region_id": parent_id})
        for alias in region.get("aliases", []):
            key = str(alias).strip()
            if not key:
                continue
            if key in aliases_seen and aliases_seen[key] != region_id:
                errors.append({"code": "DUPLICATE_REGION_ALIAS", "alias": key, "region_ids": [aliases_seen[key], region_id]})
            aliases_seen[key] = region_id
        if bbox and role in {"operational_land", "marine_macro"} and enabled and not _bbox_within(bbox, neutral_grid_bbox):
            errors.append({"code": "RANK_REGION_OUTSIDE_NEUTRAL_GRID", "region_id": region_id, "bbox": bbox})
        elif bbox and role == "source_corridor" and not _bbox_within(bbox, neutral_grid_bbox):
            warnings.append({"code": "SOURCE_CORRIDOR_EXTENDS_OUTSIDE_NEUTRAL_GRID", "region_id": region_id, "bbox": bbox})

    for role, expected in sorted(expected_counts.items()):
        actual = int(role_counts.get(role, 0))
        if actual != expected:
            errors.append({"code": "REGION_ROLE_COUNT_MISMATCH", "region_role": role, "expected": expected, "actual": actual})

    statistics_summary: dict[str, Any] = {
        "statistics_rows_supplied": statistics_rows is not None,
        "row_count": 0,
        "duplicate_key_count": 0,
        "zero_grid_series_count": 0,
        "missing_region_feature_series_count": 0,
    }
    if statistics_rows is not None:
        statistics_summary["row_count"] = len(statistics_rows)
        key_counts = Counter(
            (str(row.get("region_id")), str(row.get("feature_id")), int(row.get("forecast_hour", -1)))
            for row in statistics_rows
        )
        duplicate_keys = [key for key, count in key_counts.items() if count > 1]
        statistics_summary["duplicate_key_count"] = len(duplicate_keys)
        if duplicate_keys:
            errors.append({"code": "DUPLICATE_OPERATIONAL_STATISTICS_ROWS", "count": len(duplicate_keys), "sample": [list(key) for key in duplicate_keys[:10]]})

        active_ids = {str(item["region_id"]) for item in pass1_regions}
        row_region_ids = {str(row.get("region_id")) for row in statistics_rows}
        unexpected_ids = sorted(row_region_ids - active_ids)
        if unexpected_ids:
            errors.append({"code": "DISABLED_REGION_STATISTICS_PRESENT", "region_ids": unexpected_ids})

        rows_by_series: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in statistics_rows:
            rows_by_series[(str(row.get("region_id")), str(row.get("feature_id")))].append(row)
            region = region_map.get(str(row.get("region_id")))
            if region and not feature_allowed_for_region(str(row.get("feature_id")), str(region.get("region_role")), feature_policy):
                errors.append({"code": "FEATURE_OUTSIDE_ROLE_CONTRACT", "region_id": row.get("region_id"), "feature_id": row.get("feature_id")})

        missing_series: list[dict[str, str]] = []
        zero_grid_series: list[dict[str, str]] = []
        for region in pass1_regions:
            region_id = str(region["region_id"])
            for feature_id in role_features.get(str(region["region_role"]), []):
                series_rows = rows_by_series.get((region_id, str(feature_id)), [])
                if not series_rows:
                    missing_series.append({"region_id": region_id, "feature_id": str(feature_id)})
                    continue
                if not any(int(row.get("valid_grid_count", 0) or 0) > 0 for row in series_rows):
                    zero_grid_series.append({"region_id": region_id, "feature_id": str(feature_id)})
        statistics_summary["missing_region_feature_series_count"] = len(missing_series)
        statistics_summary["zero_grid_series_count"] = len(zero_grid_series)
        if missing_series:
            errors.append({"code": "MISSING_OPERATIONAL_REGION_FEATURE_SERIES", "count": len(missing_series), "sample": missing_series[:20]})
        if zero_grid_series:
            errors.append({"code": "ZERO_GRID_OPERATIONAL_REGION_FEATURE_SERIES", "count": len(zero_grid_series), "sample": zero_grid_series[:20]})

    region_diagnostics = [
        {
            "region_id": str(region["region_id"]),
            "region_role": str(region["region_role"]),
            "stage": str(region["stage"]),
            "enabled_in_pass1": bool(region["enabled_in_pass1"]),
            "eligible_for_risk_rank": bool(region["eligible_for_risk_rank"]),
            "mask_type": str(region["mask_type"]),
            "bbox": region["bbox"],
            "allowed_feature_count": len(role_features.get(str(region["region_role"]), [])),
        }
        for region in regions
    ]
    return {
        "schema_version": _SCHEMA_VERSION,
        "report_type": "operational_region_validation_report",
        "created_at_utc": _now_utc(),
        "validation_status": "pass" if not errors else "fail",
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "expected_region_counts": expected_counts,
        "actual_region_counts": dict(sorted(role_counts.items())),
        "total_region_count": len(regions),
        "pass1_enabled_region_count": len(pass1_regions),
        "pass2_reserved_region_count": len(pass2_regions),
        "risk_rank_eligible_region_count": sum(bool(item.get("eligible_for_risk_rank")) for item in regions),
        "neutral_grid_bbox": {key: float(value) for key, value in neutral_grid_bbox.items()},
        "statistics_summary": statistics_summary,
        "regions": region_diagnostics,
    }


def _catalog_region(region: dict[str, Any], feature_policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "region_id": str(region["region_id"]),
        "name_ko": str(region["name_ko"]),
        "aliases": list(region.get("aliases", [])),
        "region_role": str(region["region_role"]),
        "stage": str(region["stage"]),
        "enabled_in_pass1": bool(region["enabled_in_pass1"]),
        "eligible_for_risk_rank": bool(region["eligible_for_risk_rank"]),
        "bbox": {key: float(value) for key, value in region["bbox"].items()},
        "mask_type": str(region["mask_type"]),
        "mask_buffer_degrees": float(region.get("mask_buffer_degrees", 0.0)),
        "mask_fallback": str(region.get("mask_fallback", "error")),
        "parent_region_id": region.get("parent_region_id"),
        "overlap_group_id": region.get("overlap_group_id"),
        "analysis_purpose": region.get("analysis_purpose"),
        "allowed_feature_ids": list(feature_policy.get("region_role_features", {}).get(str(region["region_role"]), [])),
    }


def build_operational_region_catalog(*, init: str, regions: list[dict[str, Any]], feature_policy: dict[str, Any]) -> dict[str, Any]:
    role_counts = Counter(str(item["region_role"]) for item in regions)
    pass1_regions = [item for item in regions if item.get("enabled_in_pass1") and item.get("stage") == "pass1"]
    pass2_regions = [item for item in regions if item.get("stage") == "pass2"]
    catalog_regions = [_catalog_region(item, feature_policy) for item in regions]
    return {
        "schema_version": _SCHEMA_VERSION,
        "catalog_type": "operational_region_catalog",
        "init": str(init),
        "created_at_utc": _now_utc(),
        "region_definition_policy_id": str(regions[0].get("definition_policy_id", "")) if regions else "",
        "region_definition_policy_version": str(regions[0].get("definition_policy_version", "")) if regions else "",
        "feature_policy_id": str(feature_policy.get("policy_id", "")),
        "feature_policy_version": str(feature_policy.get("policy_version", "")),
        "region_count": len(regions),
        "pass1_enabled_region_count": len(pass1_regions),
        "pass2_reserved_region_count": len(pass2_regions),
        "risk_rank_eligible_region_count": sum(bool(item.get("eligible_for_risk_rank")) for item in regions),
        "role_counts": dict(sorted(role_counts.items())),
        "pass1_region_ids": [str(item["region_id"]) for item in pass1_regions],
        "pass2_reserved_region_ids": [str(item["region_id"]) for item in pass2_regions],
        "regions": catalog_regions,
        "catalog_fingerprint": _canonical_hash(catalog_regions),
    }


def _series_id(region_id: str, feature_id: str) -> str:
    return f"OPS_{region_id}_{feature_id.upper()}"


def build_operational_region_timeseries(*, init: str, rows: list[dict[str, Any]], catalog: dict[str, Any]) -> dict[str, Any]:
    """Convert row-oriented operational statistics into compact per-series arrays."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["region_id"]), str(row["feature_id"]))].append(row)
    region_map = {str(item["region_id"]): item for item in catalog.get("regions", [])}
    series: list[dict[str, Any]] = []
    source_catalog: dict[str, str] = {}

    for (region_id, feature_id), group_rows in sorted(grouped.items()):
        ordered = sorted(group_rows, key=lambda item: int(item["forecast_hour"]))
        first = ordered[0]
        source_ref_ids: list[str | None] = []
        for row in ordered:
            source = str(row.get("source_files", "")).strip()
            if not source:
                source_ref_ids.append(None)
                continue
            ref_id = "SRC_" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:16].upper()
            source_catalog.setdefault(ref_id, source)
            source_ref_ids.append(ref_id)
        region_meta = region_map.get(region_id, {})
        series.append(
            {
                "series_id": _series_id(region_id, feature_id),
                "region_id": region_id,
                "region_name_ko": str(first.get("region_name_ko", region_meta.get("name_ko", ""))),
                "region_role": str(first.get("region_role", region_meta.get("region_role", ""))),
                "eligible_for_risk_rank": bool(first.get("eligible_for_risk_rank", region_meta.get("eligible_for_risk_rank", False))),
                "parent_region_id": first.get("parent_region_id", region_meta.get("parent_region_id")),
                "overlap_group_id": first.get("overlap_group_id", region_meta.get("overlap_group_id")),
                "mask_type": str(first.get("mask_type", region_meta.get("mask_type", "mixed"))),
                "feature_id": feature_id,
                "feature_name_ko": str(first.get("feature_name_ko", "")),
                "level": first.get("level"),
                "units": str(first.get("units", "")),
                "forecast_hours": [int(row["forecast_hour"]) for row in ordered],
                "valid_times_utc": [str(row["valid_time_utc"]) for row in ordered],
                "valid_grid_count": [int(row.get("valid_grid_count", 0) or 0) for row in ordered],
                "mean": [row.get("mean") for row in ordered],
                "min": [row.get("min") for row in ordered],
                "max": [row.get("max") for row in ordered],
                "p95": [row.get("p95") for row in ordered],
                "max_location": [row.get("max_location") for row in ordered],
                "min_location": [row.get("min_location") for row in ordered],
                "mask_status": [str(row.get("mask_status", "unknown")) for row in ordered],
                "source_ref_ids": source_ref_ids,
            }
        )

    forecast_hours = sorted({int(row["forecast_hour"]) for row in rows})
    region_ids = sorted({str(row["region_id"]) for row in rows})
    feature_ids = sorted({str(row["feature_id"]) for row in rows})
    return {
        "schema_version": _SCHEMA_VERSION,
        "dataset_type": "operational_region_timeseries_compact",
        "init": str(init),
        "created_at_utc": _now_utc(),
        "threshold_filtering_applied": False,
        "candidate_selection_applied": False,
        "row_count": len(rows),
        "series_count": len(series),
        "region_count": len(region_ids),
        "feature_count": len(feature_ids),
        "forecast_hour_count": len(forecast_hours),
        "forecast_hours": forecast_hours,
        "region_ids": region_ids,
        "feature_ids": feature_ids,
        "statistics": ["valid_grid_count", "mean", "min", "max", "p95", "max_location", "min_location"],
        "catalog_ref": "operational_region_catalog.json",
        "source_file_catalog": dict(sorted(source_catalog.items())),
        "series": series,
        "dataset_fingerprint": _canonical_hash(series),
    }
