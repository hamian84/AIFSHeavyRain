"""Shared deterministic helpers for the Step06--09 Pass2 boundary."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from func.shared.pipeline_common import sha256sum
from func.shared.runtime_paths import runtime_path


class Pass2PipelineError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def load_json_object(path: Path, *, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Pass2PipelineError(code, f"Unable to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise Pass2PipelineError(code, f"JSON root must be an object: {path}")
    return value


def _validate_local_schema_references(schema: dict[str, Any], *, schema_name: str) -> None:
    """Fail fast when a canonical schema contains a dangling local ``$ref``.

    ``jsonschema.Draft202012Validator.check_schema`` validates the schema
    vocabulary but does not guarantee that every local JSON Pointer resolves.
    Resolve all local references here so a packaging regression is reported at
    schema-load time instead of later during candidate validation.
    """

    def resolve(reference: str) -> None:
        if not reference.startswith("#/"):
            raise Pass2PipelineError(
                "SCHEMA_LOAD_FAILED",
                f"{schema_name} contains unsupported non-local $ref: {reference}",
            )
        target: Any = schema
        for raw_part in reference.removeprefix("#/").split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                raise Pass2PipelineError(
                    "SCHEMA_LOAD_FAILED",
                    f"{schema_name} contains unresolved local $ref: {reference}",
                )
            target = target[part]

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if reference is not None:
                if not isinstance(reference, str):
                    raise Pass2PipelineError(
                        "SCHEMA_LOAD_FAILED",
                        f"{schema_name} contains a non-string $ref",
                    )
                resolve(reference)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)


def load_schema(root: Path, name: str) -> dict[str, Any]:
    schema = load_json_object(runtime_path(root, "prompts", "schemas", name), code="SCHEMA_LOAD_FAILED")
    _validate_local_schema_references(schema, schema_name=name)
    try:
        import jsonschema

        jsonschema.Draft202012Validator.check_schema(schema)
    except ModuleNotFoundError:
        pass
    except Exception as exc:
        raise Pass2PipelineError(
            "SCHEMA_LOAD_FAILED",
            f"{name} is invalid JSON Schema: {type(exc).__name__}: {exc}",
        ) from exc
    return schema


def validate_schema(root: Path, name: str, value: dict[str, Any], *, code: str = "SCHEMA_VALIDATION_FAILED") -> None:
    try:
        import jsonschema
        jsonschema.validate(value, load_schema(root, name))
    except Pass2PipelineError:
        raise
    except ModuleNotFoundError as exc:
        raise Pass2PipelineError(code, "jsonschema is required for Pass2 validation.") from exc
    except Exception as exc:
        raise Pass2PipelineError(code, f"{name} validation failed: {exc}") from exc


def write_validated_atomic(root: Path, path: Path, value: dict[str, Any], *, schema_name: str) -> None:
    """Validate then atomically replace one operational JSON output."""
    validate_schema(root, schema_name, value)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        raise Pass2PipelineError("ATOMIC_WRITE_FAILED", f"Unable to write {path}: {exc}") from exc
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def template_text_and_hash(root: Path, name: str) -> tuple[str, str]:
    path = runtime_path(root, "prompts", "templates", name)
    try:
        return path.read_text(encoding="utf-8"), sha256sum(path)
    except OSError as exc:
        raise Pass2PipelineError("PROMPT_MISSING", f"Pass2 prompt is missing: {path}") from exc


def image_data_url(path: Path) -> str:
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError as exc:
        raise Pass2PipelineError("PNG_MISSING", f"Unable to read evidence PNG: {path}") from exc
    return "data:image/png;base64," + encoded


def active_candidate_groups(package: dict[str, Any], candidate_id: str | None = None) -> list[dict[str, Any]]:
    groups = package.get("candidates")
    if not isinstance(groups, list):
        raise Pass2PipelineError("STEP06_PACKAGE_INVALID", "Step06 package has no candidates list.")
    selected = [item for item in groups if isinstance(item, dict) and (candidate_id is None or item.get("candidate_id") == candidate_id)]
    if candidate_id is not None and not selected:
        raise Pass2PipelineError("CANDIDATE_ID_MISSING", f"Candidate is not in Step06 package: {candidate_id}")
    return selected
