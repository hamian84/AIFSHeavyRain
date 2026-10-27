"""Runtime directory resolution for relocatable AIFSHeavyRain deployments.

The project keeps stable logical references such as ``dain/...`` and
``daio/...`` inside JSON, while the physical directories may be configured by
environment variables loaded before a Step process starts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

from func.shared.prompt_paths import remap_prompt_parts


RUNTIME_DIR_ENV = {
    "dain": "AIFS_DAIN_DIR",
    "daio": "AIFS_DAIO_DIR",
    "daou": "AIFS_DAOU_DIR",
    "daba": "AIFS_DABA_DIR",
    "prompts": "AIFS_PROMPTS_DIR",
    "envi": "AIFS_ENVI_DIR",
    "logs": "AIFS_LOG_DIR",
    "tmp": "AIFS_TMP_DIR",
}


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def configured_project_root(default_root: Path) -> Path:
    """Return AIFS_ROOT when configured, otherwise the caller's project root."""
    raw = os.getenv("AIFS_ROOT", "").strip()
    return _resolved(Path(raw)) if raw else _resolved(default_root)


def _environment_applies(root: Path) -> bool:
    """Use runtime variables only for the real project root, not temp roots."""
    raw = os.getenv("AIFS_ROOT", "").strip()
    if not raw:
        return False
    try:
        return _resolved(root) == _resolved(Path(raw))
    except OSError:
        return False


def runtime_dir(root: Path, logical_name: str) -> Path:
    """Resolve one logical project directory to its configured physical path."""
    if logical_name not in RUNTIME_DIR_ENV:
        raise KeyError(f"unsupported runtime directory: {logical_name}")
    if _environment_applies(root):
        raw = os.getenv(RUNTIME_DIR_ENV[logical_name], "").strip()
        if raw:
            return _resolved(Path(raw))
    return root / logical_name


def runtime_path(root: Path, logical_name: str, *parts: str | int) -> Path:
    """Resolve runtime paths; translate historical prompt refs only if old file is gone.

    The legacy Step03 package keeps its original logical prompt references and
    therefore its fingerprint. The canonical content is stored once in stepNN.
    """
    base = runtime_dir(root, logical_name)
    original = base.joinpath(*(str(part) for part in parts))
    if logical_name == "prompts":
        prompt_parts = tuple(str(part) for part in parts)
        remapped = remap_prompt_parts(prompt_parts)
        if remapped is not None and not original.exists():
            return base.joinpath(*remapped)
    return original


def resolve_step04_output_dir(root: Path, init: str) -> Path:
    """Resolve the completed Step04 provider output without assuming a provider.

    ``run_status.json`` is the active-run source of truth.  If it is unavailable
    or does not identify a usable Step04 output, a single provider result may be
    resolved as a compatibility fallback.  Multiple provider outputs without an
    active-run selection are intentionally treated as ambiguous.
    """
    status_path = runtime_path(root, "daio", init, "run_status.json")
    if status_path.is_file():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            steps = status.get("steps") if isinstance(status, dict) else None
            step04 = steps.get("step04") if isinstance(steps, dict) else None
            summary = step04.get("summary") if isinstance(step04, dict) else None
            output_ref = summary.get("output_dir") if isinstance(summary, dict) else None
            if isinstance(output_ref, str) and output_ref.strip():
                candidate = resolve_logical_ref(root, output_ref)
                if (candidate / "pass1_result.json").is_file():
                    return candidate
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    step04_root = runtime_path(root, "daio", init, "step04")
    candidates = (
        sorted(path.parent for path in step04_root.glob("*/pass1_result.json") if path.is_file())
        if step04_root.is_dir()
        else []
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(
            f"Unable to resolve a completed Step04 provider output for init={init}."
        )
    raise RuntimeError(
        "Multiple Step04 provider outputs exist and run_status.json does not identify "
        "the completed output_dir."
    )


def resolve_logical_ref(root: Path, reference: str | Path) -> Path:
    """Resolve a stable logical reference such as daio/<init>/... to disk."""
    raw = str(reference).replace("\\", "/")
    candidate = Path(raw)
    if candidate.is_absolute():
        physical = _resolved(candidate)
        if not physical.exists():
            prompt_root = _resolved(runtime_dir(root, "prompts"))
            try:
                relative = physical.relative_to(prompt_root)
            except ValueError:
                pass
            else:
                return runtime_path(root, "prompts", *relative.parts).resolve()
        return physical

    parts = tuple(part for part in candidate.parts if part not in {"", "."})
    if ".." in parts:
        raise ValueError(f"parent traversal is not allowed in logical references: {reference}")
    if parts and parts[0] in RUNTIME_DIR_ENV:
        return runtime_path(root, parts[0], *parts[1:]).resolve()

    resolved = _resolved(root / candidate)
    try:
        resolved.relative_to(_resolved(root))
    except ValueError as exc:
        raise ValueError(f"reference escapes project root: {reference}") from exc
    return resolved


def logical_ref(root: Path, path: Path) -> str:
    """Return a host-independent logical project reference for a physical path."""
    target = _resolved(path)

    for logical_name in RUNTIME_DIR_ENV:
        base = _resolved(runtime_dir(root, logical_name))
        try:
            relative = target.relative_to(base)
        except ValueError:
            continue
        return Path(logical_name, relative).as_posix()

    try:
        return target.relative_to(_resolved(root)).as_posix()
    except ValueError as exc:
        raise ValueError(f"project/runtime path cannot be expressed logically: {path}") from exc


def configured_runtime_dirs(root: Path) -> dict[str, Path]:
    return {name: runtime_dir(root, name) for name in RUNTIME_DIR_ENV}


def sanitize_physical_paths(text: str, *, root: Path, replacement: str = "<RUNTIME_PATH>") -> str:
    """Remove configured host-specific runtime roots from operational text."""
    result = str(text)
    paths: Iterable[Path] = [_resolved(root), configured_project_root(root), *configured_runtime_dirs(root).values()]
    for path in sorted({str(item) for item in paths}, key=len, reverse=True):
        if path:
            result = result.replace(path, replacement)
    return result
