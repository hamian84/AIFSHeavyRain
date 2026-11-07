"""Runtime wiring for Step step08: analysis and validation are independently imported.

The Step entry script chooses these modules and their workflow coordinator.
No legacy integrated implementation is imported or executed.
"""

from __future__ import annotations

from inspect import isclass, isfunction
from . import synthesis_context as context

_configured = False


def configure(*, analysis, validation, workflow, support):
    """Bind existing global references without changing any assessment function."""
    global _configured
    if _configured:
        return

    modules = (analysis, validation, workflow, support)
    context_values = {
        name: value
        for name, value in vars(context).items()
        if not name.startswith("__") and name != "annotations"
    }

    # Only source definitions owned by a role are exported. Imported context
    # symbols are resolved separately to preserve their original identities.
    exports = {}
    for module in modules:
        for name, value in vars(module).items():
            if name.startswith("__"):
                continue
            if not (isfunction(value) or isclass(value)):
                continue
            if getattr(value, "__module__", None) != module.__name__:
                continue
            if name in exports:
                raise RuntimeError(f"Step step08 duplicate role definition: {name}")
            exports[name] = value

    for module in modules:
        module.__dict__.update(context_values)
        module.__dict__.update(exports)

    _configured = True
