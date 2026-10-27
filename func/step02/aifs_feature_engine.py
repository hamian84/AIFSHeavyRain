"""step03: build AIFS model feature package.

Purpose
-------
This step reads raw AIFS GRIB2 files collected by step01 and inspected by
step02, then writes the quantitative Model Feature Package used by AI Pass 1.
Feature calculations are delegated to reusable classes under func/.

Inputs
------
  dain/<init>/raw/aifs/*.grib2
  daio/<init>/inventory/variable_inventory.json        # optional reference only
  daio/<init>/inventory/inventory_summary.json         # optional reference only
  daba/ai_pass1/discovery_spatial_grid.yaml             # neutral discovery tiles
  daba/ai_pass1/discovery_operational_regions.yaml       # Pass 1 operational regions + Pass 2 reserved regions
  daba/ai_pass1/operational_region_feature_policy.yaml   # fixed feature contract by region role
  daba/ai_pass1/feature_definitions.yaml               # feature calculation definitions
  daba/ai_pass1/field_summary_policy.yaml              # field summary selection policy
  daba/ai_pass1/product_catalog.yaml                   # products available to candidate planning

Outputs
-------
  daio/<init>/feature_package/heavy_rain_feature_table.csv
  daio/<init>/feature_package/field_summaries.json
  daio/<init>/feature_package/product_catalog.json
  daio/<init>/feature_package/model_feature_package.json
  daio/<init>/feature_package/missing_feature_inputs.json
  daio/<init>/feature_package/model_feature_package_summary.json
  daio/<init>/feature_package/discovery_overview_manifest.json
  daio/<init>/feature_package/discovery_overview_validation_report.json
  logs/<init>/03_build_model_feature_package.log

Scope
-----
- Computes neutral-tile statistics and role-aware operational-region statistics.
- Does not create AI candidates, call AI, generate text briefing, or render HTML.
- Missing optional fields are recorded, not treated as fatal by default.

Dependencies
------------
  pip install xarray cfgrib eccodes numpy pyyaml
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from func.shared.runtime_paths import logical_ref, runtime_path
from typing import Any, Sequence

ALLOWED_INIT_HOURS = {"00", "06", "12", "18"}
RAW_EXTS = {".grib", ".grib2", ".grb", ".grb2"}
SCRIPT_NAME = "scripts/03_build_model_feature_package.py"
STEP_ID = "step03"
G0 = 9.80665
DEFAULT_OPERATIONAL_REGIONS_FILE = Path("daba") / "ai_pass1" / "discovery_operational_regions.yaml"
DEFAULT_OPERATIONAL_FEATURE_POLICY_FILE = Path("daba") / "ai_pass1" / "operational_region_feature_policy.yaml"
DEFAULT_FEATURE_FILE = Path("daba") / "ai_pass1" / "feature_definitions.yaml"
DEFAULT_FIELD_SUMMARY_POLICY_FILE = Path("daba") / "ai_pass1" / "field_summary_policy.yaml"
DEFAULT_PRODUCT_CATALOG_FILE = Path("daba") / "ai_pass1" / "product_catalog.yaml"
DEFAULT_BROAD_MAP_PRODUCTS_FILE = Path("daba") / "ai_pass1" / "broad_map_products.yaml"
DEFAULT_DISCOVERY_SOURCE_PANEL_PRODUCTS_FILE = Path("daba") / "ai_pass1" / "discovery_source_panel_products.yaml"
DEFAULT_DISCOVERY_SPATIAL_GRID_FILE = Path("daba") / "ai_pass1" / "discovery_spatial_grid.yaml"
DEFAULT_DISCOVERY_EXECUTION_POLICY_FILE = Path("daba") / "ai_pass1" / "discovery_execution_policy.yaml"

# When this script is executed as ``python scripts/03_build_model_feature_package.py``,
# Python adds scripts/ to sys.path, not the project root.  Add the project root
# so that reusable modules can be imported from func/.
PROJECT_ROOT_FOR_IMPORT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_FOR_IMPORT))

from func.step02.base import ComponentContext  # noqa: E402
from func.shared.broad_map_builder import attach_broad_map_links, build_broad_maps  # noqa: E402
from func.step02.discovery_package import build_feature_timeseries, build_source_panel_groups  # noqa: E402
from func.shared.overview_builder import (  # noqa: E402
    build_discovery_overview_validation_report,
    build_discovery_overviews,
    build_discovery_source_manifest,
    enrich_source_manifest_with_overview_links,
)
from func.shared.output_policy import OutputPolicy, add_output_mode_argument, resolve_output_mode  # noqa: E402
from func.step02.registry import available_builders, get_component  # noqa: E402


def load_operational_region_module():
    """Load the optional-at-source-tree operational region module with a clear error."""
    try:
        from func.shared import operational_regions  # type: ignore
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError(
            "func/shared/operational_regions.py is required for the operational-region hierarchy. "
            "Add the new module before running step03."
        ) from exc
    return operational_regions


@dataclass(frozen=True)
class FeatureSpec:
    feature_id: str
    name_ko: str
    units: str
    level: int | None
    kind: str
    required_inputs: list[tuple[str, int | None]]
    builder: str




_FILENAME_RE = re.compile(
    r"aifs[_-]single[_-](?P<init>\d{10})[_-]f(?P<step>\d{3})"
    r"(?:[_-]f(?P<maxstep>\d{3}))?[_-](?P<kind>sfc|pl)"
    r"(?:[_-](?P<levels>[0-9-]+))?"
    r"(?:[_-](?P<group>[A-Za-z0-9_.-]+))?"
    r"[_-](?P<param>[A-Za-z0-9_.-]+)\.gr(?:ib)?2?$",
    re.IGNORECASE,
)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_init(init: str) -> None:
    if not re.fullmatch(r"\d{10}", init or ""):
        raise ValueError("--init must be a 10-digit YYYYMMDDHH value.")
    if init[8:] not in ALLOWED_INIT_HOURS:
        raise ValueError("--init hour must be one of 00, 06, 12, or 18.")


def init_datetime(init: str) -> datetime:
    return datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=timezone.utc)


def default_raw_dir(root: Path, init: str) -> Path:
    return runtime_path(root, "dain", init, "raw", "aifs")


def default_inventory_dir(root: Path, init: str) -> Path:
    return runtime_path(root, "daio", init, "inventory")


def default_output_dir(root: Path, init: str) -> Path:
    return runtime_path(root, "daio", init, "feature_package")


def log_path(root: Path, init: str) -> Path:
    return runtime_path(root, "logs", init, "03_build_model_feature_package.log")


def setup_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(path, mode="w", encoding="utf-8"), logging.StreamHandler(sys.stdout)],
        force=True,
    )


def rel(root: Path, path: Path) -> str:
    try:
        return logical_ref(root, path)
    except ValueError:
        return str(path)


def package_rel(out: Path, path: Path) -> str:
    """Return a path relative to the feature_package directory."""
    try:
        return str(path.relative_to(out)).replace("\\", "/")
    except ValueError:
        return path.name


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def import_yaml_module():
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "required package is not installed: PyYAML. Install with: pip install pyyaml"
        ) from exc
    return yaml


def default_operational_regions_file(root: Path) -> Path:
    return runtime_path(root, "daba", "ai_pass1", "discovery_operational_regions.yaml")


def default_operational_feature_policy_file(root: Path) -> Path:
    return runtime_path(root, "daba", "ai_pass1", "operational_region_feature_policy.yaml")


def default_feature_file(root: Path) -> Path:
    return runtime_path(root, "daba", "ai_pass1", "feature_definitions.yaml")


def default_field_summary_policy_file(root: Path) -> Path:
    return runtime_path(root, "daba", "ai_pass1", "field_summary_policy.yaml")


def default_product_catalog_file(root: Path) -> Path:
    return runtime_path(root, "daba", "ai_pass1", "product_catalog.yaml")


def default_broad_map_products_file(root: Path) -> Path:
    return runtime_path(root, "daba", "ai_pass1", "broad_map_products.yaml")



def parse_required_inputs(raw_inputs: Any, feature_id: str) -> list[tuple[str, int | None]]:
    """Validate and normalize the source-variable contract for one feature.

    YAML may express each required input as either a mapping with
    ``short_name`` and ``level`` or as a two-item sequence. Surface inputs use
    ``None`` for the level; pressure-level inputs use an integer hPa level.
    """
    if not isinstance(raw_inputs, list) or not raw_inputs:
        raise ValueError(f"feature {feature_id!r}: required_inputs must be a non-empty list.")

    parsed: list[tuple[str, int | None]] = []
    for idx, item in enumerate(raw_inputs, start=1):
        if isinstance(item, dict):
            if "short_name" not in item:
                raise ValueError(f"feature {feature_id!r}: required_inputs #{idx} missing short_name.")
            short_name = str(item["short_name"]).strip()
            level_raw = item.get("level")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            short_name = str(item[0]).strip()
            level_raw = item[1]
        else:
            raise ValueError(
                f"feature {feature_id!r}: required_inputs #{idx} must be "
                "a mapping {short_name, level} or a 2-item list."
            )

        if not short_name:
            raise ValueError(f"feature {feature_id!r}: required_inputs #{idx} has empty short_name.")

        if level_raw in (None, "", "null"):
            level = None
        else:
            try:
                level = int(level_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"feature {feature_id!r}: required input level must be integer or null."
                ) from exc
        parsed.append((short_name, level))

    return parsed

def load_feature_definitions(path: Path) -> list[FeatureSpec]:
    """Load feature calculation definitions from YAML.

    Expected YAML structure:

        features:
          - feature_id: tp_accum_mm
            name_ko: 누적강수량
            units: mm
            kind: sfc
            level: null
            required_inputs:
              - short_name: tp
                level: null
            builder: single_tp_mm

    Each builder is implemented as a reusable class under func/. The YAML
    controls which feature rows are produced, their names, units, levels, and
    source variables. This keeps forecast-support policy outside Python code
    while keeping calculation logic reusable across steps.
    """
    yaml = import_yaml_module()
    if not path.exists():
        raise FileNotFoundError(f"feature definition file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("feature definition root must be a mapping with a 'features' list.")
    raw_features = data.get("features")
    if not isinstance(raw_features, list) or not raw_features:
        raise ValueError("feature definition file must contain a non-empty 'features' list.")

    required = ["feature_id", "name_ko", "units", "kind", "required_inputs", "builder"]
    seen: set[str] = set()
    specs: list[FeatureSpec] = []
    for idx, item in enumerate(raw_features, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"feature #{idx} must be a mapping.")
        missing = [key for key in required if key not in item]
        if missing:
            raise ValueError(f"feature #{idx} missing required key(s): {', '.join(missing)}")

        feature_id = str(item["feature_id"]).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", feature_id):
            raise ValueError(f"feature #{idx} has invalid feature_id: {feature_id!r}")
        if feature_id in seen:
            raise ValueError(f"duplicate feature_id in feature definition file: {feature_id}")
        seen.add(feature_id)

        kind = str(item["kind"]).strip().lower()
        if kind not in {"sfc", "pl"}:
            raise ValueError(f"feature {feature_id!r}: kind must be 'sfc' or 'pl'.")

        level_raw = item.get("level")
        if level_raw in (None, "", "null"):
            level = None
        else:
            try:
                level = int(level_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"feature {feature_id!r}: level must be integer or null.") from exc

        if kind == "sfc" and level is not None:
            raise ValueError(f"feature {feature_id!r}: sfc feature level must be null.")
        if kind == "pl" and level is None:
            raise ValueError(f"feature {feature_id!r}: pl feature level must be an integer pressure level.")

        builder = str(item["builder"]).strip()
        allowed_builders = available_builders()
        if builder not in allowed_builders:
            allowed = ", ".join(sorted(allowed_builders))
            raise ValueError(f"feature {feature_id!r}: unsupported builder {builder!r}. Available builders: {allowed}")

        specs.append(
            FeatureSpec(
                feature_id=feature_id,
                name_ko=str(item["name_ko"]).strip(),
                units=str(item["units"]).strip(),
                level=level,
                kind=kind,
                required_inputs=parse_required_inputs(item["required_inputs"], feature_id),
                builder=builder,
            )
        )
    return specs


def list_grib_files(path: Path) -> list[Path]:
    if not path.exists():
        raise FileNotFoundError(f"raw directory not found: {path}")
    files = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in RAW_EXTS and p.stat().st_size > 0)
    if not files:
        raise RuntimeError(f"no non-empty GRIB files found in {path}")
    return files


def parse_file_refs(root: Path, init: str, raw: Path) -> tuple[dict[tuple[str, int | None, int], Path], list[dict[str, Any]]]:
    index: dict[tuple[str, int | None, int], Path] = {}
    unmatched: list[dict[str, Any]] = []
    for path in list_grib_files(raw):
        m = _FILENAME_RE.match(path.name)
        if not m:
            unmatched.append({"path": rel(root, path), "reason": "filename does not match expected AIFS pattern"})
            continue
        if m.group("init") != init:
            unmatched.append({"path": rel(root, path), "reason": f"file init {m.group('init')} does not match requested init {init}"})
            continue
        step = int(m.group("maxstep") or m.group("step"))
        params = m.group("param").split("-")
        levels: list[int | None]
        if m.group("kind") == "sfc":
            levels = [None]
        else:
            levels = [int(v) for v in (m.group("levels") or "").split("-") if v]
        for param in params:
            for level in levels:
                index[(param, level, step)] = path
    return index, unmatched


def import_science_stack():
    try:
        import numpy as np  # type: ignore
        import xarray as xr  # type: ignore
        import cfgrib  # noqa: F401  # type: ignore
        return np, xr
    except ModuleNotFoundError as exc:
        missing = exc.name or "unknown"
        raise RuntimeError(
            f"required package is not installed: {missing}. "
            "Install with: pip install numpy xarray cfgrib eccodes"
        ) from exc


def open_field(path: Path, short_name: str, level: int | None, np: Any, xr: Any):
    # indexpath='' avoids creating .idx sidecar files in the project tree.
    ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    try:
        if short_name in ds.data_vars:
            arr = ds[short_name]
        elif len(ds.data_vars) == 1:
            arr = ds[list(ds.data_vars)[0]]
        else:
            raise RuntimeError(f"{path.name}: variable {short_name!r} not found; data_vars={list(ds.data_vars)}")

        if level is not None:
            arr = select_pressure_level(arr, level)
        return arr.load()
    finally:
        ds.close()


def select_pressure_level(arr: Any, level: int):
    """Select a pressure level only when the GRIB field actually has a level dimension.

    The collector often stores one parameter/step/level combination in a separate
    GRIB2 file. In that case cfgrib exposes isobaricInhPa as a scalar, 0-D
    coordinate, not as an indexable dimension. Calling arr.sel(isobaricInhPa=...)
    on such scalar coordinates raises:

        ValueError: Could not automatically create PandasIndex for coord ... with 0 dimensions

    Therefore:
    - multi-level files: select the requested level with .sel();
    - single-level files with a 1-length dimension: squeeze the level dimension;
    - single-level files with a scalar coordinate: leave the array as-is.
    """
    for coord_name in ("isobaricInhPa", "isobaricInPa", "pressure", "level"):
        if coord_name not in arr.coords and coord_name not in arr.dims:
            continue

        target = level * 100 if coord_name == "isobaricInPa" else level

        # Case 1: actual dimension exists. Multi-level files need selection;
        # one-level files can be squeezed without building an xarray index.
        if coord_name in arr.dims:
            dim_size = int(arr.sizes.get(coord_name, 0))
            if dim_size == 1:
                return arr.squeeze(dim=coord_name, drop=True)
            try:
                return arr.sel({coord_name: target}, method="nearest")
            except ValueError:
                # Some cfgrib/xarray combinations expose the coordinate but do
                # not build an index automatically. Build the xindex explicitly.
                try:
                    return arr.set_xindex(coord_name).sel({coord_name: target}, method="nearest")
                except Exception as exc:  # noqa: BLE001 - preserve context
                    raise RuntimeError(
                        f"failed to select pressure level {level} from coord {coord_name!r}; "
                        f"dims={arr.dims}, sizes={dict(arr.sizes)}"
                    ) from exc

        # Case 2: scalar coordinate only. The file is already a single level;
        # do not call .sel() on a 0-D coordinate.
        return arr

    return arr


def lat_lon_names(arr: Any) -> tuple[str, str]:
    lat_candidates = ["latitude", "lat", "y"]
    lon_candidates = ["longitude", "lon", "x"]
    lat_name = next((name for name in lat_candidates if name in arr.coords or name in arr.dims), None)
    lon_name = next((name for name in lon_candidates if name in arr.coords or name in arr.dims), None)
    if not lat_name or not lon_name:
        raise RuntimeError(f"could not identify latitude/longitude coordinates in array dims={arr.dims}")
    return lat_name, lon_name


def subset_domain(arr: Any, domain: dict[str, Any]):
    lat_name, lon_name = lat_lon_names(arr)
    lat = arr[lat_name]
    lon = arr[lon_name]
    lon_values = lon
    lon_min = float(domain["lon_min"])
    lon_max = float(domain["lon_max"])

    # AIFS global grids commonly use 0..360 longitude. If the domain file uses
    # -180..180, normalize requested bounds to match the grid. If lon_min >
    # lon_max after normalization, treat it as a dateline-crossing domain.
    try:
        grid_lon_max = float(lon.max())
        grid_lon_min = float(lon.min())
    except Exception:  # noqa: BLE001
        grid_lon_max = 360.0
        grid_lon_min = 0.0

    if grid_lon_max > 180 and (lon_min < 0 or lon_max < 0):
        lon_min = lon_min % 360
        lon_max = lon_max % 360
    elif grid_lon_min < 0 and (lon_min > 180 or lon_max > 180):
        lon_min = ((lon_min + 180) % 360) - 180
        lon_max = ((lon_max + 180) % 360) - 180

    lat_mask = (lat >= float(domain["lat_min"])) & (lat <= float(domain["lat_max"]))
    if lon_min <= lon_max:
        lon_mask = (lon_values >= lon_min) & (lon_values <= lon_max)
    else:
        lon_mask = (lon_values >= lon_min) | (lon_values <= lon_max)
    return arr.where(lat_mask & lon_mask, drop=True)


def stats_for(arr: Any, domain: dict[str, Any], np: Any) -> dict[str, Any]:
    sub = subset_domain(arr, domain)
    values = np.asarray(sub.values, dtype="float64")
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {"grid_count": 0, "mean": None, "min": None, "max": None, "p95": None, "max_location": None}
    max_location = None
    min_location = None
    try:
        lat_name, lon_name = lat_lon_names(sub)
        raw_values = np.asarray(sub.values, dtype="float64")
        max_index = np.unravel_index(int(np.nanargmax(raw_values)), raw_values.shape)
        min_index = np.unravel_index(int(np.nanargmin(raw_values)), raw_values.shape)
        lat_axis = sub.dims.index(lat_name)
        lon_axis = sub.dims.index(lon_name)
        max_location = {
            "latitude": round(float(sub[lat_name].values[max_index[lat_axis]]), 4),
            "longitude": round(float(sub[lon_name].values[max_index[lon_axis]]), 4),
        }
        min_location = {
            "latitude": round(float(sub[lat_name].values[min_index[lat_axis]]), 4),
            "longitude": round(float(sub[lon_name].values[min_index[lon_axis]]), 4),
        }
    except Exception:
        max_location = None
        min_location = None
    return {
        "grid_count": int(values.size),
        "mean": round(float(np.nanmean(values)), 6),
        "min": round(float(np.nanmin(values)), 6),
        "max": round(float(np.nanmax(values)), 6),
        "p95": round(float(np.nanpercentile(values, 95)), 6),
        "max_location": max_location,
        "min_location": min_location,
    }




def build_feature_array(
    *,
    spec: FeatureSpec,
    step: int,
    index: dict[tuple[str, int | None, int], Path],
    np: Any,
    xr: Any,
) -> tuple[Any | None, list[dict[str, Any]], list[str]]:
    component = get_component(spec.builder)

    def load_from_path(path: Path, short_name: str, level: int | None):
        return open_field(path, short_name, level, np, xr)

    context = ComponentContext(
        spec=spec,
        step=step,
        index=index,
        open_field=load_from_path,
    )
    result = component.calculate(context)
    return result.array, result.missing_inputs, [str(p) for p in result.source_files]


def sorted_forecast_hours(index: dict[tuple[str, int | None, int], Path]) -> list[int]:
    return sorted({step for _, _, step in index})


def build_rows(
    *,
    root: Path,
    init: str,
    index: dict[tuple[str, int | None, int], Path],
    specs_all: list[FeatureSpec],
    selected_features: set[str] | None,
    neutral_tiles: list[dict[str, Any]],
    selected_tiles: set[str] | None,
    operational_regions: list[dict[str, Any]],
    operational_feature_policy: dict[str, Any],
    selected_operational_regions: set[str] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Decode each feature once, then calculate neutral and operational statistics.

    Operational regions are a fixed metadata projection layer. They never replace
    neutral-tile discovery and never select candidate hours or features dynamically.
    """
    ops = load_operational_region_module()
    np, xr = import_science_stack()
    base_time = init_datetime(init)
    rows: list[dict[str, Any]] = []
    operational_rows: list[dict[str, Any]] = []
    missing_all: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    tiles = [d for d in neutral_tiles if selected_tiles is None or d["domain_id"] in selected_tiles]
    if not tiles:
        raise ValueError("no matching neutral tiles selected. Check --tiles or discovery_spatial_grid.yaml.")
    active_operational = [
        region
        for region in operational_regions
        if bool(region.get("enabled_in_pass1", False))
        and str(region.get("stage", "pass1")) == "pass1"
        and (selected_operational_regions is None or str(region["region_id"]) in selected_operational_regions)
    ]
    if selected_operational_regions is not None:
        known = {str(region["region_id"]) for region in operational_regions}
        unknown = sorted(selected_operational_regions - known)
        if unknown:
            raise ValueError(f"unknown operational region_id requested: {', '.join(unknown)}")
    if selected_features is not None:
        available_features = {s.feature_id for s in specs_all}
        unknown_features = sorted(selected_features - available_features)
        if unknown_features:
            raise ValueError(f"unknown feature_id requested in --features: {', '.join(unknown_features)}")
    specs = [s for s in specs_all if selected_features is None or s.feature_id in selected_features]
    if not specs:
        raise ValueError("no matching features selected. Check --features or feature_definitions.yaml.")
    steps = sorted_forecast_hours(index)

    for step in steps:
        valid_time = (base_time + timedelta(hours=step)).isoformat(timespec="seconds")
        for spec in specs:
            logging.info("feature=%s step=f%03d", spec.feature_id, step)
            try:
                arr, missing, source_files = build_feature_array(spec=spec, step=step, index=index, np=np, xr=xr)
                missing_all.extend(missing)
                if arr is None:
                    continue
                source_refs = ";".join(rel(root, Path(p)) for p in source_files)
                for tile in tiles:
                    try:
                        st = stats_for(arr, tile, np)
                        rows.append(
                            {
                                "init": init,
                                "forecast_hour": step,
                                "valid_time_utc": valid_time,
                                "domain_id": tile["domain_id"],
                                "domain_name_ko": tile["name_ko"],
                                "feature_id": spec.feature_id,
                                "feature_name_ko": spec.name_ko,
                                "level": spec.level,
                                "units": spec.units,
                                "grid_count": st["grid_count"],
                                "mean": st["mean"],
                                "min": st["min"],
                                "max": st["max"],
                                "p95": st["p95"],
                                "max_location": st["max_location"],
                                "min_location": st["min_location"],
                                "source_files": source_refs,
                            }
                        )
                    except Exception as exc:  # noqa: BLE001
                        logging.exception("neutral tile statistics failed: feature=%s step=%s tile=%s", spec.feature_id, step, tile["domain_id"])
                        errors.append({"scope": "neutral_tile", "feature_id": spec.feature_id, "forecast_hour": step, "spatial_id": tile["domain_id"], "error": str(exc)})
                for region in active_operational:
                    if not ops.feature_allowed_for_region(spec.feature_id, str(region["region_role"]), operational_feature_policy):
                        continue
                    try:
                        st = ops.operational_stats_for(arr=arr, region=region, np=np, root=root)
                        operational_rows.append(
                            {
                                "init": init,
                                "forecast_hour": step,
                                "valid_time_utc": valid_time,
                                "region_id": region["region_id"],
                                "region_name_ko": region["name_ko"],
                                "region_role": region["region_role"],
                                "stage": region["stage"],
                                "eligible_for_risk_rank": bool(region.get("eligible_for_risk_rank", False)),
                                "parent_region_id": region.get("parent_region_id"),
                                "overlap_group_id": region.get("overlap_group_id"),
                                "mask_type": region.get("mask_type", "mixed"),
                                "mask_status": st.get("mask_status"),
                                "feature_id": spec.feature_id,
                                "feature_name_ko": spec.name_ko,
                                "level": spec.level,
                                "units": spec.units,
                                "valid_grid_count": st.get("valid_grid_count", st.get("grid_count", 0)),
                                "mean": st.get("mean"),
                                "min": st.get("min"),
                                "max": st.get("max"),
                                "p95": st.get("p95"),
                                "max_location": st.get("max_location"),
                                "min_location": st.get("min_location"),
                                "source_files": source_refs,
                            }
                        )
                    except Exception as exc:  # noqa: BLE001
                        logging.exception("operational region statistics failed: feature=%s step=%s region=%s", spec.feature_id, step, region["region_id"])
                        errors.append({"scope": "operational_region", "feature_id": spec.feature_id, "forecast_hour": step, "spatial_id": region["region_id"], "error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                logging.exception("feature calculation failed: feature=%s step=%s", spec.feature_id, step)
                errors.append({"scope": "feature", "feature_id": spec.feature_id, "forecast_hour": step, "spatial_id": None, "error": str(exc)})

    metadata = {
        "feature_specs": [
            {
                "feature_id": s.feature_id,
                "name_ko": s.name_ko,
                "units": s.units,
                "level": s.level,
                "kind": s.kind,
                "required_inputs": s.required_inputs,
                "builder": s.builder,
            }
            for s in specs
        ],
        "neutral_tiles": tiles,
        "operational_regions": active_operational,
        "forecast_hours": steps,
        "errors": errors,
    }
    return rows, operational_rows, missing_all, metadata


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "init",
        "forecast_hour",
        "valid_time_utc",
        "domain_id",
        "domain_name_ko",
        "feature_id",
        "feature_name_ko",
        "level",
        "units",
        "grid_count",
        "mean",
        "min",
        "max",
        "p95",
        "max_location",
        "min_location",
        "signal_level",
        "source_files",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_summary(
    *,
    init: str,
    root: Path,
    raw: Path,
    inventory_dir: Path,
    operational_regions_file: Path,
    operational_feature_policy_file: Path,
    feature_file: Path,
    out: Path,
    index: dict[tuple[str, int | None, int], Path],
    unmatched_files: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    missing: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    feature_ids = sorted({row["feature_id"] for row in rows})
    neutral_tiles = sorted({row["domain_id"] for row in rows})
    operational_region_ids = sorted({str(row["region_id"]) for row in metadata.get("operational_rows", [])})
    steps = sorted({row["forecast_hour"] for row in rows})
    errors = metadata.get("errors", [])
    expected_structural_missing_count = sum(
        1
        for item in missing
        if str(item.get("reason")) == "previous step does not exist for increment"
        and int(item.get("forecast_hour", -1)) == 0
        and str(item.get("feature_id")) in {"tp_6h_increment_mm", "cp_6h_increment_mm"}
    )
    status = "success"
    if errors:
        status = "warning"
    if not rows:
        status = "failed"
    return {
        "init": init,
        "step": STEP_ID,
        "script": SCRIPT_NAME,
        "created_at_utc": now_utc(),
        "status": status,
        "raw_dir": rel(root, raw),
        "inventory_dir": rel(root, inventory_dir),
        "operational_regions_file": rel(root, operational_regions_file),
        "operational_feature_policy_file": rel(root, operational_feature_policy_file),
        "feature_definition_file": rel(root, feature_file),
        "output_dir": rel(root, out),
        "indexed_field_count": len(index),
        "unmatched_file_count": len(unmatched_files),
        "row_count": len(rows),
        "feature_count": len(feature_ids),
        "domain_count": len(neutral_tiles),
        "neutral_tile_count": len(neutral_tiles),
        "operational_region_count": len(operational_region_ids),
        "forecast_hour_count": len(steps),
        "min_forecast_hour": min(steps) if steps else None,
        "max_forecast_hour": max(steps) if steps else None,
        "features": feature_ids,
        "domains": neutral_tiles,
        "neutral_tiles": neutral_tiles,
        "operational_regions": operational_region_ids,
        "forecast_hours": steps,
        "missing_input_count": len(missing),
        "expected_unavailable_count": expected_structural_missing_count,
        "unexpected_missing_input_count": len(missing) - expected_structural_missing_count,
        "calculation_error_count": len(errors),
        "unmatched_files": unmatched_files,
        "feature_specs": metadata["feature_specs"],
        "neutral_tile_definitions": metadata["neutral_tiles"],
        "operational_region_definitions": metadata["operational_regions"],
        "errors": errors,
        "outputs": {
            "feature_timeseries_json": rel(root, out / "feature_timeseries.json"),
            "discovery_feature_catalog_json": rel(root, out / "discovery_feature_catalog.json"),
            "operational_region_catalog_json": rel(root, out / "operational_region_catalog.json"),
            "operational_region_timeseries_json": rel(root, out / "operational_region_timeseries.compact.json"),
            "operational_region_validation_report_json": rel(root, out / "operational_region_validation_report.json"),
            "discovery_spatial_coverage_validation_report_json": rel(root, out / "discovery_spatial_coverage_validation_report.json"),
            "discovery_source_panel_manifest_json": rel(root, out / "discovery_source_panel_manifest.json"),
            "discovery_image_groups_json": rel(root, out / "discovery_image_groups.json"),
            "discovery_overviews_dir": rel(root, out / "discovery_overviews"),
            "discovery_overview_validation_report_json": rel(root, out / "discovery_overview_validation_report.json"),
            "discovery_render_style_validation_report_json": rel(root, out / "discovery_render_style_validation_report.json"),
            "product_catalog_json": rel(root, out / "product_catalog.json"),
            "model_feature_package_json": rel(root, out / "model_feature_package.json"),
            "model_feature_package_summary_json": rel(root, out / "model_feature_package_summary.json"),
            "missing_feature_inputs_json": rel(root, out / "missing_feature_inputs.json"),
        },
    }


def classify_missing_feature_inputs(missing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate F000 increment inputs that cannot exist by construction."""

    classified: list[dict[str, Any]] = []
    for item in missing:
        output = dict(item)
        if (
            str(item.get("reason")) == "previous step does not exist for increment"
            and int(item.get("forecast_hour", -1)) == 0
            and str(item.get("feature_id")) in {"tp_6h_increment_mm", "cp_6h_increment_mm"}
        ):
            output["availability_class"] = "expected_structural_unavailability"
            output["availability_reason"] = (
                "F000 6-hour increment fields are structurally unavailable because no previous forecast step exists."
            )
        else:
            output["availability_class"] = "missing_input"
        classified.append(output)
    return classified

def load_field_summary_policy(path: Path) -> dict[str, Any]:
    yaml = import_yaml_module()
    if not path.exists():
        raise FileNotFoundError(f"field summary policy file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("field summary policy root must be a mapping.")
    signal_levels = data.get("signal_levels")
    if not isinstance(signal_levels, dict):
        raise ValueError("field summary policy must contain signal_levels mapping.")
    return data


def load_yaml_mapping(path: Path, *, required: bool = True) -> dict[str, Any]:
    yaml = import_yaml_module()
    if not path.exists():
        if required:
            raise FileNotFoundError(f"YAML file not found: {path}")
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def load_product_catalog_yaml(path: Path) -> dict[str, Any]:
    yaml = import_yaml_module()
    if not path.exists():
        raise FileNotFoundError(f"product catalog file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("product catalog root must be a mapping.")
    products = data.get("available_detail_products")
    if not isinstance(products, list):
        raise ValueError("product catalog must contain available_detail_products list.")

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for idx, item in enumerate(products, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"product #{idx} must be a mapping.")
        required = ["product_id", "name_ko", "description", "hazard_types"]
        missing = [key for key in required if key not in item]
        if missing:
            raise ValueError(f"product #{idx} missing required key(s): {', '.join(missing)}")
        product_id = str(item["product_id"]).strip()
        if not re.fullmatch(r"[A-Z0-9_]+", product_id):
            raise ValueError(f"product #{idx} has invalid product_id: {product_id!r}")
        if product_id in seen:
            raise ValueError(f"duplicate product_id in product catalog: {product_id}")
        seen.add(product_id)
        hazard_types = item["hazard_types"]
        if not isinstance(hazard_types, list) or not all(str(v).strip() for v in hazard_types):
            raise ValueError(f"product {product_id}: hazard_types must be a non-empty list.")
        normalized.append(
            {
                "product_id": product_id,
                "name_ko": str(item["name_ko"]).strip(),
                "description": str(item["description"]).strip(),
                "hazard_types": [str(v).strip() for v in hazard_types],
            }
        )
    return {"product_count": len(normalized), "available_detail_products": normalized}


def _safe_id_part(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value).strip().upper())
    return text.strip("_") or "NA"


def field_summary_id(row: dict[str, Any]) -> str:
    feature = _safe_id_part(row["feature_id"])
    domain = _safe_id_part(row["domain_id"])
    forecast_hour = int(row["forecast_hour"])
    return f"FS_{feature}_{domain}_F{forecast_hour:03d}"


def row_float(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def signal_level_for(row: dict[str, Any], policy: dict[str, Any]) -> str:
    thresholds = policy.get("signal_levels", {}).get(row["feature_id"])
    p95 = row_float(row, "p95")
    if not isinstance(thresholds, dict) or p95 is None:
        return "none"
    high = thresholds.get("high_p95")
    medium = thresholds.get("medium_p95")
    if high is not None and p95 >= float(high):
        return "high"
    if medium is not None and p95 >= float(medium):
        return "medium"
    return "low"


def signal_basis_for(row: dict[str, Any], policy: dict[str, Any], level: str) -> dict[str, Any]:
    """Return machine-readable threshold metadata for a field summary.

    Do not generate natural-language hints here. Interpretation belongs to
    downstream AI/manual analysis steps, not to step03 JSON production.
    """
    feature_id = row["feature_id"]
    thresholds = policy.get("signal_levels", {}).get(feature_id)
    p95 = row_float(row, "p95")
    has_threshold = isinstance(thresholds, dict)
    high_threshold = float(thresholds["high_p95"]) if has_threshold and thresholds.get("high_p95") is not None else None
    medium_threshold = float(thresholds["medium_p95"]) if has_threshold and thresholds.get("medium_p95") is not None else None

    applied_threshold_key = None
    applied_threshold_value = None
    if level == "high":
        applied_threshold_key = "high_p95"
        applied_threshold_value = high_threshold
    elif level == "medium":
        applied_threshold_key = "medium_p95"
        applied_threshold_value = medium_threshold

    return {
        "basis_statistic": "p95",
        "basis_value": p95,
        "signal_level": level,
        "has_threshold": has_threshold,
        "applied_threshold_key": applied_threshold_key,
        "applied_threshold_value": applied_threshold_value,
        "high_threshold_value": high_threshold,
        "medium_threshold_value": medium_threshold,
        "comparison_operator": ">=",
        "policy_source": "daba/ai_pass1/field_summary_policy.yaml",
        "policy_path": f"signal_levels.{feature_id}",
    }


def build_field_summaries(rows: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    stat_names = policy.get("summary_statistics", {}).get("include", ["mean", "max", "p95"])
    if not isinstance(stat_names, list):
        raise ValueError("field summary policy summary_statistics.include must be a list.")

    selection = policy.get("field_summary_selection", {})
    include_levels = set(selection.get("include_signal_levels", ["high", "medium"]))
    include_without_threshold = bool(selection.get("include_features_without_threshold", False))

    summaries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        level = signal_level_for(row, policy)
        has_threshold = row["feature_id"] in policy.get("signal_levels", {})
        if level not in include_levels and not (include_without_threshold and not has_threshold):
            continue

        summary_id = field_summary_id(row)
        if summary_id in seen:
            raise ValueError(f"duplicate field_summary_id generated: {summary_id}")
        seen.add(summary_id)

        statistics = {name: row_float(row, name) for name in stat_names}
        summaries.append(
            {
                "field_summary_id": summary_id,
                "feature_id": row["feature_id"],
                "feature_name_ko": row["feature_name_ko"],
                "domain_id": row["domain_id"],
                "domain_name_ko": row["domain_name_ko"],
                "forecast_hour": int(row["forecast_hour"]),
                "valid_time_utc": row["valid_time_utc"],
                "statistics": statistics,
                "signal_level": level,
                "signal_basis": signal_basis_for(row, policy, level),
                "source_table_ref": {
                    "file": "heavy_rain_feature_table.csv",
                    "feature_id": row["feature_id"],
                    "domain_id": row["domain_id"],
                    "forecast_hour": int(row["forecast_hour"]),
                },
            }
        )

    return {
        "summary_type": "field_summaries",
        "field_summary_count": len(summaries),
        "field_summaries": summaries,
    }


def build_vlm_image_groups(
    *,
    init: str,
    root: Path,
    out: Path,
    broad_map_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Build a lightweight grouping manifest for downstream VLM attachments.

    This function does not prepare prompts, call an AI runtime, or encode images.
    It only records how broad-map PNG files can be split into stable attachment
    groups while preserving broad_map_id, product_id, forecast_hour, and image_path.
    """
    broad_maps = [
        item for item in broad_map_manifest.get("broad_maps", [])
        if isinstance(item, dict) and item.get("status") == "created"
    ]
    broad_maps.sort(key=lambda item: (str(item.get("product_id", "")), int(item.get("forecast_hour") or -1), str(item.get("broad_map_id", ""))))

    groups_by_product: list[dict[str, Any]] = []
    product_ids = sorted({str(item.get("product_id")) for item in broad_maps if item.get("product_id")})
    for product_id in product_ids:
        product_items = [item for item in broad_maps if str(item.get("product_id")) == product_id]
        product_items.sort(key=lambda item: (int(item.get("forecast_hour") or -1), str(item.get("broad_map_id", ""))))
        forecast_hours = [int(item["forecast_hour"]) for item in product_items if item.get("forecast_hour") is not None]
        groups_by_product.append(
            {
                "group_id": f"VLM_GROUP_{_safe_id_part(product_id)}",
                "group_type": "by_product",
                "product_id": product_id,
                "image_count": len(product_items),
                "forecast_hours": forecast_hours,
                "forecast_hour_min": min(forecast_hours) if forecast_hours else None,
                "forecast_hour_max": max(forecast_hours) if forecast_hours else None,
                "recommended_use_ko": "동일 물리량의 시간 변화와 공간 이동을 먼저 판독하는 VLM 분할 전달 묶음",
                "attachments": [
                    {
                        "broad_map_id": item.get("broad_map_id"),
                        "product_id": item.get("product_id"),
                        "forecast_hour": item.get("forecast_hour"),
                        "valid_time_utc": item.get("valid_time_utc"),
                        "image_path": item.get("image_path"),
                        "package_relative_image_path": item.get("package_relative_image_path"),
                        "image_filename": item.get("image_filename"),
                        "render_domain_id": item.get("render_domain_id"),
                        "trigger_domain_id": item.get("trigger_domain_id"),
                        "linked_field_summary_ids": item.get("linked_field_summary_ids", []),
                    }
                    for item in product_items
                ],
            }
        )

    all_forecast_hours = sorted({int(item["forecast_hour"]) for item in broad_maps if item.get("forecast_hour") is not None})
    return {
        "init": init,
        "manifest_type": "vlm_image_groups",
        "created_at_utc": now_utc(),
        "source_broad_map_manifest": rel(root, out / "broad_map_manifest.json"),
        "attachment_mode": "file_reference",
        "image_encoding": "none",
        "recommended_transfer_mode": "by_product",
        "grouping_policy": {
            "primary_grouping": "product_id",
            "preserve_original_png_filenames": True,
            "preserve_broad_map_id": True,
            "do_not_use_without_broad_map_manifest": True,
        },
        "summary": {
            "group_count": len(groups_by_product),
            "image_count": len(broad_maps),
            "product_count": len(product_ids),
            "forecast_hour_count": len(all_forecast_hours),
            "forecast_hour_min": min(all_forecast_hours) if all_forecast_hours else None,
            "forecast_hour_max": max(all_forecast_hours) if all_forecast_hours else None,
        },
        "groups_by_product": groups_by_product,
        "notes": [
            "This file is a downstream VLM attachment grouping aid only.",
            "Step03 does not generate prompts, AI responses, or base64-encoded images.",
            "Each attachment image_path must be resolved together with broad_map_manifest.json.",
            "Use package_relative_image_path when the feature_package is transferred as a standalone ZIP.",
        ],
    }


def _known_limitation_notes(missing: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    missing_pairs: set[tuple[str, int | None]] = set()
    for item in missing:
        forecast_hour_raw = item.get("forecast_hour")
        try:
            forecast_hour = int(forecast_hour_raw) if forecast_hour_raw is not None else None
        except (TypeError, ValueError):
            forecast_hour = None
        missing_pairs.add((str(item.get("feature_id")), forecast_hour))
    if ("tp_6h_increment_mm", 0) in missing_pairs and ("cp_6h_increment_mm", 0) in missing_pairs:
        notes.append("F000 6시간 강수 증분은 이전 예측시간이 없어 계산하지 않음")
    return notes


def build_vlm_context_summary(
    *,
    init: str,
    root: Path,
    out: Path,
    raw: Path,
    summary: dict[str, Any],
    field_summaries: dict[str, Any],
    product_catalog: dict[str, Any],
    broad_map_manifest: dict[str, Any],
    vlm_image_groups: dict[str, Any],
    missing: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build compact context metadata for downstream VLM request builders."""
    raw_excluded = bool(broad_map_manifest.get("raw_excluded", True))
    return {
        "init": init,
        "summary_type": "vlm_context_summary",
        "created_at_utc": now_utc(),
        "scope": "step03_output_context_for_downstream_vlm",
        "step03_role": "정량 feature, field summary, broad map 이미지와 VLM 분할 전달용 색인을 생성",
        "not_in_step03": [
            "AI/VLM API 호출",
            "AI/VLM 응답 생성",
            "AI/VLM 프롬프트 생성",
            "위험기상 후보 확정",
            "이미지 base64 인코딩",
        ],
        "raw_policy": {
            "raw_dir": rel(root, raw),
            "raw_excluded": raw_excluded,
            "source_files_are_reference_paths": bool(broad_map_manifest.get("source_files_are_reference_paths", raw_excluded)),
            "raw_required_for_regeneration": bool(broad_map_manifest.get("raw_required_for_regeneration", True)),
        },
        "counts": {
            "row_count": summary.get("row_count"),
            "field_summary_count": field_summaries.get("field_summary_count"),
            "product_count": len(product_catalog.get("available_detail_products", [])),
            "broad_map_count": broad_map_manifest.get("broad_map_count"),
            "broad_map_item_count": broad_map_manifest.get("item_count"),
            "vlm_group_count": vlm_image_groups.get("summary", {}).get("group_count"),
            "missing_input_count": summary.get("missing_input_count"),
            "calculation_error_count": summary.get("calculation_error_count"),
        },
        "forecast_hour_range": {
            "min": summary.get("min_forecast_hour"),
            "max": summary.get("max_forecast_hour"),
            "broad_map_min": vlm_image_groups.get("summary", {}).get("forecast_hour_min"),
            "broad_map_max": vlm_image_groups.get("summary", {}).get("forecast_hour_max"),
        },
        "core_files_for_vlm": [
            {"role": "quantitative_summary", "path": rel(root, out / "field_summaries.json"), "required": True},
            {"role": "image_manifest", "path": rel(root, out / "broad_map_manifest.json"), "required": True},
            {"role": "image_group_manifest", "path": rel(root, out / "vlm_image_groups.json"), "required": True},
            {"role": "detail_product_catalog", "path": rel(root, out / "product_catalog.json"), "required": True},
            {"role": "full_package_index", "path": rel(root, out / "model_feature_package.json"), "required": False},
            {"role": "feature_table", "path": rel(root, out / "heavy_rain_feature_table.csv"), "required": False},
            {"role": "missing_input_record", "path": rel(root, out / "missing_feature_inputs.json"), "required": False},
        ],
        "recommended_vlm_transfer": {
            "primary_split": "vlm_image_groups.groups_by_product",
            "common_context_files": [
                rel(root, out / "field_summaries.json"),
                rel(root, out / "broad_map_manifest.json"),
                rel(root, out / "product_catalog.json"),
                rel(root, out / "vlm_context_summary.json"),
            ],
            "image_reference_file": rel(root, out / "vlm_image_groups.json"),
        },
        "known_limitations": _known_limitation_notes(missing),
        "ai_reading_notes": [
            "field_summaries.json is a threshold-hit oriented summary focused on medium/high signals, not a full-field no-signal description.",
            "Compare product groups by forecast_hour and valid_time_utc, not by attachment order alone.",
            "QL_TCW_WIND850 shaded colors represent total column water, and arrows represent 850 hPa wind vectors.",
            "QL_PAIRED_WIND_SPEED_850 shows 850 hPa wind speed only.",
        ],
    }


def build_model_feature_package(
    *,
    init: str,
    root: Path,
    raw: Path,
    inventory_dir: Path,
    operational_regions_file: Path,
    operational_feature_policy_file: Path,
    feature_file: Path,
    field_summary_policy_file: Path,
    product_catalog_file: Path,
    discovery_source_panel_products_file: Path,
    discovery_spatial_grid_file: Path,
    discovery_execution_policy_file: Path,
    out: Path,
    summary: dict[str, Any],
    product_count: int,
    overview_source_panel_count: int,
    discovery_group_count: int,
    overview_count: int,
    output_flags: dict[str, bool],
    output_policy: OutputPolicy,
) -> dict[str, Any]:
    optional_outputs: dict[str, Any] = {
        "rule_qa": rel(root, out / "rule_qa") if output_flags["with_rule_qa"] else None,
        "csv_export": rel(root, out / "heavy_rain_feature_table.csv") if output_flags["export_csv"] else None,
    }
    compact_summary = {
        "feature_count": summary["feature_count"],
        "domain_count": summary["domain_count"],
        "forecast_hour_count": summary["forecast_hour_count"],
        "row_count": summary["row_count"],
        "product_count": product_count,
        "overview_source_panel_count": overview_source_panel_count,
        "discovery_group_count": discovery_group_count,
        "overview_count": overview_count,
        "rule_qa_enabled": output_flags["with_rule_qa"],
        "csv_exported": output_flags["export_csv"],
        "missing_input_count": summary["missing_input_count"],
        "calculation_error_count": summary["calculation_error_count"],
        "pass1_product_forecast_hours": {},
        "pass1_tier_forecast_hours": {},
        "operational_region_count": summary.get("operational_region_count", 0),
        "operational_region_row_count": summary.get("operational_region_row_count", 0),
    }
    outputs = {
        "feature_timeseries": rel(root, out / "feature_timeseries.json"),
        "discovery_feature_catalog": rel(root, out / "discovery_feature_catalog.json"),
        "operational_region_catalog": rel(root, out / "operational_region_catalog.json"),
        "operational_region_timeseries": rel(root, out / "operational_region_timeseries.compact.json"),
        "discovery_source_panel_manifest": rel(root, out / "discovery_source_panel_manifest.json"),
        "discovery_overviews_dir": rel(root, out / "discovery_overviews"),
        "discovery_overview_manifest": rel(root, out / "discovery_overview_manifest.json"),
        "product_catalog": rel(root, out / "product_catalog.json"),
        "run_validation_summary": rel(root, out / "run_validation_summary.json"),
        "optional_outputs": optional_outputs,
    }
    if output_policy.should_write("detailed_validation"):
        outputs.update(
            {
                "operational_region_validation_report": rel(root, out / "operational_region_validation_report.json"),
                "discovery_spatial_coverage_validation_report": rel(root, out / "discovery_spatial_coverage_validation_report.json"),
                "discovery_image_groups": rel(root, out / "discovery_image_groups.json"),
                "discovery_render_style_validation_report": rel(root, out / "discovery_render_style_validation_report.json"),
                "missing_feature_inputs": rel(root, out / "missing_feature_inputs.json"),
                "summary": rel(root, out / "model_feature_package_summary.json"),
            }
        )
    inputs = {
        "raw_data_dir": rel(root, raw),
        "inventory_summary": rel(root, inventory_dir / "inventory_summary.json"),
    }
    if output_policy.should_write("detailed_validation"):
        inputs["variable_inventory"] = rel(root, inventory_dir / "variable_inventory.json")
    return {
        "init": init,
        "model": "AIFS",
        "package_type": "ai_discovery_feature_package",
        "created_at_utc": now_utc(),
        "inputs": inputs,
        "configuration": {
            "operational_regions_file": rel(root, operational_regions_file),
            "operational_feature_policy_file": rel(root, operational_feature_policy_file),
            "feature_definition_file": rel(root, feature_file),
            "field_summary_policy_file": rel(root, field_summary_policy_file),
            "product_catalog_file": rel(root, product_catalog_file),
            "discovery_source_panel_products_file": rel(root, discovery_source_panel_products_file),
            "discovery_spatial_grid_file": rel(root, discovery_spatial_grid_file),
            "discovery_execution_policy_file": rel(root, discovery_execution_policy_file),
        },
        "outputs": outputs,
        "output_policy": {
            "core_outputs_only_by_default": True,
            "overview_is_core_pass1_input": True,
            **output_flags,
            **output_policy.manifest_fields(["validation_summary", "detailed_validation"]),
        },
        "summary": compact_summary,
    }

def parse_csv_set(values: str | None) -> set[str] | None:
    if not values:
        return None
    return {v.strip() for v in values.split(",") if v.strip()}


def build_neutral_tiles(config: dict[str, Any]) -> list[dict[str, Any]]:
    domain = config["domain"]
    size = config["tile_size_degrees"]
    lon_step = float(size["longitude"])
    lat_step = float(size["latitude"])
    tiles: list[dict[str, Any]] = []
    lat = float(domain["lat_min"])
    while lat < float(domain["lat_max"]):
        lon = float(domain["lon_min"])
        lat_max = min(lat + lat_step, float(domain["lat_max"]))
        while lon < float(domain["lon_max"]):
            lon_max = min(lon + lon_step, float(domain["lon_max"]))
            token = lambda value: (f"{value:.2f}".rstrip("0").rstrip(".").replace("-", "M").replace(".", "P"))
            tile_id = f"TILE_LAT{token(lat)}_{token(lat_max)}_LON{token(lon)}_{token(lon_max)}"
            tiles.append({"domain_id": tile_id, "name_ko": tile_id, "lat_min": lat, "lat_max": lat_max, "lon_min": lon, "lon_max": lon_max})
            lon = lon_max
        lat = lat_max
    return tiles


def _normalized_bbox(value: dict[str, Any], *, label: str) -> dict[str, float]:
    required = ("lon_min", "lon_max", "lat_min", "lat_max")
    missing = [key for key in required if key not in value]
    if missing:
        raise ValueError(f"{label} missing bbox key(s): {', '.join(missing)}")
    bbox = {key: float(value[key]) for key in required}
    if bbox["lon_min"] >= bbox["lon_max"] or bbox["lat_min"] >= bbox["lat_max"]:
        raise ValueError(f"{label} has invalid bbox ordering: {bbox}")
    return bbox


def validate_discovery_spatial_coverage(
    discovery_grid: dict[str, Any], source_panel_config: dict[str, Any]
) -> dict[str, Any]:
    """중립 tile과 discovery 지도 영역이 동일한지 검증한다."""

    grid_bbox = _normalized_bbox(discovery_grid.get("domain", {}), label="discovery spatial grid")
    map_bbox = _normalized_bbox(source_panel_config.get("default_domain", {}), label="source-panel map domain")
    tolerance = 1.0e-9
    mismatches = {
        key: {"spatial_grid": grid_bbox[key], "map_domain": map_bbox[key]}
        for key in grid_bbox
        if abs(grid_bbox[key] - map_bbox[key]) > tolerance
    }
    status = "aligned" if not mismatches else "mismatch"
    report = {
        "spatial_coverage_status": status,
        "spatial_grid_id": discovery_grid.get("grid_id"),
        "spatial_grid_version": discovery_grid.get("grid_version"),
        "render_domain_id": source_panel_config.get("render_domain_id"),
        "spatial_grid_bbox": grid_bbox,
        "map_domain_bbox": map_bbox,
        "mismatches": mismatches,
    }
    if status != "aligned":
        raise ValueError(
            "discovery spatial grid bbox must exactly match discovery map bbox: "
            f"grid={grid_bbox}, map={map_bbox}"
        )
    return report


def remove_output_path(path: Path) -> None:
    """이전 실행의 선택 산출물을 안전하게 제거한다."""

    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def cleanup_optional_outputs(out: Path, *, with_rule_qa: bool, generate_overviews: bool, export_csv: bool) -> None:
    """--overwrite 실행 시 현재 출력 정책에 포함되지 않는 stale 파일을 제거한다."""

    always_remove = [
        out / "discovery_tile_summaries.json",
        out / "field_summaries.json",
        out / "broad_map_manifest.json",
        out / "vlm_image_groups.json",
        out / "vlm_context_summary.json",
        out / "broad_maps",
    ]
    conditional = []
    if not with_rule_qa:
        conditional.append(out / "rule_qa")
    if not generate_overviews:
        conditional.extend([out / "discovery_overview_manifest.json", out / "discovery_overviews"])
    if not export_csv:
        conditional.append(out / "heavy_rain_feature_table.csv")
    for target in [*always_remove, *conditional]:
        remove_output_path(target)


def cleanup_legacy_discovery_outputs(out: Path) -> list[Path]:
    """Remove only obsolete per-panel discovery assets from this feature package."""

    removed: list[Path] = []
    for target in [
        out / "discovery_broad_maps",
        out / "discovery_broad_map_manifest.json",
    ]:
        if target.exists():
            remove_output_path(target)
            removed.append(target)
    return removed


def resolve_output_flags(args: argparse.Namespace, execution_policy: dict[str, Any]) -> dict[str, bool]:
    step03_policy = execution_policy.get("output_policy", {}).get("step03", {})
    overview_policy = execution_policy.get("overview_input_policy", {})
    return {
        "with_rule_qa": bool(args.with_rule_qa or step03_policy.get("generate_rule_qa_by_default", False)),
        "generate_overviews": bool(
            args.generate_overviews
            or step03_policy.get("generate_overviews_by_default", False)
            or overview_policy.get("enabled", False)
        ),
        "export_csv": bool(args.export_csv or step03_policy.get("export_csv_by_default", False)),
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="AIFSHeavyRain step03: build Model Feature Package.")
    p.add_argument("--init", required=True, help="Forecast init in YYYYMMDDHH, e.g. 2026063000")
    p.add_argument("--root", default=".", help="Project root. Default: current directory")
    p.add_argument("--raw-dir", default=None, help="Override raw AIFS GRIB directory")
    p.add_argument("--inventory-dir", default=None, help="Override step02 inventory directory")
    p.add_argument("--output-dir", default=None, help="Override feature output directory")
    p.add_argument(
        "--operational-regions-file",
        default=None,
        help="Override operational region YAML. Default: daba/ai_pass1/discovery_operational_regions.yaml",
    )
    p.add_argument(
        "--operational-feature-policy-file",
        default=None,
        help="Override operational region feature policy YAML.",
    )
    p.add_argument(
        "--feature-file",
        default=None,
        help="Override feature definition YAML. Default: daba/ai_pass1/feature_definitions.yaml",
    )
    p.add_argument(
        "--field-summary-policy-file",
        default=None,
        help="Override field summary policy YAML. Default: daba/ai_pass1/field_summary_policy.yaml",
    )
    p.add_argument(
        "--product-catalog-file",
        default=None,
        help="Override product catalog YAML. Default: daba/ai_pass1/product_catalog.yaml",
    )
    p.add_argument("--features", default=None, help="Comma-separated feature_id subset")
    p.add_argument("--tiles", default=None, help="Comma-separated neutral tile_id subset")
    p.add_argument("--operational-regions", default=None, help="Comma-separated operational region_id subset")
    p.add_argument("--fail-on-missing", action="store_true", help="Return failure when any feature input is missing")
    p.add_argument("--skip-rule-qa-maps", action="store_true", help="Do not render optional Rule-QA broad maps")
    p.add_argument("--broad-map-products-file", default=None, help="Override broad map product YAML. Default: daba/ai_pass1/broad_map_products.yaml")
    p.add_argument("--broad-map-products", default=None, help="Comma-separated broad-map product_id subset")
    p.add_argument("--max-broad-maps-per-product", type=int, default=12, help="Maximum broad maps to render per product. Default: 12")
    p.add_argument("--discovery-source-panel-products-file", default=None)
    p.add_argument("--discovery-spatial-grid-file", default=None)
    p.add_argument("--discovery-execution-policy-file", default=None)
    p.add_argument("--with-rule-qa", action="store_true", help="규칙 기반 QA 산출물을 추가 생성합니다.")
    p.add_argument("--generate-overviews", action="store_true", help="시간 패널 overview를 생성합니다. 현 AI-first 정책에서는 기본 생성됩니다.")
    p.add_argument("--export-csv", action="store_true", help="사람 확인용 heavy_rain_feature_table.csv를 추가 생성합니다.")
    p.add_argument("--overwrite", action="store_true", help="기존 step03 산출물을 명시적으로 갱신합니다.")
    add_output_mode_argument(p)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    try:
        output_policy = resolve_output_mode(args.output_mode)
    except ValueError as exc:
        print(f"step03 failed: {exc}", file=sys.stderr)
        return 2
    try:
        validate_init(args.init)
    except ValueError as exc:
        print(f"step03 실패: 설정 오류 - {exc}", file=sys.stderr)
        return 2

    setup_logging(log_path(root, args.init))
    raw = Path(args.raw_dir).resolve() if args.raw_dir else default_raw_dir(root, args.init)
    inventory_dir = Path(args.inventory_dir).resolve() if args.inventory_dir else default_inventory_dir(root, args.init)
    out = Path(args.output_dir).resolve() if args.output_dir else default_output_dir(root, args.init)
    operational_regions_file = Path(args.operational_regions_file).resolve() if args.operational_regions_file else default_operational_regions_file(root)
    operational_feature_policy_file = Path(args.operational_feature_policy_file).resolve() if args.operational_feature_policy_file else default_operational_feature_policy_file(root)
    feature_file = Path(args.feature_file).resolve() if args.feature_file else default_feature_file(root)
    field_summary_policy_file = (
        Path(args.field_summary_policy_file).resolve()
        if args.field_summary_policy_file
        else default_field_summary_policy_file(root)
    )
    product_catalog_file = (
        Path(args.product_catalog_file).resolve()
        if args.product_catalog_file
        else default_product_catalog_file(root)
    )
    broad_map_products_file = (
        Path(args.broad_map_products_file).resolve()
        if args.broad_map_products_file
        else default_broad_map_products_file(root)
    )
    discovery_source_panel_products_file = Path(args.discovery_source_panel_products_file).resolve() if args.discovery_source_panel_products_file else runtime_path(root, "daba", "ai_pass1", "discovery_source_panel_products.yaml")
    discovery_spatial_grid_file = Path(args.discovery_spatial_grid_file).resolve() if args.discovery_spatial_grid_file else runtime_path(root, "daba", "ai_pass1", "discovery_spatial_grid.yaml")
    discovery_execution_policy_file = Path(args.discovery_execution_policy_file).resolve() if args.discovery_execution_policy_file else runtime_path(root, "daba", "ai_pass1", "discovery_execution_policy.yaml")

    try:
        discovery_execution_policy = load_yaml_mapping(discovery_execution_policy_file)
        output_flags = resolve_output_flags(args, discovery_execution_policy)
        if not output_policy.should_write("detailed_validation"):
            output_flags["with_rule_qa"] = False
            output_flags["export_csv"] = False
        for removed_path in cleanup_legacy_discovery_outputs(out):
            logging.info("legacy discovery directory removed: %s", removed_path)
        if args.overwrite:
            cleanup_optional_outputs(out, **output_flags)
        ops = load_operational_region_module()
        discovery_grid = load_yaml_mapping(discovery_spatial_grid_file)
        discovery_source_panel_config = load_yaml_mapping(discovery_source_panel_products_file)
        discovery_spatial_coverage = validate_discovery_spatial_coverage(discovery_grid, discovery_source_panel_config)
        neutral_tiles = build_neutral_tiles(discovery_grid)
        operational_regions = ops.load_operational_regions(operational_regions_file)
        operational_feature_policy = ops.load_operational_feature_policy(operational_feature_policy_file)
        operational_definition_validation = ops.validate_operational_regions(
            regions=operational_regions,
            feature_policy=operational_feature_policy,
            neutral_grid_bbox=discovery_spatial_coverage["spatial_grid_bbox"],
            expected_counts=discovery_execution_policy.get("spatial_hierarchy_policy", {}).get("expected_region_counts", {}),
            statistics_rows=None,
        )
        if operational_definition_validation.get("validation_status") != "pass":
            raise ValueError("operational region definition validation failed")
        specs_all = load_feature_definitions(feature_file)
        field_summary_policy = load_field_summary_policy(field_summary_policy_file)
        product_catalog = load_product_catalog_yaml(product_catalog_file)
        index, unmatched = parse_file_refs(root, args.init, raw)
        if not index:
            raise RuntimeError("no usable AIFS GRIB files were indexed from filenames")
        rows, operational_rows, missing, metadata = build_rows(
            root=root,
            init=args.init,
            index=index,
            specs_all=specs_all,
            selected_features=parse_csv_set(args.features),
            neutral_tiles=neutral_tiles,
            selected_tiles=parse_csv_set(args.tiles),
            operational_regions=operational_regions,
            operational_feature_policy=operational_feature_policy,
            selected_operational_regions=parse_csv_set(args.operational_regions),
        )
        metadata["operational_rows"] = operational_rows
        operational_region_validation = ops.validate_operational_regions(
            regions=operational_regions,
            feature_policy=operational_feature_policy,
            neutral_grid_bbox=discovery_spatial_coverage["spatial_grid_bbox"],
            expected_counts=discovery_execution_policy.get("spatial_hierarchy_policy", {}).get("expected_region_counts", {}),
            statistics_rows=operational_rows,
        )
        if operational_region_validation.get("validation_status") != "pass":
            raise ValueError("operational region statistics validation failed")
        summary = build_summary(
            init=args.init,
            root=root,
            raw=raw,
            inventory_dir=inventory_dir,
            operational_regions_file=operational_regions_file,
            operational_feature_policy_file=operational_feature_policy_file,
            feature_file=feature_file,
            out=out,
            index=index,
            unmatched_files=unmatched,
            rows=rows,
            missing=missing,
            metadata=metadata,
        )
        field_summaries = (
            build_field_summaries(rows, field_summary_policy)
            if output_flags["with_rule_qa"]
            else {
                "init": args.init,
                "summary_type": "field_summaries",
                "field_summary_count": 0,
                "field_summaries": [],
            }
        )
        broad_map_manifest = {
            "init": args.init,
            "manifest_type": "rule_trigger_map_manifest",
            "created_at_utc": now_utc(),
            "broad_map_count": 0,
            "item_count": 0,
            "broad_maps": [],
            "classification": "rule_qa",
            "core_discovery_input": False,
        }
        np_maps, xr_maps = import_science_stack()
        specs_by_id = {spec.feature_id: spec for spec in specs_all}

        def feature_array_loader(feature_id: str, step: int):
            spec = specs_by_id.get(feature_id)
            if spec is None:
                return None, [{"feature_id": feature_id, "forecast_hour": step, "reason": "unknown feature_id"}], [], None
            arr, miss, src = build_feature_array(spec=spec, step=step, index=index, np=np_maps, xr=xr_maps)
            return arr, miss, [rel(root, Path(p)) for p in src], spec

        def raw_field_loader(short_name: str, level: int | None, step: int):
            source_path = index.get((short_name, level, step))
            if source_path is None:
                return None, [{"short_name": short_name, "level": level, "forecast_hour": step, "reason": "raw field not found"}], []
            arr = open_field(source_path, short_name, level, np_maps, xr_maps)
            return arr, [], [rel(root, source_path)]

        if output_flags["with_rule_qa"] and not args.skip_rule_qa_maps:
            broad_map_config = load_yaml_mapping(broad_map_products_file)
            broad_map_config = dict(broad_map_config)
            broad_map_config["output_subdir"] = "rule_qa/broad_maps"
            broad_map_config["asset_id_prefix"] = "RULE"
            broad_map_manifest = build_broad_maps(
                init=args.init,
                root=root,
                output_dir=out,
                field_summaries_payload=field_summaries,
                broad_map_config=broad_map_config,
                feature_array_loader=feature_array_loader,
                raw_field_loader=raw_field_loader,
                relpath_func=lambda path: rel(root, path),
                max_maps_per_product=max(1, int(args.max_broad_maps_per_product)),
                selected_products=parse_csv_set(args.broad_map_products),
            )
            field_summaries = attach_broad_map_links(field_summaries, broad_map_manifest)

        discovery_source_panel_manifest = build_discovery_source_manifest(
            init=args.init,
            source_panel_config=discovery_source_panel_config,
            root=root,
        )

        feature_specs_payload = [
            {"feature_id": s.feature_id, "name_ko": s.name_ko, "units": s.units}
            for s in specs_all
        ]
        for row in rows:
            row["signal_level"] = signal_level_for(row, field_summary_policy)
        tile_ids = {tile["domain_id"] for tile in neutral_tiles}
        feature_timeseries, discovery_tile_summaries, discovery_feature_catalog = build_feature_timeseries(
            init=args.init,
            rows=rows,
            feature_specs=feature_specs_payload,
            tile_ids=tile_ids,
            spatial_grid_bbox=discovery_spatial_coverage["spatial_grid_bbox"],
            map_domain_bbox=discovery_spatial_coverage["map_domain_bbox"],
            spatial_coverage_status=discovery_spatial_coverage["spatial_coverage_status"],
        )
        operational_region_catalog = ops.build_operational_region_catalog(
            init=args.init,
            regions=operational_regions,
            feature_policy=operational_feature_policy,
        )
        operational_region_timeseries = ops.build_operational_region_timeseries(
            init=args.init,
            rows=operational_rows,
            catalog=operational_region_catalog,
        )
        hierarchy_refs = {
            "neutral_feature_timeseries_ref": rel(root, out / "feature_timeseries.json"),
            "operational_region_catalog_ref": rel(root, out / "operational_region_catalog.json"),
            "operational_region_timeseries_ref": rel(root, out / "operational_region_timeseries.compact.json"),
            "operational_region_validation_report_ref": rel(root, out / "operational_region_validation_report.json"),
        }
        feature_timeseries["spatial_hierarchy"] = hierarchy_refs
        discovery_feature_catalog["spatial_hierarchy"] = hierarchy_refs
        discovery_image_groups = build_source_panel_groups(args.init, discovery_source_panel_manifest)
        if output_flags["generate_overviews"]:
            discovery_overview_manifest = build_discovery_overviews(
                init=args.init,
                root=root,
                output_dir=out,
                source_panel_config=discovery_source_panel_config,
                relpath_func=lambda path: rel(root, path),
                overview_policy=discovery_execution_policy.get("overview_input_policy", {}),
                feature_array_loader=feature_array_loader,
                raw_field_loader=raw_field_loader,
            )
            discovery_source_panel_manifest = enrich_source_manifest_with_overview_links(
                discovery_source_panel_manifest,
                discovery_overview_manifest,
            )
            discovery_overview_validation = build_discovery_overview_validation_report(
                root=root,
                source_panel_manifest=discovery_source_panel_manifest,
                overview_manifest=discovery_overview_manifest,
                source_panel_config=discovery_source_panel_config,
            )
            if discovery_overview_manifest.get("validation_status") != "pass" or discovery_overview_validation["validation_status"] != "pass":
                raise RuntimeError(
                    "discovery overview validation failed: "
                    f"{len(discovery_overview_manifest.get('errors', [])) + len(discovery_overview_validation.get('errors', []))} error(s)"
                )
        else:
            discovery_overview_manifest = {
                "init": args.init,
                "manifest_type": "discovery_overview_manifest",
                "created_at_utc": now_utc(),
                "overview_count": 0,
                "overviews": [],
                "generated": False,
                "validation_status": "fail",
                "errors": [{"code": "OVERVIEW_GENERATION_DISABLED"}],
                "warnings": [],
            }
            discovery_overview_validation = {
                "init": args.init,
                "report_type": "discovery_overview_validation_report",
                "created_at_utc": now_utc(),
                "validation_status": "fail",
                "errors": [{"code": "OVERVIEW_GENERATION_DISABLED"}],
                "warnings": [],
            }
        discovery_style_validation = {
            "init": args.init,
            "report_type": "discovery_render_style_validation_report",
            "created_at_utc": now_utc(),
            "style_validation_status": "pass" if discovery_overview_manifest.get("validation_status") == "pass" else "fail",
            "style_error_count": len(discovery_overview_manifest.get("errors", [])),
            "style_warning_count": len(discovery_overview_manifest.get("warnings", [])),
            "rendering_mode": discovery_overview_manifest.get("rendering_mode", "direct_matplotlib_multipanel"),
            "expected_overview_count": discovery_overview_manifest.get("expected_overview_count"),
            "overview_count": discovery_overview_manifest.get("overview_count"),
            "warnings": discovery_overview_manifest.get("warnings", []),
            "errors": discovery_overview_manifest.get("errors", []),
        }
        if discovery_style_validation["style_validation_status"] != "pass":
            raise RuntimeError(
                "discovery render style validation failed: "
                f"{discovery_style_validation['style_error_count']} error group(s)"
            )
        product_catalog = dict(product_catalog)
        product_catalog["discovery_products"] = [
            {key: item.get(key) for key in ["product_id", "product_tier", "required_for_pass1", "cadence_hours", "lead_hours", "display_title", "plot_type", "unit_label", "contour_label", "vector_unit_label", "meaning"]}
            for item in discovery_source_panel_config.get("products", []) if item.get("enabled", True)
        ]

        summary["operational_region_count"] = operational_region_catalog.get("pass1_enabled_region_count", 0)
        summary["operational_region_row_count"] = operational_region_timeseries.get("row_count", 0)
        model_feature_package = build_model_feature_package(
            init=args.init,
            root=root,
            raw=raw,
            inventory_dir=inventory_dir,
            operational_regions_file=operational_regions_file,
            operational_feature_policy_file=operational_feature_policy_file,
            feature_file=feature_file,
            field_summary_policy_file=field_summary_policy_file,
            product_catalog_file=product_catalog_file,
            discovery_source_panel_products_file=discovery_source_panel_products_file,
            discovery_spatial_grid_file=discovery_spatial_grid_file,
            discovery_execution_policy_file=discovery_execution_policy_file,
            out=out,
            summary=summary,
            product_count=len(product_catalog["available_detail_products"]),
            overview_source_panel_count=discovery_source_panel_manifest["source_panel_count"],
            discovery_group_count=discovery_image_groups.get("group_count", len(discovery_image_groups.get("groups", []))),
            overview_count=int(discovery_overview_manifest.get("overview_count", 0)),
            output_flags=output_flags,
            output_policy=output_policy,
        )
        model_feature_package["summary"]["pass1_product_forecast_hours"] = discovery_source_panel_manifest.get("product_forecast_hours", {})
        model_feature_package["summary"]["pass1_tier_forecast_hours"] = discovery_source_panel_manifest.get("tier_forecast_hours", {})
        model_feature_package["summary"]["required_pass1_product_ids"] = discovery_source_panel_manifest.get("required_pass1_product_ids", [])
        out.mkdir(parents=True, exist_ok=True)
        if output_flags["export_csv"]:
            write_csv(out / "heavy_rain_feature_table.csv", rows)
        classified_missing_inputs = classify_missing_feature_inputs(missing)
        expected_unavailable_count = sum(
            1 for item in classified_missing_inputs
            if item["availability_class"] == "expected_structural_unavailability"
        )
        summary["expected_unavailable_count"] = expected_unavailable_count
        summary["unexpected_missing_input_count"] = len(classified_missing_inputs) - expected_unavailable_count
        write_json(out / "product_catalog.json", product_catalog)
        write_json(out / "model_feature_package.json", model_feature_package)
        write_json(out / "feature_timeseries.json", feature_timeseries)
        write_json(out / "discovery_feature_catalog.json", discovery_feature_catalog)
        write_json(out / "operational_region_catalog.json", operational_region_catalog)
        write_json(out / "operational_region_timeseries.compact.json", operational_region_timeseries)
        write_json(out / "discovery_source_panel_manifest.json", discovery_source_panel_manifest)
        if output_flags["generate_overviews"]:
            write_json(out / "discovery_overview_manifest.json", discovery_overview_manifest)
        run_validation_summary = {
            "init": args.init,
            "report_type": "step03_run_validation_summary",
            "created_at_utc": now_utc(),
            "validation_status": "pass",
            "operational_region_validation_status": operational_region_validation.get("validation_status"),
            "discovery_spatial_coverage_status": discovery_spatial_coverage.get("validation_status"),
            "discovery_overview_validation_status": discovery_overview_validation.get("validation_status"),
            "discovery_render_style_validation_status": discovery_style_validation.get("style_validation_status"),
            **output_policy.manifest_fields(["validation_summary", "detailed_validation"]),
        }
        write_json(out / "run_validation_summary.json", run_validation_summary)
        if output_policy.should_write("detailed_validation"):
            write_json(
                out / "missing_feature_inputs.json",
                {
                    "init": args.init,
                    "missing_input_count": len(classified_missing_inputs),
                    "expected_unavailable_count": expected_unavailable_count,
                    "unexpected_missing_input_count": len(classified_missing_inputs) - expected_unavailable_count,
                    "missing_inputs": classified_missing_inputs,
                },
            )
            summary_payload = summary | {
                "model_feature_package_summary": model_feature_package["summary"],
                "output_policy": model_feature_package["output_policy"],
                "optional_outputs": model_feature_package["outputs"]["optional_outputs"],
            }
            write_json(out / "model_feature_package_summary.json", summary_payload)
            write_json(out / "operational_region_validation_report.json", operational_region_validation)
            write_json(out / "discovery_spatial_coverage_validation_report.json", discovery_spatial_coverage)
            write_json(out / "discovery_image_groups.json", discovery_image_groups)
            write_json(out / "discovery_overview_validation_report.json", discovery_overview_validation)
            write_json(out / "discovery_render_style_validation_report.json", discovery_style_validation)
        if output_flags["with_rule_qa"]:
            rule_qa = out / "rule_qa"
            write_json(
                rule_qa / "rule_signal_summaries.json",
                field_summaries | {"core_discovery_input": False, "classification": "rule_qa"},
            )
            write_json(
                rule_qa / "rule_trigger_map_manifest.json",
                broad_map_manifest | {"core_discovery_input": False, "classification": "rule_qa"},
            )
    except Exception as exc:  # noqa: BLE001 - CLI boundary.
        logging.exception("step03 failed")
        print(f"step03 실패: {exc}", file=sys.stderr)
        return 1

    print("\nAIFSHeavyRain step03 summary")
    print(f"  init: {args.init}")
    print(f"  indexed_field_count: {summary['indexed_field_count']}")
    print(f"  row_count: {summary['row_count']}")
    print(f"  feature_count: {summary['feature_count']}")
    print(f"  domain_count: {summary['domain_count']}")
    print(f"  product_count: {model_feature_package['summary']['product_count']}")
    print(f"  missing_input_count: {summary['missing_input_count']}")
    print(f"  calculation_error_count: {summary['calculation_error_count']}")
    print(f"  overview_source_panel_count: {discovery_source_panel_manifest['source_panel_count']}")
    print(f"  discovery_overviews_dir: {out / 'discovery_overviews'}")
    print(f"  discovery_overview_count: {discovery_overview_manifest['overview_count']}")
    print(f"  discovery_overview_validation: {discovery_overview_validation['validation_status']}")
    print(f"  discovery_style_validation: {discovery_style_validation['style_validation_status']}")
    print(f"  discovery_spatial_coverage: {discovery_spatial_coverage['spatial_coverage_status']}")
    print(f"  operational_region_validation: {operational_region_validation['validation_status']}")
    print(f"  operational_region_count: {summary['operational_region_count']}")
    print(f"  operational_region_row_count: {summary['operational_region_row_count']}")
    print(f"  with_rule_qa: {output_flags['with_rule_qa']}")
    print(f"  generate_overviews: {output_flags['generate_overviews']}")
    print(f"  export_csv: {output_flags['export_csv']}")
    print(f"  model_feature_package: {out / 'model_feature_package.json'}")
    print(f"  run_validation_summary: {out / 'run_validation_summary.json'}")
    if output_policy.should_write("detailed_validation"):
        print(f"  summary: {out / 'model_feature_package_summary.json'}")
    print(f"  log: {log_path(root, args.init)}")

    logging.info(
        "step03 success: row_count=%s operational_region_rows=%s discovery_source_panel_count=%s overview_count=%s rule_qa=%s export_csv=%s missing_input_count=%s calculation_error_count=%s",
        model_feature_package["summary"]["row_count"],
        model_feature_package["summary"]["operational_region_row_count"],
        model_feature_package["summary"]["overview_source_panel_count"],
        model_feature_package["summary"]["overview_count"],
        output_flags["with_rule_qa"],
        output_flags["export_csv"],
        model_feature_package["summary"]["missing_input_count"],
        model_feature_package["summary"]["calculation_error_count"],
    )

    if args.fail_on_missing and summary["missing_input_count"] > 0:
        print("step03 실패: missing feature inputs exist and --fail-on-missing was set.", file=sys.stderr)
        return 1
    return 0 if summary["status"] in {"success", "warning"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
