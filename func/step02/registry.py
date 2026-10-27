"""Registry for the operational diagnostic builders used by Step02 and Step05.

Only modules that define production :class:`BaseComponent` builders are
imported.  This keeps QA, History, Wiki, and unrelated helper modules outside
the diagnostic import path without changing the builder-name contract.
"""

from __future__ import annotations

import inspect
from functools import lru_cache
from importlib import import_module

from .base import BaseComponent

# Keep this sequence explicit and stable: registry iteration has historically
# been deterministic and any duplicate builder name must still fail fast.
BUILDER_MODULES = ("diagnostic_builders",)


@lru_cache(maxsize=1)
def discover_functions() -> dict[str, BaseComponent]:
    package = __package__ or "func.step02"

    registry: dict[str, BaseComponent] = {}
    for module_name in BUILDER_MODULES:
        module = import_module(f"{package}.{module_name}")
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls is BaseComponent or not issubclass(cls, BaseComponent):
                continue
            builder_name = str(getattr(cls, "builder_name", "")).strip()
            if not builder_name:
                continue
            if builder_name in registry:
                other = registry[builder_name].__class__.__name__
                raise RuntimeError(f"duplicate component builder_name={builder_name!r}: {other}, {cls.__name__}")
            registry[builder_name] = cls()
    return registry


def available_builders() -> set[str]:
    return set(discover_functions())


def get_component(builder_name: str) -> BaseComponent:
    registry = discover_functions()
    try:
        return registry[builder_name]
    except KeyError as exc:
        allowed = ", ".join(sorted(registry))
        raise KeyError(f"unsupported component builder {builder_name!r}. Available builders: {allowed}") from exc
