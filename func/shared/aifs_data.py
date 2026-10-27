"""Shared AIFS GRIB indexing, preflight, and field-loading helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from func.step02 import aifs_feature_engine as engine
from func.shared.runtime_paths import runtime_path


RAW_EXTENSIONS = {".grib", ".grib2", ".grb", ".grb2"}


def list_grib_files(raw_dir: Path) -> list[Path]:
    return sorted(path for path in raw_dir.rglob("*") if path.is_file() and path.suffix.lower() in RAW_EXTENSIONS)


def valid_time_utc(init: str, forecast_hour: int) -> str:
    base = datetime.strptime(init, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    return (base + timedelta(hours=int(forecast_hour))).isoformat()


def parse_file_refs(root: Path, init: str, raw_dir: Path):
    return engine.parse_file_refs(root, init, raw_dir)


def import_science_stack():
    return engine.import_science_stack()


def open_field(path: Path, short_name: str, level: int | None, np: Any, xr: Any):
    return engine.open_field(path, short_name, level, np, xr)


def lat_lon_names(arr: Any):
    return engine.lat_lon_names(arr)


def select_pressure_level(arr: Any, level: int):
    return engine.select_pressure_level(arr, level)


def load_feature_definitions(path: Path):
    return engine.load_feature_definitions(path)


def build_feature_array(*, spec: Any, step: int, index: Any, np: Any, xr: Any):
    return engine.build_feature_array(spec=spec, step=step, index=index, np=np, xr=xr)


def build_loaders(root: Path, init: str, raw_dir: Path):
    index, _ = parse_file_refs(root, init, raw_dir)
    np, xr = import_science_stack()

    def load(short_name: str, level: int | None, forecast_hour: int):
        path = index.get((short_name, level, int(forecast_hour)))
        if path is None:
            raise FileNotFoundError(f"AIFS field not found: {short_name} level={level} F{forecast_hour:03d}")
        return open_field(path, short_name, level, np, xr)

    return index, load, np, xr


def preflight_raw_inputs(*, root: Path, init: str, raw_dir: Path) -> dict[str, Any]:
    """Validate usable raw input in memory without creating inventory artifacts."""
    files = list_grib_files(raw_dir)
    if not files:
        raise FileNotFoundError(f"no GRIB files found under {raw_dir}")
    index, unmatched = parse_file_refs(root, init, raw_dir)
    if not index:
        raise RuntimeError("no usable GRIB field references were derived from raw filenames")
    hours = sorted({hour for _, _, hour in index})
    expected_hours = list(range(0, 121, 6))
    missing_hours = sorted(set(expected_hours) - set(hours))
    if missing_hours:
        raise RuntimeError(f"missing forecast hours: {missing_hours}")
    specs = engine.load_feature_definitions(runtime_path(root, "daba", "ai_pass1", "feature_definitions.yaml"))
    missing_required: list[dict[str, Any]] = []
    for spec in specs:
        # Six-hour increment features cannot exist at F000 by construction.
        required_hours = [hour for hour in expected_hours if not (hour == 0 and str(spec.builder).startswith("increment_"))]
        for short_name, level in spec.required_inputs:
            for hour in required_hours:
                if (short_name, level, hour) not in index:
                    missing_required.append({"feature_id": spec.feature_id, "short_name": short_name, "level": level, "forecast_hour": hour})
    if missing_required:
        first = missing_required[0]
        raise RuntimeError(
            "required AIFS field is unavailable: "
            f"feature={first['feature_id']} short_name={first['short_name']} "
            f"level={first['level']} forecast_hour={first['forecast_hour']}"
        )
    variables = sorted({f"{short_name}:{level if level is not None else 'surface'}" for short_name, level, _ in index})
    if not variables:
        raise RuntimeError("raw GRIB index does not contain any variables")
    return {
        "raw_file_count": len(files),
        "indexed_field_reference_count": len(index),
        "forecast_hours": hours,
        "variable_count": len(variables),
        "unmatched_file_count": len(unmatched),
        "required_field_check": "passed",
        "valid_time_start_utc": valid_time_utc(init, hours[0]),
        "valid_time_end_utc": valid_time_utc(init, hours[-1]),
    }
