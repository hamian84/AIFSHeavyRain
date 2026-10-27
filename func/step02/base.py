"""Base classes for reusable processing functions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class CalculationResult:
    """Result returned by a component calculation."""

    array: Any | None
    missing_inputs: list[dict[str, Any]]
    source_files: list[Path]


@dataclass(frozen=True)
class ComponentContext:
    """Runtime context passed from a step script into a reusable function.

    A processing function should use this context to find and load the fields it needs.
    This keeps field indexing and GRIB I/O in the step script, while keeping the
    actual calculation logic in func/*.py.
    """

    spec: Any
    step: int
    index: Mapping[tuple[str, int | None, int], Path]
    open_field: Callable[[Path, str, int | None], Any]

    def path(self, short_name: str, level: int | None, step: int | None = None) -> Path | None:
        return self.index.get((short_name, level, self.step if step is None else step))

    def load(self, short_name: str, level: int | None, step: int | None = None) -> Any:
        target_step = self.step if step is None else step
        path = self.path(short_name, level, target_step)
        if path is None:
            raise FileNotFoundError(f"input field not found: short_name={short_name} level={level} step=f{target_step:03d}")
        return self.open_field(path, short_name, level)

    def missing(self, short_name: str, level: int | None, reason: str, step: int | None = None) -> dict[str, Any]:
        return {
            "feature_id": self.spec.feature_id,
            "forecast_hour": self.step if step is None else step,
            "short_name": short_name,
            "level": level,
            "reason": reason,
        }

    def required_paths(self) -> tuple[dict[tuple[str, int | None], Path], list[dict[str, Any]]]:
        paths: dict[tuple[str, int | None], Path] = {}
        missing: list[dict[str, Any]] = []
        for short_name, level in self.spec.required_inputs:
            path = self.path(short_name, level)
            if path is None:
                missing.append(self.missing(short_name, level, "input file not found"))
            else:
                paths[(short_name, level)] = path
        return paths, missing


class BaseComponent:
    """Base class for reusable processing functions.

    Subclasses must set builder_name and implement calculate().  The registry
    discovers subclasses in func/*.py and maps builder_name to the class.
    """

    builder_name: str = ""

    def calculate(self, context: ComponentContext) -> CalculationResult:  # pragma: no cover - interface only
        raise NotImplementedError

    @staticmethod
    def ok(array: Any, source_files: Sequence[Path]) -> CalculationResult:
        return CalculationResult(array=array, missing_inputs=[], source_files=sorted(set(source_files)))

    @staticmethod
    def missing_only(missing_inputs: list[dict[str, Any]]) -> CalculationResult:
        return CalculationResult(array=None, missing_inputs=missing_inputs, source_files=[])
