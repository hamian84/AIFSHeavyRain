"""Project-scoped dotenv loading with non-secret configuration provenance."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from func.shared.runtime_paths import logical_ref, resolve_logical_ref, runtime_path

from dotenv import load_dotenv


_ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class ProjectEnvLoad:
    """Facts about dotenv loading; values are deliberately never retained."""

    path: Path | None
    explicit_path: bool
    file_keys: frozenset[str]
    preexisting_keys: frozenset[str]
    loaded_keys: frozenset[str]
    enabled: bool

    def source_for(self, name: str) -> str:
        if name in self.preexisting_keys:
            return "process_env"
        if name in self.loaded_keys:
            return "env_file"
        return "default"

    def relative_path(self, root: Path) -> str | None:
        if self.path is None:
            return None
        try:
            return logical_ref(root, self.path)
        except ValueError:
            return self.path.name


def resolve_project_env_path(root: Path, env_file: Path | str | None = None) -> tuple[Path, bool]:
    """Resolve an explicit dotenv path or the standard project-local path."""
    explicit = env_file is not None
    candidate = Path(env_file).expanduser() if env_file is not None else runtime_path(root, "envi", "api_keys.env")
    if not candidate.is_absolute():
        candidate = resolve_logical_ref(root, candidate)
    return candidate.resolve(), explicit


def _assignment_names(path: Path) -> frozenset[str]:
    names: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        if value.startswith("export "):
            value = value[7:].lstrip()
        name, separator, _ = value.partition("=")
        name = name.strip()
        if not separator or not _ENV_KEY.fullmatch(name):
            raise ValueError(f"invalid dotenv assignment at line {line_number}")
        names.add(name)
    return frozenset(names)


def load_project_env(
    root: Path,
    *,
    env_file: Path | str | None = None,
    override: bool = False,
    enabled: bool = True,
) -> ProjectEnvLoad:
    """Load project dotenv settings once without exposing values.

    Missing default files are allowed. An explicitly selected missing file is an
    execution configuration error. Existing process variables remain authoritative
    unless ``override=True`` is explicitly requested.
    """
    if not enabled:
        return ProjectEnvLoad(
            None,
            env_file is not None,
            frozenset(),
            frozenset(os.environ),
            frozenset(),
            False,
        )
    path, explicit = resolve_project_env_path(root.resolve(), env_file)
    if not path.is_file():
        if explicit:
            raise FileNotFoundError(f"specified dotenv file does not exist: {path.name}")
        return ProjectEnvLoad(None, False, frozenset(), frozenset(os.environ), frozenset(), True)
    file_keys = _assignment_names(path)
    preexisting_keys = frozenset(os.environ)
    load_dotenv(dotenv_path=path, override=override, encoding="utf-8")
    loaded_keys = frozenset(
        name for name in file_keys if override or name not in preexisting_keys
    )
    return ProjectEnvLoad(path, explicit, file_keys, preexisting_keys, loaded_keys, True)
