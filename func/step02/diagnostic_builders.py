"""Operational diagnostic builders used by Step02 and Step05.

This module consolidates the small builder-only modules without changing the
builder-name contract or any diagnostic calculation.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from .base import BaseComponent, CalculationResult, ComponentContext

G0 = 9.80665


def maybe_geopotential_height(arr: Any) -> Any:
    """Convert geopotential (m2 s-2) to geopotential height (gpm)."""
    try:
        val = float(arr.mean())
    except Exception:  # noqa: BLE001
        val = math.nan
    return arr / G0 if math.isfinite(val) and val > 2000 else arr


class GeopotentialHeight(BaseComponent):
    builder_name = "geopotential_height"

    def calculate(self, context: ComponentContext) -> CalculationResult:
        paths, missing = context.required_paths()
        if missing:
            return self.missing_only(missing)
        short_name, level = context.spec.required_inputs[0]
        return self.ok(maybe_geopotential_height(context.load(short_name, level)), paths.values())


def horizontal_convergence_si(u, v):
    """수평 수렴을 SI 단위 s-1로 반환한다. 양수는 수렴, 음수는 발산이다."""
    lat_name = next((n for n in ("latitude", "lat", "y") if n in u.coords), None)
    lon_name = next((n for n in ("longitude", "lon", "x") if n in u.coords), None)
    if lat_name is None or lon_name is None:
        raise ValueError("수평 수렴 계산에 위도·경도 좌표가 필요합니다.")

    latitude = u[lat_name]
    metres_per_degree_lon = 111_320.0 * np.cos(np.deg2rad(latitude))
    metres_per_degree_lon = metres_per_degree_lon.clip(min=1e-6)
    metres_per_degree_lat = 110_540.0
    du_dx = u.differentiate(lon_name) / metres_per_degree_lon
    dv_dy = v.differentiate(lat_name) / metres_per_degree_lat
    result = -(du_dx + dv_dy)
    result.attrs.update(
        {
            "long_name": "horizontal convergence",
            "units": "s-1",
            "sign_convention": "positive=convergence; negative=divergence",
        }
    )
    return result


def horizontal_convergence(u, v):
    """Return the legacy Step02 display value in 1e-5 s-1."""
    result = horizontal_convergence_si(u, v) * 1.0e5
    result.attrs.update(
        {
            "long_name": "horizontal convergence",
            "units": "1e-5 s-1",
            "sign_convention": "positive=convergence; negative=divergence",
        }
    )
    return result


class HorizontalConvergence(BaseComponent):
    builder_name = "horizontal_convergence"

    def calculate(self, context: ComponentContext) -> CalculationResult:
        paths, missing = context.required_paths()
        if missing:
            return self.missing_only(missing)
        if len(context.spec.required_inputs) < 2:
            return self.missing_only(
                [context.missing("u/v", context.spec.level, "horizontal_convergence requires u and v")]
            )
        (u_name, u_level), (v_name, v_level) = context.spec.required_inputs[:2]
        return self.ok(
            horizontal_convergence(context.load(u_name, u_level), context.load(v_name, v_level)),
            paths.values(),
        )


class SingleRaw(BaseComponent):
    builder_name = "single_raw"

    def calculate(self, context: ComponentContext) -> CalculationResult:
        paths, missing = context.required_paths()
        if missing:
            return self.missing_only(missing)
        short_name, level = context.spec.required_inputs[0]
        return self.ok(context.load(short_name, level), paths.values())


class SpecificHumidityGkg(BaseComponent):
    builder_name = "q_gkg"

    def calculate(self, context: ComponentContext) -> CalculationResult:
        paths, missing = context.required_paths()
        if missing:
            return self.missing_only(missing)
        short_name, level = context.spec.required_inputs[0]
        return self.ok(context.load(short_name, level) * 1000.0, paths.values())


class MoistureFluxMagnitude(BaseComponent):
    builder_name = "moisture_flux"

    def calculate(self, context: ComponentContext) -> CalculationResult:
        paths, missing = context.required_paths()
        if missing:
            return self.missing_only(missing)
        q_name, q_level = context.spec.required_inputs[0]
        u_name, u_level = context.spec.required_inputs[1]
        v_name, v_level = context.spec.required_inputs[2]
        q_gkg = context.load(q_name, q_level) * 1000.0
        u = context.load(u_name, u_level)
        v = context.load(v_name, v_level)
        wind = (u ** 2 + v ** 2) ** 0.5
        return self.ok(q_gkg * wind, paths.values())


def precip_to_mm(arr: Any) -> Any:
    """Return accumulated precipitation as mm."""
    units = str(
        getattr(arr, "attrs", {}).get("units")
        or getattr(arr, "attrs", {}).get("GRIB_units")
        or ""
    ).strip().lower()
    if units in {"m", "metre", "meter", "metres", "meters"}:
        return arr * 1000.0
    if "kg" in units or "mm" in units or "millimet" in units:
        return arr
    try:
        max_value = float(arr.max(skipna=True))
    except Exception:  # noqa: BLE001
        max_value = math.nan
    if math.isfinite(max_value) and max_value <= 5.0:
        return arr * 1000.0
    return arr


class _SinglePrecipBase(BaseComponent):
    precip_short_name: str = ""

    def calculate(self, context: ComponentContext) -> CalculationResult:
        paths, missing = context.required_paths()
        if missing:
            return self.missing_only(missing)
        arr = precip_to_mm(context.load(self.precip_short_name, None))
        return self.ok(arr, paths.values())


class SingleTotalPrecipitationMM(_SinglePrecipBase):
    builder_name = "single_tp_mm"
    precip_short_name = "tp"


class SingleConvectivePrecipitationMM(_SinglePrecipBase):
    builder_name = "single_cp_mm"
    precip_short_name = "cp"


class _IncrementPrecipBase(BaseComponent):
    precip_short_name: str = ""

    def calculate(self, context: ComponentContext) -> CalculationResult:
        paths, missing = context.required_paths()
        if missing:
            return self.missing_only(missing)
        prev_step = context.step - 6
        if prev_step < 0:
            return self.missing_only([
                context.missing(self.precip_short_name, None, "previous step does not exist for increment")
            ])
        prev_path = context.path(self.precip_short_name, None, prev_step)
        if prev_path is None:
            return self.missing_only([
                context.missing(self.precip_short_name, None, f"previous step f{prev_step:03d} not found")
            ])
        current = precip_to_mm(context.load(self.precip_short_name, None))
        previous = precip_to_mm(context.load(self.precip_short_name, None, prev_step))
        increment = (current - previous).clip(min=0)
        source_files = list(paths.values()) + [Path(prev_path)]
        return self.ok(increment, source_files)


class IncrementTotalPrecipitationMM(_IncrementPrecipBase):
    builder_name = "increment_tp_mm"
    precip_short_name = "tp"


class IncrementConvectivePrecipitationMM(_IncrementPrecipBase):
    builder_name = "increment_cp_mm"
    precip_short_name = "cp"


def maybe_hpa(arr: Any) -> Any:
    """Convert Pa-like pressure values to hPa, otherwise preserve as-is."""
    try:
        val = float(arr.mean())
    except Exception:  # noqa: BLE001
        val = math.nan
    return arr / 100.0 if math.isfinite(val) and val > 2000 else arr


def maybe_celsius(arr: Any) -> Any:
    """Convert Kelvin-like temperature values to degrees Celsius."""
    try:
        val = float(arr.mean())
    except Exception:  # noqa: BLE001
        val = math.nan
    return arr - 273.15 if math.isfinite(val) and val > 100 else arr


class SeaLevelPressureHpa(BaseComponent):
    builder_name = "single_msl_hpa"

    def calculate(self, context: ComponentContext) -> CalculationResult:
        paths, missing = context.required_paths()
        if missing:
            return self.missing_only(missing)
        short_name, level = context.spec.required_inputs[0]
        return self.ok(maybe_hpa(context.load(short_name, level)), paths.values())


class TemperatureCelsius(BaseComponent):
    builder_name = "temp_c"

    def calculate(self, context: ComponentContext) -> CalculationResult:
        paths, missing = context.required_paths()
        if missing:
            return self.missing_only(missing)
        short_name, level = context.spec.required_inputs[0]
        return self.ok(maybe_celsius(context.load(short_name, level)), paths.values())


class OmegaUpward(BaseComponent):
    builder_name = "omega_upward"

    def calculate(self, context: ComponentContext) -> CalculationResult:
        paths, missing = context.required_paths()
        if missing:
            return self.missing_only(missing)
        short_name, level = context.spec.required_inputs[0]
        arr = context.load(short_name, level)
        return self.ok((-arr).clip(min=0), paths.values())


class WindSpeed(BaseComponent):
    builder_name = "wind_speed"

    def calculate(self, context: ComponentContext) -> CalculationResult:
        paths, missing = context.required_paths()
        if missing:
            return self.missing_only(missing)
        if len(context.spec.required_inputs) < 2:
            return self.missing_only([
                context.missing("u/v", context.spec.level, "wind_speed requires two vector inputs")
            ])
        (u_name, u_level), (v_name, v_level) = context.spec.required_inputs[:2]
        u = context.load(u_name, u_level)
        v = context.load(v_name, v_level)
        return self.ok((u ** 2 + v ** 2) ** 0.5, paths.values())
